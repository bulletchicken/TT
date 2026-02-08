"""ElevenLabs Conversational AI controller."""

import time

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ClientTools

from tt.config import ELEVENLABS_AGENT_ID, ELEVENLABS_API_KEY
from tt.state_manager import State, StateManager
from tt.utils.audio_interface import PyAudioInterface
import tt.brain.tools  # Register tools on import
from tt.brain.handlers.tools_plug import register_with_elevenlabs


def _build_conversation():
    """Create a fresh ElevenLabs Conversation instance."""
    elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)

    client_tools = ClientTools()
    # Register all tools so they're available to the agent
    register_with_elevenlabs(client_tools)

    return Conversation(
        # API client and agent ID.
        elevenlabs,
        ELEVENLABS_AGENT_ID,

        # Assume auth is required when API_KEY is set.
        requires_auth=bool(ELEVENLABS_API_KEY),

        # Use the custom audio interface (16 kHz input, 44.1 kHz output).
        audio_interface=PyAudioInterface(),

        # Simple callbacks that print the conversation to the console.
        callback_agent_response=lambda response: print(f"Agent: {response}"),
        callback_agent_response_correction=lambda original, corrected: print(
            f"Agent: {original} -> {corrected}"
        ),
        callback_user_transcript=lambda transcript: print(f"User: {transcript}"),

        # Uncomment if you want to see latency measurements.
        callback_latency_measurement=lambda latency: print(f"Latency: {latency}ms"),
        client_tools=client_tools,
    )


def play_audio(state_mgr: StateManager | None = None):
    """
    Start an ElevenLabs conversation session and block until it ends.

    When the session finishes (session_id goes None), triggers a
    WINDING_DOWN transition via the state manager if one was provided.

    FUTURE FW integration: when session_id goes None the conversation is
    over. This is the trigger to send an IDLE command to firmware via
    serial to:
      - Cut servo PWM so arms hang free (no holding torque, saves power)
      - Disable MPU I2C comms (motion/gesture sensing not needed in idle)
      - Power down heart rate sensor (analog/I2C reads stopped)
    Then SW should loop back to wake word detection (Porcupine).
    """
    conversation = _build_conversation()
    conversation.start_session()
    print("Session started. Press Ctrl+C to stop.")

    try:
        # Keep the process alive so tool callbacks can run; otherwise the
        # interpreter can shut down before ElevenLabs finishes using the tool.
        while conversation.session_id is not None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopping session...")

    # Session is over — trigger wind-down
    if state_mgr:
        state_mgr.transition(State.WINDING_DOWN)
