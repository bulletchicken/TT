"""OpenAI Realtime API conversation controller."""

import time
import base64
import threading

import pyaudio
from tt.config import OPENAI_API_KEY
from tt.utils.audio_interface_openai import AudioIO
from tt.utils.realtime_socket import RealtimeSocket
from tt.brain.tools.tools import TOOL_DEFINITIONS
from tt.brain.conversation_log import ConversationLog
from tt.brain.handlers import dispatch

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

CHUNK_SIZE = 1024
RATE = 24000
FORMAT = pyaudio.paInt16

MODEL = "gpt-realtime-mini"
VOICE = "ballad"
INSTRUCTIONS = "You are a helpful, friendly assistant."
VAD_THRESHOLD = 0.5
INPUT_AUDIO_TRANSCRIPTION_MODEL = "whisper-1"


# -------------------------------------------------------------------
# Controller
# -------------------------------------------------------------------

class RealtimeConversation:
    def __init__(self, api_key: str):
        self.audio = AudioIO(chunk_size=CHUNK_SIZE, rate=RATE, format=FORMAT)
        self.sock = RealtimeSocket(
            api_key,
            f"wss://api.openai.com/v1/realtime?model={MODEL}",
            self._on_msg,
        )
        self.running = True
        self.log = ConversationLog(MODEL, VOICE)
        
        # Buffers for streaming data
        self.transcript_buffers = {}   # AI transcript chunks
        self.transcript_printed = set()
        self.tool_arg_buffers = {}     # Tool call argument chunks

    def start(self):
        self.sock.connect()
        self.audio.start()
        threading.Thread(target=self._mic_loop, daemon=True).start()
        print("🎧 Realtime conversation started. Speak into your mic... (Ctrl+C to quit)")

    def _mic_loop(self):
        """Stream mic audio to OpenAI. Server VAD handles speech detection."""
        while self.running:
            chunk = self.audio.read_mic_chunk()
            if chunk:
                self.sock.send({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("utf-8"),
                })
            time.sleep(0.01)

    def _on_msg(self, msg: dict):
        """Route incoming messages to handlers."""
        typ = msg.get("type")

        # Session setup
        if typ == "session.created":
            self.sock.send({
                "type": "session.update",
                "session": {
                    "voice": VOICE,
                    "instructions": INSTRUCTIONS,
                    "tools": TOOL_DEFINITIONS,
                    "tool_choice": "auto",
                    "input_audio_transcription": {"model": INPUT_AUDIO_TRANSCRIPTION_MODEL},
                    "turn_detection": {"type": "server_vad", "threshold": VAD_THRESHOLD},
                },
            })
            return

        # Error handling
        if typ == "error":
            print(f"\n❌ Server error: {msg.get('error')}")
            return

        # Dispatch to registered handlers
        dispatch(self, msg)

    def stop(self):
        self.running = False
        self.audio.stop()
        self.sock.close()
        self.log.save()


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
    
