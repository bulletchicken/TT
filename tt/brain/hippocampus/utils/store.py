from tt.brain.hippocampus.supabase_client import supabase


def store(
    model,
    duration,
    summary,
    messages,
    embeddings,
    session_start,
    session_end,
    highlights_and_embeddings,
):
    supabase.table("memories").insert(
        {
            "model": model,
            "duration": duration,
            "summary": summary,
            "messages": messages,
            "embedding": embeddings,
            "session_start": session_start,
            "session_end": session_end,
        }
    ).execute()

    for highlight, embedding in highlights_and_embeddings:
        supabase.table("highlights").insert(
            {"embedding": embedding, "context": highlight, "date": session_start}
        ).execute()
