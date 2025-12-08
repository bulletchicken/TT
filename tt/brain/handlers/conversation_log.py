"""Conversation history tracking and JSON persistence."""

import json
from datetime import datetime
from pathlib import Path

from tt.brain.hippocampus import memorize

DEFAULT_LOG_DIR = Path("conversation_logs")


class ConversationLog:
    """Tracks conversation history and saves to JSON."""

    def __init__(self, model: str, voice: str, log_dir: Path = DEFAULT_LOG_DIR):
        self.model = model
        self.voice = voice
        self.log_dir = log_dir
        self.messages: list[dict] = []
        self.session_start = datetime.now()

    def add(self, role: str, content: str = "", **extra):
        """Add a message to the conversation history."""
        entry = {"role": role, **extra}
        if content:
            entry["content"] = content
        self.messages.append(entry)

    def add_user(self, content: str):
        self.add("user", content)

    def add_assistant(self, content: str):
        self.add("assistant", content)

    def add_tool_call(self, tool_name: str, arguments: dict):
        self.add("tool_call", tool_name=tool_name, arguments=arguments)

    def add_tool_result(self, tool_name: str, output: dict):
        self.add("tool_result", tool_name=tool_name, output=output)

    def save(self):
        session_end = datetime.now()
        duration = session_end - self.session_start
        memorize(self.model, duration, self.messages, self.session_start, session_end)
