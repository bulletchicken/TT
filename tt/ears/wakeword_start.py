"""
Wake word detection using Porcupine.

Provides a blocking `wait_for_wakeword()` that can be called repeatedly
in the main lifecycle loop. Each call creates a fresh Porcupine instance
and audio stream, listens until the wake word is detected, then cleans up
and returns.
"""

import struct

import pvporcupine
import pyaudio

from tt.config import PORCUPINE_ACCESS_KEY


def wait_for_wakeword():
    """
    Block until the wake word is detected, then return.

    Creates and tears down Porcupine + PyAudio each cycle so resources
    are fully released while the conversation is active.

    FUTURE FW integration: while this function is blocking, firmware
    should be in IDLE mode:
      - Servo PWM disabled (arms free-moving, no torque, lower power draw)
      - MPU I2C disabled (no motion/gesture reads needed while idle)
      - Heart rate sensor powered down (no reads until conversation starts)
    Once this function returns (wake word detected), the caller should
    transition to ACTIVE so firmware powers everything back on.
    """
    if not PORCUPINE_ACCESS_KEY:
        raise RuntimeError("PORCUPINE_ACCESS_KEY not found in environment (.env)")

    porcupine = pvporcupine.create(
        access_key=PORCUPINE_ACCESS_KEY,
        keyword_paths=["tt/brain/models/wake_model_hey_ted.ppn"],
    )

    paud = pyaudio.PyAudio()
    audio_stream = paud.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
    )

    print("Listening for wakeword...")

    try:
        while True:
            pcm = audio_stream.read(porcupine.frame_length)
            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)
            if porcupine.process(pcm) >= 0:
                print("Wake word detected!")
                return
    finally:
        audio_stream.close()
        paud.terminate()
        porcupine.delete()
