import audioop
import queue
import threading

import pyaudio
from elevenlabs.conversational_ai.conversation import AudioInterface

# ElevenLabs expects 16kHz 16-bit PCM mono for both input and output
ELEVENLABS_RATE = 16000


class PyAudioInterface(AudioInterface):
    def __init__(self):
        # Detect hardware sample rate
        p = pyaudio.PyAudio()
        info = p.get_default_output_device_info()
        self.hw_rate = int(info.get('defaultSampleRate', 48000))
        p.terminate()
        print(f"🔊 Hardware sample rate: {self.hw_rate} Hz")

        self.p = None
        self.in_stream = None
        self.out_stream = None
        self.output_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.resample_state = None
        self.ducking = False

    def start(self, input_callback):
        self.input_callback = input_callback
        self.p = pyaudio.PyAudio()

        self.in_stream = self.p.open(
            format=pyaudio.paInt16, channels=1, rate=ELEVENLABS_RATE,
            input=True, frames_per_buffer=4000,
            stream_callback=self._on_mic_data,
        )
        self.out_stream = self.p.open(
            format=pyaudio.paInt16, channels=1, rate=self.hw_rate, output=True,
        )
        threading.Thread(target=self._play_loop, daemon=True).start()

    def stop(self):
        if self.should_stop:
            self.should_stop.set()
        if self.output_thread:
            self.output_thread.join()
        if self.in_stream:
            self.in_stream.stop_stream()
            self.in_stream.close()
        if self.out_stream:
            self.out_stream.stop_stream()
            self.out_stream.close()
        if self.p:
            self.p.terminate()

    def output(self, audio: bytes):
        # Resample 16kHz → hardware rate
        if ELEVENLABS_RATE != self.hw_rate:
            audio, self.resample_state = audioop.ratecv(
                audio, 2, 1, ELEVENLABS_RATE, self.hw_rate, self.resample_state
            )
        self.output_queue.put(audio)

    def interrupt(self):
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break
        self.resample_state = None

    def _play_loop(self):
        while not self.stop_event.is_set():
            try:
                audio = self.output_queue.get(timeout=0.2)
                self.ducking = True
                self.out_stream.write(audio)
            except queue.Empty:
                self.ducking = False

    def _on_mic_data(self, in_data, frame_count, time_info, status):
        data = audioop.mul(in_data, 2, 0.1) if self.ducking else in_data
        self.input_callback(data)
        return (None, pyaudio.paContinue)
