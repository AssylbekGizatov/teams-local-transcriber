"""Russian offline microphone window. All Tk operations stay on the UI thread."""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from recording import Recorder, RecordingError


def _transcribe(samples, sample_rate):
    from engine import Transcriber
    return Transcriber().transcribe(samples, sample_rate)


class _LazyTranscriber:
    def __init__(self):
        self.engine = None

    def __call__(self, samples, sample_rate):
        if self.engine is None:
            from engine import Transcriber
            self.engine = Transcriber()
        return self.engine.transcribe(samples, sample_rate)


class Session:
    """GUI-independent lifecycle. Workers only write to a queue, never to Tk."""

    def __init__(self, recorder, transcribe=_transcribe):
        self.recorder = recorder
        self.transcribe = _LazyTranscriber() if transcribe is _transcribe else transcribe
        self.state = "idle"
        self.results = queue.Queue()
        self._generation = 0
        self.worker = None

    def start(self, device=None):
        if self.state != "idle":
            return False
        self.recorder.start(device)
        self._generation += 1
        self.state = "recording"
        return True

    def stop(self):
        if self.state != "recording":
            return False
        self.state = "idle"
        samples, rate = self.recorder.stop()
        if len(samples) == 0:
            raise RecordingError("Запись пуста. Нажмите «Записать» и произнесите несколько слов.")
        self.state = "transcribing"
        generation = self._generation
        def work():
            nonlocal samples
            try:
                result = ("text", self.transcribe(samples, rate))
            except Exception as exc:
                result = ("error", exc)
            finally:
                samples = None
            # Queue is thread-safe; closed sessions never apply queued results.
            self.results.put((generation, result))
        self.worker = threading.Thread(target=work, name="offline-transcription", daemon=True)
        try:
            self.worker.start()
        except Exception:
            self.state = "idle"
            raise
        return True

    def poll(self):
        try:
            generation, result = self.results.get_nowait()
        except queue.Empty:
            return None
        if self.state == "closed" or generation != self._generation:
            return None
        self.state = "idle"
        return result

    def close(self):
        self.state = "closed"
        self._generation += 1
        self.recorder.discard()
        while True:
            try:
                self.results.get_nowait()
            except queue.Empty:
                break


