"""Build-time only: preserve notices, acquisition evidence and pinned test data."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys
import tkinter
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CODE_REVISION = "7447938d791c4f3e643386ee22c33777004293a5"
SAMPLE_URL = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM/example.wav"
SAMPLE_SHA256 = "d8aaaa18a5098d7c6de0595ae7ac1e64cacd0d4022af3595213bdaf23be77e69"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def fetch(url, target, expected=None, limit=60_000_000):
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
        count = 0
        while block := response.read(1024 * 1024):
            count += len(block)
            if count > limit:
                raise RuntimeError(f"Download too large: {url}")
            output.write(block)
    sha = digest(target)
    if expected and sha != expected:
        target.unlink()
        raise RuntimeError(f"Checksum mismatch: {url}")
    return {"url": url, "sha256": sha, "bytes": target.stat().st_size, "file": target.name,
            "verification": "pre-pinned SHA256" if expected else "HTTPS download; SHA256 recorded at build"}


def notice_name(path):
    name = Path(path).name.lower()
    return any(word in name for word in ("license", "licence", "copying", "notice", "copyright"))


def collect_licenses(bundle):
    licenses = bundle / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    packages = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        destination = licenses / name
        destination.mkdir(exist_ok=True)
        notices = []
        for relative in dist.files or []:
            original = Path(dist.locate_file(relative))
            if not notice_name(relative) or not original.is_file() or original.stat().st_size > 3_000_000:
                continue
            # Do not extract executable files whose parent happens to be named "licenses".
            if original.suffix.lower() in {".dll", ".exe", ".pyd", ".py", ".pyc"}:
                continue
            filename = hashlib.sha256(str(relative).encode()).hexdigest()[:8] + "-" + original.name
            copied = destination / filename
            shutil.copy2(original, copied)
            notices.append({"original": str(relative), "file": str(copied.relative_to(bundle)), "sha256": digest(copied)})
        # The antlr runtime sdist omits its BSD license file. Preserve upstream text.
        if name.lower().replace("_", "-") == "antlr4-python3-runtime" and not notices:
            item = fetch("https://raw.githubusercontent.com/antlr/antlr4/4.9.3/LICENSE.txt", destination / "LICENSE.txt")
            notices.append(item)
        if not notices:
            raise RuntimeError(f"No full license text found for {name}; do not publish an incomplete bundle")
        packages.append({"name": name, "version": dist.version, "license_expression": dist.metadata.get("License-Expression"),
                         "license_metadata": dist.metadata.get("License"), "home_page": dist.metadata.get("Home-page"),
                         "project_urls": dist.metadata.get_all("Project-URL") or [], "notices": notices})
    return packages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    notices = bundle / "licenses"
    packages = collect_licenses(bundle)
    provenance = bundle / "provenance"
    shutil.copytree(args.reports, provenance, dirs_exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE.txt")
    shutil.copy2(ROOT / "desktop-microphone" / "README.md", bundle / "README.md")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python distribution LICENSE.txt is missing")
    shutil.copy2(python_license, notices / "Python-LICENSE.txt")
    acquired = []
    acquired.append(fetch(
        "https://huggingface.co/ai-sage/GigaAM-Multilingual/resolve/2f8a57144e6ec3adfd32fe0484d9ea9913305bc8/README.md",
        notices / "GigaAM-model-card.md"))
    tk = tkinter.Tk()
    tk.withdraw()
    try:
        for component, version, directory in (
            ("tcl", tk.tk.call("info", "patchlevel"), tk.tk.call("info", "library")),
            ("tk", tk.tk.call("package", "provide", "Tk"), tk.tk.eval("set tk_library")),
        ):
            version = str(version)
            path = Path(str(directory)) / "license.terms"
            target = notices / f"{component}-{version}-license.terms"
            url = f"https://raw.githubusercontent.com/tcltk/{component}/core-{version.replace('.', '-')}/license.terms"
            if path.is_file():
                shutil.copy2(path, target)
                acquired.append({"file": target.name, "source_url": url, "sha256": digest(target), "origin": "bundled Python runtime"})
            else:
                acquired.append(fetch(url, target))
    finally:
        tk.destroy()
    acquired.append(fetch("https://raw.githubusercontent.com/PortAudio/portaudio/v19.7.0/LICENSE.txt", notices / "PortAudio-LICENSE.txt"))
    # Preserve binary build notes, identifying the actual PortAudio fork/build.
    import _sounddevice_data
    pa_notes = Path(next(iter(_sounddevice_data.__path__))) / "portaudio-binaries" / "README.md"
    shutil.copy2(pa_notes, notices / "PortAudio-binary-README.md")
    (notices / "NATIVE-LIBRARIES.txt").write_text(
        "This application uses a documented MIT-licensed GigaAM source patch that defers the optional\n"
        "soundfile import until the unused AudioDataset file-path helper is called. That helper is not\n"
        "provided by this desktop app. Its audio path uses standard-library PCM WAV and direct tensors.\n"
        "soundfile, libsndfile and libsndfile's statically linked codecs are NOT redistributed.\n"
        "Exact upstream/patched source, diff and checksums are in provenance/gigaam-lazy-soundfile-patch/.\n"
        "PortAudio is MIT-licensed; only the non-ASIO x64 DLL is distributed. See PortAudio-LICENSE.txt and\n"
        "PortAudio-binary-README.md. Tcl/Tk and Python PSF license texts are included separately.\n"
        "PyInstaller's license includes its bootloader exception. Torch/NumPy/ONNX native third-party\n"
        "notices are preserved from their wheels. Microsoft VC runtime files are supplied with Python/\n"
        "PyTorch; see their notices and https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution .\n",
        encoding="utf-8")
    info = {"python": sys.version, "python_source": "https://www.python.org/ftp/python/3.12.10/Python-3.12.10.tgz",
            "python_distribution": "actions/setup-python 3.12.10 x64; exact installed runtime files hashed in file-manifest.json",
            "python_license_sha256": digest(notices / "Python-LICENSE.txt"), "gigaam_code_revision": CODE_REVISION,
            "gigaam_source": f"https://github.com/salute-developers/GigaAM/tree/{CODE_REVISION}",
            "packages": packages, "additional_artifacts": acquired,
            "inventory_scope": "Build environment and runtime dependencies; soundfile is installed only for dependency validation and explicitly excluded from the frozen application",
            "acquisition_reports": "provenance/*.json retain pip source URLs and archive SHA256 hashes; VCS package has immutable commit ID"}
    (bundle / "dependency-inventory.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    fixture = args.reports.parent / "test-fixtures" / "example.wav"
    fetch(SAMPLE_URL, fixture, SAMPLE_SHA256, limit=1_000_000)


if __name__ == "__main__":
    main()
