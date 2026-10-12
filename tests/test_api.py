"""API/worker contract tests use deterministic fakes, not real ASR or a Teams tenant."""

import asyncio
import io
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import ConflictError, Database
from app.main import AuthenticationBoundary, create_app
from app.worker import InferenceBusyError, Worker

TOKEN = "operator-" + "x" * 40
ASR_TOKEN = "inference-" + "y" * 40
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
ASR_HEADERS = {"Authorization": f"Bearer {ASR_TOKEN}"}


class FakeASR:
    def transcribe(self, path, language=None):
        assert Path(path).is_file()
        return [{"start": 0.0, "end": 1.0, "text": "Сәлем! Привет! Hello!", "speaker": None}]


def wav_bytes():
    content = io.BytesIO()
    with wave.open(content, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    return content.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        app_api_token=TOKEN,
        asr_api_token=ASR_TOKEN,
        worker_enabled=False,
        max_upload_mb=1,
        poll_seconds=0.1,
    )


@pytest.fixture
def app(settings):
    return create_app(settings, asr=FakeASR())


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def upload(client, **data):
    return client.post(
        "/api/uploads",
        headers=HEADERS,
        files={"file": ("../sensitive.wav", wav_bytes(), "audio/wav")},
        data={
            "title": "Үш тіл / три языка",
            "language": "auto",
            "consent_confirmed": "true",
            **data,
        },
    )


def test_auth_and_security_headers(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/config", headers=ASR_HEADERS).status_code == 401
    response = client.get("/api/config", headers=HEADERS)
    assert response.status_code == 200
    assert not response.json()["teams_enabled"]
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


async def test_auth_does_not_read_unauthorized_body():
    async def forbidden(*args):
        raise AssertionError("Must not read body or call application")

    sent = []

    async def send(message):
        sent.append(message)

    middleware = AuthenticationBoundary(forbidden, TOKEN, ASR_TOKEN)
    await middleware({"type": "http", "path": "/api/uploads", "headers": []}, forbidden, send)
    assert sent[0]["status"] == 401


def test_missing_tokens_fail_closed(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, worker_enabled=False))) as client:
        assert client.get("/api/config").status_code == 503


def test_consent_size_extension_and_empty_file(client):
    assert upload(client, consent_confirmed="false").status_code == 422
    response = client.post(
        "/api/uploads",
        headers=HEADERS,
        files={"file": ("evil.m3u8", b"http://example.org/private", "text/plain")},
        data={"consent_confirmed": "true"},
    )
    assert response.status_code == 415
    response = client.post(
        "/api/uploads",
        headers=HEADERS,
        files={"file": ("empty.wav", b"", "audio/wav")},
        data={"consent_confirmed": "true"},
    )
    assert response.status_code == 422
    response = client.post(
        "/api/uploads", headers={**HEADERS, "Content-Length": "99999999"}, content=b"x"
    )
    assert response.status_code == 413


