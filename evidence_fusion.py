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

from prompts import EVIDENCE_FUSION_PROMPT, FINAL_REPORT_PROMPT
from models import (
    CrossAnalysisResult,
    FinalRiskAssessment,
    RiskLevel,
    RiskScoreBreakdown,
    ScreenshotAnalysisResult,
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

    # Check for high idle screenshots — more lenient for devs (idle during debugging)
    if ss.idle_pct > 40 and ts.overall_activity_pct > 60:
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
) -> CrossAnalysisResult:
    """
    Fuse timesheet and screenshot evidence.
    Step 1: Rule-based cross-check
    Step 2: LLM-assisted reasoning for nuance
    """
    result = _rule_based_cross_check(
        timesheet_analysis, screenshot_analysis, validation
    )

    if llm is None:
        llm = get_llm(temperature=0.0, max_tokens=2000)

    # Build screenshot data (limit to avoid token overflow)
    screenshot_classifications = []
    for c in screenshot_analysis.classifications[:50]:
        screenshot_classifications.append({
            "timestamp": c.timestamp,
            "category": c.category.value,
            "confidence": c.confidence,
            "description": c.description,
            "apps": c.applications_visible,
        })

    anomaly_details = []
    for a in timesheet_analysis.anomalies:
        anomaly_details.append({
            "date": a.session_date,
            "type": a.anomaly_type,
            "description": a.description,
            "severity": a.severity,
        })

    prompt = EVIDENCE_FUSION_PROMPT.format(
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
        }, indent=2),
        validation_info=json.dumps({
            "employee_match": validation.employee_match,
            "date_overlap": validation.date_overlap,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "timezone": validation.timezone_info,
        }, indent=2),
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You are a senior cross-referencing analyst and fraud investigator. Identify contradictions, fraud indicators, and suspicious patterns. Respond with valid JSON only."),
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
) -> tuple[float, list[str], list[RiskScoreBreakdown]]:
    """
    Compute rule-based risk score from all evidence.
    Returns (score, findings_list, score_breakdown).
    """
    risk_score = 0.0
    findings: list[str] = []
    breakdown: list[RiskScoreBreakdown] = []

    def _add(signal: str, pts: float, desc: str):
        nonlocal risk_score
        risk_score += pts
        findings.append(f"{desc} (+{pts:.0f} points)")
        breakdown.append(RiskScoreBreakdown(signal_name=signal, points=pts, description=desc))

    # --- Screenshot-derived signals ---
    if ss:
        # Repeated identical frames — strongest fraud signal
        n_repeated = len(ss.repeated_frames)
        if n_repeated > 0:
            pts = min(60, n_repeated * 30)
            _add(
                "repeated_frames", pts,
                f"{n_repeated} repeated identical frame pair(s) — same frozen screen reappearing"
            )

        # Tab-switching loop
        if ss.tab_switching_analysis and ss.tab_switching_analysis.loop_detected:
            loop_pts = 15 + 10 * min(ss.tab_switching_analysis.loop_count, 3)
            _add(
                "tab_switching_loop", loop_pts,
                f"Tab-switching loop ({ss.tab_switching_analysis.loop_count} loops) — mechanical behavior"
            )

        # Monitor configuration change
        if ss.monitor_inconsistencies:
            n_days = len(ss.monitor_inconsistencies)
            mon_pts = min(20, n_days * 10)
            _add(
                "monitor_change", mon_pts,
                f"Monitor config changed on {n_days} day(s)"
            )

        # Unauthorized site access
        if ss.unauthorized_access_events:
            _add(
                "unauthorized_sites", 15,
                f"{len(ss.unauthorized_access_events)} unauthorized site access event(s)"
            )

        # Third-party account
        if ss.third_party_accounts:
            _add(
                "third_party_accounts", 20,
                f"{len(ss.third_party_accounts)} third-party account(s) on screen"
            )

        # Zero productive work
        if ss.total_analyzed > 0 and ss.work_count == 0:
            _add(
                "zero_work", 15,
                f"Zero work in {ss.total_analyzed} screenshots"
            )

        # High non-work screenshots
        if ss.non_work_pct > 30:
            _add("high_non_work", 15, f"{ss.non_work_pct:.0f}% non-work screenshots")
        elif ss.non_work_pct > 15:
            _add("moderate_non_work", 8, f"{ss.non_work_pct:.0f}% non-work screenshots")

        # Suspicious sites
        if ss.suspicious_sites:
            pts = min(10, len(ss.suspicious_sites) * 5)
            _add("suspicious_sites", pts, f"{len(ss.suspicious_sites)} suspicious site(s) on screen")

    # --- Cross-analysis signals ---
    if ca:
        if ca.activity_gap > 30:
            _add("activity_gap", 15, f"Activity gap: {ca.activity_gap:.0f}pp between timesheet and screenshots")
        elif ca.activity_gap > 15:
            _add("moderate_activity_gap", 8, f"Activity gap: {ca.activity_gap:.0f}pp")

        n_contradictions = len(ca.contradictions)
        if n_contradictions > 0:
            pts = min(30, n_contradictions * 10)
            _add("contradictions", pts, f"{n_contradictions} contradiction(s) between data sources")

    # --- Timesheet-only signals ---
    if ts:
        # Very long sessions with no work evidence
        # For devs, long sessions are normal — only flag if NO work visible in screenshots
        if ts.very_long_sessions > 0:
            no_work_evidence = (
                ss is None or ss.total_analyzed == 0 or ss.work_pct < 15
            )
            if no_work_evidence:
                pts = min(20, ts.very_long_sessions * 8)  # Reduced weight for devs
                _add("long_sessions_no_work", pts, f"{ts.very_long_sessions} very long session(s) with no work evidence")

        # Very low overall activity — lower threshold for devs (35% can be normal)
        if ts.overall_activity_pct < 25:
            _add("low_activity", 10, f"Very low overall activity: {ts.overall_activity_pct:.0f}% (even for developers, this is concerning)")

        # High suspicious hours
        if ts.suspicious_pct > 40:
            _add("high_suspicious_hours", 15, f"Suspicious hours: {ts.suspicious_hours_total} ({ts.suspicious_pct:.0f}% of total)")
        elif ts.suspicious_pct > 20:
            _add("moderate_suspicious_hours", 8, f"Suspicious hours: {ts.suspicious_hours_total} ({ts.suspicious_pct:.0f}%)")

        # Anomaly severity weighting
        high_sev = sum(1 for a in ts.anomalies if a.severity == "high")
        med_sev = sum(1 for a in ts.anomalies if a.severity == "medium")
        anomaly_pts = min(15, high_sev * 5 + med_sev * 2)
        if anomaly_pts > 0:
            _add("anomalies", anomaly_pts, f"{len(ts.anomalies)} timesheet anomalies ({high_sev} high, {med_sev} medium)")

        # NEW: Overlapping sessions
        if ts.overlapping_session_count > 0:
            _add("overlapping_sessions", 20, f"{ts.overlapping_session_count} overlapping session(s) — physically impossible")

        # NEW: Duplicate sessions
        if ts.duplicate_session_count > 0:
            pts = min(15, ts.duplicate_session_count * 5)
            _add("duplicate_sessions", pts, f"{ts.duplicate_session_count} duplicate session(s)")

        # NEW: Suspiciously round durations
        if ts.round_duration_pct > 70 and ts.total_sessions >= 5:
            _add("round_durations", 10, f"{ts.round_duration_pct:.0f}% of sessions have round durations")

        # NEW: Activity too stable (jiggler) — use config threshold
        from config import get_settings as _get_settings
        _settings = _get_settings()
        if ts.activity_std_dev < _settings.activity_stability_threshold and ts.avg_activity_pct > 50 and ts.total_sessions >= 10:
            _add("activity_too_stable", 15, f"Activity suspiciously stable: std_dev={ts.activity_std_dev:.1f}% (threshold: {_settings.activity_stability_threshold}%)")

        # NEW: Regular start times — use config threshold
        if ts.start_time_std_min < _settings.start_time_regularity_threshold and ts.total_sessions >= 7:
            _add("regular_start_times", 8, f"Start times suspiciously regular: std_dev={ts.start_time_std_min:.0f} min")

        # NEW: Excessive daily hours — use config threshold
        if ts.days_over_12h > 0:
            _add("excessive_daily_hours", min(15, ts.days_over_12h * 5), f"{ts.days_over_12h} day(s) with >{_settings.excessive_daily_hours:.0f}h billed")

        # NEW: Regular inter-session gaps — use config threshold
        if (ts.gap_std_dev_min > 0
            and ts.gap_std_dev_min < _settings.gap_regularity_threshold
            and ts.avg_gap_between_sessions_min > 0
            and ts.total_sessions >= 5):
            _add("regular_gaps", 8, f"Inter-session gaps suspiciously regular: std_dev={ts.gap_std_dev_min:.1f} min, mean={ts.avg_gap_between_sessions_min:.0f} min")

    risk_score = min(100.0, max(0.0, risk_score))
    return risk_score, findings, breakdown


