"""Frozen-only dependency isolation; never alters machine settings."""
import ctypes.util
import os
from pathlib import Path
import sys

# The shipped binary is non-ASIO. Avoid accidental system DLL / ASIO selection.
os.environ.pop("SD_ENABLE_ASIO", None)
_portaudio = Path(sys._MEIPASS) / "_sounddevice_data" / "portaudio-binaries" / "libportaudio64bit.dll"
if not _portaudio.is_file():
    raise RuntimeError("Bundled PortAudio DLL is missing")
_original_find_library = ctypes.util.find_library


def _find_library(name):
    if name == "portaudio":
        return str(_portaudio)
    return _original_find_library(name)


ctypes.util.find_library = _find_library
