#!/usr/bin/env python3
"""Explicit one-time model provisioning. Never called by the web app/worker.

Downloads only pinned official sources, verifies SHA256 before atomic replacement,
and saves provenance. It does not import torch or execute model-provided Python.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.asr import (  # noqa: E402
    GIGAAM_CODE_REVISION,
    GIGAAM_FILES,
    GIGAAM_MODEL_REPO,
    GIGAAM_MODEL_REVISION,
    sha256_file,
)

WHISPER_REPO = "Systran/faster-whisper-small"
WHISPER_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
WHISPER_FILES = {
    "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
    "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
}


def download_verified(
    url: str, target: Path, expected_sha256: str, max_bytes: int = 2_000_000_000
) -> None:
    if target.is_file() and sha256_file(target) == expected_sha256:
        print(f"Verified existing {target.name}", flush=True)
        return
    temporary = None
    try:
        digest = hashlib.sha256()
        request = urllib.request.Request(
            url, headers={"User-Agent": "teams-asr-model-provisioner/0.1"}
        )
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as output,
        ):
            temporary = Path(output.name)
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > max_bytes:
                    raise RuntimeError("Model download exceeded the configured size limit")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError(
                f"SHA256 mismatch for {target.name}; untrusted download was discarded"
            )
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
        temporary = None
        print(f"Downloaded and verified {target.name} ({total:,} bytes)", flush=True)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("gigaam", "faster-whisper"), default="gigaam")
    parser.add_argument("--output", type=Path, help="Local destination (default: models/<backend>)")
    args = parser.parse_args()
    destination = (args.output or Path("models") / args.backend).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if args.backend == "gigaam":
        repo, revision, files = GIGAAM_MODEL_REPO, GIGAAM_MODEL_REVISION, GIGAAM_FILES
    else:
        repo, revision, files = WHISPER_REPO, WHISPER_REVISION, WHISPER_FILES
    print(f"Provisioning {repo}@{revision} into {destination}", flush=True)
    for filename, sha in files.items():
        download_verified(
            f"https://huggingface.co/{repo}/resolve/{revision}/{filename}",
            destination / filename,
            sha,
        )
    licenses = {}
    if args.backend == "gigaam":
        license_url = f"https://raw.githubusercontent.com/salute-developers/GigaAM/{GIGAAM_CODE_REVISION}/LICENSE"
        license_sha = "f00de6715714c7a63d08639cdbfaa40224eefc407302614bd19f1a8b98c875aa"
        download_verified(license_url, destination / "LICENSE", license_sha, max_bytes=100_000)
        licenses["LICENSE"] = license_sha
    # Model artifact hashes and immutable source IDs support auditing/offline copy.
    provenance = {
        "backend": args.backend,
        "repository": repo,
        "revision": revision,
        "code_revision": GIGAAM_CODE_REVISION if args.backend == "gigaam" else None,
        "license": "MIT",
        "model_card": f"https://huggingface.co/{repo}/blob/{revision}/README.md",
        "sha256": dict(files) | licenses,
        "downloaded_at": datetime.now(UTC).isoformat(),
    }
    manifest = destination / "provenance.json"
    temporary_manifest = destination / ".provenance.json.tmp"
    temporary_manifest.write_text(json.dumps(provenance, indent=2) + "\n")
    os.replace(temporary_manifest, manifest)
    print(f"Ready. Set ASR_MODEL_PATH={destination}", flush=True)
    print("The worker will not download models. Mount this directory read-only in production.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"Model provisioning failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
