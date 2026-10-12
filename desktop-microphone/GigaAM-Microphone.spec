# Build from the repository root with the pinned Windows build environment.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH)
datas, binaries, hiddenimports = [], [], []
# Hydra resolves model target classes by string, so ordinary analysis is insufficient.
# Keep GigaAM Python source for inspect/TorchScript; do not install longform extras.
for package in ("gigaam", "hydra", "omegaconf", "sentencepiece"):
    d, b, h = collect_all(package, include_py_files=True, on_error="warn once")
    datas += d
    binaries += b
    hiddenimports += h
for package in ("torch", "torchaudio", "onnxruntime", "sounddevice"):
    binaries += collect_dynamic_libs(package)
for distribution in ("gigaam", "torch", "torchaudio", "sounddevice"):
    datas += copy_metadata(distribution)
# Retain sounddevice's explicit package-relative PortAudio lookup directory.
import _sounddevice_data
sounddevice_dir = Path(next(iter(_sounddevice_data.__path__)))
binaries += [(str(sounddevice_dir / "portaudio-binaries" / "libportaudio64bit.dll"),
              "_sounddevice_data/portaudio-binaries")]
# A standalone app must not rely on a developer's installed VC runtime.
for name in ("vcruntime140.dll", "vcruntime140_1.dll"):
    path = Path(sys.base_prefix) / name
    if path.is_file():
        binaries.append((str(path), "."))

a = Analysis(
    [str(root / "app.py")], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports + ["_cffi_backend", "tkinter", "tkinter.ttk", "tkinter.filedialog",
        "tkinter.messagebox", "gigaam.encoder", "gigaam.decoder", "gigaam.decoding", "gigaam.preprocess"],
    runtime_hooks=[str(root / "build" / "runtime_hook.py")],
    excludes=["pytest", "IPython", "matplotlib", "pandas", "scipy", "numba", "pyannote", "transformers",
              "torchcodec", "gigaam.vad", "soundfile", "_soundfile", "_soundfile_data"],
    noarchive=False,
)
# PyInstaller's sounddevice hook may have collected every wheel DLL: retain x64 non-ASIO only.
def wanted(entry):
    name = str(entry[0]).lower()
    return not ("portaudio" in name and ("-asio" in name or "32bit" in name))
a.binaries = [x for x in a.binaries if wanted(x)]
a.datas = [x for x in a.datas if wanted(x)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GigaAM-Microphone",
          debug=False, strip=False, upx=False, console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GigaAM-Microphone")
