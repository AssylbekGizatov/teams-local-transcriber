"""Provisioning must permit both bot lifecycle and exact-session transcripts."""

import importlib.util
import io
import json
from pathlib import Path


def test_local_key_has_bot_and_transcript_scopes_without_printing_key(tmp_path, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[1] / "deploy/vexa/bootstrap.py"
    spec = importlib.util.spec_from_file_location("bootstrap_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests = []
    test_key = "test-only-local-connector-key"

    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, json.loads(request.data)))
            reply = {"id": 7} if len(requests) == 1 else {"token": test_key}
            return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setenv("VEXA_ADMIN_TOKEN", "test-only-admin-key")
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    output = tmp_path / "key"
    monkeypatch.setattr("sys.argv", [str(script), "--confirm-local-credential-creation", "--output", str(output)])
    module.main()
    assert requests[0][0] == "http://127.0.0.1:18057/admin/users"
    assert requests[1][0] == "http://127.0.0.1:18057/admin/users/7/tokens"
    assert set(requests[1][1]["scopes"]) == {"bot", "tx"}
    assert output.read_text().strip() == test_key
    assert test_key not in capsys.readouterr().out
