"""Handlers for audio streaming events."""

import base64


def on_audio_delta(conv, msg: dict):
    """Stream audio chunk to speaker."""
    conv.audio.push_tts(base64.b64decode(msg["delta"]))


def on_audio_done(conv, msg: dict):
    """Clear input buffer when model finishes speaking."""
    conv.sock.send({"type": "input_audio_buffer.clear"})

