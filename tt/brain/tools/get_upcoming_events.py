"""Tool: get_upcoming_events"""

from tt.brain.handlers.tools_plug import tool


@tool(description="Return upcoming calendar events for the day.")
def get_upcoming_events():
    return {
        "events": [
            {"title": "Standup", "time": "09:30", "location": "Zoom"},
            {"title": "Design review", "time": "11:00", "location": "Room 3B"},
            {"title": "1:1 with Jamie", "time": "15:00", "location": "Cafe patio"},
        ]
    }