def _determine_risk_level(score: float) -> RiskLevel:
    """Map score to risk level."""
    if score >= 80:
        return RiskLevel.CONFIRMED_FRAUD
    elif score >= 60:
        return RiskLevel.HIGH_RISK
    elif score >= 40:
        return RiskLevel.NEEDS_REVIEW
    else:
        return RiskLevel.LOW_RISK


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
    """Generate actionable recommendations based on risk level and findings."""
    recs: list[str] = []

    if risk_level == RiskLevel.CONFIRMED_FRAUD:
        recs.append("Immediate investigation required — strong evidence of fraud or manipulation.")
        recs.append("Consider suspending billing for this period pending review.")
        recs.append("Request detailed work log or screen recordings for verification.")
        recs.append("Consult legal/compliance team if financial impact is significant.")
    elif risk_level == RiskLevel.HIGH_RISK:
        recs.append("Manager should conduct a detailed review of this audit.")
        recs.append("Request employee explanation for flagged anomalies.")
        recs.append("Compare against previous audit periods for pattern changes.")
        recs.append("Consider requiring more frequent check-ins or deliverable reviews.")
    elif risk_level == RiskLevel.NEEDS_REVIEW:
        recs.append("Manager spot-check recommended for flagged sessions.")
        recs.append("Monitor for pattern persistence in future audit periods.")
        if any("activity" in f.lower() for f in findings):
            recs.append("Discuss expected activity levels with the employee.")
    else:
        recs.append("No immediate action required — work appears legitimate.")
        recs.append("Continue standard audit schedule.")

    return recs


def _invoke_llm_for_assessment(
    validation: ValidationResult,
    ts: Optional[TimesheetAnalysisResult],
    ss: Optional[ScreenshotAnalysisResult],
    ca: Optional[CrossAnalysisResult],
    llm: Optional[ChatOpenAI] = None,
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
        ca_payload = {
            "contradictions": ca.contradictions,
            "consistencies": ca.consistencies,
            "screenshot_work_pct": ca.screenshot_work_pct,
            "timesheet_activity_pct": ca.timesheet_activity_pct,
            "activity_gap": ca.activity_gap,
            "reasoning": ca.reasoning,
        }

    prompt = FINAL_REPORT_PROMPT.format(
        validation=json.dumps(validation.model_dump(), indent=2, default=str),
        timesheet_analysis=json.dumps(ts_payload, indent=2, default=str),
        screenshot_analysis=json.dumps(ss_payload, indent=2),
        cross_analysis=json.dumps(ca_payload, indent=2, default=str),
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You are a senior fraud investigator and risk assessment expert. Be direct and clear about fraud when evidence supports it. Respond with valid JSON only."),
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
        validation, timesheet_analysis, screenshot_analysis, cross_analysis,
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
