"""Bounded, memory-only microphone capture. No network or audio files."""
from __future__ import annotations

import threading
import numpy as np


class RecordingError(RuntimeError):
    pass


class Recorder:
    """One mono input stream; every stop/error releases the PortAudio handle."""

    def __init__(self, audio=None, max_seconds=300):
        if max_seconds <= 0 or max_seconds > 300:
            raise ValueError("Длительность записи должна быть от 1 до 300 секунд.")
        if audio is None:
            import sounddevice as audio
        self.audio = audio
        self.max_seconds = float(max_seconds)
        self.sample_rate = 16000
        self._lock = threading.Lock()
        self._stream = None
        self._chunks = []
        self._frames = 0
        self._recording = False
        self._limit = False
        self._error = None

    def devices(self):
        return [(i, d["name"]) for i, d in enumerate(self.audio.query_devices())
                if d["max_input_channels"] > 0]

    @property
    def reached_limit(self):
        with self._lock:
            return self._limit

    @property
    def error(self):
        with self._lock:
            return self._error

    def start(self, device=None):
        if self._stream is not None:
            raise RecordingError("Запись уже идёт.")
        self.discard()
        try:
            info = self.audio.query_devices(device, "input")
            rate = int(round(float(info["default_samplerate"])))
            if rate < 8000 or rate > 192000:
                raise RecordingError("Микрофон сообщил неподдерживаемую частоту записи.")
            self.sample_rate = rate
            with self._lock:
                self._recording = True
            self._stream = self.audio.InputStream(
                device=device, channels=1, samplerate=rate,
                dtype="float32", callback=self._callback)
            self._stream.start()
        except Exception as exc:
            self.discard()
            if isinstance(exc, RecordingError):
                raise
            raise RecordingError("Не удалось открыть микрофон. Проверьте подключение, "
                                 "разрешение на микрофон в Windows и выбранное устройство.") from exc

    def _callback(self, indata, frames, time_info, status):
        with self._lock:
            if not self._recording:
                return
            if status:
                self._error = "Звук поступает с пропусками. Закройте лишние программы и повторите запись."
                self._recording = False
                raise self.audio.CallbackStop
            remaining = int(self.max_seconds * self.sample_rate) - self._frames
            count = min(len(indata), remaining)
            if count > 0:
                self._chunks.append(np.array(indata[:count, 0], dtype=np.float32, copy=True))
                self._frames += count
            if self._frames >= int(self.max_seconds * self.sample_rate):
                self._limit = True
                self._recording = False
                raise self.audio.CallbackStop

    def stop(self):
        with self._lock:
            self._recording = False
        stream, self._stream = self._stream, None
        close_error = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                close_error = exc
            finally:
                try:
                    stream.close()
                except Exception as exc:
                    close_error = exc
        with self._lock:
            chunks, self._chunks = self._chunks, []
            error, self._error = self._error, None
            self._frames = 0
            self._limit = False
        if error:
            raise RecordingError(error)
        if close_error:
            raise RecordingError("Не удалось корректно остановить микрофон. Переподключите устройство.") from close_error
        return (np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32), self.sample_rate)

    def discard(self):
        try:
            self.stop()
        except Exception:
            # Cleanup is best-effort; stop already closes even when stopping fails.
            pass
