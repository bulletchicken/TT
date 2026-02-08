"""OpenAI Realtime API conversation controller."""

import base64
import threading
import time

import pyaudio

import tt.brain.tools  # Register tools on import
from tt.brain.handlers import find_and_run
from tt.brain.handlers.conversation_log import ConversationLog
from tt.brain.handlers.tools_plug import get_tool_definitions
from tt.brain.temporal_lobe.prompts.ted_personality import INSTRUCTIONS
from tt.config import OPENAI_API_KEY
from tt.state_manager import State, StateManager
from tt.utils.audio_interface_openai import AudioIO
from tt.utils.realtime_socket import RealtimeSocket

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

CHUNK_SIZE = 1024
RATE = 24000
FORMAT = pyaudio.paInt16

MODEL = "gpt-realtime-mini"
VOICE = "ballad"
VAD_THRESHOLD = 0.5
# other model to try: whisper-1 but is 2x the cost but still $0.006 / minute
INPUT_AUDIO_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


# -------------------------------------------------------------------
# Controller
# -------------------------------------------------------------------


class RealtimeConversation:
    def __init__(self, api_key: str, state_mgr: StateManager | None = None):
        self.audio = AudioIO(chunk_size=CHUNK_SIZE, rate=RATE, format=FORMAT)
        self.sock = RealtimeSocket(
            api_key,
            f"wss://api.openai.com/v1/realtime?model={MODEL}",
            self._on_msg,
        )
        self.running = True
        self.log = ConversationLog(MODEL, VOICE)
        self.state_mgr = state_mgr

        # Buffers for streaming data
        self.transcript_buffers = {}  # AI transcript chunks
        self.transcript_printed = set()
        self.tool_arg_buffers = {}  # Tool call argument chunks
        # Track completed user turns so tools can wait for transcripts
        self.user_turn = 0
        self.user_turn_condition = threading.Condition()

    def start(self):
        self.sock.connect()
        self.audio.start()
        threading.Thread(target=self._mic_loop, daemon=True).start()
        print(
            "🎧 Realtime conversation started. Speak into your mic... (Ctrl+C to quit)"
        )

    def _mic_loop(self):
        """Stream mic audio to OpenAI. Server VAD handles speech detection."""
        while self.running:
            chunk = self.audio.read_mic_chunk()
            if chunk:
                self.sock.send(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("utf-8"),
                    }
                )
            time.sleep(0.01)

    def _on_msg(self, msg: dict):
        """Route incoming messages to handlers."""
        typ = msg.get("type")

        # Session setup
        if typ == "session.created":
            self.sock.send(
                {
                    "type": "session.update",
                    "session": {
                        "voice": VOICE,
                        "instructions": INSTRUCTIONS,
                        "tools": get_tool_definitions(),
                        "tool_choice": "auto",
                        "input_audio_transcription": {
                            "model": INPUT_AUDIO_TRANSCRIPTION_MODEL
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": VAD_THRESHOLD,
                        },
                    },
                }
            )
            return

        # Error handling
        if typ == "error":
            print(f"\n❌ Server error: {msg.get('error')}")
            return

        # Find and run the right handler function for what to do based on this resopnse message type
        find_and_run(self, msg)

    def wait_until_done(self):
        """Block until the websocket connection closes."""
        self.sock.done_event.wait()

    def stop(self):
        # FUTURE FW integration: after the conversation ends and the websocket
        # is torn down, the state manager should notify firmware to enter idle:
        #   - Disable servo PWM (cut power so arms are free-moving, no torque)
        #   - Stop polling MPU over I2C (no motion/gesture data needed in idle)
        #   - Power down heart rate sensor (not needed until next conversation)
        # The SW side then re-enters wake word listening (Porcupine).
        self.running = False
        self.audio.stop()
        self.sock.close()
        self.log.save()

        if self.state_mgr:
            self.state_mgr.transition(State.WINDING_DOWN)


# -------------------------------------------------------------------
# Entry point (standalone, without state manager)
# -------------------------------------------------------------------


def main():
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
