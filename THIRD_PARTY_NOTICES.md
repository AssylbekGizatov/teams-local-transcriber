# Third-party components and provenance

This repository's original code is MIT licensed. This is not a claim that every dependency shares that license or that Microsoft authorizes the capture method.

| Component | Exact boundary | License / source |
|---|---|---|
| GigaAM native code | `7447938d791c4f3e643386ee22c33777004293a5` | MIT, https://github.com/salute-developers/GigaAM |
| GigaAM Multilingual 220M CTC model | HF `2f8a57144e6ec3adfd32fe0484d9ea9913305bc8`; weights/config SHA256 embedded in `app/asr.py` | MIT per model card, https://huggingface.co/ai-sage/GigaAM-Multilingual |
| Vexa | `f1a62fbbdfd2c98cb162582d82c31fe8102acc89`, fetched explicitly and locally patched | Apache-2.0 root license, https://github.com/Vexa-ai/vexa; preserve upstream LICENSE/NOTICE and THIRD_PARTY_LICENSES.md |
| faster-whisper (optional) | `1.2.1`; local adapter, not the default tested backend | MIT, https://github.com/SYSTRAN/faster-whisper |
| Faster Whisper small model (optional) | HF `536b0662742c02347bc0e980a01041f333bce120`, checksums in downloader | Model card https://huggingface.co/Systran/faster-whisper-small |
| PyTorch / torchaudio | `2.10.0` | BSD-style upstream licenses; https://pytorch.org/ |
| FFmpeg | Distribution system package | LGPL/GPL configuration-dependent; inspect the actual OS binary build https://ffmpeg.org/legal.html |
| DejaVu Sans | System font used for Unicode PDF exports | DejaVu/Bitstream Vera license https://dejavu-fonts.github.io/License.html |
| FastAPI, Starlette, Pydantic, HTTPX | Exact versions and artifacts in uv.lock / requirements.txt | Retain each installed package's distribution license metadata |
| python-docx, ReportLab | Pinned runtime packages | MIT / BSD respectively; retain installed distribution notices |

The official GigaAM example audio is used only in an opt-in engineering smoke test. It is not corporate audio and is not bundled in this source archive. Its URL, hash and expected output are documented in ASR.md. Do not infer a general speech-data redistribution license from the code license; fetch the sample from its official source for testing instead of rehosting it.

No proprietary Attendee/Elastic-2 code is included. No Microsoft SDK binary is bundled. The Vexa source is not bundled in this repository; the vendor script fetches the pinned repository with its own notices intact. Review its third-party inventory before redistribution or publishing container images. The public repository must not contain `.env`, model weights, audio, transcripts, tenant IDs or private meeting URLs.
