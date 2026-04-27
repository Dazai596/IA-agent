"""
Temporal cross-reference analysis module.

Bridges the gap between aggregate-level comparisons and session-level evidence:
  - Maps each timesheet work session to the screenshots taken during it.
  - Detects contradictions at the session level (e.g., 95% activity billed from 2-4pm
    but every screenshot in that window shows YouTube).
  - Detects sessions with zero screenshot coverage during billed hours.
  - Detects sessions where screenshot evidence contradicts claimed activity level.
  - Runs sequential pattern analysis via LLM to detect suspicious clusters.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from models import (
    CrossAnalysisResult,
    ScreenshotCategory,
    ScreenshotClassification,
    SessionScreenshotMatch,
    TemporalGap,
    TimesheetAnalysisResult,
)

logger = logging.getLogger(__name__)


# ── Time parsing helpers ─────────────────────────────────────────────────────

def _parse_session_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """
    Parse a session date + time string into a datetime.
    Handles both 12-hour (03:30:52 AM) and 24-hour (15:30:52) formats.
    """
    if not date_str or not time_str:
        return None
    try:
        from dateutil import parser as dp
        return dp.parse(f"{date_str} {time_str}")
    except Exception:
        pass
    # Manual fallback for HiveDesk format
    try:
        ts = time_str.strip().upper()
        is_pm = "PM" in ts
        is_am = "AM" in ts
        clean = ts.replace("AM", "").replace("PM", "").strip()
        parts = clean.replace(":", " ").split()
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
        if is_am and hour == 12:
            hour = 0
        elif is_pm and hour != 12:
            hour += 12
        return datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=hour, minute=minute, second=second
        )
    except Exception:
        return None


def _parse_classification_datetime(timestamp: str) -> Optional[datetime]:
    """Parse screenshot classification timestamp (format: 'YYYY-MM-DD HH:MM:SS')."""
    try:
        return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            from dateutil import parser as dp
            return dp.parse(timestamp)
        except Exception:
            return None


# ── Core mapping ─────────────────────────────────────────────────────────────

def build_session_screenshot_map(
    timesheet_analysis: TimesheetAnalysisResult,
    classifications: list[ScreenshotClassification],
    raw_sessions: list[dict],
) -> list[SessionScreenshotMatch]:
    """
    For each timesheet work session (from raw session data), find all screenshots
    whose timestamp falls within that session's start–end window.

    raw_sessions: list of dicts with keys date_start, time_start, time_end,
                  duration_minutes, activity_pct, (from AuditState input_sessions).
    """
    if not raw_sessions or not classifications:
        return []

    # Pre-parse screenshot timestamps once
    parsed_classifications: list[tuple[datetime, ScreenshotClassification]] = []
    for c in classifications:
        dt = _parse_classification_datetime(c.timestamp)
        if dt:
            parsed_classifications.append((dt, c))

    if not parsed_classifications:
        return []

    matches: list[SessionScreenshotMatch] = []

    for session in raw_sessions:
        date_str = session.get("date", "") or session.get("date_start", "")
        start_str = session.get("time_start", "")
        end_str = session.get("time_end", "")
        duration_h = float(session.get("duration_minutes", 0) or 0) / 60.0
        activity_pct = float(str(session.get("activity_pct", "0")).replace("%", "") or 0)

        session_start = _parse_session_datetime(date_str, start_str)
        session_end = _parse_session_datetime(date_str, end_str)
        if not session_start:
            continue

        # Estimate end from duration if not available
        if not session_end or session_end <= session_start:
            dur_min = float(session.get("duration_minutes", 0) or 0)
            if dur_min > 0:
                session_end = session_start + timedelta(minutes=dur_min)
            else:
                continue

        # Find screenshots in this session window
        in_session: list[ScreenshotClassification] = []
        for (ss_dt, ss_class) in parsed_classifications:
            if session_start <= ss_dt <= session_end:
                in_session.append(ss_class)

        work_c = sum(1 for c in in_session if c.category == ScreenshotCategory.WORK)
        non_work_c = sum(1 for c in in_session if c.category == ScreenshotCategory.NON_WORK)
        idle_c = sum(1 for c in in_session if c.category == ScreenshotCategory.IDLE)
        uncertain_c = sum(1 for c in in_session if c.category == ScreenshotCategory.UNCERTAIN)
        total = len(in_session) or 1

        work_pct = round(work_c / total * 100, 1)
        non_work_pct = round(non_work_c / total * 100, 1)
        idle_pct = round(idle_c / total * 100, 1)

        # Detect contradiction:
        # High activity claimed + non_work/idle majority in screenshots = contradiction
        contradiction = False
        contradiction_desc = ""
        if in_session:
            if non_work_pct >= 50 and activity_pct >= 60:
                contradiction = True
                contradiction_desc = (
                    f"Session {start_str}–{end_str} claims {activity_pct:.0f}% activity "
                    f"but {non_work_pct:.0f}% of {len(in_session)} screenshots show non-work content."
                )
            elif work_pct == 0 and len(in_session) >= 3 and activity_pct >= 40:
                contradiction = True
                contradiction_desc = (
                    f"Session {start_str}–{end_str} claims {activity_pct:.0f}% activity "
                    f"but zero work-related content in {len(in_session)} screenshots."
                )
            elif idle_pct >= 70 and activity_pct >= 70:
                contradiction = True
                contradiction_desc = (
                    f"Session {start_str}–{end_str} claims {activity_pct:.0f}% activity "
                    f"but {idle_pct:.0f}% of screenshots show idle screens."
                )

        matches.append(SessionScreenshotMatch(
            session_date=date_str,
            session_start=start_str,
            session_end=end_str,
            duration_hours=round(duration_h, 2),
            timesheet_activity_pct=activity_pct,
            screenshot_count=len(in_session),
            work_count=work_c,
            non_work_count=non_work_c,
            idle_count=idle_c,
            uncertain_count=uncertain_c,
            work_pct=work_pct,
            non_work_pct=non_work_pct,
            idle_pct=idle_pct,
            has_contradiction=contradiction,
            contradiction_description=contradiction_desc,
        ))

    return matches


# ── Temporal gap detection ───────────────────────────────────────────────────

def detect_temporal_gaps(
    session_matches: list[SessionScreenshotMatch],
) -> list[TemporalGap]:
    """
    Identify sessions where the screenshot evidence contradicts or fails to
    support the claimed timesheet activity.
    """
    gaps: list[TemporalGap] = []

    for m in session_matches:
        if m.has_contradiction and m.contradiction_description:
            # Determine gap type and severity
            if m.non_work_pct >= 50 and m.timesheet_activity_pct >= 60:
                gap_type = "non_work_during_high_activity"
                severity = "high" if m.non_work_pct >= 70 or m.timesheet_activity_pct >= 80 else "medium"
            elif m.work_pct == 0 and m.screenshot_count >= 3:
                gap_type = "zero_work_in_session"
                severity = "high"
            elif m.idle_pct >= 70 and m.timesheet_activity_pct >= 70:
                gap_type = "non_work_during_high_activity"
                severity = "high"
            else:
                gap_type = "non_work_during_high_activity"
                severity = "medium"

            gaps.append(TemporalGap(
                session_date=m.session_date,
                session_start=m.session_start,
                session_end=m.session_end,
                gap_type=gap_type,
                timesheet_activity_pct=m.timesheet_activity_pct,
                screenshot_work_pct=m.work_pct,
                screenshot_count=m.screenshot_count,
                description=m.contradiction_description,
                severity=severity,
            ))
        elif m.screenshot_count == 0 and m.duration_hours >= 1.0 and m.timesheet_activity_pct >= 70:
            # High-activity long session with NO screenshots at all
            gaps.append(TemporalGap(
                session_date=m.session_date,
                session_start=m.session_start,
                session_end=m.session_end,
                gap_type="high_activity_no_screenshots",
                timesheet_activity_pct=m.timesheet_activity_pct,
                screenshot_work_pct=0.0,
                screenshot_count=0,
                description=(
                    f"Session {m.session_start}–{m.session_end} claims {m.timesheet_activity_pct:.0f}% "
                    f"activity over {m.duration_hours:.1f}h but no screenshots were captured."
                ),
                severity="low",  # Low because HiveDesk sampling can miss short sessions
            ))

    return gaps


# ── Sequential pattern analysis ──────────────────────────────────────────────

def run_sequential_pattern_analysis(
    classifications: list[ScreenshotClassification],
    llm=None,
) -> dict:
    """
    Send the full chronological screenshot sequence to the LLM for pattern analysis.
    Detects suspicious clusters, mechanical switching, frozen-screen loops, etc.
    Returns the parsed LLM response dict (or empty dict on failure).
    """
    from prompts import SEQUENTIAL_SCREENSHOT_PATTERN_PROMPT
    from langchain_core.messages import HumanMessage, SystemMessage
    from helpers import safe_parse_llm_json

    if not classifications:
        return {}

    if llm is None:
        from config import get_llm
        llm = get_llm(temperature=0.0, max_tokens=2000)

    # Sort by timestamp
    sorted_classes = sorted(classifications, key=lambda c: c.timestamp)

    # Compute time span
    try:
        t_first = _parse_classification_datetime(sorted_classes[0].timestamp)
        t_last = _parse_classification_datetime(sorted_classes[-1].timestamp)
        if t_first and t_last:
            span_min = (t_last - t_first).total_seconds() / 60
            if span_min >= 60:
                time_span = f"{span_min / 60:.1f} hours"
            else:
                time_span = f"{span_min:.0f} minutes"
        else:
            time_span = "unknown duration"
    except Exception:
        time_span = "unknown duration"

    # Build compact sequence for the prompt (limit to 100 entries to keep tokens manageable)
    sequence_entries = []
    step = max(1, len(sorted_classes) // 100)
    for i, c in enumerate(sorted_classes[::step]):
        sequence_entries.append({
            "timestamp": c.timestamp,
            "category": c.category.value,
            "confidence": round(c.confidence, 2),
            "description": c.description[:120],
        })

    prompt = SEQUENTIAL_SCREENSHOT_PATTERN_PROMPT.format(
        screenshot_sequence=json.dumps(sequence_entries, indent=2),
        total_screenshots=len(sorted_classes),
        time_span=time_span,
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You are a fraud detection specialist. Analyze screenshot sequences for suspicious patterns. Respond with valid JSON only."),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        del response
        return safe_parse_llm_json(text)
    except Exception as e:
        logger.error(f"Sequential pattern analysis failed: {e}")
        return {}


# ── Master function ──────────────────────────────────────────────────────────

def analyze_temporal_consistency(
    timesheet_analysis: TimesheetAnalysisResult,
    classifications: list[ScreenshotClassification],
    raw_sessions: list[dict],
    llm=None,
) -> dict:
    """
    Full temporal consistency analysis.

    Returns a dict with:
        session_matches:        list[SessionScreenshotMatch]
        temporal_gaps:          list[TemporalGap]
        temporal_contradictions: list[str]   — human-readable contradiction strings
        sequential_pattern:     str          — overall pattern label from LLM
        suspicious_clusters:    list[dict]   — suspicious time clusters from LLM
    """
    logger.info("Running temporal consistency analysis...")

    # Step 1: Map sessions to screenshots
    session_matches = build_session_screenshot_map(
        timesheet_analysis, classifications, raw_sessions
    )
    logger.info(f"  Mapped {len(session_matches)} sessions to screenshots.")

    # Step 2: Detect temporal gaps / contradictions
    temporal_gaps = detect_temporal_gaps(session_matches)
    logger.info(f"  Detected {len(temporal_gaps)} temporal gap(s).")

    # Build human-readable contradiction strings
    temporal_contradictions: list[str] = []
    for g in temporal_gaps:
        if g.gap_type != "high_activity_no_screenshots":  # Low signal — don't surface as contradiction
            temporal_contradictions.append(g.description)

    # Log session-level contradictions
    contradicted_sessions = [m for m in session_matches if m.has_contradiction]
    if contradicted_sessions:
        logger.info(
            f"  FRAUD SIGNAL: {len(contradicted_sessions)} session(s) have "
            f"screenshot evidence contradicting claimed activity."
        )

    # Step 3: Sequential pattern analysis via LLM (skip if very few screenshots)
    sequential_result: dict = {}
    if len(classifications) >= 5:
        sequential_result = run_sequential_pattern_analysis(classifications, llm=llm)

    sequential_pattern = sequential_result.get("overall_pattern", "")
    suspicious_clusters = sequential_result.get("suspicious_clusters", [])

    if sequential_pattern:
        logger.info(f"  Sequential pattern: {sequential_pattern}")
    if suspicious_clusters:
        logger.info(f"  Suspicious clusters: {len(suspicious_clusters)} detected.")

    return {
        "session_matches": session_matches,
        "temporal_gaps": temporal_gaps,
        "temporal_contradictions": temporal_contradictions,
        "sequential_pattern": sequential_pattern,
        "suspicious_clusters": suspicious_clusters,
        "sequential_findings": sequential_result.get("key_pattern_findings", []),
    }
