"""Shared helpers for structured JSON returned by LLM calls."""

import re


def strip_fence(text: str) -> str:
    """Remove a surrounding ```json ... ``` fence, if present."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    return text
