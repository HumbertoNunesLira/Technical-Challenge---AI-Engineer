"""Conversation persistence: one JSONL file per session in data/sessions/.

Each line is one message: {"role", "content", "timestamp"}, and assistant lines also log the
turn's tool calls and results, token usage and stop reason. Only role and content go back to
the model (see agent.py); the rest is an audit log for debugging and transcripts.

Think of the session id as the customer's WhatsApp number: the same id resumes the same
conversation after a restart.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from emporio import config
from emporio.agent import Conversation, TurnResult
from emporio.tools import ToolContext

SESSIONS_DIR = config.PROJECT_ROOT / "data" / "sessions"
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class InvalidSessionId(ValueError):
    pass


class SessionStore:
    def __init__(self, directory: Path = SESSIONS_DIR):
        self.directory = directory

    def path(self, session_id: str) -> Path:
        # The id becomes a file name: allow only safe characters (no "../", no separators).
        if not SESSION_ID.fullmatch(session_id):
            raise InvalidSessionId(
                "Session id must be 1-64 letters, digits, '-' or '_'."
            )
        return self.directory / f"{session_id}.jsonl"

    def records(self, session_id: str) -> list[dict]:
        path = self.path(session_id)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as file:
            return [json.loads(line) for line in file if line.strip()]

    def load(self, session_id: str, tool_context: ToolContext | None = None) -> Conversation:
        """Rebuild a conversation: text history plus the verification failures so far."""
        records = self.records(session_id)
        context = tool_context or ToolContext(session_id=session_id, now=config.agent_now())
        # Restore the lockout counter, so restarting the chat does not reset it.
        context.failed_verifications = sum(
            1
            for record in records
            for call in record.get("tool_calls", [])
            if call["name"] == "get_order_status"
            and call["result"].get("error") == "verification_failed"
        )
        return Conversation(
            session_id=session_id,
            history=[{"role": r["role"], "content": r["content"]} for r in records],
            tool_context=context,
        )

    def append_turn(self, session_id: str, user_message: str, result: TurnResult) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        lines = [
            {"role": "user", "content": user_message, "timestamp": timestamp},
            {
                "role": "assistant",
                "content": result.reply,
                "timestamp": timestamp,
                "tool_calls": result.tool_calls,
                "usage": result.usage,
                "stop_reason": result.stop_reason,
                "latency_s": round(result.latency_s, 2),
            },
        ]
        path = self.path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            for line in lines:
                file.write(json.dumps(line, ensure_ascii=False) + "\n")

    def reset(self, session_id: str) -> None:
        self.path(session_id).unlink(missing_ok=True)
