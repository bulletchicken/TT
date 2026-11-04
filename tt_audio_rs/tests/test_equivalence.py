"""
Checks that the Rust pipeline (tt_audio_rs) behaves exactly like the Python
one it replaces, byte for byte.

Run from the repo root:
    python -m unittest discover -s tt_audio_rs/tests -v
    TT_AUDIO_HW=1 python -m unittest discover -s tt_audio_rs/tests -v   # + real devices
"""

import audioop
import os
import queue
import random
import time
import unittest

import tt_audio_rs
from tt.utils.audio_interface import PyAudioInterface
from tt.utils.audio_interface_openai import AudioIO as PyAudioIO
from tt.utils.audio_interface_rust import RustAudioInterface

ALL_SAMPLES = b"".join(i.to_bytes(2, "little", signed=True) for i in range(-32768, 32768))
HARDWARE = os.getenv("TT_AUDIO_HW") == "1"


class TestKernels(unittest.TestCase):
    def test_mul_matches_audioop_for_every_sample(self):
        rng = random.Random(0)
        factors = [0.1, 0.5623, 1.0, 2.0, -1.0, 0.0, 1e-9, 3.7] + [rng.uniform(-4, 4) for _ in range(8)]
        for factor in factors:
            self.assertEqual(tt_audio_rs.mul(ALL_SAMPLES, 2, factor), audioop.mul(ALL_SAMPLES, 2, factor), factor)

    def test_ratecv_matches_audioop_across_chunked_streams(self):
        rates = [(16000, 48000), (16000, 44100), (24000, 48000), (48000, 16000),
                 (44100, 16000), (16000, 22050), (8000, 16000), (16000, 16000)]
        rng = random.Random(1)
        for inrate, outrate in rates:
            py_state = rs_state = None
            for _ in range(300):
                fragment = os.urandom(2 * rng.choice([0, 1, 2, rng.randint(3, 5000)]))
                expected, py_state = audioop.ratecv(fragment, 2, 1, inrate, outrate, py_state)
                got, rs_state = tt_audio_rs.ratecv(fragment, 2, 1, inrate, outrate, rs_state)
                self.assertEqual(got, expected, (inrate, outrate))
                self.assertEqual(rs_state, py_state, (inrate, outrate))

    def test_rejects_partial_frames(self):
        with self.assertRaises(ValueError):
            tt_audio_rs.mul(b"\x00\x01\x02", 2, 0.5)
        with self.assertRaises(ValueError):
            tt_audio_rs.ratecv(b"\x00", 2, 1, 16000, 48000, None)


class TestOpenAIAudioIO(unittest.TestCase):
    """Drive the Python and Rust AudioIO through the same callback sequence."""

    def setUp(self):
        self.py = PyAudioIO(chunk_size=1024, rate=24000)
        self.rs = tt_audio_rs.AudioIO(chunk_size=1024, rate=24000)

    def tearDown(self):
        self.py.p.terminate()

    def test_random_sequences_match(self):
        rng = random.Random(7)
        ducking_states = set()
        for step in range(5000):
            op = rng.random()
            if op < 0.35:
                # Averages a bit less than one speaker buffer per push, so the
                # buffer alternately fills up and runs dry (ducking on and off).
                tts = os.urandom(2 * rng.randint(0, 1800))
                self.py.push_tts(tts)
                self.rs.push_tts(tts)
            elif op < 0.7:
                played = self.py._spk_cb(None, 1024, {}, 0)
                self.assertEqual(self.rs._spk_cb(None, 1024, {}, 0), played, step)
                self.assertEqual(self.rs.ducking, self.py.ducking, step)
                ducking_states.add(self.py.ducking)
            else:
                mic = os.urandom(2048)
                self.py._mic_cb(mic, 1024, {}, 0)
                self.rs._mic_cb(mic, 1024, {}, 0)
                self.assertEqual(self.rs.read_mic_chunk(), self.py.read_mic_chunk(), step)
        self.assertEqual(ducking_states, {True, False})

    def test_mic_is_ducked_by_5db_while_tts_plays(self):
        mic = os.urandom(2048)
        self.rs.ducking = True
        self.rs._mic_cb(mic, 1024, {}, 0)
        self.assertEqual(self.rs.read_mic_chunk(), audioop.mul(mic, 2, 0.5623))
        self.rs.ducking = False
        self.rs._mic_cb(mic, 1024, {}, 0)
        self.assertEqual(self.rs.read_mic_chunk(), mic)
        self.assertIsNone(self.rs.read_mic_chunk())

    def test_rejects_formats_other_than_int16(self):
        with self.assertRaises(ValueError):
            tt_audio_rs.AudioIO(format=1)  # paFloat32


