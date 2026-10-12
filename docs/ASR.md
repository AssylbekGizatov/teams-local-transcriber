# Local speech recognition

## What runs locally

The default recognizer is **GigaAM Multilingual 220M CTC**. It is an acoustic speech-recognition model, with no external speech API and no additional LLM. The same multilingual decoder handles Kazakh (`kk`), Russian (`ru`), and English (`en`). The application accepts a language hint, but GigaAM does **not** support forcing the output language. Selecting `auto` has the same inference behavior for this backend.

The [official model card](https://huggingface.co/ai-sage/GigaAM-Multilingual/blob/2f8a57144e6ec3adfd32fe0484d9ea9913305bc8/README.md) reports strong Kazakh/Russian performance and moderate English performance. Those are upstream benchmark claims, not measurements on your meetings. Validate on consented recordings with your microphones, accents, terminology, and code-switching before relying on the transcript. CTC output is generally lowercase and unpunctuated; this service does not send it to a language model for correction.

A local **faster-whisper** backend is also implemented. The explicit downloader provisions the multilingual `small` model as an optional resource-conscious alternative. Its Kazakh and English quality has not been measured here. The adapter can consume another operator-provisioned compatible CTranslate2 directory; runtime downloads remain disabled.

There is no diarization or identity inference. Every result has `speaker: null`. Audio/word timestamps are model estimates, not guaranteed word alignment.

## Reproducible model and code pins

| Artifact | Immutable source |
| --- | --- |
| GigaAM Python implementation | [salute-developers/GigaAM at 7447938d791c4f3e643386ee22c33777004293a5](https://github.com/salute-developers/GigaAM/tree/7447938d791c4f3e643386ee22c33777004293a5) |
| GigaAM multilingual CTC weights/config | [ai-sage/GigaAM-Multilingual at 2f8a57144e6ec3adfd32fe0484d9ea9913305bc8](https://huggingface.co/ai-sage/GigaAM-Multilingual/tree/2f8a57144e6ec3adfd32fe0484d9ea9913305bc8) |
| Optional faster-whisper small | [Systran/faster-whisper-small at 536b0662742c02347bc0e980a01041f333bce120](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120) |

GigaAM weights are 883,170,115 bytes. SHA256:

```
e1db43873ec5e296f229572e06e2470fc157ac9f8d4aacabda295630b9b91728
```

The exact config SHA256 is also pinned in `app/asr.py`. The optional Whisper weight/config/tokenizer/vocabulary hashes are pinned in `scripts/download_models.py`. Provisioning writes `provenance.json`, including immutable revisions and hashes. No `main`/`ctc` branch lookup occurs at application runtime.

The GigaAM code [MIT license](https://github.com/salute-developers/GigaAM/blob/7447938d791c4f3e643386ee22c33777004293a5/LICENSE) is included in the provisioned model directory. The GigaAM and optional Whisper model cards identify MIT licensing. Preserve upstream notices when redistributing dependencies or weights. Application licensing does not replace dependency licensing.

## Installation and one-time provisioning

Run from the project root after installing the base application. Python 3.12 was used for the actual inference check. FFmpeg must be installed separately.

```bash
# CPU wheels avoid downloading CUDA libraries on a CPU host.
uv pip install --python .venv/bin/python \
  torch==2.10.0 torchaudio==2.10.0 \
  --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -r requirements-asr.lock

# This command intentionally uses the network to fetch official artifacts.
python scripts/download_models.py --backend gigaam --output ./models/gigaam
```

Set:

```dotenv
ASR_BACKEND=gigaam
ASR_MODEL_PATH=/absolute/path/to/models/gigaam
ASR_DEVICE=cpu
ASR_CHUNK_SECONDS=24
```

`requirements-asr.lock` pins the complete CPU ASR dependency versions validated on Linux x86_64 / Python 3.12. `requirements-asr.in` is the root dependency specification; changing platforms or GPU runtimes requires resolving and revalidating the corresponding dependency set.

`ASR_MODEL_PATH` must be a **local directory** containing `config.json` and `pytorch_model.bin`, not a hub model name or `.ckpt` path. Missing or changed weights cause an actionable error; the worker will never silently fetch a substitute.

The native adapter constructs GigaAM from the checksum-pinned config using an explicit allowlist of native classes. It loads the tensor state dict with `torch.load(weights_only=True)`. It does not execute Hugging Face `modeling_gigaam.py`, use `trust_remote_code`, or use upstream `load_model()`'s auto-download paths. The upstream long-form helper/pyannote integration is deliberately not required.

For offline machines, provision on a controlled build machine, copy the full model directory and installed dependencies, verify hashes, and mount the model directory read-only. Restrict model-directory write access to the administrator. Network denial on the ASR worker is recommended defense in depth; the meeting connector may independently need Microsoft access.

For optional faster-whisper:

```bash
uv pip install --python .venv/bin/python faster-whisper==1.2.1
python scripts/download_models.py --backend faster-whisper --output ./models/faster-whisper
```

```dotenv
ASR_BACKEND=faster-whisper
ASR_MODEL_PATH=/absolute/path/to/models/faster-whisper
ASR_DEVICE=cpu
ASR_COMPUTE_TYPE=int8
```

The faster-whisper loader is always invoked with `local_files_only=True`, and requires `tokenizer.json` locally so it cannot fall back to a tokenizer hub lookup. Use a compatible CUDA PyTorch/CTranslate2 installation for GPU deployments; GPU execution has not been tested in this environment. `compute_type` applies only to faster-whisper; the GigaAM adapter runs float32.

## Integration contract

```python
from pathlib import Path
from app.asr import ASRSettings, LocalASR

recognizer = LocalASR(ASRSettings(
    backend="gigaam",
    model_path=Path("models/gigaam"),
    device="cpu",
    cpu_threads=4,
    chunk_seconds=24,
    overlap_seconds=1,
    max_audio_seconds=14400,
    max_input_bytes=2_147_483_648,
    ffmpeg_timeout_seconds=600,
))
segments = recognizer.transcribe(Path("meeting.m4a"), language="kk")
# [{"start": 0.12, "end": 18.48, "text": "...", "speaker": None}, ...]
```

`language` accepts `None`, `auto`, `kk`, `ru`, or `en`. Local model loading is deferred until a non-silent chunk arrives. One instance serializes inference with a lock; use the application's durable queue rather than launching many model copies. `ASRError`, `AudioDecodeError`, and `ModelUnavailableError` have sanitized operator-facing messages. Do not log meeting text or raw decoder output.

`normalize_audio(source, destination, settings)` writes normalized PCM and returns the exact decoded duration. Application callers should provide a private temporary destination. The recognizer does so automatically and cleans normalized/chunk files on success and failure.

## Media and segmentation limits

- FFmpeg is invoked with an argument list, `shell=False`, no stdin, a timeout, bounded CPU threads, a local-file protocol whitelist, and a demuxer whitelist excluding playlists and concat inputs.
- Inputs are normalized to mono 16 kHz signed 16-bit PCM. Supported demuxers include WAV, MP3, M4A/MP4/MOV, Matroska/WebM, Ogg, FLAC, AAC, AMR, and ASF. A supplied container must contain an audio stream.
- File-size and decoded-duration limits are enforced. Decoding reads at most `max_audio_seconds + 1` seconds so exceeding the limit is detected and rejected, rather than silently returning a partial meeting. Compressed source bytes and normalized PCM both consume disk space; provision temporary storage accordingly.
- Default settings permit 4 hours and 2 GiB of source input, subject to the application's possibly stricter upload limit. Four hours of normalized PCM uses about 461 MB. Longer settings require corresponding storage and supervision.
- Each neural inference window is at most 25 seconds, matching [upstream's short-form bound](https://github.com/salute-developers/GigaAM/blob/7447938d791c4f3e643386ee22c33777004293a5/gigaam/model.py). Default windows are 24 seconds with 1 second of overlap.
- Overlap is split at its midpoint. A word is retained only in the window owning its timestamp midpoint, and reported timestamps are clipped to ownership boundaries. This makes output segments non-overlapping without guessed text matching. Estimated timestamps can still cause seam omissions or repetition; inspect difficult boundaries during acceptance testing.
- Only exact digital silence skips inference. This is not a trained voice-activity detector. Very quiet speech is not discarded by a heuristic energy threshold. Tiny non-silent clips are zero-padded to 0.5 seconds for the model, and timestamps are clipped back to the real duration.
- The neural model has no wall-clock cancellation primitive in this adapter. Bound total job duration and enforce memory/CPU limits at the worker/container supervisor; FFmpeg itself has a process timeout.

## Verification performed

Verified in the build environment on 2026-10-12:

- SHA256 verification of the actual 883 MB official CTC checkpoint and config, followed by local model construction with the pinned code.
- Real CPU inference using PyTorch/torchaudio `2.10.0+cpu` on the [official 11.29-second Russian example](https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM/example.wav). Output matched the upstream test's expected ASR text. The adapter returned one timestamped segment, from 0.080 to 10.890 seconds.
- Three concatenated copies of that example, 33.87 seconds total, passed through real two-window inference. Output segments were non-overlapping. The split reconstructed all three repetitions without an extra or missing word at that tested boundary.
- A real 10 ms nonzero PCM input completed without convolution/shape errors and returned no speech. Exact digital silence was decoded by FFmpeg and skipped the neural model.
- The single-sample cold run took about 7.1 seconds with two inference threads and approximately 1.91 GiB peak process RSS. This is an environment-specific smoke measurement, not a meeting-capacity or latency guarantee.
- The full real FastAPI upload → durable SQLite queue → worker → stored transcript → TXT/Markdown/DOCX/PDF export flow passed. Text extracted from all four formats matched the neural transcript. Reopening the database preserved results; source uploads and normalized temporary PCM were removed. The local `/v1/audio/transcriptions` endpoint separately returned the same text and the true 11.29-second WAV duration.
- The real neural test was also run with Python socket connections and URL downloads patched to fail; it completed successfully.
- Unit/contract tests explicitly label fake recognizers. They cover overlap boundaries, timestamps, missing/tampered models, local-only faster-whisper construction, real FFmpeg normalization/silence/malformed media, duration and size limits, decoder timeout, and atomic download/checksum handling.

Run the lightweight checks:

```bash
uv run pytest tests/test_asr.py -q
uv run ruff check app/asr.py scripts/download_models.py tests/test_asr.py
```

Run the opt-in real neural test after provisioning (the test never downloads):

```bash
ASR_REAL_MODEL_DIR=/absolute/path/to/models/gigaam \
ASR_REAL_SAMPLE=/absolute/path/to/consented-short-russian.wav \
uv run pytest tests/test_asr.py -k opt_in_real -q
```

Real Kazakh/English accuracy, multi-speaker meeting quality, diarization, CUDA, faster-whisper neural inference, and Teams live-call transport were **not** validated by these ASR checks. Keep a held-out, consented evaluation set for each language and code-switched meetings. Measure word error rate with an agreed normalization policy, deletion/repetition near chunk boundaries, processing time, memory, and silence/noise behavior before a production rollout.
