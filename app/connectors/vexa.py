"""Teams browser capture through a pinned, self-hosted Vexa gateway.

This is not a Microsoft Graph media bot or a claim of platform permission.
See docs/TEAMS_CONNECTOR.md for the deployment/approval boundary and wire contract.
The durable journal guarantees at most one *attempt* per idempotency key. Vexa has
no create idempotency token, so an ambiguous POST is deliberately never replayed.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

UPSTREAM_REVISION = "f1a62fbbdfd2c98cb162582d82c31fe8102acc89"
_TERMINAL = {"completed", "failed", "cancelled"}
_ACTIVE = {"requested", "joining", "awaiting_admission", "active", "stopping", "needs_help"}
_STATUS = {
    "requested": "requested",
    "joining": "joining",
    "awaiting_admission": "waiting",
    "active": "active",
    "stopping": "stopping",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "completed",
    "needs_help": "needs_review",
}
_BLOCKED_REASONS = {
    "awaiting_admission_rejected",
    "awaiting_admission_timeout",
    "auth_session_missing",
    "validation_error",
    "join_failure",
    "evicted",
}
_HOSTS = {"teams.microsoft.com", "teams.live.com"}


class ConnectorError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class ConnectorDisabled(ConnectorError):
    pass


class ConnectorPolicyError(ConnectorError):
    pass


@dataclass(frozen=True)
class VexaConfig:
    base_url: str
    api_key: str
    enabled: bool = False
    authorization_reference: str = ""
    allowed_tenant_ids: tuple[str, ...] = ()
    allowed_meeting_ids: tuple[str, ...] = ()
    state_path: Path = Path("data/connector.sqlite3")
    bot_name: str = "Local transcription bot"
    request_timeout_seconds: float = 20.0
    max_meeting_seconds: int = 7200
    lobby_timeout_seconds: int = 300


def parse_teams_url(url: str) -> dict[str, str | None]:
    """Strict local parsing, with no navigation, redirects, or URL logging.

    Only the two commercial/personal hosts exercised by this integration are
    supported. Government clouds and redirect/shortening services fail closed.
    A URL tenant hint is not authenticated tenant identity or proof of consent.
    """
    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 4096
        or any(c.isspace() or ord(c) < 32 for c in url)
    ):
        raise ConnectorPolicyError(
            "Provide a complete HTTPS Teams meeting link without whitespace."
        )
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            raise ValueError
    except ValueError:
        raise ConnectorPolicyError(
            "Only direct https://teams.microsoft.com or teams.live.com meeting links are allowed."
        ) from None
    path = unquote(parsed.path)
    classic = re.fullmatch(
        r"/l/meetup-join/(19:meeting_[A-Za-z0-9_+=.-]{1,220}@thread\.v2)/0/?", path
    )
    short = re.fullmatch(r"/meet/([0-9]{5,32})/?", path)
    if not classic and not short:
        raise ConnectorPolicyError(
            "Unsupported Teams link. Use a direct /l/meetup-join/.../0 or /meet/<numeric-id> link."
        )
    native = (classic or short).group(1)
    tenant = None
    try:
        contexts = parse_qs(parsed.query, keep_blank_values=True).get("context", [])
        if len(contexts) > 1:
            raise ValueError
        if contexts:
            context = json.loads(contexts[0])
            tenant = context.get("Tid") if isinstance(context, dict) else None
            if tenant is not None:
                if not isinstance(tenant, str) or not re.fullmatch(
                    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", tenant
                ):
                    raise ValueError
                tenant = tenant.lower()
    except (ValueError, TypeError):
        raise ConnectorPolicyError("Teams link has an invalid tenant context.") from None
    return {"native_meeting_id": native, "tenant_id": tenant, "host": parsed.hostname}


def _utc_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
    except ValueError:
        return None


def normalize_segments(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Vexa's relative OR epoch timestamps to meeting-relative seconds.

    Vexa mixed capture sometimes emits epoch seconds. We never export these as
    billion-second SRT offsets. Unknown epoch anchors fail loudly, not fabricate.
    """
    base = _utc_epoch(doc.get("start_time"))
    output: list[dict[str, Any]] = []
    for index, segment in enumerate(doc.get("segments") or []):
        if not isinstance(segment, dict):
            raise ConnectorError("Vexa returned a malformed transcript segment.")
        text = segment.get("text", "")
        if not isinstance(text, str):
            raise ConnectorError("Vexa returned non-text transcript content.")
        if not text.strip():
            continue
        try:
            start, end = float(segment.get("start", 0)), float(segment.get("end", 0))
        except (TypeError, ValueError):
            raise ConnectorError("Vexa returned invalid transcript timestamps.") from None
        if not math.isfinite(start) or not math.isfinite(end):
            raise ConnectorError("Vexa returned non-finite transcript timestamps.")
        if start > 100_000_000 or end > 100_000_000:
            if base is None:
                raise ConnectorError("Vexa returned epoch timestamps without a meeting start time.")
            start, end = start - base, end - base
        if end < start or end < 0:
            raise ConnectorError("Vexa returned reversed or out-of-range transcript timestamps.")
        start, end = max(0.0, start), max(0.0, end)
        output.append(
            {
                "id": str(segment.get("segment_id") or f"{index}:{start:.3f}"),
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text.strip(),
                "speaker": segment.get("speaker"),
                "language": segment.get("language"),
                "final": segment.get("completed", True) is not False,
            }
        )
    return sorted(output, key=lambda s: (s["start"], s["end"], s["id"]))