class MicrophoneApp:
    def __init__(self, root, recorder=None, transcribe=_transcribe):
        self.root = root
        self.session = Session(recorder if recorder is not None else Recorder(), transcribe)
        self._after = None
        self._started = 0.0
        self._saved_text = ""
        self._device_ids = [None]
        root.title("Микрофон → текст · офлайн")
        root.geometry("800x650")
        root.minsize(660, 530)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Микрофон → текст", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Локальное распознавание на процессоре. Интернет и сервер не нужны.").pack(anchor="w", pady=(5, 12))
        devices = ttk.Frame(frame)
        devices.pack(fill="x")
        ttk.Label(devices, text="Микрофон:").pack(side="left", padx=(0, 8))
        self.device = ttk.Combobox(devices, state="readonly")
        self.device.pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(devices, text="Обновить", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(8, 0))
        self.consent = tk.BooleanVar(value=False)
        self.consent_button = ttk.Checkbutton(
            frame, variable=self.consent,
            text="У меня есть право на запись, и все участники согласны.")
        self.consent_button.pack(anchor="w", pady=(16, 5))
        ttk.Label(frame, text="Запись начинается только по кнопке. Максимум 5 минут.\n"
                             "Аудио хранится только в памяти и не сохраняется в файл.").pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=14)
        self.record_button = ttk.Button(buttons, text="● Записать", command=self.start)
        self.record_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="■ Стоп и распознать", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.timer = ttk.Label(buttons, text="00:00 / 05:00")
        self.timer.pack(side="right")
        self.status = tk.StringVar(value="Готово к записи.")
        ttk.Label(frame, textvariable=self.status, wraplength=740).pack(anchor="w", pady=(0, 10))
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill="both", expand=True)
        self.text = tk.Text(text_frame, wrap="word", undo=True, font=("Segoe UI", 12))
        scrollbar = ttk.Scrollbar(text_frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 0))
        self.copy_button = ttk.Button(actions, text="Копировать текст", command=self.copy, state="disabled")
        self.copy_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="Сохранить TXT…", command=self.save, state="disabled")
        self.save_button.pack(side="left", padx=8)
        self.text.bind("<<Modified>>", self._text_changed)
        self.refresh()
        self._schedule()

    def _text_changed(self, event=None):
        if self.text.edit_modified():
            state = "normal" if self.text.get("1.0", "end-1c").strip() else "disabled"
            self.copy_button.configure(state=state)
            self.save_button.configure(state=state)
            self.text.edit_modified(False)

    def _unsaved(self):
        value = self.text.get("1.0", "end-1c")
        return bool(value.strip()) and value != self._saved_text

    def refresh(self):
        if self.session.state != "idle":
            return
        current = self.device.current()
        chosen = self._device_ids[current] if 0 <= current < len(self._device_ids) else None
        try:
            devices = self.session.recorder.devices()
            self._device_ids = [None] + [i for i, _ in devices]
            self.device["values"] = ["Системный микрофон по умолчанию"] + [f"{name} (№ {i})" for i, name in devices]
            self.device.current(self._device_ids.index(chosen) if chosen in self._device_ids else 0)
            if not devices:
                self.status.set("Микрофоны не найдены. Подключите микрофон и нажмите «Обновить».")
        except Exception:
            self._device_ids = [None]
            self.device["values"] = ["Системный микрофон по умолчанию"]
            self.device.current(0)
            self.status.set("Не удалось получить список микрофонов. Проверьте подключение и разрешения Windows.")

    def _controls(self):
        state = self.session.state
        idle = state == "idle"
        self.record_button.configure(state="normal" if idle else "disabled")
        self.stop_button.configure(state="normal" if state == "recording" else "disabled")
        self.refresh_button.configure(state="normal" if idle else "disabled")
        self.device.configure(state="readonly" if idle else "disabled")
        self.consent_button.configure(state="normal" if idle else "disabled")

    def start(self):
        if self.session.state != "idle":
            return
        if not self.consent.get():
            messagebox.showwarning("Согласие на запись", "Подтвердите право на запись и согласие участников.", parent=self.root)
            return
        if self._unsaved() and not messagebox.askyesno(
                "Предыдущий текст", "После распознавания предыдущий текст будет заменён. Продолжить без сохранения?", parent=self.root):
            return
        try:
            index = self.device.current()
            device = self._device_ids[index] if 0 <= index < len(self._device_ids) else None
            if self.session.start(device):
                self._started = time.monotonic()
                self.timer.configure(text="00:00 / 05:00")
                self.status.set("Идёт запись. Нажмите «Стоп и распознать», когда закончите.")
        except Exception as exc:
            self._show_error(exc)
        self._controls()

    def stop(self):
        if self.session.state != "recording":
            return
        try:
            if self.session.stop():
                self.status.set("Распознавание на процессоре… Первая загрузка модели может занять несколько минут.")
                self.consent.set(False)
        except Exception as exc:
            self._show_error(exc)
        self._controls()

    def _show_error(self, exc):
        # Expected domain errors are suitable for users; do not show raw OS paths or tracebacks.
        if isinstance(exc, (RecordingError, FileNotFoundError)):
            detail = str(exc)
        elif exc.__class__.__name__ in ("TranscriptionError", "ModelError", "ModelNotFoundError", "EngineError"):
            detail = str(exc)
        else:
            detail = "Не удалось распознать запись. Проверьте комплектность папки приложения и свободную память."
        self.status.set(detail)
        messagebox.showerror("Не удалось завершить действие", detail, parent=self.root)

    def _schedule(self):
        if self.session.state != "closed":
            self._after = self.root.after(100, self._tick)

    def _tick(self):
        self._after = None
        if self.session.state == "closed":
            return
        if self.session.state == "recording":
            elapsed = min(300, int(time.monotonic() - self._started))
            self.timer.configure(text=f"{elapsed // 60:02d}:{elapsed % 60:02d} / 05:00")
            if elapsed >= 300 or self.session.recorder.reached_limit or self.session.recorder.error:
                self.stop()
        result = self.session.poll()
        if result:
            kind, value = result
            if kind == "text":
                if value.strip():
                    self.text.delete("1.0", "end")
                    self.text.insert("1.0", value)
                    self.status.set("Готово. Проверьте текст, при необходимости исправьте и сохраните.")
                else:
                    self.status.set("Речь не распознана. Предыдущий текст сохранён. Проверьте микрофон и повторите запись.")
            else:
                self._show_error(value)
            self._controls()
        self._schedule()

    def copy(self):
        value = self.text.get("1.0", "end-1c")
        if not value.strip():
            self.status.set("Пока нет текста для копирования.")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.status.set("Текст скопирован в буфер обмена.")
        except tk.TclError:
            self.status.set("Не удалось скопировать текст. Попробуйте ещё раз.")

    def save(self):
        value = self.text.get("1.0", "end-1c")
        if not value.strip():
            self.status.set("Пока нет текста для сохранения.")
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Сохранить расшифровку", defaultextension=".txt",
                                           filetypes=[("Текст UTF-8", "*.txt")], confirmoverwrite=True)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as output:
                output.write(value)
            self._saved_text = value
            self.status.set("Текст сохранён в UTF-8.")
        except (OSError, UnicodeError):
            messagebox.showerror("Сохранение", "Не удалось сохранить файл. Выберите доступную папку и проверьте свободное место.", parent=self.root)

    def close(self, force=False):
        if self.session.state == "closed":
            return
        if not force and (self._unsaved() or self.session.state in ("recording", "transcribing")) and not messagebox.askyesno(
                "Закрыть приложение?", "Несохранённый текст и результат текущей записи будут потеряны. Закрыть приложение?", parent=self.root):
            return
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        self.session.close()
        self.root.destroy()


