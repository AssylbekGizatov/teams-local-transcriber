#!/usr/bin/env python3
"""Build a clean source deployment ZIP, never a secret/data/model backup."""

import argparse
import hashlib
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'.git', '.venv', 'node_modules', 'data', 'models', 'vendor', 'artifacts', '__pycache__'}


def bundle_paths(root: Path) -> list[Path]:
    # Only reviewed, tracked/staged source files; never sweep untracked local files.
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '-z'],
        cwd=root, check=True, capture_output=True,
    )
    paths = []
    for name in sorted(set(result.stdout.decode().split('\0')) - {''}):
        path = Path(name)
        if any(part in FORBIDDEN_PARTS for part in path.parts):
            continue
        if path.name.startswith('.env') and path.name != '.env.example':
            continue
        if path.suffix.lower() in {'.zip', '.tar', '.gz', '.wav', '.mp3', '.mp4', '.sqlite', '.db', '.log', '.pem', '.key'}:
            continue
        full = root / path
        if full.is_symlink():
            raise ValueError(f'Symlinks are not allowed in the source bundle: {path}')
        if full.is_file():
            paths.append(path)
    return paths


def build_bundle(root: Path, output: Path) -> int:
    paths = bundle_paths(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = 'teams-asr-platform/'
    sums = []
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            data = (root / path).read_bytes()
            archive.writestr(prefix + path.as_posix(), data)
            sums.append(f'{hashlib.sha256(data).hexdigest()}  {path.as_posix()}')
        archive.writestr(prefix + 'SHA256SUMS.txt', '\n'.join(sums) + '\n')
    return len(paths)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/teams-asr-platform-easy-start.zip')
    args = parser.parse_args()
    count = build_bundle(ROOT, args.output.resolve())
    print(f'Created source deployment bundle ({count} files): {args.output}')
    print('No container image, model weights, credentials or meeting data included.')
