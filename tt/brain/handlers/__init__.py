"""
Message handlers for OpenAI Realtime API events.

Each handler receives (conv, msg) where:
- conv: RealtimeConversation instance
- msg: The message dict from the API
"""

from tt.brain.handlers.audio import (
    on_audio_delta,
    on_audio_done,
)
from tt.brain.handlers.transcript import (
    on_ai_transcript_delta,
    on_ai_transcript_done,
    on_user_transcript_completed,
    on_user_transcript_failed,
)
from tt.brain.handlers.tool_calls import (
    on_function_call_args_delta,
    on_function_call_args_done,
)


# Handler registry: maps event type -> handler function
HANDLERS = {
    "response.audio.delta": on_audio_delta,
    "response.audio.done": on_audio_done,
    "response.audio_transcript.delta": on_ai_transcript_delta,
    "response.audio_transcript.done": on_ai_transcript_done,
    "conversation.item.input_audio_transcription.completed": on_user_transcript_completed,
    "conversation.item.input_audio_transcription.failed": on_user_transcript_failed,
    "response.function_call_arguments.delta": on_function_call_args_delta,
    "response.function_call_arguments.done": on_function_call_args_done,
}


def dispatch(conv, msg: dict) -> bool:
    """
    Dispatch a message to its handler.
    Returns True if handled, False otherwise.
    """
    handler = HANDLERS.get(msg.get("type"))
    if handler:
        handler(conv, msg)
        return True
    return False