async def test_audio_job_lifecycle_edit_and_export(app, client):
    response = upload(client)
    assert response.status_code == 201, response.text
    meeting = response.json()
    assert meeting["status"] == "queued"
    assert "audio_path" not in meeting and "handle" not in meeting
    assert len(client.get("/api/meetings", headers=HEADERS).json()["items"]) == 1
    original = app.state.db.get(meeting["id"])
    assert Path(original["audio_path"]).name.startswith("audio-")
    await app.state.worker.tick()
    await asyncio.gather(*app.state.worker.audio_tasks.values())
    current = client.get(f"/api/meetings/{meeting['id']}", headers=HEADERS).json()
    assert current["status"] == "completed"
    assert current["segments"][0]["text"] == "Сәлем! Привет! Hello!"
    assert not Path(original["audio_path"]).exists()
    segment = current["segments"][0]
    url = f"/api/meetings/{meeting['id']}/segments/{segment['id']}"
    edit = client.patch(
        url,
        headers=HEADERS,
        json={"text": "Түзетілген мәтін", "speaker": "Speaker 1", "version": current["version"]},
    )
    assert edit.status_code == 200
    assert (
        client.patch(
            url, headers=HEADERS, json={"text": "Stale", "version": current["version"]}
        ).status_code
        == 409
    )
    app.state.db.put_segments(meeting["id"], [{**segment, "text": "Provider overwrite"}])
    assert app.state.db.get(meeting["id"])["segments"][0]["text"] == "Түзетілген мәтін"
    for fmt, prefix in [("txt", None), ("md", None), ("docx", b"PK"), ("pdf", b"%PDF")]:
        exported = client.get(f"/api/meetings/{meeting['id']}/export?format={fmt}", headers=HEADERS)
        assert exported.status_code == 200, (
            exported.text[:200] if exported.status_code != 200 else ""
        )
        assert "attachment" in exported.headers["content-disposition"]
        if prefix:
            assert exported.content.startswith(prefix)
        else:
            assert "Түзетілген мәтін" in exported.text


def test_queued_stop_and_retry_do_not_leave_audio(app, client):
    meeting = upload(client).json()
    path = Path(app.state.db.get(meeting["id"])["audio_path"])
    result = client.post(f"/api/meetings/{meeting['id']}/stop", headers=HEADERS)
    assert result.json()["status"] == "cancelled"
    assert not path.exists()
    assert client.post(f"/api/meetings/{meeting['id']}/stop", headers=HEADERS).status_code == 200
    assert client.post(f"/api/meetings/{meeting['id']}/retry", headers=HEADERS).status_code == 409


