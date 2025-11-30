"""Tool: get_weather"""

from tt.brain.handlers.tools_plug import tool


@tool(
    description="Return the weather for a city.",
    parameters={
        "city": {"type": "string", "description": "City name", "required": True}
    }
)
def get_weather(city: str):
    return {
        "city": city,
        "summary": "Sunny",
        "temp_c": 22,
        "temp_f": 72,
    }

