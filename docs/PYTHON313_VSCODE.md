# VS Code microphone notebook: Python 3.13

Use [`gigaam_microphone_vscode_py313.ipynb`](../notebooks/gigaam_microphone_vscode_py313.ipynb). The existing local Python 3.12 and Colab notebooks remain unchanged.

## Smallest end-user workflow (Windows x64)

1. Download this repository ZIP and extract it. Open that folder in VS Code and open the new notebook. Choose the **local, ordinary 64-bit Python 3.13** kernel, including an existing 3.13.11 installation. No second ASR environment is created; the worker uses exactly `sys.executable` from the selected kernel. VS Code Python/Jupyter extensions and `ipykernel` are prerequisites for executing any notebook.
2. Have the approved corporate installation process install the dependencies from `requirements-microphone-py313.txt` into that Python. The source manifest contains a pinned GigaAM Git dependency; a requirements importer that supports only PyPI packages cannot provision that line by itself. See the wheelhouse route below. Git is needed only on the approved build machine, not the user's runtime machine. Do not run the older Python 3.12 setup cells.
3. Obtain approved FFmpeg and the pinned GigaAM model directory through the organization's authorized process. Put the model under `models/gigaam`, or edit `MODEL`. Set `FFMPEG` to the full path to `ffmpeg.exe` if not on PATH. FFmpeg is an external executable, not provided by a Python package named `ffmpeg`.
4. Run the cells; then click **Start**, speak, **Stop**. The microphone is opened only on Start. The preflight validates local files/imports and lists audio devices; it never installs or downloads anything. Inference stays local and uses the same GigaAM Multilingual model for RU, KK, EN and mixed speech.

The notebook itself is not a portable bundle of dependencies and 883 MB model weights. Downloading just its `.ipynb` file does not supply the repository's `app/asr.py`, dependencies, FFmpeg, or model. Its first cell clearly identifies missing local paths.

## Corporate/offline installation

The default is **no install and no download**. If dependencies are already provisioned in the selected kernel, skip the optional install cell. If IT supplies a complete approved wheelhouse, set its path and `INSTALL_FROM_WHEELHOUSE = True`, run that cell once, restart the kernel, and return the flag to False. It uses:

```powershell
python -m pip install --no-index --only-binary=:all: --find-links C:\approved\gigaam-wheels -r requirements-microphone-py313-offline.txt
```

This does not contact PyPI or GitHub and does not compile code on the restricted workstation. Use **Windows x64 / ordinary CPython 3.13** wheels, not Linux, ARM64, 32-bit, or free-threaded (`cp313t`) builds. Packages supplied through the corporate requirements importer must target the same Python printed by the first cell.

### For the approved dependency/build process

The offline manifest deliberately uses `gigaam==0.2.0` instead of a VCS URL, so offline pip does not try to fetch Git. That wheel must be built from official GigaAM commit `7447938d791c4f3e643386ee22c33777004293a5`; the version number alone is not source-provenance verification. Do not substitute an unrelated wheel with the same name. The build process should preserve source provenance, approve/scan the wheels, and provide its own checksum manifest. This repository does not publish a prebuilt third-party wheelhouse or claim its integrity from package names alone.

`antlr4-python3-runtime==4.9.3` has no usable public wheel: it and GigaAM require wheel building on the approved build machine. A public-index install with `--only-binary=:all:` is therefore insufficient. Runtime machines can still remain wheel-only after IT prepares these wheels. One-time preparation on an authorized Windows x64 Python 3.13 machine with Git, approved compiler/build capabilities as necessary, and permitted official source access:

```powershell
# Only on the approved connected build machine, from this project's root:
py -3.13 -m pip wheel --wheel-dir wheelhouse torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cpu
py -3.13 -m pip wheel --wheel-dir wheelhouse --find-links wheelhouse -r requirements-microphone-py313.txt
```

These preparation commands are documented, not automatically executed by the notebook, and have not been executed on Windows in this validation. The second command builds pinned upstream GigaAM and antlr wheels and gathers dependencies. Resolve any build issue in the approved build process; do not loosen workplace restrictions. The CPU local versions (`2.10.0+cpu`) satisfy the manifests' `==2.10.0` public-version pins. Corporate mirror/proxy/certificate configuration remains owned by IT; the notebook does not override it, disable TLS, use `--trusted-host`, or install arbitrary package sources.

The two manifests pin the ASR snapshot and direct UI requirements. They are not cross-platform hash-locked full dependency closures; the approved process should freeze and checksum its final platform-specific wheelhouse.

### Model provisioning

On an authorized connected machine, the existing command explicitly downloads fixed files and verifies SHA256:

```powershell
python scripts/download_models.py --backend gigaam --output models/gigaam
```

Transfer the resulting directory by the approved corporate process. The notebook re-verifies `config.json` and `pytorch_model.bin` hashes locally. Source: [ai-sage/GigaAM-Multilingual](https://huggingface.co/ai-sage/GigaAM-Multilingual/tree/2f8a57144e6ec3adfd32fe0484d9ea9913305bc8). The notebook does not download the model, run remote Python from Hugging Face, or change permissions/network settings.

## Validation, 2026-10-12

**Passed on Linux x86_64, CPython 3.13.5 (ordinary GIL build):**

- Real installation of pinned GigaAM source and ASR dependencies; Torch/Torchaudio `2.10.0+cpu`, NumPy `2.5.4`, ONNX `1.19.1`, ONNX Runtime `1.23.2`, ipykernel `6.30.1`, ipywidgets `8.1.7`, sounddevice `0.5.2`.
- `uv pip check`: all 64 installed packages compatible (includes pytest tooling).
- The exact `WORKER_CODE` embedded in the new notebook also ran successfully with the explicit FFmpeg path and returned the same nonempty text.
- Actual local neural inference using verified model weights and the upstream Russian `example.wav`, with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`: a nonempty Russian transcription, approximately 11.43 seconds total including model loading. This is one smoke test, not WER or real-time evidence, and does not establish Kazakh/English accuracy.
- The actual notebook configuration and skipped-install cells execute successfully. Full preflight stops at the missing Linux system PortAudio library in this cloud container; live microphone operation is not claimed. The notebook reports this distinctly from missing Python packages.
- 20 existing/new local-notebook structural and mocked microphone lifecycle tests: recording success, stop state, cleanup, overflow, device failure, cancellation, timeout, and Windows/POSIX process-tree cleanup calls. These use fake audio/processes, not hardware. The test-only `asyncio_mode` warning comes from omitting pytest-asyncio from this minimal environment.

**Not executed/verified:** exact CPython **3.13.11**, Windows installation/inference, actual Windows microphone permissions/hardware, VS Code widget rendering, corporate importer or wheelhouse installation, GPU, ARM64, free-threaded Python. The target 3.13.11 uses the same ordinary `cp313` ABI as tested 3.13.5, but that is compatibility rationale, not an executed patch-version test. An attempted additional interpreter provision did not complete, so no 3.13.11 execution is claimed.

Official compatibility sources: [PyTorch Windows supported Python versions](https://pytorch.org/get-started/locally/), [PyTorch 2.10 install commands](https://pytorch.org/get-started/previous-versions/), [ONNX Runtime 1.23.2 files](https://pypi.org/project/onnxruntime/1.23.2/#files).

The cloud runtime has no user microphone. The notebook has bounded audio (30 seconds / 5 MB), a 600-second inference timeout, no automatic recording or TXT export, and temporary-audio cleanup on normal/error/cancellation paths. Force-killing a kernel or machine can leave temporary files. Clear notebook outputs and widget state before sharing transcripts or notebooks.
