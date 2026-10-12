"""Offline ASR with bounded audio decoding and explicit model provisioning.

Importing this module does not import torch, contact a model hub, or load weights.
The only network-capable model operation lives in scripts/download_models.py.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16_000
GIGAAM_CODE_REVISION = "7447938d791c4f3e643386ee22c33777004293a5"
GIGAAM_MODEL_REPO = "ai-sage/GigaAM-Multilingual"
GIGAAM_MODEL_REVISION = "2f8a57144e6ec3adfd32fe0484d9ea9913305bc8"
GIGAAM_FILES = {
    "config.json": "c830232c7d51688a630a221517b52585ab5ee57e1d3c21bcbae01759351d2653",
    "pytorch_model.bin": "e1db43873ec5e296f229572e06e2470fc157ac9f8d4aacabda295630b9b91728",
}
SUPPORTED_LANGUAGES = frozenset({"kk", "ru", "en"})


class ASRError(RuntimeError):
    """An operator-safe failure; never includes meeting text or FFmpeg stderr."""


class AudioDecodeError(ASRError):
    pass


class ModelUnavailableError(ASRError):
    pass


@dataclass(frozen=True)
class ASRSettings:
    backend: str = "gigaam"
    model_path: Path | str = Path("models/gigaam")
    device: str = "cpu"
    compute_type: str = "int8"  # faster-whisper only; GigaAM uses float32
    cpu_threads: int = 4
    chunk_seconds: float = 24.0
    overlap_seconds: float = 1.0
    max_audio_seconds: float = 14_400.0
    max_input_bytes: int = 2_147_483_648
    ffmpeg_timeout_seconds: float = 600.0
    ffmpeg_binary: str = "ffmpeg"
    temp_root: Path | str | None = None

    def __post_init__(self) -> None:
        if self.backend not in {"gigaam", "faster-whisper"}:
            raise ValueError("ASR backend must be gigaam or faster-whisper")
        if self.device not in {"cpu", "cuda", "auto"} and not (
            self.device.startswith("cuda:") and self.device[5:].isdigit()
        ):
            raise ValueError("ASR device must be cpu, auto, cuda, or cuda:N")
        numeric = (
            self.chunk_seconds,
            self.overlap_seconds,
            self.max_audio_seconds,
            self.ffmpeg_timeout_seconds,
        )
        if not all(math.isfinite(x) for x in numeric):
            raise ValueError("ASR time limits must be finite")
        if not 1 <= self.chunk_seconds <= 25:
            raise ValueError("ASR chunk_seconds must be between 1 and 25")
        if not 0 <= self.overlap_seconds < self.chunk_seconds / 2:
            raise ValueError("ASR overlap_seconds must be less than half the chunk")
        if self.max_audio_seconds <= 0 or self.ffmpeg_timeout_seconds <= 0:
            raise ValueError("ASR time limits must be positive")
        if self.max_input_bytes <= 0 or not 1 <= self.cpu_threads <= 128:
            raise ValueError("ASR resource limits are invalid")


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_gigaam_files(directory: Path) -> None:
    for filename, expected in GIGAAM_FILES.items():
        path = directory / filename
        if not path.is_file():
            raise ModelUnavailableError(
                "GigaAM model files are missing; run scripts/download_models.py explicitly"
            )
        if sha256_file(path) != expected:
            raise ModelUnavailableError(
                "GigaAM model checksum mismatch; reprovision official files"
            )


def normalize_audio(source: Path, destination: Path, settings: ASRSettings) -> float:
    """Decode only local media to bounded mono PCM. No shell and no URL inputs."""
    source = Path(source).resolve()
    if not source.is_file():
        raise AudioDecodeError("Audio input is not a readable local file")
    if source.stat().st_size > settings.max_input_bytes:
        raise AudioDecodeError("Audio input exceeds the configured size limit")
    # Exclude playlist/concat demuxers and network protocols. Even a disguised
    # playlist cannot fetch URLs or walk local referenced media with this list.
    command = [
        settings.ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        "mov,matroska,wav,mp3,ogg,flac,aac,amr,asf",
        "-threads",
        str(settings.cpu_threads),
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-t",
        str(settings.max_audio_seconds + 1),
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-threads",
        str(settings.cpu_threads),
        "-f",
        "wav",
        str(destination),
    ]
    try:
        subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=settings.ffmpeg_timeout_seconds,
            check=True,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise AudioDecodeError("FFmpeg is not installed or not configured") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioDecodeError("Audio decoding exceeded the configured time limit") from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise AudioDecodeError("Audio could not be decoded as supported local media") from exc
    try:
        with wave.open(str(destination), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != SAMPLE_RATE
            ):
                raise AudioDecodeError("Audio normalization returned an unsupported format")
            duration = audio.getnframes() / SAMPLE_RATE
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioDecodeError("Audio normalization did not produce valid PCM") from exc
    if duration > settings.max_audio_seconds:
        raise AudioDecodeError("Audio duration exceeds the configured limit")
    return duration


@dataclass(frozen=True)
class AudioWindow:
    start_frame: int
    end_frame: int
    own_start: float
    own_end: float


def audio_windows(total_frames: int, settings: ASRSettings) -> list[AudioWindow]:
    """Overlapping inputs with disjoint ownership split at overlap midpoints."""
    size = int(settings.chunk_seconds * SAMPLE_RATE)
    overlap = int(settings.overlap_seconds * SAMPLE_RATE)
    starts = []
    position = 0
    while position < total_frames:
        starts.append(position)
        if position + size >= total_frames:
            break
        position += size - overlap
    result = []
    for index, start in enumerate(starts):
        end = min(start + size, total_frames)
        left = 0 if index == 0 else start + overlap / 2
        right = total_frames if index == len(starts) - 1 else end - overlap / 2
        result.append(AudioWindow(start, end, left / SAMPLE_RATE, right / SAMPLE_RATE))
    return result


class _GigaAMBackend:
    """Native implementation; no Transformers custom/remote Python is executed."""

    def __init__(self, settings: ASRSettings) -> None:
        directory = Path(settings.model_path).resolve()
        verify_gigaam_files(directory)
        try:
            import torch
            from gigaam.model import GigaAMASR
            from omegaconf import OmegaConf
        except (ImportError, OSError) as exc:
            raise ModelUnavailableError(
                "GigaAM dependencies are unavailable; install requirements-asr.in"
            ) from exc
        torch.set_num_threads(settings.cpu_threads)
        self.torch = torch
        config = json.loads((directory / "config.json").read_text())["cfg"]["model"]["cfg"]
        # The hash above pins the entire config; also explicitly allowlist all
        # Hydra targets so configs cannot choose arbitrary importable callables.
        allowed = {
            "preprocessor": (
                "modeling_gigaam.FeatureExtractor",
                "gigaam.preprocess.FeatureExtractor",
            ),
            "encoder": ("modeling_gigaam.ConformerEncoder", "gigaam.encoder.ConformerEncoder"),
            "head": ("modeling_gigaam.CTCHead", "gigaam.decoder.CTCHead"),
            "decoding": ("modeling_gigaam.CTCGreedyDecoding", "gigaam.decoding.CTCGreedyDecoding"),
        }
        for section, (original, target) in allowed.items():
            if config[section].get("_target_") != original:
                raise ModelUnavailableError("Unsupported GigaAM configuration")
            config[section]["_target_"] = target
        config["encoder"]["flash_attn"] = False
        device = settings.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model = GigaAMASR(OmegaConf.create(config))
            # This official HF file is a tensor state dict. Never unpickle a
            # user-supplied .ckpt and never relax weights_only to load one.
            state = torch.load(
                directory / "pytorch_model.bin", map_location="cpu", weights_only=True
            )
            state = {key.removeprefix("model."): value for key, value in state.items()}
            model.load_state_dict(state, strict=True)
            del state
            self.model = model.eval().to(device)
        except (RuntimeError, ValueError, KeyError, OSError) as exc:
            raise ModelUnavailableError(
                "GigaAM could not load; verify pinned dependencies, device, and memory"
            ) from exc

    def transcribe_chunk(self, path: Path, language: str | None) -> tuple[str, list[Any]]:
        # Decode already-normalized PCM ourselves. Upstream .transcribe calls
        # FFmpeg again without our time/resource limits; use its pure model path.
        import numpy as np

        with wave.open(str(path), "rb") as audio:
            values = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").copy()
        torch = self.torch
        wav = (
            torch.from_numpy(values).to(device=self.model._device, dtype=self.model._dtype)
            / 32768.0
        )
        wav = wav.unsqueeze(0)
        lengths = torch.tensor([wav.shape[-1]], device=self.model._device)
        with torch.inference_mode():
            encoded, encoded_lengths = self.model.forward(wav, lengths)
            text, words = self.model._decode(
                encoded, encoded_lengths, lengths, word_timestamps=True
            )[0]
        # GigaAM has one multilingual decoder and no language-forcing argument.
        return text, words or []


class _FasterWhisperBackend:
    def __init__(self, settings: ASRSettings) -> None:
        directory = Path(settings.model_path).resolve()
        required = ("model.bin", "config.json", "tokenizer.json")
        if not directory.is_dir() or not all((directory / name).is_file() for name in required):
            raise ModelUnavailableError(
                "faster-whisper requires a pre-downloaded local model directory"
            )
        try:
            from faster_whisper import WhisperModel

            device, index = settings.device, 0
            if device.startswith("cuda:"):
                device, index = "cuda", int(device[5:])
            self.model = WhisperModel(
                str(directory),
                device=device,
                device_index=index,
                compute_type=settings.compute_type,
                cpu_threads=settings.cpu_threads,
                num_workers=1,
                local_files_only=True,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise ModelUnavailableError(
                "faster-whisper could not load; verify dependencies, model, and device"
            ) from exc

    def transcribe_chunk(self, path: Path, language: str | None) -> tuple[str, list[Any]]:
        segments, _ = self.model.transcribe(
            str(path),
            language=language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        segments = list(segments)
        return " ".join(s.text.strip() for s in segments), [
            w for s in segments for w in (s.words or [])
        ]


def _word_field(word: Any, field: str) -> Any:
    if isinstance(word, dict):
        return word.get(field, word.get("word") if field == "text" else None)
    return getattr(word, field, getattr(word, "word", None) if field == "text" else None)


class LocalASR:
    """One lazily loaded local model, serialized inference, bounded PCM windows."""

    def __init__(self, settings: ASRSettings | None = None, **kwargs: Any) -> None:
        if settings is not None and kwargs:
            raise TypeError("Pass ASRSettings or keyword settings, not both")
        self.settings = settings or ASRSettings(**kwargs)
        self._backend: Any = None
        self._lock = threading.Lock()

    def _get_backend(self) -> Any:
        if self._backend is None:
            backend = _GigaAMBackend if self.settings.backend == "gigaam" else _FasterWhisperBackend
            self._backend = backend(self.settings)
        return self._backend

    def transcribe(self, path: Path, language: str | None = None) -> list[dict[str, Any]]:
        if language == "auto":
            language = None
        if language is not None and language not in SUPPORTED_LANGUAGES:
            raise ASRError("Supported language hints are kk, ru, en, or auto")
        with tempfile.TemporaryDirectory(prefix="teams-asr-", dir=self.settings.temp_root) as temp:
            normalized = Path(temp) / "normalized.wav"
            normalize_audio(Path(path), normalized, self.settings)
            results = []
            with self._lock, wave.open(str(normalized), "rb") as audio:
                windows = audio_windows(audio.getnframes(), self.settings)
                for window in windows:
                    audio.setpos(window.start_frame)
                    pcm = audio.readframes(window.end_frame - window.start_frame)
                    samples = array.array("h", pcm)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    # Only exact digital silence is suppressed, not quiet speech.
                    if not samples or not any(samples):
                        continue
                    # Conformer convolutions require a minimum receptive field.
                    # Padding is removed again by ownership timestamp bounds.
                    pcm += b"\0" * max(0, SAMPLE_RATE - len(pcm))  # >= 0.5 seconds
                    chunk = Path(temp) / "chunk.wav"
                    with wave.open(str(chunk), "wb") as output:
                        output.setnchannels(1)
                        output.setsampwidth(2)
                        output.setframerate(SAMPLE_RATE)
                        output.writeframes(pcm)
                    try:
                        text, words = self._get_backend().transcribe_chunk(chunk, language)
                    except ASRError:
                        raise
                    except Exception as exc:
                        raise ASRError(
                            "Local ASR inference failed; inspect worker diagnostics"
                        ) from exc
                    if not text.strip():
                        continue
                    offset = window.start_frame / SAMPLE_RATE
                    kept = []
                    for word in words:
                        try:
                            start = float(_word_field(word, "start")) + offset
                            end = float(_word_field(word, "end")) + offset
                            value = str(_word_field(word, "text") or "").strip()
                        except (TypeError, ValueError) as exc:
                            raise ASRError("ASR produced invalid word timestamps") from exc
                        if not math.isfinite(start) or not math.isfinite(end) or end < start:
                            raise ASRError("ASR produced invalid word timestamps")
                        midpoint = (start + end) / 2
                        if value and window.own_start <= midpoint < window.own_end:
                            kept.append(
                                (max(start, window.own_start), min(end, window.own_end), value)
                            )
                    if kept:
                        results.append(
                            {
                                "start": round(min(x[0] for x in kept), 3),
                                "end": round(max(x[1] for x in kept), 3),
                                "text": " ".join(x[2] for x in kept),
                                "speaker": None,
                            }
                        )
                    elif not words:
                        if self.settings.overlap_seconds:
                            raise ASRError(
                                "ASR returned no timestamps; overlap cannot be merged safely"
                            )
                        results.append(
                            {
                                "start": round(window.own_start, 3),
                                "end": round(window.own_end, 3),
                                "text": text.strip(),
                                "speaker": None,
                            }
                        )
            return results
