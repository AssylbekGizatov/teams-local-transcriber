import json
from pathlib import Path

from app.asr import GIGAAM_FILES
from scripts.build_bundle import bundle_paths

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_checksum_manifest_matches_runtime():
    assert json.loads((ROOT / 'easy-start/model-checksums.json').read_text()) == GIGAAM_FILES


def test_launchers_do_not_bypass_policy_or_elevate():
    for path in (ROOT / 'easy-start').rglob('*'):
        if path.suffix not in {'.cmd', '.ps1'}:
            continue
        text = path.read_text().lower()
        for forbidden in ('-executionpolicy', 'set-executionpolicy', '-verb runas', 'iex ', 'invoke-expression'):
            assert forbidden not in text
    text = (ROOT / 'easy-start/launcher.ps1').read_text()
    assert "'--no-build','--pull','never','platform'" in text
    assert "Start-Process $Url" in text
    assert 'Set-Clipboard' in text
    assert 'APP_API_TOKEN=$' not in text.split('function Invoke-Launcher')[1]


def test_bundle_includes_notebooks_and_excludes_private_data():
    paths = {str(p) for p in bundle_paths(ROOT)}
    assert 'notebooks/gigaam_microphone_vscode_py313.ipynb' in paths
    assert 'easy-start/START.cmd' in paths
    assert '.env.example' in paths
    assert '.env' not in paths
    assert not any(p.startswith(('models/', 'data/', '.git/', '.venv/', 'vendor/')) for p in paths)
