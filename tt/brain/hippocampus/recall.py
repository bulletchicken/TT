from tt.brain.hippocampus.supabase_client import supabase
from tt.brain.hippocampus.utils.embed import embed


def recall(message):
    memories = supabase.rpc(
        "match_documents",
        {
            "query_embedding": embed(message),
            "match_threshold": 0.3,
            "match_count": 1,
        },
    ).execute()
    return memories.data[0]["summary"]


if __name__ == "__main__":
    print(recall("Sad"))
