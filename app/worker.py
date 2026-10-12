"""One durable dispatcher per deployment. Heavy local ASR runs outside the event loop."""

import asyncio
import logging
from pathlib import Path

from app.config import Settings
from app.db import Database

logger = logging.getLogger(__name__)


class InferenceBusyError(RuntimeError):
    pass


class Worker:
    def __init__(self, settings: Settings, db: Database, asr=None, connector=None):
        self.settings, self.db = settings, db
        self.asr = asr
        self.connector = connector
        self.inference_lock = asyncio.Lock()
        self.inference_waiters = 0
        self.running = False
        self.task: asyncio.Task | None = None
        self.audio_tasks: dict[str, asyncio.Task] = {}
        self.warmup_task: asyncio.Task | None = None
        self.model_state = "not_loaded"

    def get_asr(self):
        if self.asr is None:
            from app.asr import ASRSettings, LocalASR

            self.asr = LocalASR(
                ASRSettings(
                    backend=self.settings.asr_backend,
                    model_path=self.settings.asr_model_path,
                    device=self.settings.asr_device,
                    cpu_threads=self.settings.asr_cpu_threads,
                    chunk_seconds=self.settings.asr_chunk_seconds,
                    compute_type=self.settings.asr_compute_type,
                    max_audio_seconds=self.settings.max_audio_seconds,
                    max_input_bytes=self.settings.max_upload_mb * 1024 * 1024,
                    temp_root=self.settings.data_dir / "tmp",
                )
            )
        return self.asr

    async def transcribe(self, path: Path, language: str | None):
        if self.inference_waiters >= self.settings.max_inference_queue:
            raise InferenceBusyError("Local inference queue is full")
        self.inference_waiters += 1
        try:
            async with self.inference_lock:
                inference = asyncio.create_task(
                    asyncio.to_thread(self.get_asr().transcribe, path, language=language)
                )
                try:
                    return await asyncio.shield(inference)
                except asyncio.CancelledError:
                    # Python cannot cancel the model's native thread. Keep its lock
                    # and input file alive until it really exits.
                    await asyncio.gather(inference, return_exceptions=True)
                    raise
        finally:
            self.inference_waiters -= 1

    async def prepare_model(self):
        self.model_state = "loading"
        try:
            async with self.inference_lock:
                await asyncio.to_thread(self.get_asr().prepare)
            self.model_state = "ready"
        except Exception as exc:
            self.model_state = "failed"
            logger.warning("Model warmup failed (%s)", type(exc).__name__)

    def get_connector(self):
        if self.connector is None:
            from app.connectors.vexa import VexaConfig, VexaConnector

            self.connector = VexaConnector(
                VexaConfig(
                    base_url=self.settings.vexa_api_url,
                    api_key=self.settings.vexa_api_key,
                    enabled=self.settings.teams_enabled,
                    authorization_reference=self.settings.teams_authorization_reference,
                    allowed_tenant_ids=tuple(
                        x.strip()
                        for x in self.settings.teams_allowed_tenant_ids.split(",")
                        if x.strip()
                    ),
                    allowed_meeting_ids=tuple(
                        x.strip()
                        for x in self.settings.teams_allowed_meeting_ids.split(",")
                        if x.strip()
                    ),
                    state_path=self.settings.data_dir / "connector.sqlite3",
                    bot_name=self.settings.bot_name,
                )
            )
        return self.connector

    async def start(self):
        self.db.recover()
        self.running = True
        if self.settings.asr_preload:
            self.warmup_task = asyncio.create_task(self.prepare_model())
        self.task = asyncio.create_task(self.run())

    async def close(self):
        self.running = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.audio_tasks:
            await asyncio.gather(*self.audio_tasks.values(), return_exceptions=True)
        if self.warmup_task:
            await asyncio.gather(self.warmup_task, return_exceptions=True)
        if self.connector:
            await self.connector.aclose()

    async def run(self):
        while self.running:
            try:
                await self.tick()
            except Exception:
                logger.exception("Dispatcher tick failed")
            await asyncio.sleep(self.settings.poll_seconds)

    async def tick(self):
        for meeting in self.db.active():
            if meeting["source"] == "upload":
                if meeting["status"] == "stopping":
                    self.db.update(
                        meeting["id"],
                        status="cancelled",
                        error="Stopped by operator; in-flight inference result will be discarded.",
                    )
                elif (
                    meeting["status"] == "queued"
                    and not self.audio_tasks
                    and self.inference_waiters < self.settings.max_inference_queue
                ):
                    if self.db.claim(meeting["id"], "transcribing"):
                        task = asyncio.create_task(self.process_audio(meeting))
                        self.audio_tasks[meeting["id"]] = task
                        task.add_done_callback(
                            lambda _, key=meeting["id"]: self.audio_tasks.pop(key, None)
                        )
            else:
                await self.process_teams(meeting)

    async def process_audio(self, meeting: dict):
        audio_path = Path(meeting["audio_path"])
        try:
            segments = await self.transcribe(
                audio_path, None if meeting["language"] == "auto" else meeting["language"]
            )
            current = self.db.get(meeting["id"])
            if current["stop_requested"] or current["status"] == "cancelled":
                self.db.update(meeting["id"], status="cancelled", error=None)
                return
            self.db.put_segments(meeting["id"], segments)
            self.db.update(meeting["id"], status="completed", error=None)
        except Exception as exc:
            logger.warning("Local ASR failed for job %s (%s)", meeting["id"], type(exc).__name__)
            # Exception text can contain filenames/tokens; don't publish arbitrary provider details.
            self.db.update(
                meeting["id"],
                status="failed",
                error=f"Local ASR failed ({type(exc).__name__}). Check model files and server logs; no external fallback was used.",
            )
        finally:
            if not self.settings.retain_audio:
                audio_path.unlink(missing_ok=True)
                self.db.update(meeting["id"], audio_path=None)

    async def process_teams(self, meeting: dict):
        meeting_id = meeting["id"]
        try:
            connector = self.get_connector()
            if meeting["stop_requested"] and meeting["status"] != "queued":
                meeting["status"] = "stopping"
            if meeting["status"] == "queued":
                if self.settings.asr_preload:
                    if self.model_state in {"not_loaded", "loading"}:
                        return
                    if self.model_state == "failed":
                        self.db.update(
                            meeting_id, status="failed",
                            error="Local ASR warmup failed. Check model files, dependencies and memory; no bot was joined.",
                        )
                        return
                if not self.db.claim(meeting_id, "joining"):
                    return
                handle = await connector.join(
                    meeting_url=meeting["meeting_url"],
                    title=meeting["title"],
                    language=meeting["language"],
                    idempotency_key=meeting_id,
                    acknowledgement=True,
                )
                current = self.db.get(meeting_id)
                self.db.update(
                    meeting_id,
                    handle=handle,
                    status="stopping" if current["stop_requested"] else "joining",
                    error=None,
                )
                return
            if meeting["status"] == "stopping":
                if not meeting["handle"]:
                    self.db.update(
                        meeting_id,
                        status="needs_review",
                        error="A started join has no persisted handle. Verify the bot has left in Vexa/Teams before restarting.",
                    )
                    return
                result = await connector.stop(meeting["handle"])
                status = result.get("status", "stopping")
                if status == "needs_review":
                    self.db.update(
                        meeting_id,
                        status="needs_review",
                        error=result.get("error")
                        or "Confirm the bot has left Teams in Vexa before restarting.",
                    )
                elif status in {"completed", "stopped", "cancelled"}:
                    snapshot = await connector.poll(meeting["handle"])
                    self.db.put_segments(
                        meeting_id,
                        snapshot.get("segments", []),
                        replace_snapshot=snapshot.get("authoritative", False),
                    )
                    verified = snapshot.get("status")
                    if verified in {"completed", "failed", "cancelled"}:
                        self.db.update(
                            meeting_id,
                            status="failed" if verified == "failed" else "completed",
                            error=snapshot.get("error") if verified == "failed" else None,
                        )
                    elif verified == "needs_review":
                        self.db.update(
                            meeting_id,
                            status="needs_review",
                            error=snapshot.get("error") or "Cannot verify the bot stopped.",
                        )
                    else:
                        self.db.update(
                            meeting_id,
                            status="stopping",
                            error="Stop requested; waiting for verified terminal session status.",
                        )
                return
            if not meeting["handle"]:
                self.db.update(
                    meeting_id,
                    status="needs_review",
                    error="Missing connector handle. Verify Vexa session before retrying.",
                )
                return
            snapshot = await connector.poll(meeting["handle"])
            self.db.put_segments(
                meeting_id,
                snapshot.get("segments", []),
                replace_snapshot=snapshot.get("authoritative", False),
            )
            if snapshot["status"] == "needs_review" and meeting["handle"].get("meeting_id"):
                # A known browser session stopped at an access gate must be torn down,
                # not abandoned while the application stops polling it.
                result = await connector.stop(meeting["handle"])
                if result.get("status") == "needs_review":
                    self.db.update(
                        meeting_id,
                        status="needs_review",
                        error=result.get("error") or snapshot.get("error"),
                    )
                else:
                    self.db.update(
                        meeting_id,
                        status="stopping",
                        stop_requested=1,
                        error=snapshot.get("error")
                        or "Access gate reached; stopping the known bot session.",
                    )
                return
            mapping = {"requested": "joining", "waiting": "waiting_room", "active": "recording"}
            status = mapping.get(snapshot["status"], snapshot["status"])
            if status not in {
                "joining",
                "waiting_room",
                "recording",
                "transcribing",
                "stopping",
                "completed",
                "failed",
                "needs_review",
            }:
                status = "needs_review"
            if status != meeting["status"] or snapshot.get("error") != meeting["error"]:
                self.db.update(meeting_id, status=status, error=snapshot.get("error"))
        except Exception as exc:
            logger.warning("Teams connector error for job %s (%s)", meeting_id, type(exc).__name__)
            # Never blindly retry a possibly successful external join or stop.
            if meeting["status"] == "stopping" and getattr(exc, "retryable", False):
                self.db.update(
                    meeting_id,
                    error="Stop is still being verified; the local gateway is temporarily unavailable. Polling will continue.",
                )
            elif meeting["status"] in {"queued", "joining", "stopping"}:
                self.db.update(
                    meeting_id,
                    status="needs_review",
                    error=f"Teams operation could not be confirmed ({type(exc).__name__}). Check the session in Vexa/Teams before trying again.",
                )
            else:
                self.db.update(
                    meeting_id,
                    error=f"Teams status is temporarily unavailable ({type(exc).__name__}); polling will continue.",
                )
