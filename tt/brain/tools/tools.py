import datetime


# Tools defined for the OpenAI Realtime API session.
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "get_current_time",
        "description": "Return the current time in ISO 8601 format.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "type": "function",
        "name": "get_user_profile",
        "description": "Return mock user profile info.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_upcoming_events",
        "description": "Return mock upcoming calendar events for the day.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_reminders",
        "description": "Return mock reminders with due times.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_weather",
        "description": "Return mock weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
            },
            "required": ["city"],
        },
    },
]


def get_current_time():
    return {"time": datetime.datetime.now(datetime.timezone.utc).isoformat()}


# Mock data the tools can return today.
MOCK_USER_PROFILE = {
    "name": "Alex Rivera",
    "location": "San Francisco",
    "role": "Product Manager",
}

MOCK_EVENTS = [
    {"title": "Standup", "time": "09:30", "location": "Zoom"},
    {"title": "Design review", "time": "11:00", "location": "Room 3B"},
    {"title": "1:1 with Jamie", "time": "15:00", "location": "Cafe patio"},
]

MOCK_REMINDERS = [
    {"title": "Pick up dry cleaning", "due": "today 6pm"},
    {"title": "Order groceries", "due": "today 8pm"},
]


def get_user_profile():
    return MOCK_USER_PROFILE


def get_upcoming_events():
    return {"events": MOCK_EVENTS}


def get_reminders():
    return {"reminders": MOCK_REMINDERS}


def get_weather(city: str):
    return {
        "city": city,
        "summary": "Sunny",
        "temp_c": 22,
        "temp_f": 72,
    }


TOOL_FUNCTIONS = {
    "get_current_time": lambda **_: get_current_time(),
    "get_user_profile": lambda **_: get_user_profile(),
    "get_upcoming_events": lambda **_: get_upcoming_events(),
    "get_reminders": lambda **_: get_reminders(),
    "get_weather": lambda **kwargs: get_weather(**kwargs),
}


def run_tool(tool_name: str, args: dict):
    """Dispatch a tool call from the realtime model."""
    fn = TOOL_FUNCTIONS.get(tool_name)
    if not fn:
        raise ValueError(f"Unknown tool: {tool_name}")
    return fn(**(args or {}))
