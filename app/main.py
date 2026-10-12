"""Authenticated API, local inference endpoint and static operator dashboard."""

import asyncio
import hmac
import logging
import os
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings, get_settings
from app.db import ACTIVE, ConflictError, Database
from app.readiness import service_readiness
from app.worker import InferenceBusyError, Worker

logger = logging.getLogger(__name__)
Language = Literal["auto", "kk", "ru", "en"]


class CreateMeeting(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    meeting_url: str = Field(min_length=10, max_length=4000)
    language: Language = "auto"
    consent_confirmed: bool = False


class EditSegment(BaseModel):
    text: str = Field(max_length=20000)
    speaker: str | None = Field(default=None, max_length=200)
    version: int = Field(ge=1)


def public_meeting(meeting: dict) -> dict:
    return {
        key: value
        for key, value in meeting.items()
        if key not in {"audio_path", "handle", "meeting_url"}
    }


class AuthenticationBoundary:
    """Reject unauthorized bodies before Starlette starts multipart parsing."""

    def __init__(self, app, operator_token: str, inference_token: str):
        self.app, self.operator_token, self.inference_token = app, operator_token, inference_token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            expected = (
                self.operator_token
                if path.startswith("/api/")
                else self.inference_token
                if path.startswith("/v1/")
                else None
            )
            if expected is not None:
                authorization = dict(scope.get("headers", [])).get(b"authorization", b"")
                valid = bool(expected) and hmac.compare_digest(
                    authorization, b"Bearer " + expected.encode()
                )
                if not valid:
                    status = 401 if expected else 503
                    await JSONResponse(
                        {
                            "detail": "Invalid or missing Bearer token"
                            if expected
                            else "Service authentication is not configured"
                        },
                        status_code=status,
                    )(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """Enforce upload limits even for chunked requests, before multipart spooling."""

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(
                scope, receive, send
            )
            return
        if length > self.limit:
            await JSONResponse({"detail": "Upload exceeds server limit"}, status_code=413)(
                scope, receive, send
            )
            return
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > self.limit:
                raise HTTPException(status_code=413, detail="Upload exceeds server limit")
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings: Settings | None = None, *, asr=None, connector=None) -> FastAPI:
    settings = settings or get_settings()
    os.umask(0o077)
    settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (settings.data_dir / "tmp").mkdir(mode=0o700, parents=True, exist_ok=True)
    uploads_dir = settings.data_dir / "audio"
    uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = Database(settings.db_path)
    worker = Worker(settings, db, asr=asr, connector=connector)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.app_api_token:
            logger.warning("APP_API_TOKEN not configured: all protected endpoints are disabled")
        if settings.worker_enabled:
            await worker.start()
        yield
        if settings.worker_enabled:
            await worker.close()

    app = FastAPI(
        title="Teams ASR Platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db, app.state.worker, app.state.settings = db, worker, settings
    app.add_middleware(
        BodyLimitMiddleware, limit=settings.max_upload_mb * 1024 * 1024 + 1024 * 1024
    )
    app.add_middleware(
        AuthenticationBoundary,
        operator_token=settings.app_api_token,
        inference_token=settings.asr_api_token,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=[x.strip() for x in settings.trusted_hosts.split(",")]
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def check_token(expected: str, authorization: str | None):
        if not expected:
            raise HTTPException(503, "Service authentication is not configured")
        provided = (
            authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        )
        if not hmac.compare_digest(provided.encode(), expected.encode()):
            raise HTTPException(
                401, "Invalid or missing Bearer token", headers={"WWW-Authenticate": "Bearer"}
            )

    def operator(authorization: Annotated[str | None, Header()] = None):
        check_token(settings.app_api_token, authorization)

    def asr_client(authorization: Annotated[str | None, Header()] = None):
        check_token(settings.asr_api_token, authorization)

    def require_model():
        if asr is None and not settings.model_configured:
            raise HTTPException(
                503, "Local ASR model is not installed. Run the model download script first."
            )

    def get_meeting(meeting_id: str):
        meeting = db.get(meeting_id)
        if meeting is None:
            raise HTTPException(404, "Meeting not found")
        return meeting

    async def save_upload(file: UploadFile) -> Path:
        # Original names never become local paths. No URLs are accepted for ingestion.
        suffix = Path(file.filename or "audio.wav").suffix.lower()
        if suffix not in {
            ".wav",
            ".mp3",
            ".m4a",
            ".mp4",
            ".ogg",
            ".flac",
            ".webm",
            ".aac",
            ".opus",
        }:
            raise HTTPException(415, "Unsupported audio/video extension")
        fd, filename = tempfile.mkstemp(prefix="audio-", suffix=suffix, dir=uploads_dir)
        size = 0
        path = Path(filename)
        try:
            with os.fdopen(fd, "wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, "Upload exceeds server limit")
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "Empty audio file")
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/readiness", dependencies=[Depends(operator)])
    def readiness():
        return service_readiness(settings, worker)

    @app.get("/api/config", dependencies=[Depends(operator)])
    async def config():
        return {
            "asr_backend": settings.asr_backend,
            "model_configured": settings.model_configured or asr is not None,
            "teams_enabled": settings.teams_enabled,
            "max_upload_mb": settings.max_upload_mb,
            "max_audio_seconds": settings.max_audio_seconds,
            "bot_name": settings.bot_name,
            "retain_audio": settings.retain_audio,
            "retention_days": settings.retention_days,
            "notice": "Teams browser connector is experimental and requires organization/platform approval. Participant notice and permission are required. Speaker labels are unverified cues, not biometric identification.",
        }

    @app.get("/api/meetings", dependencies=[Depends(operator)])
    async def list_meetings(
        limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)
    ):
        return {"items": db.list(limit, offset)}

    @app.post("/api/meetings", status_code=201, dependencies=[Depends(operator)])
    async def create_meeting(payload: CreateMeeting):
        require_model()
        if not payload.title.strip():
            raise HTTPException(422, "Title cannot be blank")
        if not settings.teams_enabled:
            raise HTTPException(
                403, "Teams connector is disabled pending documented approval and configuration"
            )
        if not payload.consent_confirmed:
            raise HTTPException(422, "Confirm meeting-specific authority and participant notice")
        from app.connectors.vexa import ConnectorError

        try:
            # Validation happens before durable admission; connector repeats all policy checks before joining.
            worker.get_connector().validate_join(
                payload.meeting_url, payload.consent_confirmed, payload.language
            )
        except (ValueError, ConnectorError) as exc:
            # Connector-specific policy exceptions are intentionally normalized here.
            raise HTTPException(422, str(exc)) from exc
        try:
            result = db.create(
                title=payload.title.strip(),
                source="teams",
                language=payload.language,
                meeting_url=payload.meeting_url,
                authorization_reference=settings.teams_authorization_reference,
                max_pending=settings.max_pending_jobs,
            )
        except ConflictError as exc:
            raise HTTPException(429, str(exc)) from exc
        return public_meeting(result)

    @app.post("/api/uploads", status_code=201, dependencies=[Depends(operator)])
    async def upload_audio(
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form(max_length=200)] = "Recording",
        language: Annotated[Language, Form()] = "auto",
        consent_confirmed: Annotated[bool, Form()] = False,
    ):
        require_model()
        if not consent_confirmed:
            raise HTTPException(422, "Confirm that you are authorized to process this recording")
        path = await save_upload(file)
        try:
            result = db.create(
                title=title.strip() or "Recording",
                source="upload",
                language=language,
                audio_path=str(path),
                max_pending=settings.max_pending_jobs,
            )
        except ConflictError as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(429, str(exc)) from exc
        return public_meeting(result)

    @app.get("/api/meetings/{meeting_id}", dependencies=[Depends(operator)])
    async def meeting_detail(meeting_id: str):
        return public_meeting(get_meeting(meeting_id))

    @app.post("/api/meetings/{meeting_id}/stop", dependencies=[Depends(operator)])
    async def stop_meeting(meeting_id: str):
        meeting = get_meeting(meeting_id)
        if meeting["status"] not in ACTIVE:
            return public_meeting(meeting)
        if meeting["status"] == "queued":
            db.update(meeting_id, stop_requested=1, status="cancelled", error=None)
            if (
                meeting["source"] == "upload"
                and not settings.retain_audio
                and meeting["audio_path"]
            ):
                Path(meeting["audio_path"]).unlink(missing_ok=True)
                db.update(meeting_id, audio_path=None)
        else:
            db.update(meeting_id, stop_requested=1, status="stopping", error=None)
        return public_meeting(get_meeting(meeting_id))

    @app.post("/api/meetings/{meeting_id}/retry", dependencies=[Depends(operator)])
    async def retry_meeting(meeting_id: str):
        meeting = get_meeting(meeting_id)
        if meeting["source"] == "teams":
            raise HTTPException(
                409,
                "Check the prior bot session in Vexa/Teams; create a new meeting only after confirming it is stopped",
            )
        if meeting["status"] not in {"failed", "cancelled"}:
            raise HTTPException(409, "Only failed or cancelled audio jobs can be retried")
        if not meeting["audio_path"] or not Path(meeting["audio_path"]).is_file():
            raise HTTPException(
                409, "Source audio was deleted according to retention settings; upload it again"
            )
        require_model()
        db.update(meeting_id, status="queued", stop_requested=0, error=None)
        return public_meeting(get_meeting(meeting_id))

    @app.patch("/api/meetings/{meeting_id}/segments/{segment_id}", dependencies=[Depends(operator)])
    async def edit_segment(meeting_id: str, segment_id: str, payload: EditSegment):
        try:
            return public_meeting(
                db.edit_segment(
                    meeting_id, segment_id, payload.text, payload.speaker, payload.version
                )
            )
        except KeyError as exc:
            raise HTTPException(404, "Meeting or segment not found") from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/meetings/{meeting_id}/export", dependencies=[Depends(operator)])
    async def export(meeting_id: str, format: Literal["txt", "md", "docx", "pdf"] = "txt"):
        from app.exports import export_transcript

        meeting = get_meeting(meeting_id)
        contents, mime_type, filename = await asyncio.to_thread(
            export_transcript, public_meeting(meeting), format
        )
        # Use ASCII server-controlled filename so user titles cannot inject response headers.
        extension = "docx" if format == "docx" else format
        return Response(
            contents,
            media_type=mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="transcript-{meeting_id}.{extension}"'
            },
        )

    @app.post("/v1/audio/transcriptions", dependencies=[Depends(asr_client)])
    async def transcriptions(
        file: Annotated[UploadFile, File()],
        model: Annotated[str, Form()] = "gigaam",
        language: Annotated[str | None, Form()] = None,
        response_format: Annotated[str, Form()] = "verbose_json",
    ):
        require_model()
        if response_format not in {"json", "verbose_json", "text"}:
            raise HTTPException(422, "Supported response formats: json, verbose_json, text")
        if language not in {None, "", "auto", "kk", "ru", "en"}:
            raise HTTPException(422, "Supported languages: kk, ru, en, auto")
        path = await save_upload(file)
        try:
            duration = 0.0
            try:
                with wave.open(str(path), "rb") as wav:
                    duration = wav.getnframes() / wav.getframerate()
            except (wave.Error, EOFError):
                pass
            segments = await worker.transcribe(
                path, None if language in {None, "", "auto"} else language
            )
            text = " ".join(segment["text"] for segment in segments)
            if response_format == "text":
                return Response(text, media_type="text/plain")
            if response_format == "json":
                return {"text": text}
            return {
                "text": text,
                "language": language or "auto",
                "duration": duration or max((segment["end"] for segment in segments), default=0),
                "segments": [{"id": index, **segment} for index, segment in enumerate(segments)],
            }
        except HTTPException:
            raise
        except InferenceBusyError as exc:
            raise HTTPException(
                429, "Local inference queue is full; retry later", headers={"Retry-After": "5"}
            ) from exc
        except Exception as exc:
            logger.warning("Internal ASR request failed (%s)", type(exc).__name__)
            raise HTTPException(
                503,
                "Local ASR failed; check model configuration and audio format. No external fallback.",
            ) from exc
        finally:
            path.unlink(missing_ok=True)

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
