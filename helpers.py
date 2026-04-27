"""Shared utility functions."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


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


# ── LLM JSON Parsing ────────────────────────────────────────────────────────


def safe_parse_llm_json(raw: str, default: Optional[dict] = None) -> dict:
    """
    Robustly parse JSON from an LLM response.
    Handles markdown code blocks, extra text before/after JSON, and common errors.
    Returns default (or empty dict) on failure.
    """
    if default is None:
        default = {}

    text = raw.strip()

    # Strip markdown code fences
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass

    # Try to find JSON array
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start != -1 and bracket_end != -1 and bracket_end > bracket_start:
        try:
            result = json.loads(text[bracket_start : bracket_end + 1])
            if isinstance(result, list):
                return {"items": result}
        except json.JSONDecodeError:
            pass

    logger.warning(f"Failed to parse LLM JSON response: {text[:300]}")
    return default


# ── Session Validation Utilities ────────────────────────────────────────────


def detect_session_overlaps(
    sessions: list[dict],
) -> list[dict]:
    """
    Detect overlapping sessions on the same date.
    sessions: list of dicts with keys 'date_start', 'time_start', 'time_end', 'duration_minutes'.
    Returns list of overlap descriptions.
    """
    from datetime import datetime

    overlaps: list[dict] = []

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for s in sessions:
        date = s.get("date_start", "")
        if date:
            by_date.setdefault(date, []).append(s)

    for date, day_sessions in by_date.items():
        # Parse start/end times
        parsed: list[tuple[int, int, dict]] = []
        for s in day_sessions:
            try:
                start_min = _time_str_to_minutes(s.get("time_start", ""))
                end_min = _time_str_to_minutes(s.get("time_end", ""))
                if end_min == 0 and start_min > 0:
                    # Estimate end from duration
                    end_min = start_min + round(float(s.get("duration_minutes", 0) or 0))
                if end_min <= start_min:
                    end_min = start_min + round(float(s.get("duration_minutes", 0) or 0))
                parsed.append((start_min, end_min, s))
            except (ValueError, TypeError):
                continue

        # Sort by start time and check overlaps
        parsed.sort(key=lambda x: x[0])
        for i in range(len(parsed) - 1):
            s1_start, s1_end, s1 = parsed[i]
            s2_start, s2_end, s2 = parsed[i + 1]
            if s2_start < s1_end:
                overlap_min = min(s1_end, s2_end) - s2_start
                overlaps.append({
                    "date": date,
                    "session_1": f"{s1.get('time_start', '')} - {s1.get('time_end', '')}",
                    "session_2": f"{s2.get('time_start', '')} - {s2.get('time_end', '')}",
                    "overlap_minutes": overlap_min,
                    "severity": "critical",
                })

    return overlaps


def _time_str_to_minutes(time_str: str) -> int:
    """Convert time string like '03:30:52 AM' or '15:30:52' to minutes since midnight."""
    time_str = time_str.strip().upper()
    if not time_str:
        return 0

    # Remove seconds if present, strip AM/PM
    is_pm = "PM" in time_str
    is_am = "AM" in time_str
    clean = time_str.replace("AM", "").replace("PM", "").strip()
    parts = clean.replace(":", " ").split()

    if len(parts) < 2:
        return 0

    hour = int(parts[0])
    minute = int(parts[1])

    if is_am and hour == 12:
        hour = 0
    elif is_pm and hour != 12:
        hour += 12

    return hour * 60 + minute


def detect_duplicate_sessions(
    sessions: list[dict],
) -> list[dict]:
    """
    Detect duplicate sessions by hashing (employee, date, time_start, duration).
    Returns list of duplicate descriptions.
    """
    seen: dict[str, int] = {}
    duplicates: list[dict] = []

    for s in sessions:
        key = (
            f"{s.get('employee', '')}"
            f"|{s.get('date_start', '')}"
            f"|{s.get('time_start', '')}"
            f"|{s.get('duration_minutes', 0):.0f}"
        )
        h = hashlib.md5(key.encode()).hexdigest()[:12]
        if h in seen:
            seen[h] += 1
            duplicates.append({
                "date": s.get("date_start", ""),
                "time_start": s.get("time_start", ""),
                "duration_minutes": s.get("duration_minutes", 0),
                "count": seen[h] + 1,
                "severity": "high",
            })
        else:
            seen[h] = 0

    return duplicates


def compute_date_overlap_pct(
    ts_start: str, ts_end: str, ss_start: str, ss_end: str
) -> float:
    """
    Compute the percentage of date overlap between two ranges.
    Returns 0.0-100.0.
    """
    from dateutil import parser as dateparser

    try:
        ts_s = dateparser.parse(ts_start)
        ts_e = dateparser.parse(ts_end) if ts_end else ts_s
        ss_s = dateparser.parse(ss_start)
        ss_e = dateparser.parse(ss_end) if ss_end else ss_s

        # Overlap
        overlap_start = max(ts_s, ss_s)
        overlap_end = min(ts_e, ss_e)
        if overlap_start > overlap_end:
            return 0.0

        overlap_days = (overlap_end - overlap_start).days + 1
        total_days = max(
            (max(ts_e, ss_e) - min(ts_s, ss_s)).days + 1,
            1,
        )
        return round(overlap_days / total_days * 100, 1)
    except (ValueError, TypeError):
        return 0.0