def run():
    root = tk.Tk()
    try:
        MicrophoneApp(root)
    except Exception:
        messagebox.showerror("Запуск", "Не удалось запустить приложение. Проверьте комплектность папки и доступ к звуковым устройствам.", parent=root)
        root.destroy()
        raise
    root.mainloop()


def self_test():
    """Real Tk + fake microphone + fake inference; usable under Xvfb and Windows CI."""
    import numpy as np
    class FakeRecorder:
        reached_limit = False
        error = None
        active = False
        def devices(self):
            return [(0, "Тестовый микрофон")]
        def start(self, device=None):
            self.active = True
        def stop(self):
            self.active = False
            return np.ones(1600, dtype=np.float32), 16000
        def discard(self):
            self.active = False
    root = tk.Tk()
    root.withdraw()
    recorder = FakeRecorder()
    app = MicrophoneApp(root, recorder, lambda samples, rate: "Проверка офлайн")
    try:
        app.consent.set(True)
        app.start()
        assert app.session.state == "recording" and recorder.active
        app.start()  # Repeated clicks must not start another stream.
        app.stop()
        app.stop()
        app.session.worker.join(timeout=5)
        root.after_cancel(app._after)
        app._after = None
        app._tick()
        assert app.session.state == "idle"
        assert app.text.get("1.0", "end-1c") == "Проверка офлайн"
        app.copy()
        assert root.clipboard_get() == "Проверка офлайн"
    finally:
        app._saved_text = app.text.get("1.0", "end-1c")
        app.close(force=True)
    assert not recorder.active and app.session.state == "closed"
    return True
