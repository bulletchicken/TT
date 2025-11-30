"""Tool: get_reminders"""

from tt.brain.handlers.tools_plug import tool


@tool(description="Return reminders with due times.")
def get_reminders():
    return {
        "reminders": [
            {"title": "Pick up dry cleaning", "due": "today 6pm"},
            {"title": "Order groceries", "due": "today 8pm"},
        ]
    }

