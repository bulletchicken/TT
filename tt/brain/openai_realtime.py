import json
import time
import base64
import threading

import pyaudio
from tt.config import OPENAI_API_KEY
from tt.utils.audio_interface_openai import AudioIO
from tt.utils.realtime_socket import RealtimeSocket
from tt.brain.tools.tools import TOOL_DEFINITIONS, run_tool

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

# VAD (Voice Activity Detection) Configuration
VAD_THRESHOLD = 0.5
VAD_CREATE_RESPONSE = True
VAD_INTERRUPT_RESPONSE = True


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
        self.tool_argument_buffers = {}  # incremental tool call args keyed by call_id
        self.user_text_buffers = {}      # transcribed user text per response_id
        self.response_text_buffers = {}  # model text per response_id
        self._user_prefix_printed = set()
        self._ai_prefix_printed = set()
        self.response_audio_transcripts = {}  # transcripts for audio output

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
            # Configure voice + instructions + VAD once when the session starts
            self.sock.send({
                "type": "session.update",
                "session": {
                    "voice": VOICE,
                    "instructions": INSTRUCTIONS,
                    "tools": TOOL_DEFINITIONS,
                    "tool_choice": "auto",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": VAD_THRESHOLD,
                        "create_response": VAD_CREATE_RESPONSE,
                        "interrupt_response": VAD_INTERRUPT_RESPONSE,
                    },
                },
            })

        elif typ == "response.audio.delta":
            # Audio streaming from model
            data = base64.b64decode(msg["delta"])
            self.audio.push_tts(data)

        elif typ == "response.audio.done":
            # Clear input buffer when model is done talking
            self.sock.send({"type": "input_audio_buffer.clear"})

        elif typ == "response.input_text.delta":
            # ASR transcript of what the user said (from the model side)
            rid = msg.get("response_id") or "_default"
            delta = msg.get("delta", "")
            self.user_text_buffers[rid] = self.user_text_buffers.get(rid, "") + delta
            if rid not in self._user_prefix_printed:
                print("\nUser: ", end="", flush=True)
                self._user_prefix_printed.add(rid)
            print(delta, end="", flush=True)

        elif typ == "response.input_text.done":
            rid = msg.get("response_id") or "_default"
            full = self.user_text_buffers.pop(rid, "")
            self._user_prefix_printed.discard(rid)
            if full:
                print()  # newline after user transcript completes

        elif typ == "response.output_text.delta":
            # Text the model is sending back
            rid = msg.get("response_id") or "_default"
            delta = msg.get("delta", "")
            self.response_text_buffers[rid] = self.response_text_buffers.get(rid, "") + delta
            if rid not in self._ai_prefix_printed:
                print("\nAI: ", end="", flush=True)
                self._ai_prefix_printed.add(rid)
            print(delta, end="", flush=True)

        elif typ == "response.output_text.done":
            rid = msg.get("response_id") or "_default"
            full = self.response_text_buffers.pop(rid, "")
            self._ai_prefix_printed.discard(rid)
            if full:
                print()  # newline after model text completes

        elif typ == "response.audio_transcript.delta":
            # Transcript of the audio the model is speaking
            rid = msg.get("response_id") or "_default"
            delta = msg.get("delta", "")
            self.response_audio_transcripts[rid] = self.response_audio_transcripts.get(rid, "") + delta
            if rid not in self._ai_prefix_printed:
                print("\nAI: ", end="", flush=True)
                self._ai_prefix_printed.add(rid)
            print(delta, end="", flush=True)

        elif typ == "response.audio_transcript.done":
            rid = msg.get("response_id") or "_default"
            self.response_audio_transcripts.pop(rid, None)
            self._ai_prefix_printed.discard(rid)
            print()  # newline after audio transcript completes

        elif typ == "response.function_call_arguments.delta":
            # Tool call args stream in over multiple messages; buffer them by call_id
            call_id = msg["call_id"]
            delta = msg.get("delta", "")
            self.tool_argument_buffers[call_id] = self.tool_argument_buffers.get(call_id, "") + delta

        elif typ == "response.function_call_arguments.done":
            call_id = msg["call_id"]
            tool_name = msg["name"]
            args_json = self.tool_argument_buffers.pop(call_id, "") + msg.get("arguments", "")
            try:
                parsed_args = json.loads(args_json or "{}")
            except json.JSONDecodeError:
                parsed_args = {}

            print(f"\n🛠️  Model requested tool: {tool_name} with args: {parsed_args}")

            try:
                tool_output = run_tool(tool_name, parsed_args)
            except Exception as e:
                tool_output = {"error": str(e)}

            # Send tool result back to the model so it can continue the response.
            # Realtime expects a conversation item with type function_call_output.
            payload = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(tool_output),
                },
            }

            self.sock.send(payload)
            # Ask the model to continue now that it has the tool result
            self.sock.send({
                "type": "response.create",
                "response": {"modalities": ["audio", "text"]},
            })

        elif typ == "error":
            print("Server error:", msg.get("error"))

        else:
            # Ignore routine events to reduce noise
            ignored = {
                "response.content_part.done",
                "response.output_item.done",
                "response.done",
                "rate_limits.updated",
                "input_audio_buffer.cleared",
            }
            if typ not in ignored:
                print(f"\n[debug] Unhandled message type: {typ} -> {msg}")

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
