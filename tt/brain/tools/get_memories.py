"""Tool: get_memories"""

from datetime import datetime, timezone

from tt.brain.handlers.tools_plug import tool
from tt.brain.hippocampus.recall import recall
from tt.brain.tools.utils.recency import describe_recency


@tool(
    description=(
        "Use this frequently to stay grounded in past conversations. "
        "When in doubt, call this tool to retrieve context and improve your answers."
    ),
)
def get_memories(message: str):
    retrieved_data = recall(message) or []
    now = datetime.now(timezone.utc)

    memories = []
    for entry in retrieved_data:
        date_str = entry.get("date")
        context = entry.get("context", "")
        how_long_ago = describe_recency(date_str, now) if date_str else "unknown"
        memories.append({"context": context, "when": how_long_ago})

    return {"memories": memories}


if __name__ == "__main__":
    print(get_memories("What do I do to organize"))
