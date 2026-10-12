"""Cheap, side-effect-free prerequisite checks; never load a model or contact Teams."""

import importlib.util
import shutil
from pathlib import Path

from app.asr import GIGAAM_FILES
from app.config import Settings


def service_readiness(settings: Settings, worker) -> dict:
    required = (
        tuple(GIGAAM_FILES)
        if settings.asr_backend == "gigaam"
        else ("model.bin", "config.json", "tokenizer.json")
    )
    directory = Path(settings.asr_model_path) if settings.asr_model_path else None
    try:
        missing = [
            name
            for name in required
            if directory is None
            or not (directory / name).is_file()
            or (directory / name).stat().st_size == 0
        ]
    except OSError:
        missing = list(required)
    modules = (
        ("torch", "gigaam", "omegaconf")
        if settings.asr_backend == "gigaam"
        else ("faster_whisper",)
    )
    missing_dependencies = []
    for name in modules:
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, OSError):
            available = False
        if not available:
            missing_dependencies.append(name)
    ffmpeg_available = shutil.which("ffmpeg") is not None
    worker_running = bool(worker.running and worker.task and not worker.task.done())
    # Presence is intentionally not checksum verification. The production GigaAM
    # loader verifies pinned hashes before loading; a dashboard fetch must not
    # read gigabytes of weights or trust an editable provenance manifest.
    local_ok = not missing and not missing_dependencies and ffmpeg_available and worker_running
    teams_configured = bool(
        settings.teams_enabled
        and settings.teams_authorization_reference.strip()
        and any(x.strip() for x in settings.teams_allowed_meeting_ids.split(","))
        and settings.vexa_api_key.strip()
        and settings.vexa_api_url.strip()
        and settings.asr_api_token
    )
    return {
        "status": "prerequisites_present" if local_ok else "needs_setup",
        "scope": "local_prerequisites_only",
        "model": {
            "backend": settings.asr_backend,
            "status": "files_present" if not missing else "missing_files",
            "missing_files": missing,
            "checksum_verification": "not_checked",
            "inference_verification": "not_tested",
            "warmup": worker.model_state,
        },
        "dependencies": {
            "status": "discoverable" if not missing_dependencies else "missing",
            "missing": missing_dependencies,
            "runtime_imports": "not_tested",
        },
        "ffmpeg": {"status": "executable_found" if ffmpeg_available else "missing"},
        "worker": {"status": "running" if worker_running else "not_running"},
        "teams": {
            "status": "disabled"
            if not settings.teams_enabled
            else ("configured_unverified" if teams_configured else "configuration_incomplete"),
            "live_verification": "not_tested",
        },
    }
