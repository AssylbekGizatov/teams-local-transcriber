#!/usr/bin/env python3
"""Offline checks of the exact source fork; never starts Docker or joins meetings."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def verify(root: Path) -> None:
    revision = (root / ".local-build/UPSTREAM_REVISION").read_text().strip()
    assert revision == (ROOT / "UPSTREAM_REVISION").read_text().strip(), (
        "Upstream revision mismatch"
    )
    patch_hash = hashlib.sha256((ROOT / "local-only.patch").read_bytes()).hexdigest()
    assert (root / ".local-build/PATCH_SHA256").read_text().strip() == patch_hash, (
        "Patch identity mismatch"
    )
    for entry in (ROOT / "PATCHED_FILES.sha256").read_text().splitlines():
        expected, relative = entry.split("  ", 1)
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        assert actual == expected, f"Changed or missing hardened source: {relative}"
    source = (
        root / "core/meetings/services/meeting-api/src/meeting_api/bot_spawn/service.py"
    ).read_text()
    tree = ast.parse(source)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_capture_signal_from_context"
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[fn], type_ignores=[]), "<reviewed capture policy>", "exec"),
        namespace,
    )
    for ctx in ({}, {"capture_signal": True}, {"capture_signal": False}, {"capture_signal": None}):
        assert namespace["_capture_signal_from_context"](ctx) is False
    assert "configured = {}" in source
    assert 'transcription_service_url != "http://asr-app:8000"' in source
    assert "recording_upload_url=None" in source
    browser = (root / "core/meetings/modules/remote-browser/src/browser.ts").read_text()
    assert (
        "from 'playwright';" in browser
        and "StealthPlugin" not in browser
        and "addInitScript" not in browser
    )
    schema = json.loads(
        (root / "core/meetings/contracts/invocation.v1/invocation.schema.json").read_text()
    )
    assert "maxBotTime" in schema["$defs"]["AutomaticLeave"]["properties"]
    print(
        "PASS: pinned fork, no fixture tape, local-only STT, ordinary Playwright, explicit meeting ceiling"
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent.parent / "vendor/vexa")