class TestElevenLabsAudio(unittest.TestCase):
    def setUp(self):
        self.py = PyAudioInterface()
        self.rs = RustAudioInterface()

    def drain_python(self):
        chunks = []
        while True:
            try:
                chunks.append(self.py.output_queue.get_nowait())
            except queue.Empty:
                return b"".join(chunks)

    def test_output_and_interrupt_match_python(self):
        self.assertEqual(self.rs.audio.hw_rate, self.py.hw_rate)
        rng = random.Random(3)
        compared = 0
        for step in range(800):
            op = rng.random()
            if op < 0.8:
                audio = os.urandom(2 * rng.randint(0, 4000))
                self.py.output(audio)
                self.rs.output(audio)
            elif op < 0.9:
                expected = self.drain_python()
                self.assertEqual(self.rs.audio._drain(), expected, step)
                compared += len(expected)
            else:
                # Both drop everything pending and restart the resampler.
                self.py.interrupt()
                self.rs.interrupt()
        self.assertEqual(self.rs.audio._drain(), self.drain_python())
        self.assertGreater(compared, 0)

    def test_rejects_partial_frames(self):
        with self.assertRaises(ValueError):
            self.rs.output(b"\x00\x01\x02")


@unittest.skipUnless(HARDWARE, "set TT_AUDIO_HW=1 to use the real mic and speaker")
class TestHardware(unittest.TestCase):
    """Opens the default devices. Only silence is played."""

    def test_openai_audio_io_streams(self):
        io = tt_audio_rs.AudioIO()
        io.start()
        try:
            with self.assertRaises(RuntimeError):
                io._spk_cb(None, 1024, {}, 0)
            chunks, start = 0, time.monotonic()
            while time.monotonic() - start < 1.5:
                io.push_tts(bytes(4800))
                while io.read_mic_chunk() is not None:
                    chunks += 1
                time.sleep(0.05)
        finally:
            io.stop()
        io.stop()  # idempotent
        stats = io.stats()
        self.assertGreaterEqual(chunks, 25)  # 24000 / 1024 = 23.4 per second
        self.assertGreater(stats["speaker"]["callbacks"], 25)
        self.assertEqual(stats["mic_dropped_bytes"], 0)
        self.assertEqual(stats["tts_dropped_bytes"], 0)

    def test_elevenlabs_interface_streams(self):
        iface = RustAudioInterface()
        chunks = []
        iface.start(chunks.append)
        for _ in range(10):
            iface.output(bytes(3200))  # 100 ms at 16 kHz
            time.sleep(0.1)
        self.assertTrue(iface.audio.ducking)
        iface.stop()
        delivered = len(chunks)
        time.sleep(0.5)
        self.assertEqual(len(chunks), delivered, "input_callback ran after stop()")
        self.assertGreaterEqual(delivered, 3)
        self.assertEqual({len(c) for c in chunks}, {8000})
        with self.assertRaises(RuntimeError):
            iface.start(chunks.append)

    def test_stop_from_inside_input_callback(self):
        # The ElevenLabs SDK does this when the websocket closes mid-send.
        iface = RustAudioInterface()
        calls = []

        def callback(chunk):
            calls.append(chunk)
            iface.stop()

        iface.start(callback)
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
