"""Adapter contract tests. FakeBackend tests are NOT speech-accuracy tests.

FFmpeg tests decode real WAV media. Real neural inference is opt-in via
ASR_REAL_MODEL_DIR and an optional ASR_REAL_SAMPLE path, not a hidden download.
"""

import array
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.asr import (
    ASRError,
    ASRSettings,
    AudioDecodeError,
    LocalASR,
    ModelUnavailableError,
    _FasterWhisperBackend,
    _GigaAMBackend,
    audio_windows,
    normalize_audio,
    verify_gigaam_files,
)


def wav_file(path, seconds=1.0, amplitude=1000, rate=16000, channels=1):
    samples = array.array("h", [amplitude] * int(seconds * rate) * channels)
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())
    return path


@pytest.fixture
def require_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not installed")


class FakeBackend:
    """Test-only predetermined recognizer, never exposed as a runtime backend."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.durations = []
        self.languages = []

    def transcribe_chunk(self, path, language):
        with wave.open(str(path), "rb") as audio:
            self.durations.append(audio.getnframes() / audio.getframerate())
        self.languages.append(language)
        return next(self.responses)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"backend": "mock"},
        {"device": "https://remote"},
        {"chunk_seconds": 26},
        {"chunk_seconds": 0},
        {"overlap_seconds": 12},
        {"overlap_seconds": -1},
        {"max_audio_seconds": float("inf")},
        {"cpu_threads": 0},
        {"max_input_bytes": 0},
        {"ffmpeg_timeout_seconds": 0},
    ],
)
def test_invalid_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        ASRSettings(**kwargs)


def test_windows_cover_timeline_without_ownership_overlap():
    windows = audio_windows(62 * 16000, ASRSettings())
    assert len(windows) == 3
    assert windows[0].own_start == 0
    assert windows[-1].own_end == 62
    assert all((w.end_frame - w.start_frame) / 16000 <= 25 for w in windows)
    assert all(
        left.own_end == right.own_start for left, right in zip(windows, windows[1:], strict=False)
    )


def test_empty_audio_has_no_windows():
    assert audio_windows(0, ASRSettings()) == []


def test_real_ffmpeg_converts_stereo_to_mono(tmp_path, require_ffmpeg):
    source = wav_file(tmp_path / "source.wav", rate=48000, channels=2)
    destination = tmp_path / "normalized.wav"
    assert normalize_audio(source, destination, ASRSettings()) == pytest.approx(1)
    with wave.open(str(destination), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)


def test_real_silence_skips_model_and_removes_temporary_audio(tmp_path, require_ffmpeg):
    source = wav_file(tmp_path / "silence.wav", seconds=28, amplitude=0)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    asr = LocalASR(model_path=tmp_path / "nonexistent", temp_root=temporary)
    assert asr.transcribe(source, "kk") == []
    assert asr._backend is None
    assert list(temporary.iterdir()) == []


def test_fake_overlap_owns_boundary_words_once(tmp_path, require_ffmpeg):
    source = wav_file(tmp_path / "long.wav", seconds=47)
    asr = LocalASR()
    backend = FakeBackend(
        [
            (
                "hello boundary",
                [dict(text="hello", start=0.1, end=1), dict(text="boundary", start=23.5, end=23.9)],
            ),
            (
                "boundary әлем",
                [dict(text="boundary", start=0.5, end=0.9), dict(text="әлем", start=1, end=2)],
            ),
        ]
    )
    asr._backend = backend
    result = asr.transcribe(source, "kk")
    assert [x["text"] for x in result] == ["hello", "boundary әлем"]
    assert result[1]["start"] == 23.5
    assert result[1]["end"] == 25
    assert all(x["speaker"] is None for x in result)
    assert result[0]["end"] <= result[1]["start"]
    assert backend.durations == [24, 24]
    assert backend.languages == ["kk", "kk"]


def test_fake_very_short_input_is_padded_but_timestamps_are_clipped(tmp_path, require_ffmpeg):
    source = wav_file(tmp_path / "tiny.wav", seconds=0.05)
    asr = LocalASR()
    backend = FakeBackend([("a", [dict(text="a", start=0, end=0.08)])])
    asr._backend = backend
    assert asr.transcribe(source) == [dict(start=0, end=0.05, text="a", speaker=None)]
    assert backend.durations == [0.5]


def test_fake_missing_word_timestamps_fails_instead_of_unsafe_overlap(tmp_path, require_ffmpeg):
    source = wav_file(tmp_path / "audio.wav")
    asr = LocalASR()
    asr._backend = FakeBackend([("hello", [])])
    with pytest.raises(ASRError, match="overlap"):
        asr.transcribe(source)


def test_fake_no_overlap_supports_plain_segment(tmp_path, require_ffmpeg):
    source = wav_file(tmp_path / "audio.wav")
    asr = LocalASR(overlap_seconds=0)
    asr._backend = FakeBackend([("hello", [])])
    assert asr.transcribe(source) == [dict(start=0, end=1, text="hello", speaker=None)]


@pytest.mark.parametrize("start,end", [(float("nan"), 1), (1, float("inf")), (2, 1)])
def test_fake_invalid_word_timestamps_fail(tmp_path, require_ffmpeg, start, end):
    source = wav_file(tmp_path / "audio.wav")
    asr = LocalASR()
    asr._backend = FakeBackend([("word", [dict(text="word", start=start, end=end)])])
    with pytest.raises(ASRError, match="invalid word timestamps"):
        asr.transcribe(source)


def test_unsupported_language_rejected_before_audio_read():
    with pytest.raises(ASRError, match="language"):
        LocalASR().transcribe(Path("not-there"), "zh")


def test_missing_model_fails_without_download(tmp_path, monkeypatch):
    import urllib.request

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **kw: pytest.fail("Runtime network call")
    )
    with pytest.raises(ModelUnavailableError, match="missing"):
        _GigaAMBackend(ASRSettings(model_path=tmp_path))


def test_wrong_model_checksum_is_rejected_before_import(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "pytorch_model.bin").write_bytes(b"untrusted pickle")
    with pytest.raises(ModelUnavailableError, match="checksum"):
        verify_gigaam_files(tmp_path)


def test_faster_whisper_passes_local_files_only(tmp_path, monkeypatch):
    captured = {}
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / filename).touch()

    def constructor(path, **kwargs):
        captured.update(path=path, **kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=constructor))
    _FasterWhisperBackend(ASRSettings(backend="faster-whisper", model_path=tmp_path))
    assert captured["local_files_only"] is True
    assert captured["path"] == str(tmp_path.resolve())
    assert captured["device"] == "cpu"


def test_real_malformed_input_and_remote_playlist_rejected(tmp_path, require_ffmpeg):
    source = tmp_path / "malformed.wav"
    source.write_bytes(b"#EXTM3U\nhttps://example.org/private.wav\n")
    with pytest.raises(AudioDecodeError):
        LocalASR().transcribe(source)


def test_real_duration_limit_rejects_instead_of_truncating(tmp_path, require_ffmpeg):
    source = wav_file(tmp_path / "long.wav", seconds=2)
    with pytest.raises(AudioDecodeError, match="duration"):
        LocalASR(max_audio_seconds=1).transcribe(source)


def test_input_size_limit(tmp_path):
    source = wav_file(tmp_path / "audio.wav")
    with pytest.raises(AudioDecodeError, match="size"):
        LocalASR(max_input_bytes=2).transcribe(source)


def test_decoder_timeout_is_sanitized(tmp_path, monkeypatch):
    source = wav_file(tmp_path / "audio.wav")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AudioDecodeError, match="time limit"):
        LocalASR().transcribe(source)


def test_opt_in_real_gigaam_inference(tmp_path, monkeypatch):
    model = os.getenv("ASR_REAL_MODEL_DIR")
    sample = os.getenv("ASR_REAL_SAMPLE")
    if not model or not sample:
        pytest.skip(
            "Set ASR_REAL_MODEL_DIR and ASR_REAL_SAMPLE for a local neural inference smoke test"
        )
    import socket
    import urllib.request

    def deny_network(*args, **kwargs):
        pytest.fail("Real inference attempted an unexpected network connection")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(urllib.request, "urlopen", deny_network)
    asr = LocalASR(model_path=Path(model), cpu_threads=2)
    result = asr.transcribe(Path(sample), "ru")
    assert result and all(item["text"].strip() for item in result)
    assert all(0 <= item["start"] <= item["end"] for item in result)


def test_explicit_downloader_verifies_before_replacing(tmp_path, monkeypatch):
    import hashlib
    import io
    import urllib.request

    from scripts.download_models import download_verified

    payload = b"trusted bytes"
    expected = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "weights.bin"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(payload))
    download_verified("https://huggingface.co/example", destination, expected)
    assert destination.read_bytes() == payload
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"corrupt"))
    with pytest.raises(RuntimeError, match="SHA256"):
        download_verified("https://huggingface.co/example", destination, "0" * 64)
    assert destination.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [destination]


def test_explicit_downloader_enforces_byte_limit(tmp_path, monkeypatch):
    import io
    import urllib.request

    from scripts.download_models import download_verified

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"too long"))
    with pytest.raises(RuntimeError, match="size limit"):
        download_verified("https://huggingface.co/example", tmp_path / "weights", "0" * 64, 2)
    assert list(tmp_path.iterdir()) == []


def test_opt_in_real_api_smoke(tmp_path, monkeypatch):
    """Actual neural upload/worker/export check; does not contact a Teams tenant."""
    model = os.getenv("ASR_REAL_MODEL_DIR")
    sample = os.getenv("ASR_REAL_SAMPLE")
    if not model or not sample:
        pytest.skip("Set ASR_REAL_MODEL_DIR and ASR_REAL_SAMPLE for the real API smoke")
    import asyncio
    import io
    import secrets

    from docx import Document
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "import-default"))
    from app.config import Settings
    from app.db import Database
    from app.main import create_app

    settings = Settings(
        data_dir=tmp_path / "service",
        app_api_token=secrets.token_urlsafe(40),
        asr_api_token=secrets.token_urlsafe(40),
        asr_model_path=model,
        asr_backend="gigaam",
        asr_device="cpu",
        worker_enabled=False,
        retain_audio=False,
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {settings.app_api_token}"}
    audio = Path(sample).read_bytes()
    with TestClient(app) as client:
        response = client.post(
            "/api/uploads",
            headers=headers,
            files={"file": ("sample.wav", audio, "audio/wav")},
            data={
                "title": "Real local inference test",
                "language": "ru",
                "consent_confirmed": "true",
            },
        )
        assert response.status_code == 201, response.text
        meeting_id = response.json()["id"]
        source = Path(app.state.db.get(meeting_id)["audio_path"])

        async def finish_job():
            await app.state.worker.tick()
            await asyncio.gather(*list(app.state.worker.audio_tasks.values()))

        asyncio.run(finish_job())
        current = client.get(f"/api/meetings/{meeting_id}", headers=headers).json()
        assert current["status"] == "completed", current
        assert current["segments"]
        text = " ".join(item["text"] for item in current["segments"])
        assert text.strip()
        assert not source.exists()
        assert not list((settings.data_dir / "tmp").iterdir())
        assert Database(settings.db_path).get(meeting_id)["status"] == "completed"
        for format in ("txt", "md", "docx", "pdf"):
            response = client.get(
                f"/api/meetings/{meeting_id}/export?format={format}", headers=headers
            )
            assert response.status_code == 200
            if format in {"txt", "md"}:
                assert all(segment["text"] in response.text for segment in current["segments"])
            elif format == "docx":
                paragraphs = "\n".join(
                    p.text for p in Document(io.BytesIO(response.content)).paragraphs
                )
                assert all(segment["text"] in paragraphs for segment in current["segments"])
            else:
                assert response.content.startswith(b"%PDF")
                if shutil.which("pdftotext"):
                    path = tmp_path / "transcript.pdf"
                    path.write_bytes(response.content)
                    extracted = subprocess.run(
                        ["pdftotext", str(path), "-"], check=True, capture_output=True
                    ).stdout.decode()
                    assert all(
                        segment["text"] in " ".join(extracted.split())
                        for segment in current["segments"]
                    )
