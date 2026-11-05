"""Configuration from environment variables (and an optional .env file).

Values are read when the functions are called, not at import time, so tests and scripts can
change the environment between runs.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from emporio.store_hours import to_local

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5"}
DEFAULT_EFFORT = "medium"  # Claude Haiku 5.5 default; low | medium | high | xhigh | max

# Thinking tokens count toward this cap (adaptive thinking is always on for Haiku 5.5), so
# it is a safety ceiling, not a reply-length target. Reply length is set by the prompt.
MAX_TOKENS = 16000

# Abuse limits, shared by the CLI and the API.
MAX_MESSAGE_LENGTH = 1000  # characters per customer message
RATE_LIMIT_MESSAGES = 10  # messages per session...
RATE_LIMIT_WINDOW_S = 60  # ...per rolling window (API only)


def llm_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def llm_model() -> str:
    provider = llm_provider()
    return (os.getenv("LLM_MODEL") or "").strip() or DEFAULT_MODELS.get(provider, "")


def llm_effort() -> str:
    return (os.getenv("LLM_EFFORT") or DEFAULT_EFFORT).strip().lower()


def agent_now() -> datetime | None:
    """The agent clock from AGENT_NOW (ISO datetime), or None for real time.

    A value without a UTC offset is read as store time (America/Campo_Grande).
    """
    raw = (os.getenv("AGENT_NOW") or "").strip()
    if not raw:
        return None
    return to_local(datetime.fromisoformat(raw))


def show_tools() -> bool:
    return (os.getenv("SHOW_TOOLS") or "").strip().lower() in {"1", "true", "yes"}
