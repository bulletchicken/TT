import audioop
import threading

import pyaudio


class AudioIO:
    """Audio I/O interface for OpenAI Realtime API."""
    
    def __init__(self, chunk_size=1024, rate=24000, format=pyaudio.paInt16):
        self.p = pyaudio.PyAudio()
        self.mic_queue = []
        self.mic_lock = threading.Lock()
        self.audio_buffer = bytearray()
        self._stop_event = threading.Event()
        self.ducking = False
        self.chunk_size = chunk_size
        self.rate = rate
        self.format = format

    # Mic callback: collect raw mic audio
    def _mic_cb(self, in_data, frame_count, time_info, status):
        data = in_data
        if self.ducking:
            try:
                # Reduce mic input by -5 dB while TTS is playing
                data = audioop.mul(in_data, 2, 0.5623)
            except Exception:
                data = in_data
        with self.mic_lock:
            self.mic_queue.append(data)
        return (None, pyaudio.paContinue)

    # Speaker callback: play whatever audio we have from the model
    def _spk_cb(self, in_data, frame_count, time_info, status):
        needed = frame_count * 2  # 2 bytes per sample
        if len(self.audio_buffer) >= needed:
            chunk = self.audio_buffer[:needed]
            self.audio_buffer = self.audio_buffer[needed:]
            self.ducking = True  # Enable ducking when playing audio
        else:
            chunk = self.audio_buffer + b"\x00" * (needed - len(self.audio_buffer))
            self.audio_buffer.clear()
            self.ducking = False  # Disable ducking when no audio
        return (bytes(chunk), pyaudio.paContinue)

    def start(self):
        self.in_stream = self.p.open(
            format=self.format,
            channels=1,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=self._mic_cb,
        )
        self.out_stream = self.p.open(
            format=self.format,
            channels=1,
            rate=self.rate,
            output=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=self._spk_cb,
        )
        self.in_stream.start_stream()
        self.out_stream.start_stream()

    def stop(self):
        self._stop_event.set()
        self.in_stream.stop_stream()
        self.in_stream.close()
        self.out_stream.stop_stream()
        self.out_stream.close()
        self.p.terminate()

    def push_tts(self, audio_bytes: bytes):
        self.audio_buffer.extend(audio_bytes)

    def read_mic_chunk(self):
        with self.mic_lock:
            if self.mic_queue:
                return self.mic_queue.pop(0)
        return None

