from tt.brain.hippocampus.supabase_client import supabase


def store(model, duration, summary, messages, embeddings, session_start, session_end):
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
