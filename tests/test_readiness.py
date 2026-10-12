"""Readiness is an authenticated prerequisite report, never a live smoke test."""

import json

import pytest
from fastapi.testclient import TestClient

from app.asr import GIGAAM_FILES
from app.config import Settings
from app.main import create_app

TOKEN = "operator-" + "x" * 40
ASR_TOKEN = "inference-" + "y" * 40
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def settings(tmp_path, **kwargs):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        app_api_token=TOKEN,
        asr_api_token=ASR_TOKEN,
        worker_enabled=False,
        **kwargs,
    )


def test_authentication_and_liveness(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/readiness").status_code == 401
        assert (
            client.get(
                "/api/readiness", headers={"Authorization": f"Bearer {ASR_TOKEN}"}
            ).status_code
            == 401
        )
        response = client.get("/api/readiness", headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["status"] == "needs_setup"
        assert data["model"]["status"] == "missing_files"
        assert data["worker"]["status"] == "not_running"
        assert data["teams"]["status"] == "disabled"


@pytest.mark.parametrize(
    "backend,required",
    [
        ("gigaam", tuple(GIGAAM_FILES)),
        ("faster-whisper", ("model.bin", "config.json", "tokenizer.json")),
    ],
)
def test_file_presence_does_not_claim_verification(tmp_path, monkeypatch, backend, required):
    model = tmp_path / "private-model-location"
    model.mkdir()
    for name in required:
        (model / name).write_bytes(b"not real model weights")
    monkeypatch.setattr("app.asr.sha256_file", lambda _: pytest.fail("must not hash weights"))
    monkeypatch.setattr("app.asr.LocalASR._get_backend", lambda _: pytest.fail("must not load ASR"))
    monkeypatch.setattr("app.readiness.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("app.readiness.shutil.which", lambda _: "/private/bin/ffmpeg")
    app = create_app(settings(tmp_path, asr_backend=backend, asr_model_path=str(model)))
    with TestClient(app) as client:
        data = client.get("/api/readiness", headers=HEADERS).json()
        assert data["model"]["status"] == "files_present"
        assert data["model"]["checksum_verification"] == "not_checked"
        assert data["model"]["inference_verification"] == "not_tested"
        assert data["dependencies"]["runtime_imports"] == "not_tested"
        assert data["ffmpeg"]["status"] == "executable_found"
        assert str(model) not in json.dumps(data)
        assert "/private" not in json.dumps(data)
        (model / required[0]).write_bytes(b"")
        data = client.get("/api/readiness", headers=HEADERS).json()
        assert required[0] in data["model"]["missing_files"]


def test_missing_runtime_and_teams_do_not_leak_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr("app.readiness.importlib.util.find_spec", lambda _: None)
    monkeypatch.setattr("app.readiness.shutil.which", lambda _: None)
    config = settings(
        tmp_path,
        teams_enabled=True,
        teams_authorization_reference="private-approval",
        teams_allowed_meeting_ids="private-meeting",
        teams_allowed_tenant_ids="private-tenant",
        vexa_api_url="https://private-vexa.example",
        vexa_api_key="private-key",
    )
    app = create_app(config)
    monkeypatch.setattr(
        app.state.worker, "get_connector", lambda: pytest.fail("no connector calls")
    )
    with TestClient(app) as client:
        data = client.get("/api/readiness", headers=HEADERS).json()
        assert data["dependencies"]["status"] == "missing"
        assert data["ffmpeg"]["status"] == "missing"
        assert data["teams"]["status"] == "configured_unverified"
        assert data["teams"]["live_verification"] == "not_tested"
        assert "private-" not in json.dumps(data)
        assert TOKEN not in json.dumps(data)
        assert ASR_TOKEN not in json.dumps(data)
        config.asr_api_token = ""
        assert (
            client.get("/api/readiness", headers=HEADERS).json()["teams"]["status"]
            == "configuration_incomplete"
        )


def test_prerequisites_present_does_not_mean_inference_ready(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    for name in GIGAAM_FILES:
        (model / name).write_bytes(b"placeholder")
    monkeypatch.setattr("app.readiness.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("app.readiness.shutil.which", lambda _: "ffmpeg")
    config = settings(tmp_path, asr_model_path=str(model))
    config.worker_enabled = True
    with TestClient(create_app(config)) as client:
        data = client.get("/api/readiness", headers=HEADERS).json()
        assert data["status"] == "prerequisites_present"
        assert data["scope"] == "local_prerequisites_only"
        assert data["worker"]["status"] == "running"
        assert data["model"]["inference_verification"] == "not_tested"
