"""
JARVIS NEXUS — Session Manager
Persists agent sessions as JSONL logs for auditability and replay.
Each task gets a session file with timestamped entries for every
message, tool call, tool result, and recovery event.
"""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SESSIONS_DIR, LOGS_DIR


class SessionManager:
    """Manages session state and persistence for agent tasks."""

    def __init__(self, task_description: str = ""):
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.task_description = task_description
        self.start_time = time.time()
        self.entries: List[Dict] = []
        self.tool_call_count = 0
        self.turn_count = 0

        # Session file
        self.session_file = SESSIONS_DIR / f"session_{self.session_id}.jsonl"
        self._write_entry({
            "type": "session_start",
            "task": task_description,
            "session_id": self.session_id,
        })

    def _write_entry(self, entry: Dict):
        """Append an entry to the session log."""
        entry["timestamp"] = time.time()
        entry["iso_time"] = datetime.now().isoformat()
        self.entries.append(entry)
        try:
            with open(self.session_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass  # Don't crash the agent if logging fails

    def log_user_message(self, content: str):
        self._write_entry({"type": "user_message", "content": content[:500]})

    def log_assistant_message(self, content: str):
        self.turn_count += 1
        self._write_entry({"type": "assistant_message", "turn": self.turn_count, "content": content[:1000]})

    def log_tool_call(self, tool_name: str, arguments: Dict):
        self.tool_call_count += 1
        self._write_entry({
            "type": "tool_call",
            "call_number": self.tool_call_count,
            "tool": tool_name,
            "arguments": {k: str(v)[:200] for k, v in arguments.items()},
        })

    def log_tool_result(self, tool_name: str, success: bool, output: str):
        self._write_entry({
            "type": "tool_result",
            "tool": tool_name,
            "success": success,
            "output": output[:500],
        })

    def log_recovery(self, strategy_name: str, instruction: str):
        self._write_entry({
            "type": "recovery_strategy",
            "strategy": strategy_name,
            "instruction": instruction[:300],
        })

    def log_completion(self, summary: str, stats: Dict):
        elapsed = time.time() - self.start_time
        self._write_entry({
            "type": "session_complete",
            "summary": summary,
            "elapsed_seconds": round(elapsed, 1),
            "tool_calls": self.tool_call_count,
            "turns": self.turn_count,
            "heal_stats": stats,
        })

    def get_session_summary(self) -> str:
        elapsed = time.time() - self.start_time
        return (
            f"Session: {self.session_id}\n"
            f"Task: {self.task_description[:100]}\n"
            f"Duration: {elapsed:.0f}s\n"
            f"Turns: {self.turn_count}\n"
            f"Tool Calls: {self.tool_call_count}\n"
            f"Log: {self.session_file}"
        )
