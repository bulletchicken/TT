import audioop
import queue
import threading

import pyaudio
from elevenlabs.conversational_ai.conversation import AudioInterface


class PyAudioInterface(AudioInterface):
    def __init__(self):
        self.pyaudio = pyaudio
        self.input_sample_rate = 16000
        self.output_sample_rate = 44100
        self.input_frames_per_buffer = 4000  # 250 ms @ 16 kHz
        self.output_frames_per_buffer = 2756  # ~62.5 ms @ 44.1 kHz

        self.input_callback = None
        self.output_queue = None
        self.should_stop = None
        self.output_thread = None
        self.p = None
        self.in_stream = None
        self.out_stream = None
        self.ducking = False

    def start(self, input_callback):
        self.input_callback = input_callback
        self.output_queue = queue.Queue()
        self.should_stop = threading.Event()
        self.output_thread = threading.Thread(target=self._output_thread, daemon=True)

        self.p = self.pyaudio.PyAudio()
        self.in_stream = self.p.open(
            format=self.pyaudio.paInt16,
            channels=1,
            rate=self.input_sample_rate,
            input=True,
            stream_callback=self._in_callback,
            frames_per_buffer=self.input_frames_per_buffer,
            start=True,
        )
        self.out_stream = self.p.open(
            format=self.pyaudio.paInt16,
            channels=1,
            rate=self.output_sample_rate,
            output=True,
            frames_per_buffer=self.output_frames_per_buffer,
            start=True,
        )

        self.output_thread.start()

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
        if self.output_queue:
            self.output_queue.put(audio)

    def interrupt(self):
        if not self.output_queue:
            return
        try:
            while True:
                self.output_queue.get_nowait()
        except queue.Empty:
            pass

    def _output_thread(self):
        if not self.output_queue or not self.out_stream or not self.should_stop:
            return
        while not self.should_stop.is_set():
            try:
                audio = self.output_queue.get(timeout=0.25)
                self.ducking = True
                self.out_stream.write(audio)
            except queue.Empty:
                self.ducking = False
                continue
            
    def _in_callback(self, in_data, frame_count, time_info, status):
        data = in_data
        if self.ducking:
            try:
                data = audioop.mul(in_data, 2, 0.1)  # -20 dB while TTS is playing
            except Exception:
                data = in_data
        if self.input_callback:
            self.input_callback(data)
        return (None, self.pyaudio.paContinue)

