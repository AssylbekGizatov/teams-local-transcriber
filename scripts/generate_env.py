#!/usr/bin/env python3
"""Generate local-only credentials without printing secrets. Never overwrite by default."""

import argparse
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".env"))
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / ".env.example"
    content = (
        source.read_text()
        .replace("APP_API_TOKEN=\n", f"APP_API_TOKEN={secrets.token_urlsafe(36)}\n")
        .replace("ASR_API_TOKEN=\n", f"ASR_API_TOKEN={secrets.token_urlsafe(36)}\n")
    )
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content)
    print(
        f"Created {args.output} with distinct tokens and owner-only permissions. Keep it private."
    )


if __name__ == "__main__":
    main()
