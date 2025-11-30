"""Conversation history tracking and JSON persistence."""

import json
from datetime import datetime
from pathlib import Path

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
        """Save conversation to a JSON file."""
        if not self.messages:
            print("No conversation to save.")
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)
        filename = self.log_dir / f"conversation_{self.session_start:%Y%m%d_%H%M%S}.json"
        
        session_end = datetime.now()
        duration = session_end - self.session_start
        duration_seconds = int(duration.total_seconds())

        with open(filename, "w", encoding="utf-8") as f:
            json.dump({
                "session_start": self.session_start.isoformat(),
                "session_end": session_end.isoformat(),
                "duration_seconds": duration_seconds,
                "model": self.model,
                "voice": self.voice,
                "messages": self.messages,
            }, f, indent=2, ensure_ascii=False)

        print(f"💾 Conversation saved to: {filename}")

