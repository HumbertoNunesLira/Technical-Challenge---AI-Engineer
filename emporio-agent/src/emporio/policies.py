"""Policy lookup: the store manual split into one markdown file per topic.

The files in data/policies/ keep the manual's original wording. Where the manual contradicts
itself, a clearly marked "Editorial note" at the top of the file says which rule prevails.
"""

from __future__ import annotations

from pathlib import Path

POLICIES_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

# Also the enum of the get_policy tool, so the model can only ask for an existing topic.
TOPICS = (
    "store_info",
    "hours",
    "payment",
    "returns",
    "shipping",
    "promotions",
    "warranty",
    "privacy",
    "scope_and_tone",
)


def get_policy(topic: str) -> dict:
    """Return {"topic", "text"} for a known topic, or an error listing the valid topics."""
    normalized = topic.strip().lower()
    if normalized not in TOPICS:
        return {
            "error": "unknown_topic",
            "message": f"Unknown topic '{topic}'. Valid topics: {', '.join(TOPICS)}.",
        }
    text = (POLICIES_DIR / f"{normalized}.md").read_text(encoding="utf-8")
    return {"topic": normalized, "text": text}
