"""Tool: get_memories"""

from tt.brain.handlers.tools_plug import tool
from tt.brain.hippocampus.recall import recall


@tool(
    description="Use this frequently to stay grounded in past conversations. When in doubt, call this tool to retrieve context and improve your answers.",
)
def get_memories(message: str):
    return {"from memory": recall(message)}
