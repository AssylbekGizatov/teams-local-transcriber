import pathlib
import sys
import unittest

import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from recording import Recorder, RecordingError


class CallbackStop(Exception):
    pass


class FakeStream:
    def __init__(self, audio, **kwargs):
        self.audio = audio
        self.callback = kwargs['callback']
        self.closed = False
        self.started = False
    def start(self):
        self.started = True
        if self.audio.fail_start:
            raise RuntimeError('start')
    def stop(self):
        if self.audio.fail_stop:
            raise RuntimeError('stop')
        self.started = False
    def close(self):
        self.closed = True


class FakeAudio:
    CallbackStop = CallbackStop
    fail_start = False
    fail_stop = False
    def query_devices(self, *args):
        if args:
            return {'default_samplerate': 16000}
        return [{'name': 'Input', 'max_input_channels': 1},
                {'name': 'Output', 'max_input_channels': 0}]
    def InputStream(self, **kwargs):
        self.stream = FakeStream(self, **kwargs)
        return self.stream


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.audio = FakeAudio()
        self.recorder = Recorder(self.audio, max_seconds=1)
    def tearDown(self):
        self.recorder.discard()
    def test_filters_output_devices(self):
        self.assertEqual(self.recorder.devices(), [(0, 'Input')])
    def test_mono_float32_copy_and_cleanup(self):
        self.recorder.start()
        incoming = np.ones((8000, 1), dtype=np.float32)
        self.audio.stream.callback(incoming, 8000, None, None)
        incoming[:] = 0
        samples, rate = self.recorder.stop()
        self.assertEqual(rate, 16000)
        self.assertEqual(samples.dtype, np.float32)
        self.assertEqual(samples.shape, (8000,))
        self.assertTrue(np.all(samples == 1))
        self.assertTrue(self.audio.stream.closed)
        self.assertEqual(len(self.recorder.stop()[0]), 0)
    def test_bounded_at_exact_limit(self):
        self.recorder.start()
        with self.assertRaises(CallbackStop):
            self.audio.stream.callback(np.ones((20000, 1)), 20000, None, None)
        self.assertTrue(self.recorder.reached_limit)
        self.assertEqual(len(self.recorder.stop()[0]), 16000)
    def test_start_failure_closes_stream(self):
        self.audio.fail_start = True
        with self.assertRaises(RecordingError):
            self.recorder.start()
        self.assertTrue(self.audio.stream.closed)
    def test_stop_failure_still_closes_and_discards(self):
        self.recorder.start()
        self.audio.fail_stop = True
        with self.assertRaises(RecordingError):
            self.recorder.stop()
        self.assertTrue(self.audio.stream.closed)
        self.assertEqual(len(self.recorder.stop()[0]), 0)
    def test_capture_status_fails_without_partial_transcription(self):
        self.recorder.start()
        with self.assertRaises(CallbackStop):
            self.audio.stream.callback(np.ones((32, 1)), 32, None, 'overflow')
        self.assertIsNotNone(self.recorder.error)
        with self.assertRaises(RecordingError):
            self.recorder.stop()
        self.assertTrue(self.audio.stream.closed)
    def test_duplicate_start_does_not_replace_stream(self):
        self.recorder.start()
        stream = self.audio.stream
        with self.assertRaises(RecordingError):
            self.recorder.start()
        self.assertIs(stream, self.audio.stream)
    def test_discard_clears_samples(self):
        self.recorder.start()
        self.audio.stream.callback(np.ones((32, 1)), 32, None, None)
        self.recorder.discard()
        self.assertEqual(len(self.recorder.stop()[0]), 0)
    def test_maximum_hard_limit(self):
        with self.assertRaises(ValueError):
            Recorder(self.audio, max_seconds=301)
