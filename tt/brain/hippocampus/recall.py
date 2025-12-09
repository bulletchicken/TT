from tt.brain.hippocampus.supabase_client import supabase
from tt.brain.hippocampus.utils.embed import embed


def recall(message):
    response = supabase.rpc(
        "find_highlights",
        {
            # Supabase RPC expects a single embedding vector, not a list
            "query_embedding": embed(message)[0],
            "match_threshold": 0.3,
            "match_count": 2,
        },
    ).execute()
    # Supabase returns a list of highlight rows (may be empty)
    return response.data or []


if __name__ == "__main__":
    print(recall("Sad"))
