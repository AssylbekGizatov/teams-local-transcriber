"""Real thread/async boundaries: preload, cancellation and transient stop errors."""

import asyncio
import threading

import pytest

from app.config import Settings
from app.connectors.vexa import ConnectorError
from app.db import Database
from app.worker import Worker


@pytest.mark.asyncio
async def test_cancelled_inference_keeps_lock_and_input_alive(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    class BlockingASR:
        def transcribe(self, path, language=None):
            calls.append(path)
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
            assert path.exists()
            return []

    settings = Settings(_env_file=None, data_dir=tmp_path)
    worker = Worker(settings, Database(settings.db_path), asr=BlockingASR())
    sample = tmp_path / "input.wav"
    sample.write_bytes(b"fixture")
    first = asyncio.create_task(worker.transcribe(sample, None))
    assert await asyncio.to_thread(entered.wait, 5)
    first.cancel()
    second = asyncio.create_task(worker.transcribe(sample, None))
    try:
        await asyncio.sleep(0.05)
        assert worker.inference_lock.locked()
        assert len(calls) == 1
        assert not first.done()
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert first.cancelled()
    assert len(calls) == 2
    assert worker.inference_waiters == 0


@pytest.mark.asyncio
async def test_failed_warmup_never_joins_a_bot(tmp_path):
    class BrokenASR:
        def prepare(self):
            raise RuntimeError("private model path or credential")

    class NoJoin:
        async def join(self, **kwargs):
            pytest.fail("failed model must not join a meeting")

    settings = Settings(_env_file=None, data_dir=tmp_path, asr_preload=True)
    db = Database(settings.db_path)
    worker = Worker(settings, db, asr=BrokenASR(), connector=NoJoin())
    job = db.create(title="Warmup", source="teams", language="auto")
    await worker.process_teams(db.get(job["id"]))
    assert db.get(job["id"])["status"] == "queued"
    await worker.prepare_model()
    await worker.process_teams(db.get(job["id"]))
    current = db.get(job["id"])
    assert worker.model_state == "failed"
    assert current["status"] == "failed"
    assert "private" not in current["error"]
    assert current["handle"] is None


@pytest.mark.asyncio
async def test_successful_warmup_is_ready_before_join(tmp_path):
    events = []

    class ASR:
        def prepare(self):
            events.append("warmup")

    class Connector:
        async def join(self, **kwargs):
            events.append("join")
            return {"meeting_id": 1}

    settings = Settings(_env_file=None, data_dir=tmp_path, asr_preload=True)
    db = Database(settings.db_path)
    worker = Worker(settings, db, asr=ASR(), connector=Connector())
    job = db.create(title="Warmup", source="teams", language="auto")
    await worker.prepare_model()
    await worker.process_teams(db.get(job["id"]))
    assert events == ["warmup", "join"]
    assert db.get(job["id"])["status"] == "joining"


@pytest.mark.asyncio
async def test_transient_stop_error_keeps_reconciling(tmp_path):
    class Connector:
        async def stop(self, handle):
            raise ConnectorError("temporary gateway timeout", retryable=True)

    settings = Settings(_env_file=None, data_dir=tmp_path)
    db = Database(settings.db_path)
    job = db.create(title="Stop", source="teams", language="auto")
    db.update(job["id"], status="stopping", stop_requested=1, handle={"meeting_id": 1})
    worker = Worker(settings, db, connector=Connector())
    await worker.process_teams(db.get(job["id"]))
    current = db.get(job["id"])
    assert current["status"] == "stopping"
    assert "Polling will continue" in current["error"]


@pytest.mark.asyncio
async def test_stopped_failed_capture_is_not_reported_as_success(tmp_path):
    class Connector:
        async def stop(self, handle):
            return {"status": "completed"}

        async def poll(self, handle):
            return {"status": "failed", "segments": [], "error": "Capture failed"}

    settings = Settings(_env_file=None, data_dir=tmp_path)
    db = Database(settings.db_path)
    job = db.create(title="Stop failure", source="teams", language="auto")
    db.update(job["id"], status="stopping", stop_requested=1, handle={"meeting_id": 1})
    worker = Worker(settings, db, connector=Connector())
    await worker.process_teams(db.get(job["id"]))
    current = db.get(job["id"])
    assert current["status"] == "failed"
    assert current["error"] == "Capture failed"
