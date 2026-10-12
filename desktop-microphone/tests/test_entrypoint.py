import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("desktop_entrypoint", ROOT / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class EntryPointTests(unittest.TestCase):
    def test_diagnostic_error_preserves_stack_without_locals(self):
        try:
            private_audio = "do not serialize locals"
            raise AttributeError("missing test API")
        except AttributeError as error:
            result = app.diagnostic_error(error)
        self.assertIn("AttributeError: missing test API", result["traceback"])
        self.assertIn("test_diagnostic_error_preserves_stack_without_locals", result["traceback"])
        self.assertNotIn(private_audio, result["traceback"])
        self.assertEqual(result["stage"], "entrypoint")
        self.assertIn("platform", result)

    def test_network_guard_blocks_connections_and_server(self):
        script = '''
import app, socket
app.disable_network()
for call in [lambda: socket.create_connection(("127.0.0.1", 9)), lambda: socket.getaddrinfo("example.com", 443), lambda: socket.socket().connect(("127.0.0.1", 9)), lambda: socket.socket().bind(("127.0.0.1", 0))]:
    try: call()
    except OSError: pass
    else: raise AssertionError("Network call was allowed")
'''
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, timeout=15)

    def test_pcm16_wav_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix="microphone test ") as folder:
            path = Path(folder) / "sample with spaces.wav"
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b"\x00\x40" * 160)
            values, rate = app.read_wav(path)
            self.assertEqual(rate, 16000)
            self.assertEqual(len(values), 160)
            self.assertTrue((values == .5).all())

    def test_stereo_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(2)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b"\0" * 640)
            with self.assertRaises(ValueError):
                app.read_wav(path)


if __name__ == "__main__":
    unittest.main()
