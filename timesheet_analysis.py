"""
Timesheet statistical analysis module.
Computes metrics, detects anomalies, and identifies patterns.
Rule-based analysis FIRST, then LLM interprets the findings.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Optional

import duckdb
import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from models import (
    SessionAnomaly,
    SuspiciousWindow,
    TimesheetAnalysisResult,
    TimesheetData,
    WorkSession,
)
from config import get_llm, get_settings
from helpers import format_timedelta, safe_divide
from prompts import TIMESHEET_REASONING_PROMPT

logger = logging.getLogger(__name__)


def _parse_time_hour(time_str: str) -> int:
    """Extract hour (0-23) from time string like '03:30:52 AM'."""
    time_str = time_str.strip().upper()
    parts = time_str.replace(":", " ").replace("AM", "").replace("PM", "").split()
    if len(parts) < 1:
        return 0
    hour = int(parts[0])
    is_pm = "PM" in time_str
    is_am = "AM" in time_str
    if is_am and hour == 12:
        hour = 0
    elif is_pm and hour != 12:
        hour += 12
    return hour


def _detect_anomalies(sessions: list[WorkSession]) -> list[SessionAnomaly]:
    """Detect anomalies in work sessions using rule-based checks."""
    settings = get_settings()
    anomalies: list[SessionAnomaly] = []

    for s in sessions:
        start_hour = _parse_time_hour(s.time_start)

        # Very short session
        if s.duration_minutes < settings.very_short_session_min:
            anomalies.append(
                SessionAnomaly(
                    session_date=s.date_start,
                    session_time=s.time_start,
                    anomaly_type="very_short_session",
                    description=(
                        f"Session lasted only {s.duration_minutes:.1f} min "
                        f"(threshold: {settings.very_short_session_min} min)"
                    ),
                    severity="low",
                )
            )

        # Very long session
        if s.duration_minutes > settings.very_long_session_min:
            anomalies.append(
                SessionAnomaly(
                    session_date=s.date_start,
                    session_time=s.time_start,
                    anomaly_type="very_long_session",
                    description=(
                        f"Session lasted {s.duration_minutes:.0f} min "
                        f"({s.duration_minutes / 60:.1f} hrs)"
                    ),
                    severity="medium",
                )
            )

        # Low activity
        if s.activity_pct < settings.low_activity_threshold:
            anomalies.append(
                SessionAnomaly(
                    session_date=s.date_start,
                    session_time=s.time_start,
                    anomaly_type="low_activity",
                    description=(
                        f"Activity at {s.activity_pct:.0f}% "
                        f"(threshold: {settings.low_activity_threshold}%)"
                    ),
                    severity="medium",
                )
            )

        # High idle time ratio
        idle_ratio = safe_divide(s.idle_minutes, s.duration_minutes)
        if s.duration_minutes > 30 and idle_ratio > 0.5:
            anomalies.append(
                SessionAnomaly(
                    session_date=s.date_start,
                    session_time=s.time_start,
                    anomaly_type="high_idle_ratio",
                    description=(
                        f"Idle {idle_ratio:.0%} of session time "
                        f"({s.idle_minutes:.0f} min idle out of {s.duration_minutes:.0f} min)"
                    ),
                    severity="medium",
                )
            )

    return anomalies


def _compute_daily_breakdown(sessions: list[WorkSession]) -> dict[str, float]:
    """Compute total hours worked per day."""
    daily: dict[str, float] = defaultdict(float)
    for s in sessions:
        daily[s.date_start] += s.duration_minutes / 60
    return dict(sorted(daily.items()))


def _compute_duckdb_stats(sessions: list[WorkSession]) -> dict:
    """Use DuckDB for efficient aggregate computation."""
    records = []
    for s in sessions:
        records.append({
            "date": s.date_start,
            "duration_min": s.duration_minutes,
            "active_min": s.active_minutes,
            "idle_min": s.idle_minutes,
            "activity_pct": s.activity_pct,
            "start_hour": _parse_time_hour(s.time_start),
        })

    df = pd.DataFrame(records)
    con = duckdb.connect()
    con.register("sessions", df)

    stats = con.execute("""
        SELECT
            COUNT(*) as total_sessions,
            SUM(duration_min) / 60.0 as total_hours,
            SUM(active_min) / 60.0 as active_hours,
            AVG(duration_min) as avg_duration_min,
            AVG(activity_pct) as avg_activity,
            MIN(activity_pct) as min_activity,
            MAX(activity_pct) as max_activity,
            SUM(CASE WHEN activity_pct < 50 THEN 1 ELSE 0 END) as low_activity_sessions,
            SUM(CASE WHEN duration_min < 5 THEN 1 ELSE 0 END) as very_short,
            SUM(CASE WHEN duration_min > 360 THEN 1 ELSE 0 END) as very_long
        FROM sessions
    """).fetchone()

    con.close()

    return {
        "total_sessions": int(stats[0]),
        "total_hours": float(stats[1]),
        "active_hours": float(stats[2]),
        "avg_duration_min": float(stats[3]),
        "avg_activity": float(stats[4]),
        "min_activity": float(stats[5]),
        "max_activity": float(stats[6]),
        "low_activity_sessions": int(stats[7]),
        "very_short": int(stats[8]),
        "very_long": int(stats[9]),
    }


def _llm_timesheet_reasoning(
    result: TimesheetAnalysisResult,
    sessions: list[WorkSession],
) -> str:
    """
    Use LLM to interpret the statistical findings.
    Returns the reasoning string to attach to the result.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return ""

    llm = get_llm(temperature=0.0, max_tokens=1500)

    # Build a rich data payload for the LLM
    session_details = []
    for s in sessions:
        session_details.append({
            "date": s.date_start,
            "start": s.time_start,
            "end": s.time_end,
            "duration_min": round(s.duration_minutes, 1),
            "active_min": round(s.active_minutes, 1),
            "idle_min": round(s.idle_minutes, 1),
            "activity_pct": s.activity_pct,
            "project": s.project,
        })

    anomaly_details = []
    for a in result.anomalies:
        anomaly_details.append({
            "date": a.session_date,
            "time": a.session_time,
            "type": a.anomaly_type,
            "description": a.description,
            "severity": a.severity,
        })

    metrics = {
        "employee": sessions[0].employee if sessions else "Unknown",
        "project": sessions[0].project if sessions else "Unknown",
        "total_sessions": result.total_sessions,
        "total_duration_hours": result.total_duration_hours,
        "total_active_hours": result.total_active_hours,
        "overall_activity_pct": result.overall_activity_pct,
        "avg_session_duration_min": result.avg_session_duration_min,
        "avg_activity_pct": result.avg_activity_pct,
        "min_activity_pct": result.min_activity_pct,
        "max_activity_pct": result.max_activity_pct,
        "sessions_below_50_pct_activity": result.sessions_below_50_pct,
        "very_short_sessions": result.very_short_sessions,
        "very_long_sessions": result.very_long_sessions,
        "daily_breakdown_hours": result.daily_breakdown,
        "anomalies": anomaly_details,
        "all_sessions": session_details,
    }

    prompt = TIMESHEET_REASONING_PROMPT.format(
        timesheet_metrics=json.dumps(metrics, indent=2)
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You are a work-pattern analyst. Respond with valid JSON only."),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        del response  # Free full response object
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        data = json.loads(text)
        parts = []
        assessment = data.get("overall_assessment", "")
        if assessment:
            parts.append(assessment)
        suspicious = data.get("suspicious_indicators", [])
        if suspicious:
            parts.append("SUSPICIOUS INDICATORS: " + "; ".join(suspicious))
        return " ".join(parts) if parts else ""

    except Exception as e:
        logger.error(f"LLM timesheet reasoning failed: {e}")
        return ""


def _compute_suspicious_windows(
    sessions: list[WorkSession],
    anomalies: list[SessionAnomaly],
    screenshot_analysis=None,
) -> tuple[list[SuspiciousWindow], str, float]:
    """
    FIX 5: Compute suspicious time windows and total suspicious hours.

    Suspicious windows come from:
    - Sessions with very low activity (high idle ratio)
    - Very long sessions flagged as anomalies
    - Time gaps covered by repeated frames (from screenshot analysis)
    - Tab-switching loop periods

    Returns: (suspicious_windows, suspicious_hours_total_str, suspicious_pct)
    """
    from datetime import timedelta

    windows: list[SuspiciousWindow] = []
    total_suspicious_seconds = 0.0
    total_session_seconds = 0.0

    for s in sessions:
        total_session_seconds += s.duration.total_seconds()

        # Sessions with >50% idle time and duration > 30 min are suspicious
        idle_ratio = safe_divide(s.idle_minutes, s.duration_minutes)
        if s.duration_minutes > 30 and idle_ratio > 0.5:
            suspicious_seconds = s.idle_minutes * 60
            total_suspicious_seconds += suspicious_seconds
            idle_td = timedelta(seconds=suspicious_seconds)
            windows.append(SuspiciousWindow(
                start=f"{s.date_start} {s.time_start}",
                end=f"{s.date_end} {s.time_end}",
                duration=format_timedelta(idle_td),
                duration_seconds=suspicious_seconds,
                reason=f"High idle ratio ({idle_ratio:.0%}) — {s.idle_minutes:.0f} min idle in {s.duration_minutes:.0f} min session",
                session_date=s.date_start,
            ))

        # Very long sessions (>6h) — the entire excess beyond 6h is suspicious
        settings = get_settings()
        if s.duration_minutes > settings.very_long_session_min:
            excess_min = s.duration_minutes - settings.very_long_session_min
            excess_sec = excess_min * 60
            total_suspicious_seconds += excess_sec
            excess_td = timedelta(seconds=excess_sec)
            windows.append(SuspiciousWindow(
                start=f"{s.date_start} {s.time_start}",
                end=f"{s.date_end} {s.time_end}",
                duration=format_timedelta(excess_td),
                duration_seconds=excess_sec,
                reason=f"Very long session ({s.duration_minutes:.0f} min / {s.duration_minutes/60:.1f}h) — excess time beyond {settings.very_long_session_min/60:.0f}h",
                session_date=s.date_start,
            ))

    # Add windows from repeated frames if screenshot analysis provided
    if screenshot_analysis and hasattr(screenshot_analysis, "repeated_frames"):
        for rf in screenshot_analysis.repeated_frames:
            gap_sec = rf.time_gap_minutes * 60
            total_suspicious_seconds += gap_sec
            gap_td = timedelta(seconds=gap_sec)
            windows.append(SuspiciousWindow(
                start=rf.first_occurrence,
                end=rf.repeat_occurrence,
                duration=format_timedelta(gap_td),
                duration_seconds=gap_sec,
                reason=f"Repeated identical frame ({rf.similarity_score:.0%} similar) — {rf.time_gap_minutes:.0f} min gap",
                session_date=rf.first_occurrence[:10],
            ))

    # Compute totals
    suspicious_td = timedelta(seconds=total_suspicious_seconds)
    suspicious_hours_str = format_timedelta(suspicious_td)
    suspicious_pct = safe_divide(total_suspicious_seconds, total_session_seconds) * 100

    return windows, suspicious_hours_str, round(suspicious_pct, 1)


def analyze_timesheet(timesheet: TimesheetData, screenshot_analysis=None) -> TimesheetAnalysisResult:
    """
    Perform complete statistical analysis of timesheet data.
    Returns structured metrics, detected anomalies, and LLM reasoning.
    screenshot_analysis: optional ScreenshotAnalysisResult for cross-referencing suspicious windows.
    """
    if not timesheet.sessions:
        return TimesheetAnalysisResult(
            total_sessions=0,
            total_duration_hours=0,
            total_active_hours=0,
            overall_activity_pct=0,
            avg_session_duration_min=0,
            avg_activity_pct=0,
            min_activity_pct=0,
            max_activity_pct=0,
            sessions_below_50_pct=0,
            very_short_sessions=0,
            very_long_sessions=0,
            reasoning="No sessions to analyze.",
        )

    stats = _compute_duckdb_stats(timesheet.sessions)
    anomalies = _detect_anomalies(timesheet.sessions)
    daily = _compute_daily_breakdown(timesheet.sessions)

    overall_activity = safe_divide(
        timesheet.total_active.total_seconds(),
        timesheet.total_duration.total_seconds(),
    ) * 100

    # FIX 5: Compute suspicious hours
    suspicious_windows, suspicious_hours_str, suspicious_pct = _compute_suspicious_windows(
        timesheet.sessions, anomalies, screenshot_analysis,
    )

    result = TimesheetAnalysisResult(
        total_sessions=stats["total_sessions"],
        total_duration_hours=round(stats["total_hours"], 2),
        total_active_hours=round(stats["active_hours"], 2),
        overall_activity_pct=round(overall_activity, 1),
        avg_session_duration_min=round(stats["avg_duration_min"], 1),
        avg_activity_pct=round(stats["avg_activity"], 1),
        min_activity_pct=round(stats["min_activity"], 1),
        max_activity_pct=round(stats["max_activity"], 1),
        sessions_below_50_pct=stats["low_activity_sessions"],
        very_short_sessions=stats["very_short"],
        very_long_sessions=stats["very_long"],
        anomalies=anomalies,
        daily_breakdown=daily,
        suspicious_hours_total=suspicious_hours_str,
        suspicious_pct=suspicious_pct,
        suspicious_windows=suspicious_windows,
    )

    # LLM reasoning on top of statistical analysis
    reasoning = _llm_timesheet_reasoning(result, timesheet.sessions)
    if reasoning:
        result.reasoning = reasoning

    logger.info(
        f"Timesheet analysis complete: {result.total_sessions} sessions, "
        f"{result.total_duration_hours:.1f}h total, "
        f"{len(anomalies)} anomalies detected, "
        f"suspicious hours: {suspicious_hours_str} ({suspicious_pct:.1f}%)"
    )

    return result
