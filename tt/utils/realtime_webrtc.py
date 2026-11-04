import asyncio
import fractions
import json
import threading

import av
import httpx
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

CALLS_URL = "https://api.openai.com/v1/realtime/calls"
MIC_POLL_INTERVAL = 0.01  # seconds between AudioIO mic queue checks
# The server paces audio in real time, so arrivals jitter (~100 ms bursts measured).
# Buffer this much at the start of each response so playback never underruns.
PLAYOUT_DELAY = 0.15  # seconds
# Keep forwarding audio this long after output_audio_buffer.stopped: the last
# frames are still in aiortc's jitter buffer when the event arrives.
PLAYOUT_TAIL = 0.3  # seconds


class _MicTrack(MediaStreamTrack):
    """Outbound audio track fed by AudioIO.read_mic_chunk() (mono s16 PCM)."""

    kind = "audio"

    def __init__(self, audio, rate):
        super().__init__()
        self.audio = audio
        self.rate = rate
        self.pts = 0

    async def recv(self):
        while True:
            if self.readyState != "live":
                raise MediaStreamError
            chunk = self.audio.read_mic_chunk()
            if chunk:
                break
            await asyncio.sleep(MIC_POLL_INTERVAL)

        samples = len(chunk) // 2
        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        plane = frame.planes[0]
        plane.update(chunk + bytes(plane.buffer_size - len(chunk)))
        frame.sample_rate = self.rate
        frame.time_base = fractions.Fraction(1, self.rate)
        frame.pts = self.pts
        self.pts += samples
        return frame


class RealtimeWebRTC:
    """
    WebRTC transport for the OpenAI Realtime API.

    Audio travels as Opus over media tracks (mic from AudioIO.read_mic_chunk,
    model speech to AudioIO.push_tts); JSON events travel over the
    "oai-events" data channel. aiortc runs on its own asyncio loop thread, so
    send() can be called from any thread and on_msg is called on that loop.
    """

    def __init__(self, api_key, session: dict, audio, rate, on_msg):
        self.api_key = api_key
        self.session = session  # Realtime session config, sent with the SDP offer
        self.audio = audio
        self.rate = rate
        self.on_msg = on_msg
        self.done_event = threading.Event()  # Set when the connection is fully closed
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._pc = None
        self._dc = None

        # Playback state (loop thread only). The server sends frames nonstop,
        # so only forward audio while output_audio_buffer says it is speaking.
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        self._speaking = False
        self._buffering = False
        self._held = bytearray()
        self._stop_at = None

    def connect(self, timeout=20):
        """Negotiate the call and block until the event channel is open."""
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._connect(), self._loop).result(timeout)

    async def _connect(self):
        pc = self._pc = RTCPeerConnection()
        pc.addTrack(_MicTrack(self.audio, self.rate))
        dc = self._dc = pc.createDataChannel("oai-events")
        opened = asyncio.Event()
        dc.on("open", opened.set)
        dc.on("message", self._on_message)
        dc.on("close", self.done_event.set)
        pc.on("track", lambda track: asyncio.ensure_future(self._play(track)))

        @pc.on("connectionstatechange")
        def _on_state():
            if pc.connectionState in ("failed", "closed"):
                self.done_event.set()

        await pc.setLocalDescription(await pc.createOffer())
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                CALLS_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={
                    "sdp": (None, pc.localDescription.sdp),
                    "session": (None, json.dumps(self.session)),
                },
            )
        if resp.status_code != 201:
            raise RuntimeError(f"Realtime call failed ({resp.status_code}): {resp.text}")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=resp.text, type="answer"))
        await opened.wait()

    def _on_message(self, raw):
        msg = json.loads(raw)
        self._track_speaking(msg.get("type"))
        try:
            self.on_msg(msg)
        except Exception as e:
            print("Handler error:", e)

    def _track_speaking(self, typ):
        if typ == "output_audio_buffer.started":
            self._speaking, self._buffering, self._stop_at = True, True, None
        elif typ == "output_audio_buffer.stopped":
            self._stop_at = self._loop.time() + PLAYOUT_TAIL
        elif typ == "output_audio_buffer.cleared":
            # Interrupted (user barged in): drop anything not yet played
            self._speaking, self._held = False, bytearray()

    async def _play(self, track):
        """Decode the model's audio (48 kHz Opus) to AudioIO's rate and queue it."""
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            pcm = b"".join(
                bytes(f.planes[0])[: f.samples * 2] for f in self._resampler.resample(frame)
            )
            if self._stop_at is not None and self._loop.time() >= self._stop_at:
                self._speaking, self._stop_at = False, None
                self._flush()
            if not self._speaking:
                continue
            if self._buffering:
                self._held += pcm
                if len(self._held) >= PLAYOUT_DELAY * self.rate * 2:
                    self._flush()
            else:
                self.audio.push_tts(pcm)

    def _flush(self):
        if self._held:
            self.audio.push_tts(bytes(self._held))
            self._held = bytearray()
        self._buffering = False

    def send(self, obj: dict):
        try:
            self._loop.call_soon_threadsafe(self._send, json.dumps(obj))
        except RuntimeError as e:  # loop already closed
            print("Send error:", e)

    def _send(self, raw):
        try:
            self._dc.send(raw)
        except Exception as e:
            print("Send error:", e)

    def close(self):
        # FUTURE FW integration: when the connection closes, the system should
        # transition to IDLE. At that point, send a serial command to the
        # firmware (e.g. "IDLE\n") to:
        #   - Cut PWM signal to servos so arms go free-moving (no holding torque)
        #   - Disable I2C communication to the MPU (accelerometer/gyro)
        #   - Disable I2C/analog reads from the heart rate sensor
        # This reduces power draw and wear while waiting for the next wake word.
        if not self._thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop).result(5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        if not self._thread.is_alive():
            self._loop.close()
        self.done_event.set()

    async def _close(self):
        if self._pc:
            await self._pc.close()
        # Each conversation gets a fresh loop; don't leak its executor thread
        await self._loop.shutdown_default_executor()
