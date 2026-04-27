"""
Evidence fusion module.
Cross-references screenshot analysis with timesheet metrics.
Uses rule-based contradiction detection + LLM-assisted reasoning.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from prompts import (
    EVIDENCE_FUSION_PROMPT, FINAL_REPORT_PROMPT, SEQUENTIAL_SCREENSHOT_PATTERN_PROMPT,
    build_evidence_fusion_prompt, build_final_report_prompt,
)
from models import (
    CrossAnalysisResult,
    FinalRiskAssessment,
    RiskLevel,
    RiskScoreBreakdown,
    ScreenshotAnalysisResult,
    SessionScreenshotMatch,
    TemporalGap,
    TimesheetAnalysisResult,
    ValidationResult,
)
from config import get_llm, get_settings
from helpers import safe_parse_llm_json

logger = logging.getLogger(__name__)


# ── Rule-based cross-check ──────────────────────────────────────────────────


def _rule_based_cross_check(
    ts: TimesheetAnalysisResult,
    ss: ScreenshotAnalysisResult,
    validation: ValidationResult,
    department: str = "developer",
) -> CrossAnalysisResult:
    """
    Rule-based cross-analysis before LLM reasoning.
    Detects obvious contradictions and consistencies.
    Now includes sample-size weighting.
    """
    contradictions: list[str] = []
    consistencies: list[str] = []

    # Check employee/date match from validation
    if validation.employee_match is False:
        contradictions.append(
            "CRITICAL: Timesheet and screenshot report are for different employees."
        )
    if validation.date_overlap is False:
        contradictions.append(
            "CRITICAL: Timesheet and screenshot report cover different time periods."
        )

    # Activity gap analysis with sample-size weighting
    activity_gap = abs(ts.overall_activity_pct - ss.work_pct)

    # Higher screenshot count = more reliable comparison
    sample_confidence = min(1.0, ss.total_analyzed / 20.0)  # Full confidence at 20+ screenshots
    gap_threshold_high = 30 - (10 * sample_confidence)  # 30pp with few screenshots, 20pp with many
    gap_threshold_low = 15 - (5 * sample_confidence)

    if activity_gap > gap_threshold_high:
        contradictions.append(
            f"Large activity gap: timesheet reports {ts.overall_activity_pct:.0f}% activity "
            f"but only {ss.work_pct:.0f}% of screenshots show work "
            f"(gap: {activity_gap:.0f}pp, based on {ss.total_analyzed} screenshots)."
        )
    elif activity_gap < gap_threshold_low:
        consistencies.append(
            f"Activity levels consistent: timesheet {ts.overall_activity_pct:.0f}% vs "
            f"screenshots {ss.work_pct:.0f}% work (gap: {activity_gap:.0f}pp)."
        )

    # Check for high non-work screenshots with high reported activity
    if ss.non_work_pct > 25 and ts.overall_activity_pct > 70:
        contradictions.append(
            f"Timesheet shows {ts.overall_activity_pct:.0f}% activity but "
            f"{ss.non_work_pct:.0f}% of screenshots show non-work content."
        )

    # Check for high idle screenshots — threshold varies by department
    from department_config import get_department_thresholds as _get_dt
    _idle_thresh = _get_dt(department)["idle_ratio_threshold"] * 100
    if ss.idle_pct > _idle_thresh and ts.overall_activity_pct > 60:
        contradictions.append(
            f"Timesheet shows {ts.overall_activity_pct:.0f}% activity but "
            f"{ss.idle_pct:.0f}% of screenshots show idle screens."
        )

    # Positive: work + reasonable activity (lower threshold for devs)
    if ss.work_pct > 60 and ts.overall_activity_pct > 35:
        consistencies.append(
            f"Strong work indicators: {ss.work_pct:.0f}% work screenshots and "
            f"{ts.overall_activity_pct:.0f}% timesheet activity."
        )

    return CrossAnalysisResult(
        contradictions=contradictions,
        consistencies=consistencies,
        screenshot_work_pct=ss.work_pct,
        timesheet_activity_pct=ts.overall_activity_pct,
        activity_gap=activity_gap,
    )


def fuse_evidence(
    timesheet_analysis: TimesheetAnalysisResult,
    screenshot_analysis: ScreenshotAnalysisResult,
    validation: ValidationResult,
    llm: Optional[ChatOpenAI] = None,
    raw_sessions: Optional[list[dict]] = None,
    department: str = "developer",
) -> CrossAnalysisResult:
    """
    Fuse timesheet and screenshot evidence.
    Step 1: Rule-based cross-check
    Step 2: Temporal consistency analysis (session-level matching)
    Step 3: LLM-assisted reasoning with temporal evidence
    """
    result = _rule_based_cross_check(
        timesheet_analysis, screenshot_analysis, validation, department=department
    )

    if llm is None:
        llm = get_llm(temperature=0.0, max_tokens=2000)

    # ── Step 2: Temporal analysis ────────────────────────────────────────
    temporal_summary_text = "No temporal session-level analysis available."
    if raw_sessions and screenshot_analysis.classifications:
        try:
            from temporal_analysis import analyze_temporal_consistency
            temporal = analyze_temporal_consistency(
                timesheet_analysis=timesheet_analysis,
                classifications=screenshot_analysis.classifications,
                raw_sessions=raw_sessions,
                llm=llm,
            )
            result.session_screenshot_matches = temporal["session_matches"]
            result.temporal_gaps = temporal["temporal_gaps"]
            result.temporal_contradictions = temporal["temporal_contradictions"]
            result.sequential_pattern = temporal.get("sequential_pattern", "")
            result.suspicious_clusters = temporal.get("suspicious_clusters", [])

            # Add temporal contradictions to the main contradictions list
            for tc in temporal["temporal_contradictions"]:
                if tc not in result.contradictions:
                    result.contradictions.append(tc)

            # Build a compact temporal evidence summary for the LLM prompt
            contradicted = [m for m in temporal["session_matches"] if m.has_contradiction]
            temporal_summary_text = json.dumps({
                "sessions_analyzed": len(temporal["session_matches"]),
                "sessions_with_contradictions": len(contradicted),
                "session_contradictions": [
                    {
                        "session": f"{m.session_date} {m.session_start}-{m.session_end}",
                        "claimed_activity_pct": m.timesheet_activity_pct,
                        "screenshot_work_pct": m.work_pct,
                        "screenshot_non_work_pct": m.non_work_pct,
                        "screenshot_count": m.screenshot_count,
                        "description": m.contradiction_description,
                    }
                    for m in contradicted
                ],
                "temporal_gaps": [
                    {"type": g.gap_type, "session": f"{g.session_date} {g.session_start}-{g.session_end}", "desc": g.description}
                    for g in temporal["temporal_gaps"]
                ],
                "sequential_pattern": temporal.get("sequential_pattern", ""),
                "suspicious_clusters": temporal.get("suspicious_clusters", [])[:5],
                "sequential_findings": temporal.get("sequential_findings", []),
            }, indent=2)
        except Exception as e:
            logger.error(f"Temporal analysis failed: {e}")
            temporal_summary_text = f"Temporal analysis failed: {e}"

    # ── Step 3: LLM fusion with temporal context ─────────────────────────
    # Build screenshot data (sorted chronologically, limit to 60 entries)
    sorted_classes = sorted(screenshot_analysis.classifications, key=lambda c: c.timestamp)
    screenshot_classifications = []
    for c in sorted_classes[:60]:
        entry = {
            "timestamp": c.timestamp,
            "category": c.category.value,
            "confidence": c.confidence,
            "description": c.description,
            "apps": c.applications_visible,
        }
        if c.ocr_text:
            entry["ocr_excerpt"] = c.ocr_text[:150]
        screenshot_classifications.append(entry)

    anomaly_details = [
        {"date": a.session_date, "type": a.anomaly_type, "description": a.description, "severity": a.severity}
        for a in timesheet_analysis.anomalies
    ]

    prompt = build_evidence_fusion_prompt(department).format(
        screenshot_analysis=json.dumps({
            "total_analyzed": screenshot_analysis.total_analyzed,
            "work_count": screenshot_analysis.work_count,
            "non_work_count": screenshot_analysis.non_work_count,
            "idle_count": screenshot_analysis.idle_count,
            "uncertain_count": screenshot_analysis.uncertain_count,
            "work_pct": screenshot_analysis.work_pct,
            "non_work_pct": screenshot_analysis.non_work_pct,
            "idle_pct": screenshot_analysis.idle_pct,
            "summary": screenshot_analysis.summary,
            "repeated_frames": len(screenshot_analysis.repeated_frames),
            "suspicious_sites": [s.site_name for s in screenshot_analysis.suspicious_sites],
            "third_party_accounts": len(screenshot_analysis.third_party_accounts),
            "per_screenshot_details": screenshot_classifications,
        }, indent=2),
        timesheet_analysis=json.dumps({
            "total_sessions": timesheet_analysis.total_sessions,
            "total_hours": timesheet_analysis.total_duration_hours,
            "active_hours": timesheet_analysis.total_active_hours,
            "overall_activity_pct": timesheet_analysis.overall_activity_pct,
            "avg_session_duration_min": timesheet_analysis.avg_session_duration_min,
            "avg_activity_pct": timesheet_analysis.avg_activity_pct,
            "min_activity_pct": timesheet_analysis.min_activity_pct,
            "max_activity_pct": timesheet_analysis.max_activity_pct,
            "sessions_below_50_pct": timesheet_analysis.sessions_below_50_pct,
            "daily_breakdown_hours": timesheet_analysis.daily_breakdown,
            "anomalies": anomaly_details,
            "activity_std_dev": timesheet_analysis.activity_std_dev,
            "duplicate_sessions": timesheet_analysis.duplicate_session_count,
            "overlapping_sessions": timesheet_analysis.overlapping_session_count,
        }, indent=2),
        validation_info=json.dumps({
            "employee_match": validation.employee_match,
            "date_overlap": validation.date_overlap,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "timezone": validation.timezone_info,
        }, indent=2),
        temporal_evidence=temporal_summary_text,
    )

    try:
        response = llm.invoke([
            SystemMessage(content=(
                "You are a senior cross-referencing analyst and fraud investigator. "
                "Identify contradictions, fraud indicators, and suspicious patterns using ALL available evidence including temporal session-level data. "
                "Respond with valid JSON only."
            )),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        del response

        data = safe_parse_llm_json(text)
        if data:
            result.reasoning = data.get("reasoning", "")
            for c in data.get("contradictions", []):
                if c not in result.contradictions:
                    result.contradictions.append(c)
            for c in data.get("consistencies", []):
                if c not in result.consistencies:
                    result.consistencies.append(c)
            for tc in data.get("temporal_contradictions", []):
                if tc not in result.temporal_contradictions:
                    result.temporal_contradictions.append(tc)

    except Exception as e:
        logger.error(f"LLM evidence fusion failed: {e}")
        result.reasoning = f"LLM reasoning unavailable: {e}"

    return result


# ── Risk scoring (refactored into sub-functions) ────────────────────────────


def _compute_rule_based_score(
    validation: ValidationResult,
    ts: Optional[TimesheetAnalysisResult],
    ss: Optional[ScreenshotAnalysisResult],
    ca: Optional[CrossAnalysisResult],
    department: str = "developer",
) -> tuple[float, list[str], list[RiskScoreBreakdown]]:
    """
    Compute rule-based risk score from all available evidence.
    Returns (score, findings_list, score_breakdown).

    Calibrated for accurate detection — signals are weighted by reliability:
      - Hard signals (impossible sessions, repeated frames): high points
      - Session-level temporal contradictions: high points
      - Aggregate-level soft signals: lower points
    """
    risk_score = 0.0
    findings: list[str] = []
    breakdown: list[RiskScoreBreakdown] = []

    def _add(signal: str, pts: float, desc: str):
        nonlocal risk_score
        risk_score += pts
        findings.append(f"{desc} (+{pts:.0f} points)")
        breakdown.append(RiskScoreBreakdown(signal_name=signal, points=pts, description=desc))

    # ── Screenshot signals ────────────────────────────────────────────────
    if ss:
        n_repeated = len(ss.repeated_frames)
        if n_repeated == 1:
            _add("repeated_frames", 15, f"1 repeated identical frame pair detected (possible loop)")
        elif n_repeated == 2:
            _add("repeated_frames", 22, f"2 repeated identical frame pairs detected")
        elif n_repeated >= 3:
            pts = min(35, 22 + (n_repeated - 2) * 5)
            _add("repeated_frames", pts, f"{n_repeated} repeated identical frame pairs detected")

        # Tab-switching loop (any detected loop is worth noting)
        if ss.tab_switching_analysis and ss.tab_switching_analysis.loop_detected:
            pts = 8 + min(10, ss.tab_switching_analysis.loop_count * 2)
            _add("tab_switching_loop", pts,
                 f"Tab-switching loop ({ss.tab_switching_analysis.loop_count} loops) detected")

        # Zero productive work — more sensitive threshold (5+ screenshots, was 10)
        if ss.total_analyzed >= 5 and ss.work_count == 0:
            _add("zero_work", 20,
                 f"No work-related activity in any of {ss.total_analyzed} screenshots")

        # High non-work — threshold lowered to 30% (was 50%)
        if ss.non_work_pct > 50:
            _add("high_non_work", 12, f"{ss.non_work_pct:.0f}% of screenshots show non-work activity")
        elif ss.non_work_pct > 30:
            _add("elevated_non_work", 6, f"{ss.non_work_pct:.0f}% of screenshots show non-work activity")

        # Suspicious sites detected via OCR
        if ss.suspicious_sites:
            pts = min(15, len(ss.suspicious_sites) * 5)
            sites = ", ".join(s.site_name for s in ss.suspicious_sites[:3])
            _add("suspicious_sites", pts, f"Suspicious sites detected: {sites}")

        # Third-party accounts (different email in screenshots)
        if ss.third_party_accounts:
            _add("third_party_accounts", 15,
                 f"{len(ss.third_party_accounts)} third-party account(s) visible in screenshots")

        # Monitor inconsistencies (possible screen-sharing or spoofing)
        if ss.monitor_inconsistencies:
            _add("monitor_inconsistencies", 8,
                 f"Monitor configuration changes on {len(ss.monitor_inconsistencies)} day(s)")

    # ── Temporal / session-level signals ─────────────────────────────────
    if ca and ca.temporal_gaps:
        critical_gaps = [g for g in ca.temporal_gaps if g.severity in ("high", "critical")]
        medium_gaps = [g for g in ca.temporal_gaps if g.severity == "medium"]

        if critical_gaps:
            pts = min(30, len(critical_gaps) * 12)
            _add("temporal_contradiction_high", pts,
                 f"{len(critical_gaps)} session(s) with screenshot evidence directly contradicting claimed activity level")

        if medium_gaps:
            pts = min(15, len(medium_gaps) * 6)
            _add("temporal_contradiction_medium", pts,
                 f"{len(medium_gaps)} session(s) with moderate screenshot vs activity inconsistency")

    # Sequential pattern flags
    if ca and ca.sequential_pattern in ("suspicious_clustering", "frozen_screen_loop", "work_absent"):
        _add("suspicious_sequential_pattern", 15,
             f"Screenshot sequence shows suspicious pattern: {ca.sequential_pattern}")
    if ca and ca.suspicious_clusters:
        pts = min(10, len(ca.suspicious_clusters) * 4)
        _add("suspicious_clusters", pts,
             f"{len(ca.suspicious_clusters)} suspicious activity cluster(s) detected in screenshot timeline")

    # ── Cross-analysis signals ────────────────────────────────────────────
    if ca:
        # Activity gap — threshold lowered to 25pp (was 40pp)
        if ca.activity_gap > 40:
            _add("activity_gap_large", 12,
                 f"Large activity gap: {ca.activity_gap:.0f}pp (timesheet vs screenshots)")
        elif ca.activity_gap > 25:
            _add("activity_gap_moderate", 6,
                 f"Moderate activity gap: {ca.activity_gap:.0f}pp (timesheet vs screenshots)")

        # Contradictions — threshold lowered to 2 (was 3)
        n_contradictions = len(ca.contradictions)
        if n_contradictions >= 3:
            pts = min(12, n_contradictions * 3)
            _add("contradictions", pts,
                 f"{n_contradictions} contradiction(s) between timesheet and screenshot data")
        elif n_contradictions == 2:
            _add("contradictions", 6, f"2 contradiction(s) between data sources")

    # ── Timesheet signals ─────────────────────────────────────────────────
    if ts:
        # Low overall activity — use department-specific threshold
        from department_config import get_department_thresholds
        dept_thresholds = get_department_thresholds(department)
        low_act_thresh = dept_thresholds["low_activity_threshold"]
        very_low_thresh = low_act_thresh * 0.45  # Very low = less than half the expected minimum
        if ts.overall_activity_pct < very_low_thresh:
            _add("very_low_activity", 10, f"Very low overall activity: {ts.overall_activity_pct:.0f}% (threshold: {low_act_thresh:.0f}%)")
        elif ts.overall_activity_pct < low_act_thresh * 0.65:
            _add("low_activity", 5, f"Low overall activity: {ts.overall_activity_pct:.0f}% (threshold: {low_act_thresh:.0f}%)")

        # Overlapping sessions — physically impossible
        if ts.overlapping_session_count > 0:
            pts = min(25, ts.overlapping_session_count * 12)
            _add("overlapping_sessions", pts,
                 f"{ts.overlapping_session_count} overlapping session(s) — physically impossible")

        # Duplicate sessions — threshold lowered to 2 (was 3)
        if ts.duplicate_session_count >= 3:
            _add("duplicate_sessions", 12,
                 f"{ts.duplicate_session_count} duplicate session(s) detected")
        elif ts.duplicate_session_count == 2:
            _add("duplicate_sessions", 6, "2 duplicate session(s) detected")

        # Mouse jiggler / activity simulator detection
        from config import get_settings as _get_settings
        _settings = _get_settings()
        if ts.activity_std_dev < 1.0 and ts.avg_activity_pct > 60 and ts.total_sessions >= 15:
            _add("activity_too_stable", 15,
                 f"Activity suspiciously stable: std_dev={ts.activity_std_dev:.1f}% — possible mouse jiggler")
        elif ts.activity_std_dev < _settings.activity_stability_threshold and ts.avg_activity_pct > 50 and ts.total_sessions >= 10:
            _add("activity_borderline_stable", 7,
                 f"Activity variance lower than expected: std_dev={ts.activity_std_dev:.1f}%")

        # Excessive daily hours (>14h is suspicious even for developers)
        if ts.days_over_12h > 0:
            pts = min(10, ts.days_over_12h * 4)
            _add("excessive_daily_hours", pts,
                 f"{ts.days_over_12h} day(s) with >12h billed")

    risk_score = min(100.0, max(0.0, risk_score))
    return risk_score, findings, breakdown


def _determine_risk_level(score: float) -> RiskLevel:
    """
    Map score to risk level.
    Bands aligned with FINAL_REPORT_PROMPT criteria for consistent LLM/rule agreement.
    """
    if score >= 80:
        return RiskLevel.CONFIRMED_FRAUD
    elif score >= 60:
        return RiskLevel.HIGH_RISK
    elif score >= 35:
        return RiskLevel.NEEDS_REVIEW
    elif score >= 15:
        return RiskLevel.LOW_RISK
    else:
        return RiskLevel.VALID_WORK


def _compute_confidence(
    ts: Optional[TimesheetAnalysisResult],
    ss: Optional[ScreenshotAnalysisResult],
    ca: Optional[CrossAnalysisResult],
    n_findings: int,
) -> float:
    """Compute confidence based on available evidence."""
    screenshots_analyzed = ss.total_analyzed if ss else 0

    base_confidence = 0.3
    if ts is not None:
        base_confidence += 0.2
    if screenshots_analyzed > 0:
        # Logarithmic scaling: diminishing returns after ~30 screenshots
        base_confidence += min(0.3, 0.1 + 0.2 * (1 - math.exp(-screenshots_analyzed / 15)))
    if ca is not None:
        base_confidence += 0.1
    if n_findings > 3:
        base_confidence += 0.1

    # Cap at 0.5 if zero screenshots (was 0.4, raised slightly for enhanced timesheet analysis)
    if screenshots_analyzed == 0:
        base_confidence = min(0.5, base_confidence)

    return min(1.0, max(0.0, base_confidence))


def _generate_recommendations(risk_level: RiskLevel, findings: list[str]) -> list[str]:
    """Generate actionable, specific recommendations based on risk level and findings."""
    recs: list[str] = []

    if risk_level == RiskLevel.CONFIRMED_FRAUD:
        recs.append("URGENT: Escalate to management immediately — multiple hard fraud signals detected.")
        recs.append("Suspend payment pending manual review of raw timesheet data and screenshots.")
        recs.append("Request direct explanation from employee for the flagged sessions.")
        recs.append("Compare against previous audit periods to assess duration of pattern.")
    elif risk_level == RiskLevel.HIGH_RISK:
        recs.append("Schedule a review meeting — significant evidence patterns require explanation.")
        recs.append("Do not approve payment for flagged sessions until evidence is reviewed.")
        recs.append("Collect additional monitoring data (next billing period) before making a final decision.")
    elif risk_level == RiskLevel.NEEDS_REVIEW:
        recs.append("Flag for manual spot-check: review the specific sessions listed in contradictions.")
        recs.append("Request a brief written summary of work completed for the period.")
        recs.append("These findings may have legitimate explanations — context matters.")
    elif risk_level == RiskLevel.LOW_RISK:
        recs.append("Minor irregularities noted but no strong fraud signals — approve with standard review.")
        recs.append("Continue standard audit schedule.")
    else:  # VALID_WORK
        recs.append("Work evidence is consistent and clean — approve payment.")
        recs.append("No action required — continue standard audit schedule.")

    # Add specific finding-based recommendations
    for f in findings:
        if "overlapping" in f.lower():
            recs.append("Investigate overlapping sessions — these are physically impossible and require explanation.")
            break
    for f in findings:
        if "third_party" in f.lower() or "third-party" in f.lower():
            recs.append("Verify employee identity — a different account email was visible in screenshots.")
            break

    return recs


def _invoke_llm_for_assessment(
    validation: ValidationResult,
    ts: Optional[TimesheetAnalysisResult],
    ss: Optional[ScreenshotAnalysisResult],
    ca: Optional[CrossAnalysisResult],
    llm: Optional[ChatOpenAI] = None,
    department: str = "developer",
) -> dict:
    """Invoke LLM for detailed reasoning. Returns parsed dict or empty dict on failure."""
    if llm is None:
        llm = get_llm(temperature=0.0, max_tokens=4000)

    # Build payloads
    ts_payload = {}
    if ts:
        anomaly_list = [
            {"date": a.session_date, "type": a.anomaly_type, "desc": a.description, "severity": a.severity}
            for a in ts.anomalies
        ]
        ts_payload = {
            "total_sessions": ts.total_sessions,
            "total_hours": ts.total_duration_hours,
            "active_hours": ts.total_active_hours,
            "overall_activity_pct": ts.overall_activity_pct,
            "avg_session_min": ts.avg_session_duration_min,
            "avg_activity_pct": ts.avg_activity_pct,
            "min_activity_pct": ts.min_activity_pct,
            "max_activity_pct": ts.max_activity_pct,
            "sessions_below_50_pct": ts.sessions_below_50_pct,
            "very_short_sessions": ts.very_short_sessions,
            "very_long_sessions": ts.very_long_sessions,
            "daily_breakdown": ts.daily_breakdown,
            "anomalies": anomaly_list,
            "suspicious_hours_total": ts.suspicious_hours_total,
            "suspicious_pct": ts.suspicious_pct,
            "llm_reasoning": ts.reasoning,
            # Enhanced stats
            "activity_std_dev": ts.activity_std_dev,
            "duration_std_dev": ts.duration_std_dev,
            "p25_duration_min": ts.p25_duration_min,
            "p75_duration_min": ts.p75_duration_min,
            "round_duration_pct": ts.round_duration_pct,
            "duplicate_sessions": ts.duplicate_session_count,
            "overlapping_sessions": ts.overlapping_session_count,
            "days_over_12h": ts.days_over_12h,
            "start_time_std_min": ts.start_time_std_min,
            "avg_gap_between_sessions_min": ts.avg_gap_between_sessions_min,
            "gap_std_dev_min": ts.gap_std_dev_min,
        }

    ss_payload = {"summary": "No screenshots analyzed"}
    if ss:
        top_classifications = [
            {"ts": c.timestamp, "cat": c.category.value, "conf": c.confidence, "desc": c.description}
            for c in ss.classifications[:30]
        ]
        repeated_summary = [
            {"first": r.first_occurrence, "repeat": r.repeat_occurrence,
             "gap_min": r.time_gap_minutes, "similarity": r.similarity_score}
            for r in ss.repeated_frames
        ]
        ss_payload = {
            "total_analyzed": ss.total_analyzed,
            "work_count": ss.work_count,
            "work_pct": ss.work_pct,
            "non_work_count": ss.non_work_count,
            "non_work_pct": ss.non_work_pct,
            "idle_count": ss.idle_count,
            "idle_pct": ss.idle_pct,
            "uncertain_count": ss.uncertain_count,
            "summary": ss.summary,
            "sample_classifications": top_classifications,
            "repeated_frames": repeated_summary,
            "tab_switching": ss.tab_switching_analysis.model_dump() if ss.tab_switching_analysis else None,
            "monitor_inconsistencies": len(ss.monitor_inconsistencies),
            "unauthorized_access_events": len(ss.unauthorized_access_events),
            "third_party_accounts": len(ss.third_party_accounts),
            "suspicious_sites": [s.site_name for s in ss.suspicious_sites],
        }

    ca_payload = {}
    if ca:
        # Include temporal/session-level evidence in the cross-analysis payload
        session_contradictions = []
        if ca.session_screenshot_matches:
            for m in ca.session_screenshot_matches:
                if m.has_contradiction:
                    session_contradictions.append({
                        "session": f"{m.session_date} {m.session_start}-{m.session_end}",
                        "claimed_activity_pct": m.timesheet_activity_pct,
                        "screenshot_work_pct": m.work_pct,
                        "non_work_pct": m.non_work_pct,
                        "screenshot_count": m.screenshot_count,
                        "description": m.contradiction_description,
                    })

        ca_payload = {
            "contradictions": ca.contradictions,
            "consistencies": ca.consistencies,
            "screenshot_work_pct": ca.screenshot_work_pct,
            "timesheet_activity_pct": ca.timesheet_activity_pct,
            "activity_gap": ca.activity_gap,
            "reasoning": ca.reasoning,
            "session_level_contradictions": session_contradictions,
            "temporal_gaps": [
                {"type": g.gap_type, "session": f"{g.session_date} {g.session_start}-{g.session_end}",
                 "claimed_activity": g.timesheet_activity_pct, "screenshot_work_pct": g.screenshot_work_pct,
                 "desc": g.description, "severity": g.severity}
                for g in (ca.temporal_gaps or [])
            ],
            "sequential_pattern": ca.sequential_pattern or "",
            "suspicious_clusters_count": len(ca.suspicious_clusters or []),
        }

    prompt = build_final_report_prompt(department).format(
        validation=json.dumps(validation.model_dump(), indent=2, default=str),
        timesheet_analysis=json.dumps(ts_payload, indent=2, default=str),
        screenshot_analysis=json.dumps(ss_payload, indent=2),
        cross_analysis=json.dumps(ca_payload, indent=2, default=str),
    )

    try:
        response = llm.invoke([
            SystemMessage(content=(
                "You are a senior fraud analyst and work-pattern investigator. "
                "Assess the evidence objectively and accurately. Do not apply a lenient default — "
                "let the evidence determine the risk level. Respond with valid JSON only."
            )),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        del response
        return safe_parse_llm_json(text)
    except Exception as e:
        logger.error(f"LLM risk assessment failed: {e}")
        return {}


def generate_risk_assessment(
    validation: ValidationResult,
    timesheet_analysis: Optional[TimesheetAnalysisResult],
    screenshot_analysis: Optional[ScreenshotAnalysisResult],
    cross_analysis: Optional[CrossAnalysisResult],
    llm: Optional[ChatOpenAI] = None,
    raw_sessions: Optional[list[dict]] = None,
    department: str = "developer",
) -> FinalRiskAssessment:
    """
    Generate final risk assessment combining rule-based scoring + LLM reasoning.
    """
    # If the bundle is fundamentally invalid, short-circuit
    if not validation.is_valid and validation.errors:
        critical_errors = [e for e in validation.errors if "CRITICAL" in e.upper() or "different" in e.lower()]
        if critical_errors:
            return FinalRiskAssessment(
                risk_score=0.0,
                risk_level=RiskLevel.INVALID_BUNDLE,
                confidence=0.9,
                reasoning=f"Bundle is invalid: {'; '.join(validation.errors)}",
                key_findings=validation.errors,
                facts=validation.errors,
                interpretations=["Cannot assess risk due to data mismatch."],
                recommendations=["Re-submit with matching timesheet and screenshot data."],
            )

    # Step 1: Rule-based scoring
    risk_score, findings, breakdown = _compute_rule_based_score(
        validation, timesheet_analysis, screenshot_analysis, cross_analysis, department=department,
    )

    # Step 2: Determine risk level
    risk_level = _determine_risk_level(risk_score)

    # Step 3: Compute confidence
    confidence = _compute_confidence(
        timesheet_analysis, screenshot_analysis, cross_analysis, len(findings),
    )

    # Step 4: Generate recommendations
    recommendations = _generate_recommendations(risk_level, findings)

    # Step 5: LLM reasoning
    data = _invoke_llm_for_assessment(
        validation, timesheet_analysis, screenshot_analysis, cross_analysis, llm,
        department=department,
    )

    if data:
        return FinalRiskAssessment(
            risk_score=risk_score,
            risk_level=risk_level,
            confidence=confidence,
            reasoning=data.get("reasoning", ""),
            key_findings=data.get("key_findings", findings),
            facts=data.get("facts", []),
            interpretations=data.get("interpretations", []),
            fraud_assessment=data.get("fraud_assessment", ""),
            score_breakdown=breakdown,
            recommendations=recommendations,
        )

    return FinalRiskAssessment(
        risk_score=risk_score,
        risk_level=risk_level,
        confidence=confidence,
        reasoning=f"Rule-based assessment (LLM unavailable). Score driven by: {'; '.join(findings)}",
        key_findings=findings,
        facts=findings,
        interpretations=["LLM reasoning unavailable — assessment based on rules only."],
        score_breakdown=breakdown,
        recommendations=recommendations,
    )