def test_local_asr_endpoint_uses_separate_service_credential(client):
    files = {"file": ("chunk.wav", wav_bytes(), "audio/wav")}
    assert client.post("/v1/audio/transcriptions", headers=HEADERS, files=files).status_code == 401
    response = client.post(
        "/v1/audio/transcriptions",
        headers=ASR_HEADERS,
        files=files,
        data={"model": "gigaam", "response_format": "verbose_json", "language": "kk"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["duration"] == 1.0
    assert response.json()["language"] == "kk"
    assert response.json()["segments"][0]["text"] == "Сәлем! Привет! Hello!"
    assert (
        client.post(
            "/v1/audio/transcriptions", headers=ASR_HEADERS, files=files, data={"language": "xx"}
        ).status_code
        == 422
    )


def test_teams_disabled_and_missing_resources(client):
    assert (
        client.post(
            "/api/meetings",
            headers=HEADERS,
            json={
                "title": "Test",
                "meeting_url": "https://teams.microsoft.com/meet/1234567",
                "consent_confirmed": True,
            },
        ).status_code
        == 403
    )
    assert client.get("/api/meetings/not-a-meeting", headers=HEADERS).status_code == 404


async def test_stop_during_join_is_preserved(settings):
    started, finish = asyncio.Event(), asyncio.Event()

    class DelayedConnector:
        async def join(self, **kwargs):
            started.set()
            await finish.wait()
            return {"meeting_id": 123, "native_meeting_id": "test"}

        async def stop(self, handle):
            return {"status": "completed"}

        async def poll(self, handle):
            return {"status": "completed", "segments": []}

    database = Database(settings.db_path)
    meeting = database.create(
        title="Test",
        source="teams",
        language="auto",
        meeting_url="https://teams.microsoft.com/meet/1234567",
    )
    worker = Worker(settings, database, connector=DelayedConnector())
    task = asyncio.create_task(worker.process_teams(meeting))
    await started.wait()
    database.update(meeting["id"], status="stopping", stop_requested=1)
    finish.set()
    await task
    assert database.get(meeting["id"])["status"] == "stopping"
    await worker.process_teams(database.get(meeting["id"]))
    assert database.get(meeting["id"])["status"] == "completed"


async def test_inference_queue_is_bounded(settings):
    worker = Worker(settings, Database(settings.db_path), asr=FakeASR())
    worker.inference_waiters = settings.max_inference_queue
    with pytest.raises(InferenceBusyError):
        await worker.transcribe(Path("never-opened.wav"), None)


def test_recovery_and_queue_bounds(settings):
    database = Database(settings.db_path)
    meeting = database.create(
        title="Local", source="upload", language="auto", audio_path="audio.wav"
    )
    database.claim(meeting["id"], "transcribing")
    remote = database.create(title="Teams", source="teams", language="auto")
    database.claim(remote["id"], "joining")
    database.recover()
    assert database.get(meeting["id"])["status"] == "queued"
    assert database.get(remote["id"])["status"] == "needs_review"
    with pytest.raises(ConflictError):
        database.create(title="Too many", source="upload", language="auto", max_pending=1)


async def test_uncertain_join_stop_never_claims_cancelled(settings):
    class NoSideEffects:
        async def stop(self, handle):
            raise AssertionError("Unknown handles cannot be stopped")

    database = Database(settings.db_path)
    meeting = database.create(title="Unknown join", source="teams", language="auto")
    database.update(meeting["id"], status="stopping", stop_requested=1)
    worker = Worker(settings, database, connector=NoSideEffects())
    await worker.process_teams(database.get(meeting["id"]))
    assert database.get(meeting["id"])["status"] == "needs_review"


async def test_stop_requires_verified_terminal_status(settings):
    class MissingSession:
        async def stop(self, handle):
            return {"status": "completed"}

        async def poll(self, handle):
            return {"status": "needs_review", "segments": [], "error": "Missing exact row"}

    database = Database(settings.db_path)
    meeting = database.create(title="Missing", source="teams", language="auto")
    database.update(meeting["id"], status="stopping", stop_requested=1, handle={"meeting_id": 1})
    worker = Worker(settings, database, connector=MissingSession())
    await worker.process_teams(database.get(meeting["id"]))
    assert database.get(meeting["id"])["status"] == "needs_review"


async def test_known_access_gate_is_stopped(settings):
    class BlockedSession:
        stopped = False

        async def stop(self, handle):
            self.stopped = True
            return {"status": "stopping"}

        async def poll(self, handle):
            return {"status": "needs_review", "segments": [], "error": "Authentication required"}

    database = Database(settings.db_path)
    meeting = database.create(title="Gate", source="teams", language="auto")
    database.update(meeting["id"], status="joining", handle={"meeting_id": 1})
    connector = BlockedSession()
    worker = Worker(settings, database, connector=connector)
    await worker.process_teams(database.get(meeting["id"]))
    assert connector.stopped
    assert database.get(meeting["id"])["status"] == "stopping"


def test_authoritative_snapshot_removes_retracted_drafts_preserves_edits(settings):
    database = Database(settings.db_path)
    meeting = database.create(title="Live", source="teams", language="auto")
    a = {"id": "A", "start": 0, "end": 1, "text": "Draft", "final": False}
    b = {"id": "B", "start": 1, "end": 2, "text": "Stable", "final": True}
    database.put_segments(meeting["id"], [a, b])
    assert database.get(meeting["id"])["segments"][0]["final"] is False
    database.put_segments(meeting["id"], [b], replace_snapshot=True)
    assert [s["id"] for s in database.get(meeting["id"])["segments"]] == ["B"]
    database.put_segments(meeting["id"], [a, b])
    current = database.get(meeting["id"])
    database.edit_segment(meeting["id"], "A", "Operator correction", None, current["version"])
    database.put_segments(meeting["id"], [b], replace_snapshot=True)
    assert [s["text"] for s in database.get(meeting["id"])["segments"]] == [
        "Operator correction",
        "Stable",
    ]
    database.put_segments(meeting["id"], [])
    assert len(database.get(meeting["id"])["segments"]) == 2
