from elevenlabs.conversational_ai.conversation import AudioInterface
from tt_audio_rs import ElevenLabsAudio


class RustAudioInterface(AudioInterface):
    """Drop-in for PyAudioInterface, backed by the Rust pipeline in tt_audio_rs."""

    def __init__(self):
        self.audio = ElevenLabsAudio()
        print(f"🔊 Hardware sample rate: {self.audio.hw_rate} Hz")

    def start(self, input_callback):
        self.audio.start(input_callback)

    def stop(self):
        self.audio.stop()

    def output(self, audio: bytes):
        self.audio.output(audio)

    def interrupt(self):
        self.audio.interrupt()
