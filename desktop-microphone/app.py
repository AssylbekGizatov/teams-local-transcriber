"""Frozen desktop entry point. Recording is never started by import/startup."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback
import wave


DIAGNOSTIC_STAGE = "entrypoint"

def diagnostic_error(error):
    return {"status": "failed", "error_type": type(error).__name__, "error": str(error),
            "stage": DIAGNOSTIC_STAGE, "traceback": traceback.format_exc(),
            "platform": sys.platform, "os_name": os.name, "frozen": bool(getattr(sys, "frozen", False))}

def disable_network():
    """Defense in depth, in this process only; no Windows settings are changed."""
    def denied(*args, **kwargs):
        raise OSError("Сетевые операции отключены в этом приложении.")
    class OfflineSocket(socket.socket):
        connect = denied
        connect_ex = denied
        bind = denied
        listen = denied
        sendto = denied
        sendmsg = denied
    socket.socket = OfflineSocket
    socket.create_connection = denied
    socket.getaddrinfo = denied
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")


def read_wav(path):
    import numpy as np
    from engine import MAX_SECONDS
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or not 8000 <= rate <= 192000:
            raise ValueError("Диагностический WAV: только mono PCM16, 8–192 кГц.")
        if wav.getnframes() > MAX_SECONDS * rate:
            raise ValueError("Диагностический WAV длиннее 5 минут.")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
    return samples, rate


def self_test():
    global DIAGNOSTIC_STAGE
    DIAGNOSTIC_STAGE = "import numpy"
    import numpy
    DIAGNOSTIC_STAGE = "import sounddevice"
    import sounddevice
    DIAGNOSTIC_STAGE = "import torch"
    import torch
    DIAGNOSTIC_STAGE = "import torchaudio"
    import torchaudio
    DIAGNOSTIC_STAGE = "import gigaam"
    from gigaam.model import GigaAMASR
    import gigaam.encoder, gigaam.decoder, gigaam.decoding, gigaam.preprocess
    from engine import verify_model, bundle_root
    from gui import self_test as gui_test
    DIAGNOSTIC_STAGE = "verify bundled model"
    verify_model(bundle_root() / "models" / "gigaam")
    DIAGNOSTIC_STAGE = "Tk GUI lifecycle"
    result = gui_test()
    DIAGNOSTIC_STAGE = "runtime isolation assertions"
    if torch.version.cuda is not None:
        raise AssertionError("Portable package must use CPU-only PyTorch")
    if getattr(sys, "frozen", False):
        import importlib.util
        if importlib.util.find_spec("soundfile") is not None:
            raise AssertionError("Unused soundfile backend must not be bundled")
    try:
        socket.create_connection(("127.0.0.1", 9))
    except OSError:
        pass
    else:
        raise AssertionError("Network guard not active")
    return {"status": "passed", "frozen": bool(getattr(sys, "frozen", False)), "python": sys.version, "torch": torch.__version__, "torchaudio": torchaudio.__version__, "numpy": numpy.__version__, "portaudio": sounddevice.get_portaudio_version()[1], "network": "Python socket connections disabled", "ffmpeg": "not required; PCM tensor inference", "microphone_hardware_tested": False, "gui": result}


def main():
    global DIAGNOSTIC_STAGE
    # PyInstaller needs writable streams even in windowed mode for third-party
    # diagnostic handlers. We deliberately discard rather than store speech logs.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    disable_network()
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--input-wav", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    try:
        if args.self_test:
            result = self_test()
        elif args.input_wav:
            DIAGNOSTIC_STAGE = "real CPU inference"
            from engine import Transcriber, MODEL_REVISION
            started = time.monotonic()
            samples, rate = read_wav(args.input_wav)
            text = Transcriber(args.model_dir).transcribe(samples, rate)
            result = {"status": "passed", "text": text, "duration_seconds": len(samples) / rate, "inference_seconds": round(time.monotonic() - started, 3), "model_revision": MODEL_REVISION, "frozen": bool(getattr(sys, "frozen", False)), "network_guard": True}
        else:
            from gui import run
            run()
            return 0
        if args.output_json:
            args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        if args.self_test or args.input_wav:
            result = diagnostic_error(error)
            if args.output_json:
                args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(str(error), file=sys.stderr)
        else:
            import tkinter.messagebox
            tkinter.messagebox.showerror("GigaAM", "Не удалось запустить приложение. Распакуйте весь ZIP в обычную папку.\n\n" + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