class VexaConnector:
    def __init__(self, config: VexaConfig, *, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client or httpx.AsyncClient(
            timeout=config.request_timeout_seconds, follow_redirects=False, trust_env=False
        )
        self._owns_client = client is None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _configuration(self, *, joining: bool) -> None:
        if joining and not self.config.enabled:
            raise ConnectorDisabled(
                "Experimental Teams capture is disabled. Complete the documented platform/tenant authorization review before enabling it."
            )
        if not self.config.api_key or not self.config.base_url:
            raise ConnectorDisabled(
                "A dedicated self-hosted Vexa gateway and API key are required."
            )
        try:
            parsed = urlsplit(self.config.base_url)
            host = parsed.hostname or ""
            private_ip = (
                ipaddress.ip_address(host).is_private if re.fullmatch(r"[0-9.:]+", host) else False
            )
            local_name = host in {
                "localhost",
                "gateway",
                "vexa-gateway",
                "host.docker.internal",
            } or host.endswith((".internal", ".local"))
            if (
                parsed.scheme not in {"http", "https"}
                or not (private_ip or local_name)
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError
        except ValueError:
            raise ConnectorPolicyError(
                "Vexa must use an explicitly local/private gateway origin, with no credentials, path, or query in its URL."
            ) from None
        if joining:
            if not self.config.authorization_reference.strip():
                raise ConnectorPolicyError(
                    "Set a real tenant/platform approval record reference before enabling browser capture."
                )
            if not self.config.allowed_meeting_ids:
                raise ConnectorPolicyError(
                    "At least one explicitly approved meeting ID must be allowlisted; a tenant URL hint is not authorization."
                )
            if not self.config.bot_name.strip() or "transcri" not in self.config.bot_name.lower():
                raise ConnectorPolicyError(
                    "The visible bot name must clearly identify transcription, for example 'Local transcription bot'."
                )

    def validate_join(
        self, meeting_url: str, acknowledgement: bool, language: str = "auto"
    ) -> dict:
        self._configuration(joining=True)
        if acknowledgement is not True:
            raise ConnectorPolicyError(
                "This meeting requires explicit acknowledgement of authorization, participant notice, and any required consent."
            )
        if language not in {"auto", "ru", "kk", "en"}:
            raise ConnectorPolicyError("Language must be auto, ru, kk, or en.")
        parsed = parse_teams_url(meeting_url)
        tenant_allowed = parsed["tenant_id"] is not None and parsed["tenant_id"] in {
            s.lower() for s in self.config.allowed_tenant_ids
        }
        if parsed["native_meeting_id"] not in self.config.allowed_meeting_ids or (
            self.config.allowed_tenant_ids and not tenant_allowed
        ):
            raise ConnectorPolicyError("This meeting is outside the configured approval allowlist.")
        return parsed

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        path = Path(self.config.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        path.chmod(0o600)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS join_attempts (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, native TEXT NOT NULL, created REAL NOT NULL, handle TEXT NOT NULL)"
        )
        conn.commit()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _save(self, key: str, handle: dict) -> None:
        with self._database() as db:
            db.execute("UPDATE join_attempts SET handle=? WHERE key=?", (json.dumps(handle), key))

    async def _request(
        self, method: str, path: str, *, allowed: tuple[int, ...] = (200,), **kwargs
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                self.config.base_url.rstrip("/") + path,
                headers={"X-API-Key": self.config.api_key},
                **kwargs,
            )
        except httpx.HTTPError:
            raise ConnectorError(
                "Self-hosted Vexa is unreachable or timed out.", retryable=True
            ) from None
        if response.status_code not in allowed:
            # Never reflect upstream response bodies: these can contain join passcodes,
            # internal addresses, credentials, and third-party-controlled HTML/text.
            if response.status_code in {401, 403}:
                raise ConnectorPolicyError(
                    "Vexa denied access. Check its dedicated API key and service authorization."
                )
            raise ConnectorError(
                f"Vexa returned HTTP {response.status_code}.",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError):
            raise ConnectorError("Vexa returned an invalid JSON object.") from None

    async def _meetings(self, native: str) -> list[dict]:
        matches = []
        for offset in range(0, 10000, 100):
            doc = self._json(
                await self._request(
                    "GET", "/bots", params={"platform": "teams", "limit": 100, "offset": offset}
                )
            )
            rows = doc.get("meetings")
            if not isinstance(rows, list):
                raise ConnectorError("Vexa returned an invalid meeting list.")
            matches.extend(
                r
                for r in rows
                if isinstance(r, dict)
                and r.get("native_meeting_id") == native
                and r.get("platform") == "teams"
            )
            if not doc.get("has_more"):
                return matches
        raise ConnectorError(
            "Vexa reconciliation exceeded its history limit; operator review is required."
        )

    async def join(
        self,
        meeting_url: str,
        title: str,
        language: str,
        idempotency_key: str,
        acknowledgement: bool = False,
    ) -> dict:
        parsed = self.validate_join(meeting_url, acknowledgement, language)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise ConnectorPolicyError("A stable, non-empty job idempotency key is required.")
        native = parsed["native_meeting_id"]
        fingerprint = hashlib.sha256(
            json.dumps([meeting_url, title, language], ensure_ascii=False).encode()
        ).hexdigest()
        async with self._lock:
            with self._database() as db:
                prior = db.execute(
                    "SELECT * FROM join_attempts WHERE key=?", (idempotency_key,)
                ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ConnectorPolicyError(
                        "This idempotency key was already used with different meeting parameters."
                    )
                return json.loads(prior["handle"])
            with self._database() as db:
                for pending in db.execute(
                    "SELECT handle FROM join_attempts WHERE native=?", (native,)
                ):
                    if not json.loads(pending["handle"]).get("terminal"):
                        raise ConnectorPolicyError(
                            "An unresolved local join attempt exists for this meeting. Reconcile it before creating a replacement job."
                        )
            existing = await self._meetings(native)
            if any(row.get("status") in _ACTIVE for row in existing):
                raise ConnectorPolicyError(
                    "A bot already exists for this meeting. Reconcile its existing job before starting another."
                )
            handle = {
                "provider": "vexa",
                "revision": UPSTREAM_REVISION,
                "native_meeting_id": native,
                "meeting_id": None,
                "idempotency_key": idempotency_key,
                "uncertain": True,
                "attempted_at": time.time(),
                "previous_meeting_ids": [r.get("id") for r in existing],
            }
            # Commit BEFORE any side effect. A process/power loss after this boundary
            # returns this uncertain handle on retry, never another create POST.
            with self._database() as db:
                try:
                    db.execute(
                        "INSERT INTO join_attempts VALUES (?, ?, ?, ?, ?)",
                        (idempotency_key, fingerprint, native, time.time(), json.dumps(handle)),
                    )
                except sqlite3.IntegrityError:
                    winner = db.execute(
                        "SELECT fingerprint,handle FROM join_attempts WHERE key=?",
                        (idempotency_key,),
                    ).fetchone()
                    if winner["fingerprint"] != fingerprint:
                        raise ConnectorPolicyError("Concurrent idempotency key conflict.") from None
                    return json.loads(winner["handle"])
            payload = {
                "platform": "teams",
                "meeting_url": meeting_url,
                "bot_name": self.config.bot_name,
                "recording_enabled": False,
                "transcribe_enabled": True,
                "automatic_leave": {
                    "max_bot_time": self.config.max_meeting_seconds * 1000,
                    "max_wait_for_admission": self.config.lobby_timeout_seconds * 1000,
                    "max_time_left_alone": 60_000,
                },
            }
            if language != "auto":
                payload["language"] = language
            try:
                response = await self._request("POST", "/bots", allowed=(200, 201), json=payload)
                doc = self._json(response)
                if (
                    isinstance(doc.get("id"), bool)
                    or not isinstance(doc.get("id"), int)
                    or doc["id"] <= 0
                    or doc.get("native_meeting_id") != native
                    or doc.get("platform") != "teams"
                ):
                    raise ConnectorError(
                        "Vexa create response did not identify the expected meeting."
                    )
                handle.update(meeting_id=doc["id"], uncertain=False)
            except ConnectorError as error:
                handle["error"] = str(error)
            self._save(idempotency_key, handle)
            return handle

    def _handle(self, handle: dict) -> tuple[str, int | None]:
        self._configuration(joining=False)
        if (
            not isinstance(handle, dict)
            or handle.get("provider") != "vexa"
            or not isinstance(handle.get("native_meeting_id"), str)
        ):
            raise ConnectorPolicyError("Invalid connector handle.")
        key = handle.get("idempotency_key")
        with self._database() as db:
            row = db.execute(
                "SELECT native,handle FROM join_attempts WHERE key=?", (key,)
            ).fetchone()
        if not row or row["native"] != handle["native_meeting_id"]:
            raise ConnectorPolicyError("This connector does not own the requested meeting handle.")
        owned = json.loads(row["handle"])
        if owned.get("meeting_id") != handle.get("meeting_id"):
            raise ConnectorPolicyError("Meeting handle does not match the durable join record.")
        return row["native"], owned.get("meeting_id")

    async def poll(self, handle: dict) -> dict:
        native, meeting_id = self._handle(handle)
        if meeting_id is None:
            candidates = [
                r.get("id")
                for r in await self._meetings(native)
                if r.get("id") not in handle.get("previous_meeting_ids", [])
            ]
            return {
                "status": "needs_review",
                "segments": [],
                "completion_reason": None,
                "error": "Join outcome is uncertain. No automatic rejoin will occur; inspect the dedicated Vexa account and stop any orphaned bot before creating a new job.",
                "candidate_meeting_ids": candidates,
                "authoritative": False,
            }
        response = await self._request(
            "GET", f"/transcripts/by-id/{meeting_id}", allowed=(200, 404)
        )
        if response.status_code == 404:
            return {
                "status": "needs_review",
                "segments": [],
                "completion_reason": None,
                "error": "Vexa no longer has the exact meeting row. It has not been rejoined.",
                "authoritative": False,
            }
        doc = self._json(response)
        if (
            doc.get("id") != meeting_id
            or doc.get("native_meeting_id") != native
            or doc.get("platform") != "teams"
        ):
            raise ConnectorPolicyError(
                "Vexa returned a different meeting than the requested handle."
            )
        raw_status = doc.get("status")
        data = doc.get("data") or {}
        reason = doc.get("completion_reason") or data.get("completion_reason")
        status = _STATUS.get(raw_status, "needs_review")
        error = None
        if reason in _BLOCKED_REASONS:
            status = "failed" if raw_status in _TERMINAL else "needs_review"
            error = f"Capture blocked: {reason}. No automatic rejoin is permitted."
        elif status == "failed":
            error = "Vexa reported capture failure. Review its local logs."
        elif status == "needs_review":
            error = "Capture requires operator review. Authentication, CAPTCHA, recording denial, and policy gates must not be bypassed."
        segments = normalize_segments(doc)
        if raw_status in _TERMINAL:
            self._save(handle["idempotency_key"], {**handle, "terminal": True})
        return {
            "status": status,
            "segments": segments,
            "authoritative": True,
            "completion_reason": reason,
            "error": error,
        }

    async def stop(self, handle: dict) -> dict:
        native, meeting_id = self._handle(handle)
        if meeting_id is None:
            raise ConnectorPolicyError(
                "Cannot safely stop an ambiguous join by room ID. Inspect the dedicated Vexa account and confirm the bot identity first."
            )
        rows = await self._meetings(native)
        active = [r for r in rows if r.get("status") in _ACTIVE]
        if any(r.get("id") != meeting_id for r in active):
            raise ConnectorPolicyError(
                "Vexa stop affects every active bot on this meeting link; another bot exists, so operator review is required."
            )
        if not any(r.get("id") == meeting_id for r in rows):
            return {
                "status": "needs_review",
                "error": "The exact meeting row is missing; stop is unconfirmed.",
            }
        if not any(r.get("id") == meeting_id for r in active):
            exact = next(r for r in rows if r.get("id") == meeting_id)
            if exact.get("status") not in _TERMINAL:
                return {
                    "status": "needs_review",
                    "error": "Unknown upstream status; stop is unconfirmed.",
                }
            self._save(handle["idempotency_key"], {**handle, "terminal": True})
            return {"status": "completed", "already_stopped": True}
        response = await self._request(
            "DELETE", "/bots/teams/" + quote(native, safe=""), allowed=(200, 202, 204, 404)
        )
        # 404 can mean a preceding stop already requested teardown, not that its
        # browser actually exited. The caller must keep polling to terminal state.
        return {"status": "stopping", "already_stopped": response.status_code == 404}
