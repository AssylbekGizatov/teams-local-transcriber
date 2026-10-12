# Maintainer packaging notes

The `Portable Windows microphone` workflow builds on Windows Server 2022 x64
with CPython 3.12.10, the final Python 3.12 release with official Windows
installers. This is an explicit compatibility pin, not a claim that it is the
latest Python security release. Review the runtime and dependency pins before
subsequent releases. Build-time network access is required; the delivered app
requires no Python installation, package installation, FFmpeg or model download.

PyTorch and torchaudio are exactly `2.10.0+cpu`, downloaded only from the official
CPU wheel index. Every additional runtime and build dependency is pinned. Pip
acquisition reports preserve artifact URLs and SHA256 hashes; the GigaAM VCS
requirement preserves its immutable commit. A checksum change is not silently
accepted for the model, config, test fixture or patched GigaAM source.

## Explicit minimal upstream modification

`patch_gigaam.py` verifies the LF-normalized SHA256 of the pinned official
`gigaam/utils.py`, then moves `import soundfile as sf` into the only optional
file-path helper that uses it. The model's tensor code is unchanged. This allows
omitting `soundfile`, `libsndfile`, and libsndfile's statically embedded codecs.
The build environment installs soundfile for upstream dependency validation, but
it is explicitly excluded by PyInstaller and prohibited in the DLL audit. The
frozen self-test also checks it cannot be imported. Both complete source
versions, their hashes, and the diff are preserved in the portable bundle.
A separate build-time interpreter denies soundfile imports and tests native
GigaAM imports and tensor duration. Real frozen model inference is mandatory.

The one-directory bundle keeps required PyTorch/torchaudio and PortAudio DLLs,
Python, Tcl/Tk, and model weights adjacent to the executable. The runtime hook
pins PortAudio lookup to the included non-ASIO x64 DLL. No ASIO DLL is included.
The static PE import audit rejects unbundled VC/OMP runtimes even if they happen
to be installed on the CI runner. Windows system DLLs and API-set contracts are
OS-provided. This audit does not prove every possible dynamic loading path.

Full dependency notices, Python PSF and Tcl/Tk texts, PortAudio notice, GigaAM
code license and model card, acquisition reports, and a per-file SHA256 manifest
are included. The dependency inventory also records build-only packages;
installation in the build environment does not mean every package is embedded.

## Verification and release

`smoke.py` extracts the final ZIP to a path containing spaces and launches the
actual windowed executable in an empty working directory with an isolated PATH
and empty model caches. Tests include Tk UI, native imports, model checksums,
Python-level socket guard, CPU-only torch, and exact official-fixture inference.
The fixture itself is not redistributed. The report explicitly does not claim a
pristine Windows machine, OS-level network isolation, or tested microphone
hardware. A physical-microphone acceptance check on a destination Windows PC is
still required before deployment.

Pushes produce 30-day Actions artifacts only. A nonempty `release_tag` supplied
explicitly to `workflow_dispatch` publishes the successfully tested ZIP,
checksum, test report, and DLL audit as a GitHub Release. The accepted tag shape
is `microphone-vMAJOR.MINOR.PATCH` with an optional suffix. The ZIP must be below
2 GiB. Releases are never automatic on pushes, existing release assets are not
silently overwritten, and security settings are never modified.
