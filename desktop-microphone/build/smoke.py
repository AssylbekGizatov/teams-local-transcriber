"""Test the exact extracted ZIP with a minimal Windows PATH and empty caches."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile

EXPECTED_TEXT = "ничьих не требуя похвал счастлив уж я надеждой сладкой что дева с трепетом любви посмотрит может быть украдкой на песни грешные мои у лукоморья дуб зеленый"
FIXTURE_SHA256 = "d8aaaa18a5098d7c6de0595ae7ac1e64cacd0d4022af3595213bdaf23be77e69"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    results = output / "smoke-results"
    results.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "tests": [], "physical_microphone_tested": False,
              "network_isolation": "Application Python socket guard and offline flags; no OS firewall modification or OS-level network isolation",
              "clean_runtime_scope": "Extracted GUI EXE, spaces in paths, empty working directory/cache, Python variables removed and PATH limited to System32. Hosted runner still has Python installed; this is not a pristine-machine certification."}
    try:
        fixture = output / "test-fixtures" / "example.wav"
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == FIXTURE_SHA256
        with tempfile.TemporaryDirectory(prefix="GigaAM portable test with spaces ") as temp:
            root = Path(temp)
            extracted = root / "Extracted application with spaces"
            with zipfile.ZipFile(args.zip) as zipped:
                zipped.extractall(extracted)
            bundle = extracted / "GigaAM-Microphone"
            exe = bundle / "GigaAM-Microphone.exe"
            cwd = root / "Empty working directory"
            cwd.mkdir()
            cache = root / "Empty user cache"
            cache.mkdir()
            env = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "TCL_LIBRARY", "TK_LIBRARY", "SD_ENABLE_ASIO"):
                env.pop(name, None)
            env.update(PATH=str(Path(env["SystemRoot"]) / "System32"), HF_HOME=str(cache / "hf"),
                       TORCH_HOME=str(cache / "torch"), XDG_CACHE_HOME=str(cache), PYTHONNOUSERSITE="1")
            for label, extra in (("gui-and-native-imports", ["--self-test"]),
                                 ("real-cpu-inference", ["--input-wav", str(fixture)])):
                result_path = results / f"{label}.json"
                command = [str(exe), *extra, "--output-json", str(result_path)]
                started = time.monotonic()
                completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=900, check=False)
                if not result_path.is_file():
                    raise RuntimeError(f"{label}: EXE returned {completed.returncode} without a result JSON; possible native loader failure")
                data = json.loads(result_path.read_text(encoding="utf-8"))
                entry = {"name": label, "exit_code": completed.returncode, "seconds": round(time.monotonic() - started, 3), "result": data}
                report["tests"].append(entry)
                assert completed.returncode == 0 and data["status"] == "passed", entry
                assert data["frozen"] is True, "Test accidentally ran an unfrozen Python script"
                if label == "real-cpu-inference":
                    assert data["text"].strip() == EXPECTED_TEXT, "Official fixture transcript differs from expected upstream text"
                    assert 11.0 < data["duration_seconds"] < 11.6
                else:
                    assert "+cpu" in data["torch"] and "+cpu" in data["torchaudio"]
            assert not list(cache.rglob("*.bin")) and not list(cache.rglob("*.ckpt")), "Unexpected cached model"
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (output / "test-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
