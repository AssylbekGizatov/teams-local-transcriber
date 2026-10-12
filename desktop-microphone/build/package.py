"""Audit the portable tree and package it without external runtime dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

import pefile


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dll_audit(bundle):
    files = [p for p in bundle.rglob("*") if p.is_file()]
    by_name = {p.name.lower(): p for p in files}
    for required in ("GigaAM-Microphone.exe", "python312.dll", "_tkinter.pyd", "libportaudio64bit.dll"):
        if required.lower() not in by_name:
            raise RuntimeError(f"Required portable runtime file missing: {required}")
    for forbidden in ("*asio*.dll", "*cuda*.dll", "*cublas*.dll", "*cudnn*.dll", "ffmpeg*.exe", "python.exe", "pip.exe", "*sndfile*.dll"):
        if list(bundle.rglob(forbidden)):
            raise RuntimeError(f"Unexpected runtime executable/library: {forbidden}")
    system = Path(os.environ["SystemRoot"]) / "System32"
    result = {"status": "passed", "method": "PE import and delay-import audit; API-set contracts resolved by Windows",
              "scope_limit": "Static dependency inspection cannot establish all dynamic LoadLibrary paths or certify a clean Windows machine.",
              "files": [], "missing": []}
    for path in files:
        if path.suffix.lower() not in {".exe", ".dll", ".pyd"}:
            continue
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                             pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        imports = []
        for entry in list(getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])) + list(getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])):
            name = entry.dll.decode("ascii").lower()
            if name in by_name:
                resolved = "bundle:" + by_name[name].relative_to(bundle).as_posix()
            elif name.startswith(("api-ms-", "ext-ms-")):
                resolved = "Windows API-set contract"
            elif name.startswith(("vcruntime", "msvcp140", "libomp", "libiomp")):
                resolved = None  # Developer's installed VC/OMP runtime is not sufficient.
            elif (system / name).is_file():
                resolved = "Windows System32:" + name
            else:
                resolved = None
            imports.append({"name": name, "resolved": resolved})
            if not resolved:
                result["missing"].append({"file": path.relative_to(bundle).as_posix(), "dll": name})
        result["files"].append({"file": path.relative_to(bundle).as_posix(), "sha256": sha256(path), "imports": imports})
        pe.close()
    if result["missing"]:
        result["status"] = "failed"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    audit = dll_audit(bundle)
    (output / "dll-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    if audit["status"] != "passed":
        raise RuntimeError("Unresolved portable DLL dependencies; inspect dll-audit.json")
    manifest = [{"file": path.relative_to(bundle).as_posix(), "size": path.stat().st_size, "sha256": sha256(path)}
                for path in sorted(bundle.rglob("*")) if path.is_file() and path.name != "file-manifest.json"]
    (bundle / "file-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = output / "GigaAM-Microphone-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zipped:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zipped.write(path, Path(bundle.name) / path.relative_to(bundle))
    if archive.stat().st_size >= 2 * 1024 ** 3:
        archive.unlink()
        raise RuntimeError("Portable ZIP exceeds the 2 GiB release asset limit; no partial asset will be published")
    archive.with_suffix(".zip.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii")
    print(f"Packaged {archive.name}: {archive.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
