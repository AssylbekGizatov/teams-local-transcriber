#!/usr/bin/env python3
"""Operator-run local provisioning. Never imported or run by normal app startup.

Creates a dedicated local Vexa service identity and a scoped API key. This grants
persistent local access; execute only after the operator approves that change.
No Microsoft account, OAuth grant, or real meeting is created.
"""

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-local-credential-creation", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/vexa-api-key"))
    args = parser.parse_args()
    admin_token = os.environ.get("VEXA_ADMIN_TOKEN")
    if not admin_token:
        parser.error("Export the VEXA_ADMIN_TOKEN configured for this local Vexa stack.")
    if args.output.exists():
        parser.error("Refusing to create another token over an existing output file.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def post(path, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:18057" + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-Admin-API-Key": admin_token},
        )
        with opener.open(request, timeout=20) as response:
            return json.load(response)

    user = post(
        "/admin/users",
        {
            "email": "teams-asr@localhost.invalid",
            "name": "Local Teams ASR",
            "max_concurrent_bots": 1,
        },
    )
    token = post(
        f"/admin/users/{int(user['id'])}/tokens",
        {"scopes": ["bot", "tx"], "name": "Local Teams ASR connector"},
    )
    value = token.get("token")
    if not isinstance(value, str) or not value:
        raise SystemExit(
            "Token response did not contain a key. Inspect the local admin API; do not retry blindly."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(value + "\n")
    print(
        f"Created local scoped key in {args.output}. Copy it to the app VEXA_API_KEY setting; never publish this file."
    )


if __name__ == "__main__":
    main()
