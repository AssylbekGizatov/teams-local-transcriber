import os
import pathlib
import sys
import threading
import types
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gui import Session, self_test, MicrophoneApp
from recording import RecordingError


class FakeRecorder:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.discards = 0
        self.samples = np.ones(32, dtype=np.float32)
        self.reached_limit = False
        self.error = None
    def start(self, device=None):
        self.starts += 1
    def stop(self):
        self.stops += 1
        return self.samples, 16000
    def discard(self):
        self.discards += 1
    def devices(self):
        return [(0, 'Fake microphone')]


class SessionTests(unittest.TestCase):
    def test_success_and_duplicate_clicks(self):
        recorder = FakeRecorder()
        session = Session(recorder, lambda samples, rate: 'Текст')
        self.assertTrue(session.start())
        self.assertFalse(session.start())
        self.assertTrue(session.stop())
        self.assertFalse(session.stop())
        self.assertFalse(session.start())
        session.worker.join(2)
        self.assertEqual(session.poll(), ('text', 'Текст'))
        self.assertEqual(session.state, 'idle')
        self.assertEqual((recorder.starts, recorder.stops), (1, 1))
    def test_worker_error_returns_to_idle(self):
        def failure(samples, rate):
            raise RuntimeError('failed')
        session = Session(FakeRecorder(), failure)
        session.start()
        session.stop()
        session.worker.join(2)
        result = session.poll()
        self.assertEqual(result[0], 'error')
        self.assertEqual(session.state, 'idle')
    def test_close_while_transcribing_ignores_late_result(self):
        release = threading.Event()
        entered = threading.Event()
        def blocked(samples, rate):
            entered.set()
            release.wait(2)
            return 'Late text'
        session = Session(FakeRecorder(), blocked)
        session.start()
        session.stop()
        self.assertTrue(entered.wait(1))
        session.close()
        release.set()
        session.worker.join(2)
        self.assertIsNone(session.poll())
        self.assertEqual(session.state, 'closed')
        self.assertFalse(session.start())
    def test_default_engine_is_lazy_and_reused(self):
        calls = []
        class Engine:
            def __init__(self):
                calls.append('load')
            def transcribe(self, samples, rate):
                return 'cached'
        session = Session(FakeRecorder())
        self.assertEqual(calls, [])
        with patch.dict(sys.modules, {'engine': types.SimpleNamespace(Transcriber=Engine)}):
            for _ in range(2):
                session.start()
                session.stop()
                session.worker.join(2)
                self.assertEqual(session.poll(), ('text', 'cached'))
        self.assertEqual(calls, ['load'])

    def test_empty_audio(self):
        recorder = FakeRecorder()
        recorder.samples = np.empty(0, dtype=np.float32)
        session = Session(recorder)
        session.start()
        with self.assertRaises(RecordingError):
            session.stop()
        self.assertEqual(session.state, 'idle')
    def test_close_during_recording(self):
        recorder = FakeRecorder()
        session = Session(recorder)
        session.start()
        session.close()
        self.assertEqual(recorder.discards, 1)
        self.assertFalse(session.stop())


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Tk requires Windows display or Xvfb')
class WindowTests(unittest.TestCase):
    def test_real_tk_smoke(self):
        self.assertTrue(self_test())
    def test_unsaved_close_can_be_cancelled(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        app = MicrophoneApp(root, FakeRecorder())
        try:
            self.assertEqual(str(app.copy_button['state']), 'disabled')
            self.assertEqual(str(app.save_button['state']), 'disabled')
            app.text.insert('1.0', 'Unsaved text')
            root.update()
            self.assertEqual(str(app.save_button['state']), 'normal')
            with patch('gui.messagebox.askyesno', return_value=False) as confirm:
                app.close()
                confirm.assert_called_once()
            self.assertEqual(app.session.state, 'idle')
        finally:
            app.close(force=True)

    def test_consent_blocks_recording(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        recorder = FakeRecorder()
        app = MicrophoneApp(root, recorder)
        try:
            with patch('gui.messagebox.showwarning') as warning:
                app.start()
                warning.assert_called_once()
            self.assertEqual(recorder.starts, 0)
        finally:
            app.close(force=True)
    def test_txt_utf8_and_overwrite_prompt(self):
        import tkinter as tk
        import tempfile
        root = tk.Tk()
        root.withdraw()
        app = MicrophoneApp(root, FakeRecorder())
        try:
            app.text.insert('1.0', 'Қазақша / Русский\n')
            with tempfile.TemporaryDirectory() as folder:
                path = pathlib.Path(folder) / 'test.txt'
                with patch('gui.filedialog.asksaveasfilename', return_value=str(path)) as dialog:
                    app.save()
                    self.assertTrue(dialog.call_args.kwargs['confirmoverwrite'])
                self.assertEqual(path.read_text(encoding='utf-8'), 'Қазақша / Русский\n')
        finally:
            app.close(force=True)
