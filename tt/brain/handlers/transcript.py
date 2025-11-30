"""Handlers for transcript events (user and AI)."""


def on_ai_transcript_delta(conv, msg: dict):
    """Stream AI transcript to console."""
    rid = msg.get("response_id", "_default")
    delta = msg.get("delta", "")
    
    # Buffer the transcript
    conv.transcript_buffers[rid] = conv.transcript_buffers.get(rid, "") + delta
    
    # Print prefix once per response
    if rid not in conv.transcript_printed:
        print("\nAI: ", end="", flush=True)
        conv.transcript_printed.add(rid)
    
    print(delta, end="", flush=True)


def on_ai_transcript_done(conv, msg: dict):
    """Finalize AI transcript and save to history."""
    rid = msg.get("response_id", "_default")
    full_transcript = conv.transcript_buffers.pop(rid, "")
    conv.transcript_printed.discard(rid)
    print()
    
    if full_transcript:
        conv.log.add_assistant(full_transcript)


def on_user_transcript_completed(conv, msg: dict):
    """Handle completed user speech transcription."""
    transcript = msg.get("transcript", "")
    if transcript:
        print(f"\nUser: {transcript}")
        conv.log.add_user(transcript)


def on_user_transcript_failed(conv, msg: dict):
    """Handle failed user speech transcription."""
    error = msg.get("error", {})
    print(f"\n⚠️  Transcription failed: {error.get('message', 'Unknown error')}")

