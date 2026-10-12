"""Offline notebook contract checks; these do not imply a live Colab test."""

import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks/gigaam_microphone_colab.ipynb"


def test_colab_notebook_structure_and_python():
    notebook = json.loads(NOTEBOOK.read_text())
    assert notebook["nbformat"] == 4
    assert notebook["metadata"]["colab"]["private_outputs"] is True
    identifiers = []
    for cell in notebook["cells"]:
        identifiers.append(cell["id"])
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None
            assert cell["outputs"] == []
            compile("".join(cell["source"]), str(NOTEBOOK), "exec")
    assert len(set(identifiers)) == len(identifiers)
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
    assert 'REPO_REVISION = "dd7b94a6c680d1ca891eeaa799bd68d70cf2559e"' in source
    assert "TemporaryDirectory" in source
    assert "base64.b64decode(encoded, validate=True)" in source
    assert "download_models.py" in source
    assert "requirements-asr.lock" in source
    assert 'f"torch==2.10.0+{wheel_channel}"' in source
    assert 'f"torchaudio==2.10.0+{wheel_channel}"' in source
    assert "max_audio_seconds=35" in source
    assert "MediaRecorder" in source
    assert "getUserMedia({audio: true, video: false})" in source
    assert "from google.colab import output" in source
    assert "Drive" in source


def test_embedded_inference_compiles():
    notebook = json.loads(NOTEBOOK.read_text())
    found = set()
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        tree = ast.parse("".join(cell["source"]))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"probe", "INFERENCE_CODE"}:
                        compile(node.value.value, target.id, "exec")
                        found.add(target.id)
    assert found == {"probe", "INFERENCE_CODE"}


def test_browser_recorder_lifecycle():
    subprocess.run(["node", str(ROOT / "tests/colab_recorder_smoke.cjs")], check=True)


def test_revision_is_immutable():
    notebook = json.loads(NOTEBOOK.read_text())
    setup = next("".join(c["source"]) for c in notebook["cells"] if "REPO_REVISION =" in "".join(c["source"]))
    assert re.search(r'REPO_REVISION = "[0-9a-f]{40}"', setup)


def test_download_javascript_syntax(tmp_path):
    notebook = json.loads(NOTEBOOK.read_text())
    cell = next(
        "".join(c["source"])
        for c in notebook["cells"]
        if c["cell_type"] == "code" and "text_json =" in "".join(c["source"])
    )
    captured = []
    namespace = {
        "last_transcript": 'Қазақша, русский, English: "quotes" \\ newline\n</script>',
        "json": json,
        "Javascript": lambda value: value,
        "display": captured.append,
    }
    exec(compile(cell, "notebook-download", "exec"), namespace)
    assert len(captured) == 1
    javascript = tmp_path / "download.js"
    javascript.write_text(captured[0])
    subprocess.run(["node", "--check", str(javascript)], check=True)
