"""Shared utility functions."""

from __future__ import annotations

from datetime import timedelta


def format_timedelta(td: timedelta) -> str:
    """Format timedelta as HH:MM:SS string."""
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(abs(total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    sign = "-" if total_seconds < 0 else ""
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns default on zero denominator."""
    if denominator == 0:
        return default
    return numerator / denominator
