"""Handlers for audio streaming events."""


def on_audio_done(conv, msg: dict):
    """Clear input buffer when model finishes speaking."""
    conv.sock.send({"type": "input_audio_buffer.clear"})
