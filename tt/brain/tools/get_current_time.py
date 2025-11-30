"""Tool: get_current_time"""

import datetime
from tt.brain.handlers.tools_plug import tool


@tool(description="Return the current time in ISO 8601 format.")
def get_current_time():
    return {"time": datetime.datetime.now(datetime.timezone.utc).isoformat()}

