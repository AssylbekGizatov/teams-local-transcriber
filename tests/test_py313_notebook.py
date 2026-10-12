"""Offline structural and mocked microphone lifecycle checks; no hardware/model calls."""
import ast
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks/gigaam_microphone_vscode_py313.ipynb"


def notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def controller_source():
    return next(
        "".join(cell["source"])
        for cell in notebook()["cells"]
        if cell["cell_type"] == "code" and "MIC_CONTROLLER" in "".join(cell["source"])
    ).split('if "microphone" in globals():')[0]


def test_local_notebook_clean_and_compilable():
    doc = notebook()
    assert doc["nbformat"] == 4
    for cell in doc["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None
            assert cell["outputs"] == []
            ast.parse("".join(cell["source"]))
    assert "widgets" not in doc["metadata"]
    source = NOTEBOOK.read_text(encoding="utf-8")
    assert "INSTALL_FROM_WHEELHOUSE = False" in source
    assert "--no-index" in source
    assert "ASR_PY = Path(sys.executable)" in source
    assert "git\", \"clone" not in source
    assert "RawInputStream" in source
    assert "temp_root=Path(sys.argv[1]).parent" in source
    assert "taskkill" in source and "killpg" in source


class Widget:
    def __init__(self, *args, **kwargs):
        self.value = ""
        self.disabled = False
        self.__dict__.update(kwargs)

    def on_click(self, handler):
        self.handler = handler


class FakeSoundDevice:
    class CallbackStop(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    def __init__(self, status=False, fail_open=False):
        self.status = status
        self.fail_open = fail_open
        self.entered = False
        self.exited = False

    def query_devices(self, device, kind):
        return {"default_samplerate": 8000}

    def check_input_settings(self, **kwargs):
        assert kwargs["channels"] == 1 and kwargs["dtype"] == "int16"

    def RawInputStream(self, **kwargs):
        owner = self

        class Stream:
            def __enter__(self):
                owner.entered = True
                if owner.fail_open:
                    raise RuntimeError("device unavailable")
                try:
                    kwargs["callback"](b"\x01\x00" * 8000, 8000, None, owner.status)
                except (owner.CallbackStop, owner.CallbackAbort):
                    kwargs["finished_callback"]()
                return self

            def __exit__(self, *args):
                owner.exited = True

        return Stream()


@pytest.fixture
def controller(tmp_path):
    fake_sd = FakeSoundDevice()
    widget_names = ("Button", "Label", "Textarea", "Layout", "HTML", "VBox", "HBox")
    env = {
        "sd": fake_sd,
        "widgets": SimpleNamespace(**dict.fromkeys(widget_names, Widget)),
        "INPUT_DEVICE": None,
        "Path": Path,
        "os": os,
        "subprocess": subprocess,
        "ASR_PY": Path("python"),
        "MODEL": tmp_path / "model",
        "REPO": tmp_path,
        "INFERENCE_DEVICE": "cpu",
        "FFMPEG": "/approved/ffmpeg",
    }
    exec(compile(controller_source(), str(NOTEBOOK), "exec"), env)
    env["MAX_BYTES"] = 10000
    obj = env["MicrophoneNotebook"]()
    yield obj, env, fake_sd
    obj.close()


def fake_process_factory(env, *, failure=False, timeout=False):
    seen = []

    class Process:
        def __init__(self, args, **kwargs):
            self.returncode = 1 if failure else 0
            self.path = Path(args[3])
            seen.append(self.path)
            assert self.path.is_file()
            assert self.path.stat().st_size <= env["MAX_BYTES"]
            # Simulate nested normalized data owned by the parent temp directory.
            nested = self.path.parent / "teams-asr-child"
            nested.mkdir()
            (nested / "normalized.wav").write_bytes(b"private audio")
            assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, timeout=None):
            if timeout and self.returncode is None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return '{"text":"test transcript"}\n', ""

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    env["subprocess"] = SimpleNamespace(
        Popen=Process,
        PIPE=subprocess.PIPE,
        DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired,
        CREATE_NEW_PROCESS_GROUP=512,
    )
    return seen


def test_record_inference_cleanup_and_explicit_export(controller):
    obj, env, sound = controller
    paths = fake_process_factory(env)
    obj._record_and_transcribe()
    assert sound.entered and sound.exited
    assert obj.result.value == "test transcript"
    assert obj.download.value == ""
    assert obj.stop_button.disabled
    assert not obj.start_button.disabled
    assert paths and not paths[0].parent.exists()
    obj.prepare_download()
    assert 'download="gigaam-transcript.txt"' in obj.download.value


def test_inference_error_cleans_audio(controller):
    obj, env, sound = controller
    paths = fake_process_factory(env, failure=True)
    obj._record_and_transcribe()
    assert sound.exited
    assert "Ошибка" in obj.status.value
    assert paths and not paths[0].parent.exists()
    assert obj.result.value == ""


def test_audio_overflow_does_not_infer(controller):
    obj, env, sound = controller
    sound.status = True
    paths = fake_process_factory(env)
    obj._record_and_transcribe()
    assert sound.exited and not paths
    assert "переполнение" in obj.status.value
    assert obj.stop_button.disabled


def test_open_error_resets_controls(controller):
    obj, env, sound = controller
    sound.fail_open = True
    paths = fake_process_factory(env)
    obj._record_and_transcribe()
    assert not paths
    assert obj.stop_button.disabled and not obj.start_button.disabled
    assert "device unavailable" in obj.status.value


def test_cancel_before_inference(controller):
    obj, env, sound = controller
    paths = fake_process_factory(env)
    obj.cancel_event.set()
    obj.stop_event.set()
    obj._record_and_transcribe()
    assert sound.exited and not paths
    obj.close()
    assert obj.closed and obj.start_button.disabled


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_running_process_is_killed_and_temp_cleaned(controller, reason):
    obj, env, sound = controller
    seen = []
    killed = []

    class RunningProcess:
        returncode = None
        pid = 12345

        def __init__(self, args, **kwargs):
            seen.append(Path(args[3]))
            nested = seen[0].parent / "teams-asr-nested"
            nested.mkdir()
            (nested / "normalized.wav").write_bytes(b"private")
            if reason == "cancel":
                obj.cancel_event.set()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert self.returncode == -9

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            assert timeout is None
            assert self.returncode == -9
            return "", ""

    def kill_tree(process):
        killed.append(process.pid)
        process.returncode = -9

    env["subprocess"] = SimpleNamespace(
        Popen=RunningProcess,
        PIPE=subprocess.PIPE,
        DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired,
        CREATE_NEW_PROCESS_GROUP=512,
    )
    obj._kill_process_tree = kill_tree
    if reason == "timeout":
        env["INFERENCE_TIMEOUT"] = -1
    obj._record_and_transcribe()
    assert sound.exited
    assert killed == [12345]
    assert seen and not seen[0].parent.exists()
    assert obj.result.value == ""


@pytest.mark.parametrize("name", ["nt", "posix"])
def test_process_tree_termination_platform_calls(controller, name):
    obj, env, sound = controller
    calls = []
    process = SimpleNamespace(pid=5678, poll=lambda: None, kill=lambda: calls.append("kill"))
    env["os"] = SimpleNamespace(name=name, killpg=lambda *args: calls.append(args))
    env["subprocess"] = SimpleNamespace(
        run=lambda args, **kwargs: calls.append(args), DEVNULL=subprocess.DEVNULL
    )
    obj._kill_process_tree(process)
    if name == "nt":
        assert calls == [["taskkill", "/PID", "5678", "/T", "/F"], "kill"]
    else:
        assert calls == [(5678, env["signal"].SIGKILL)]
