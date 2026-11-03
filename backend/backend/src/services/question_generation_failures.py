"""Safe public messages for failures in asynchronous question generation."""

from __future__ import annotations


def safe_generation_failure_message(error: object) -> str:
    """Classify an internal exception without exposing providers, keys, or SQL."""
    text = str(error or "").lower()
    if any(marker in text for marker in ("429", "rate limit")):
        return "The AI provider is busy. Your draft is safe; retry in a moment."
    if any(marker in text for marker in ("timeout", "timed out", "504")):
        return "The AI provider took too long to respond. Your draft is safe; retry."
    if any(marker in text for marker in ("502", "503", "service unavailable", "connection")):
        return "The AI provider is temporarily unavailable. Your draft is safe; retry shortly."
    if any(marker in text for marker in ("not configured", "api key", "authentication", "unauthorized")):
        return "AI generation is temporarily unavailable. Your draft is safe; try again later."
    if any(marker in text for marker in ("short question bank", "no items returned")):
        return "The AI returned an incomplete question bank. Your draft is safe; retry."
    if any(marker in text for marker in ("stale", "draft was changed", "cancelled")):
        return "The draft changed while questions were generating. Review it, then retry."
    return "Question generation could not be completed. Your draft is safe; retry."
