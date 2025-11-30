import signal

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ClientTools

from tt.config import ELEVENLABS_AGENT_ID, ELEVENLABS_API_KEY
from tt.utils.audio_interface import PyAudioInterface
import tt.brain.tools  # Register tools on import
from tt.brain.handlers.tools_plug import register_with_elevenlabs

elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)

client_tools = ClientTools()
#I'll register the tools with this function that goes through all the functions and makes them available...
register_with_elevenlabs(client_tools)



conversation = Conversation(
    # API client and agent ID.
    elevenlabs,
    ELEVENLABS_AGENT_ID,

    # Assume auth is required when API_KEY is set.
    requires_auth=bool(ELEVENLABS_API_KEY),

    # Use the custom audio interface (16 kHz input, 44.1 kHz output).
    audio_interface=PyAudioInterface(),

    # Simple callbacks that print the conversation to the console.
    callback_agent_response=lambda response: print(f"Agent: {response}"),
    callback_agent_response_correction=lambda original, corrected: print(f"Agent: {original} -> {corrected}"),
    callback_user_transcript=lambda transcript: print(f"User: {transcript}"),

    # Uncomment if you want to see latency measurements.
    callback_latency_measurement=lambda latency: print(f"Latency: {latency}ms"),
    client_tools=client_tools,
)

def play_audio():
    conversation.start_session()
    print("Session started.")
