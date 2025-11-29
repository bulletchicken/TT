import time
import base64
import threading
import audioop

import pyaudio

from ..config import OPENAI_API_KEY
from ..utils.audio_interface_openai import AudioIO
from ..utils.realtime_socket import RealtimeSocket

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

CHUNK_SIZE = 1024          # audio frames per buffer
RATE = 24000               # sample rate
FORMAT = pyaudio.paInt16   # 16-bit PCM
SILENCE_SECONDS = 0.8      # how long of silence ends an utterance

MODEL = "gpt-realtime-mini"
VOICE = "ballad"
INSTRUCTIONS = "You are a helpful, friendly assistant."


# -------------------------------------------------------------------
# High-level controller
# -------------------------------------------------------------------

class RealtimeConversation:
    def __init__(self, api_key):
        self.audio = AudioIO(chunk_size=CHUNK_SIZE, rate=RATE, format=FORMAT)
        self.sock = RealtimeSocket(
            api_key,
            f"wss://api.openai.com/v1/realtime?model={MODEL}",
            self.on_msg,
        )
        self.last_mic_time = None
        self.sent_commit_for_segment = True  # nothing in progress yet
        self.running = True

    def start(self):
        self.sock.connect()
        self.audio.start()
        threading.Thread(target=self._mic_loop, daemon=True).start()
        print("🎧 Realtime conversation started. Speak into your mic... (Ctrl+C to quit)")

    def _mic_loop(self):
        while self.running:
            # Send mic audio to OpenAI
            chunk = self.audio.read_mic_chunk()
            now = time.time()

            if chunk:
                enc = base64.b64encode(chunk).decode("utf-8")
                self.sock.send({"type": "input_audio_buffer.append", "audio": enc})
                self.last_mic_time = now
                self.sent_commit_for_segment = False

            # If we've been silent long enough, commit and request a response
            if (
                self.last_mic_time is not None
                and not self.sent_commit_for_segment
                and (now - self.last_mic_time) > SILENCE_SECONDS
            ):
                self.sock.send({"type": "input_audio_buffer.commit"})
                self.sock.send({
                    "type": "response.create",
                    "response": {"modalities": ["audio", "text"]},
                })
                self.sent_commit_for_segment = True

            time.sleep(0.01)

    def on_msg(self, msg: dict):
        typ = msg.get("type")

        if typ == "session.created":
            # Configure voice + instructions once when the session starts
            self.sock.send({
                "type": "session.update",
                "session": {
                    "voice": VOICE,
                    "instructions": INSTRUCTIONS,
                },
            })

        elif typ == "response.audio.delta":
            # Audio streaming from model
            data = base64.b64decode(msg["delta"])
            self.audio.push_tts(data)

        elif typ == "response.audio.done":
            # Clear input buffer when model is done talking
            self.sock.send({"type": "input_audio_buffer.clear"})

        elif typ == "response.output_text.delta":
            # Optional: print out text as it streams
            print(msg.get("delta", ""), end="", flush=True)

        elif typ == "response.output_text.done":
            print()  # newline after a text response is finished

        elif typ == "error":
            print("Server error:", msg.get("error"))

    def stop(self):
        self.running = False
        self.audio.stop()
        self.sock.close()


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------

def main():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not found in environment (.env)")

    conv = RealtimeConversation(OPENAI_API_KEY)
    conv.start()
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        conv.stop()


if __name__ == "__main__":
    main()

