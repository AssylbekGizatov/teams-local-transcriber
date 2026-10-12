import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine
import numpy as np


class EngineTests(unittest.TestCase):
    def test_pins_match_service(self):
        spec = importlib.util.spec_from_file_location("service_asr", ROOT.parent / "app/asr.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertEqual(engine.MODEL_FILES, module.GIGAAM_FILES)
        self.assertEqual(engine.MODEL_REVISION, module.GIGAAM_MODEL_REVISION)
        self.assertEqual(engine.CODE_REVISION, module.GIGAAM_CODE_REVISION)

    def test_missing_and_tampered_model_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(engine.EngineError):
                engine.verify_model(folder)
            for filename in engine.MODEL_FILES:
                (Path(folder) / filename).write_bytes(b"invalid")
            with self.assertRaises(engine.EngineError):
                engine.verify_model(folder)

    def test_windows_have_no_ownership_gap(self):
        for total in [1, 16000, 24 * 16000, 24 * 16000 + 1, 300 * 16000]:
            windows = list(engine.windows(total))
            self.assertEqual(windows[0][2], 0)
            self.assertEqual(windows[-1][3], total / 16000)
            for first, second in zip(windows, windows[1:]):
                self.assertEqual(first[3], second[2])
                self.assertLessEqual(first[1] - first[0], 24 * 16000)

    def test_silence_does_not_load_model(self):
        transcriber = engine.Transcriber("missing")
        with patch.object(transcriber, "load", side_effect=AssertionError("unexpected load")):
            self.assertEqual(transcriber.transcribe(np.zeros(20, dtype=np.float32), 16000), "")
            self.assertEqual(transcriber.transcribe(np.array([], dtype=np.float32), 16000), "")

    def test_invalid_input_rejected_before_load(self):
        transcriber = engine.Transcriber("missing")
        for samples, rate in [(np.zeros((5, 2)), 16000), (np.array([np.nan]), 16000), (np.ones(1), 0), (np.ones(1), 16000.0), (np.zeros(300 * 8000 + 1), 8000)]:
            with self.assertRaises(engine.EngineError):
                transcriber.transcribe(samples, rate)


if __name__ == "__main__":
    unittest.main()
