"""
Timesheet statistical analysis module.
Computes metrics, detects anomalies, and identifies patterns.
Rule-based analysis FIRST, then LLM interprets the findings.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import timedelta
from typing import Optional

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
from helpers import (
    detect_duplicate_sessions,
    detect_session_overlaps,
    format_timedelta,
    safe_divide,
    safe_parse_llm_json,
)
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


def _parse_time_minutes(time_str: str) -> int:
    """Extract total minutes since midnight from time string."""
    time_str = time_str.strip().upper()
    parts = time_str.replace(":", " ").replace("AM", "").replace("PM", "").split()
    if len(parts) < 2:
        return 0
    hour = int(parts[0])
    minute = int(parts[1])
    is_pm = "PM" in time_str
    is_am = "AM" in time_str
    if is_am and hour == 12:
        hour = 0
    elif is_pm and hour != 12:
        hour += 12
    return hour * 60 + minute


def _detect_anomalies(sessions: list[WorkSession]) -> list[SessionAnomaly]:
    """Detect anomalies in work sessions using rule-based checks."""
    settings = get_settings()
    anomalies: list[SessionAnomaly] = []

    for s in sessions:
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

        # High idle time ratio — use configured threshold (higher for devs)
        idle_ratio = safe_divide(s.idle_minutes, s.duration_minutes)
        if s.duration_minutes > 30 and idle_ratio > settings.idle_ratio_threshold:
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


def _detect_advanced_anomalies(sessions: list[WorkSession]) -> list[SessionAnomaly]:
    """Detect advanced fraud patterns not covered by basic anomaly checks."""
    anomalies: list[SessionAnomaly] = []
    if len(sessions) < 3:
        return anomalies

    # ── 1. Overlapping sessions (impossible for one person) ──────────────
    session_dicts = [
        {
            "date_start": s.date_start,
            "time_start": s.time_start,
            "time_end": s.time_end,
            "duration_minutes": s.duration_minutes,
        }
        for s in sessions
    ]
    overlaps = detect_session_overlaps(session_dicts)
    for o in overlaps:
        anomalies.append(
            SessionAnomaly(
                session_date=o["date"],
                session_time=o["session_1"],
                anomaly_type="overlapping_sessions",
                description=(
                    f"Sessions overlap: {o['session_1']} and {o['session_2']} "
                    f"({o['overlap_minutes']:.0f} min overlap)"
                ),
                severity="high",
            )
        )

    # ── 2. Duplicate sessions ────────────────────────────────────────────
    dup_dicts = [
        {
            "employee": s.employee,
            "date_start": s.date_start,
            "time_start": s.time_start,
            "duration_minutes": s.duration_minutes,
        }
        for s in sessions
    ]
    duplicates = detect_duplicate_sessions(dup_dicts)
    for d in duplicates:
        anomalies.append(
            SessionAnomaly(
                session_date=d["date"],
                session_time=d["time_start"],
                anomaly_type="duplicate_session",
                description=(
                    f"Duplicate session detected: {d['duration_minutes']:.0f} min "
                    f"appearing {d['count']} times"
                ),
                severity="high",
            )
        )

    # ── 3. Suspiciously round durations ──────────────────────────────────
    round_count = 0
    for s in sessions:
        dur_min = s.duration_minutes
        if dur_min > 0 and dur_min % 30 == 0:  # Exactly 30, 60, 90, 120, etc.
            round_count += 1
    round_pct = safe_divide(round_count, len(sessions)) * 100
    if round_pct > 70 and len(sessions) >= 5:
        anomalies.append(
            SessionAnomaly(
                session_date=sessions[0].date_start,
                session_time="",
                anomaly_type="round_durations",
                description=(
                    f"{round_pct:.0f}% of sessions ({round_count}/{len(sessions)}) "
                    f"have exactly round durations (multiples of 30 min) — "
                    f"statistically unlikely for real work"
                ),
                severity="high",
            )
        )

    # ── 4. Mouse jiggler / activity simulator detection ──────────────────
    # Suspiciously stable activity across many sessions
    # For web devs, threshold is tighter (std_dev < 2.0) since devs in flow
    # can have genuinely consistent patterns
    settings = get_settings()
    if len(sessions) >= 10:
        activities = [s.activity_pct for s in sessions if s.duration_minutes > 10]
        if len(activities) >= 10:
            mean_act = sum(activities) / len(activities)
            variance = sum((a - mean_act) ** 2 for a in activities) / len(activities)
            std_dev = math.sqrt(variance)
            if std_dev < settings.activity_stability_threshold and mean_act > 50:
                anomalies.append(
                    SessionAnomaly(
                        session_date=sessions[0].date_start,
                        session_time="",
                        anomaly_type="activity_too_stable",
                        description=(
                            f"Activity suspiciously stable: mean={mean_act:.1f}%, "
                            f"std_dev={std_dev:.1f}% across {len(activities)} sessions. "
                            f"Even focused developers show more variance. Possible mouse jiggler."
                        ),
                        severity="high",
                    )
                )

    # ── 5. Suspiciously regular start times ──────────────────────────────
    if len(sessions) >= 7:
        start_minutes = []
        for s in sessions:
            if s.time_start:
                m = _parse_time_minutes(s.time_start)
                if m > 0:
                    start_minutes.append(m)
        if len(start_minutes) >= 7:
            mean_start = sum(start_minutes) / len(start_minutes)
            var_start = sum((m - mean_start) ** 2 for m in start_minutes) / len(start_minutes)
            std_start = math.sqrt(var_start)
            if std_start < settings.start_time_regularity_threshold:
                anomalies.append(
                    SessionAnomaly(
                        session_date=sessions[0].date_start,
                        session_time="",
                        anomaly_type="regular_start_times",
                        description=(
                            f"Start times suspiciously regular: std_dev={std_start:.1f} min "
                            f"across {len(start_minutes)} sessions. "
                            f"Possible automated scheduling."
                        ),
                        severity="medium",
                    )
                )

    # ── 6. Cross-day excessive hours ─────────────────────────────────────
    daily_hours: dict[str, float] = defaultdict(float)
    for s in sessions:
        daily_hours[s.date_start] += s.duration_minutes / 60
    for date, hours in daily_hours.items():
        if hours > settings.excessive_daily_hours:
            anomalies.append(
                SessionAnomaly(
                    session_date=date,
                    session_time="",
                    anomaly_type="excessive_daily_hours",
                    description=(
                        f"Total billed hours on {date}: {hours:.1f}h "
                        f"(across all projects). Over {settings.excessive_daily_hours:.0f}h in a single day is suspicious."
                    ),
                    severity="high",
                )
            )

    # ── 7. Inter-session gap regularity ──────────────────────────────────
    if len(sessions) >= 5:
        # Sort sessions chronologically
        sorted_sessions = sorted(sessions, key=lambda s: (s.date_start, s.time_start))
        gaps: list[float] = []
        for i in range(len(sorted_sessions) - 1):
            s1 = sorted_sessions[i]
            s2 = sorted_sessions[i + 1]
            if s1.date_start == s2.date_start:
                end_min = _parse_time_minutes(s1.time_end) if s1.time_end else (
                    _parse_time_minutes(s1.time_start) + s1.duration_minutes
                )
                start_min = _parse_time_minutes(s2.time_start)
                gap = start_min - end_min
                if 0 < gap < 480:  # Reasonable gap (0 to 8 hours)
                    gaps.append(gap)

        if len(gaps) >= 5:
            mean_gap = sum(gaps) / len(gaps)
            var_gap = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
            std_gap = math.sqrt(var_gap)
            if std_gap < settings.gap_regularity_threshold and mean_gap > 0:
                anomalies.append(
                    SessionAnomaly(
                        session_date=sorted_sessions[0].date_start,
                        session_time="",
                        anomaly_type="regular_gaps",
                        description=(
                            f"Inter-session gaps suspiciously regular: "
                            f"mean={mean_gap:.0f} min, std_dev={std_gap:.1f} min "
                            f"across {len(gaps)} gaps. Possible automation."
                        ),
                        severity="medium",
                    )
                )

    # ── 8. Identical repeated session patterns ───────────────────────────
    # Same (task, duration, activity) on consecutive days
    if len(sessions) >= 5:
        sorted_by_date = sorted(sessions, key=lambda s: s.date_start)
        streak = 1
        for i in range(1, len(sorted_by_date)):
            s_prev = sorted_by_date[i - 1]
            s_curr = sorted_by_date[i]
            same_pattern = (
                s_prev.task == s_curr.task
                and abs(s_prev.duration_minutes - s_curr.duration_minutes) < 2
                and abs(s_prev.activity_pct - s_curr.activity_pct) < 2
            )
            if same_pattern:
                streak += 1
            else:
                if streak >= 5:
                    anomalies.append(
                        SessionAnomaly(
                            session_date=sorted_by_date[i - streak].date_start,
                            session_time="",
                            anomaly_type="identical_pattern_streak",
                            description=(
                                f"{streak} consecutive sessions with identical pattern: "
                                f"task='{s_prev.task}', duration~{s_prev.duration_minutes:.0f}min, "
                                f"activity~{s_prev.activity_pct:.0f}%. "
                                f"Strongly suggests template/fabricated entries."
                            ),
                            severity="high",
                        )
                    )
                streak = 1
        # Check final streak
        if streak >= 5:
            s = sorted_by_date[-1]
            anomalies.append(
                SessionAnomaly(
                    session_date=sorted_by_date[-streak].date_start,
                    session_time="",
                    anomaly_type="identical_pattern_streak",
                    description=(
                        f"{streak} consecutive sessions with identical pattern: "
                        f"task='{s.task}', duration~{s.duration_minutes:.0f}min, "
                        f"activity~{s.activity_pct:.0f}%."
                    ),
                    severity="high",
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
    """Compute aggregate statistics using pandas (replaces DuckDB for lower memory)."""
    import pandas as pd
    import numpy as np

    records = []
    for s in sessions:
        records.append({
            "date": s.date_start,
            "duration_min": s.duration_minutes,
            "active_min": s.active_minutes,
            "idle_min": s.idle_minutes,
            "activity_pct": s.activity_pct,
            "start_hour": _parse_time_hour(s.time_start),
            "start_minutes": _parse_time_minutes(s.time_start),
        })

    df = pd.DataFrame(records)

    # Basic stats
    total_sessions = len(df)
    total_hours = df["duration_min"].sum() / 60.0
    active_hours = df["active_min"].sum() / 60.0
    avg_duration_min = df["duration_min"].mean()
    avg_activity = df["activity_pct"].mean()
    min_activity = df["activity_pct"].min()
    max_activity = df["activity_pct"].max()
    low_activity_sessions = int((df["activity_pct"] < 50).sum())
    very_short = int((df["duration_min"] < 5).sum())
    very_long = int((df["duration_min"] > 360).sum())
    activity_std_dev = df["activity_pct"].std(ddof=1) if total_sessions > 1 else 0.0
    duration_std_dev = df["duration_min"].std(ddof=1) if total_sessions > 1 else 0.0
    p25_duration = df["duration_min"].quantile(0.25) if total_sessions > 0 else 0.0
    p75_duration = df["duration_min"].quantile(0.75) if total_sessions > 0 else 0.0
    start_time_std = df["start_minutes"].std(ddof=1) if total_sessions > 1 else 0.0

    # Round duration check
    if total_sessions > 0:
        round_count = ((df["duration_min"] > 0) & (df["duration_min"] % 30 == 0)).sum()
        round_pct = float(round_count * 100.0 / total_sessions)
    else:
        round_pct = 0.0

    # Daily hours stats
    daily_hours = df.groupby("date")["duration_min"].sum() / 60.0
    max_daily_hours = float(daily_hours.max()) if len(daily_hours) > 0 else 0.0
    days_over_12h = int((daily_hours > 12).sum())

    # Gap analysis (between same-day sessions)
    avg_gap_min = 0.0
    gap_std_min = 0.0
    if total_sessions > 1:
        gaps = []
        for date, group in df.groupby("date"):
            if len(group) < 2:
                continue
            sorted_g = group.sort_values("start_minutes")
            start_mins = sorted_g["start_minutes"].values
            dur_mins = sorted_g["duration_min"].values
            for i in range(len(start_mins) - 1):
                gap = start_mins[i + 1] - (start_mins[i] + dur_mins[i])
                if 0 < gap < 480:
                    gaps.append(gap)
        if gaps:
            avg_gap_min = float(np.mean(gaps))
            gap_std_min = float(np.std(gaps, ddof=1)) if len(gaps) > 1 else 0.0

    # Replace NaN with 0
    def _safe(v):
        return 0.0 if (v != v) else float(v)  # NaN != NaN

    return {
        "total_sessions": total_sessions,
        "total_hours": _safe(total_hours),
        "active_hours": _safe(active_hours),
        "avg_duration_min": _safe(avg_duration_min),
        "avg_activity": _safe(avg_activity),
        "min_activity": _safe(min_activity),
        "max_activity": _safe(max_activity),
        "low_activity_sessions": low_activity_sessions,
        "very_short": very_short,
        "very_long": very_long,
        "activity_std_dev": _safe(activity_std_dev),
        "duration_std_dev": _safe(duration_std_dev),
        "p25_duration": _safe(p25_duration),
        "p75_duration": _safe(p75_duration),
        "start_time_std": _safe(start_time_std),
        "round_duration_pct": round_pct,
        "max_daily_hours": max_daily_hours,
        "days_over_12h": days_over_12h,
        "avg_gap_min": avg_gap_min,
        "gap_std_min": gap_std_min,
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
        # Enhanced stats
        "activity_std_dev": result.activity_std_dev,
        "duration_std_dev": result.duration_std_dev,
        "p25_duration_min": result.p25_duration_min,
        "p75_duration_min": result.p75_duration_min,
        "round_duration_pct": result.round_duration_pct,
        "duplicate_sessions": result.duplicate_session_count,
        "overlapping_sessions": result.overlapping_session_count,
        "max_daily_hours": result.max_daily_hours,
        "days_over_12h": result.days_over_12h,
        "start_time_std_min": result.start_time_std_min,
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
        del response

        data = safe_parse_llm_json(text)
        if not data:
            return ""

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
    Compute suspicious time windows and total suspicious hours.
    """
    from datetime import timedelta

    windows: list[SuspiciousWindow] = []
    total_suspicious_seconds = 0.0
    total_session_seconds = 0.0

    for s in sessions:
        total_session_seconds += s.duration.total_seconds()

        # Sessions with high idle ratio and duration > 30 min are suspicious
        idle_ratio = safe_divide(s.idle_minutes, s.duration_minutes)
        settings = get_settings()
        if s.duration_minutes > 30 and idle_ratio > settings.idle_ratio_threshold:
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
    advanced_anomalies = _detect_advanced_anomalies(timesheet.sessions)
    anomalies.extend(advanced_anomalies)

    daily = _compute_daily_breakdown(timesheet.sessions)

    overall_activity = safe_divide(
        timesheet.total_active.total_seconds(),
        timesheet.total_duration.total_seconds(),
    ) * 100

    # Compute suspicious hours
    suspicious_windows, suspicious_hours_str, suspicious_pct = _compute_suspicious_windows(
        timesheet.sessions, anomalies, screenshot_analysis,
    )

    # Count overlaps and duplicates from anomalies
    overlap_count = sum(1 for a in anomalies if a.anomaly_type == "overlapping_sessions")
    duplicate_count = sum(1 for a in anomalies if a.anomaly_type == "duplicate_session")

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
        # Enhanced stats
        activity_std_dev=round(stats["activity_std_dev"], 2),
        duration_std_dev=round(stats["duration_std_dev"], 2),
        p25_duration_min=round(stats["p25_duration"], 1),
        p75_duration_min=round(stats["p75_duration"], 1),
        round_duration_pct=round(stats["round_duration_pct"], 1),
        duplicate_session_count=duplicate_count,
        overlapping_session_count=overlap_count,
        max_daily_hours=round(stats["max_daily_hours"], 1),
        days_over_12h=stats["days_over_12h"],
        start_time_std_min=round(stats["start_time_std"], 1),
        avg_gap_between_sessions_min=round(stats["avg_gap_min"], 1),
        gap_std_dev_min=round(stats["gap_std_min"], 1),
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
