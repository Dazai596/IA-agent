"""
Evidence fusion module.
Cross-references screenshot analysis with timesheet metrics.
Uses rule-based contradiction detection + LLM-assisted reasoning.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from prompts import EVIDENCE_FUSION_PROMPT, FINAL_REPORT_PROMPT
from models import (
    CrossAnalysisResult,
    FinalRiskAssessment,
    RiskLevel,
    ScreenshotAnalysisResult,
    TimesheetAnalysisResult,
    ValidationResult,
)
from config import get_llm, get_settings

logger = logging.getLogger(__name__)


def _rule_based_cross_check(
    ts: TimesheetAnalysisResult,
    ss: ScreenshotAnalysisResult,
    validation: ValidationResult,
) -> CrossAnalysisResult:
    """
    Rule-based cross-analysis before LLM reasoning.
    Detects obvious contradictions and consistencies.
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

    # Activity gap analysis
    activity_gap = abs(ts.overall_activity_pct - ss.work_pct)

    if activity_gap > 30:
        contradictions.append(
            f"Large activity gap: timesheet reports {ts.overall_activity_pct:.0f}% activity "
            f"but only {ss.work_pct:.0f}% of screenshots show work "
            f"(gap: {activity_gap:.0f} percentage points)."
        )
    elif activity_gap < 15:
        consistencies.append(
            f"Activity levels consistent: timesheet {ts.overall_activity_pct:.0f}% vs "
            f"screenshots {ss.work_pct:.0f}% work (gap: {activity_gap:.0f}pp)."
        )

    # Check for high non-work screenshots with high reported activity
    if ss.non_work_pct > 20 and ts.overall_activity_pct > 80:
        contradictions.append(
            f"Timesheet shows {ts.overall_activity_pct:.0f}% activity but "
            f"{ss.non_work_pct:.0f}% of screenshots show non-work content."
        )

    # Check for high idle screenshots with high reported activity
    if ss.idle_pct > 30 and ts.overall_activity_pct > 70:
        contradictions.append(
            f"Timesheet shows {ts.overall_activity_pct:.0f}% activity but "
            f"{ss.idle_pct:.0f}% of screenshots show idle screens."
        )

    # Positive: high work + high activity
    if ss.work_pct > 70 and ts.overall_activity_pct > 70:
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
    # Rule-based first
    result = _rule_based_cross_check(
        timesheet_analysis, screenshot_analysis, validation
    )

    # LLM reasoning with enriched data
    if llm is None:
        llm = get_llm(temperature=0.0, max_tokens=2000)

    # Build rich screenshot data — include per-screenshot classifications
    screenshot_classifications = []
    for c in screenshot_analysis.classifications[:50]:  # limit to avoid token overflow
        screenshot_classifications.append({
            "timestamp": c.timestamp,
            "category": c.category.value,
            "confidence": c.confidence,
            "description": c.description,
            "apps": c.applications_visible,
        })

    # Build rich timesheet data — include anomalies and daily breakdown
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
        del response  # Free full response object
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        data = json.loads(text)
        result.reasoning = data.get("reasoning", "")

        # Merge LLM-found contradictions/consistencies with rule-based ones
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


def generate_risk_assessment(
    validation: ValidationResult,
    timesheet_analysis: Optional[TimesheetAnalysisResult],
    screenshot_analysis: Optional[ScreenshotAnalysisResult],
    cross_analysis: Optional[CrossAnalysisResult],
    llm: Optional[ChatOpenAI] = None,
) -> FinalRiskAssessment:
    """
    FIX 3: Rebuilt risk scoring model.

    Scoring rules (additive, base = 0):
      - Confirmed repeated identical frames: +30 each (up to 60)
      - Tab-switching loop detected: +25
      - Zero real productive activity across session: +15
      - Monitor configuration change mid-day: +10
      - Unauthorized site access: +15
      - Third-party account found: +20
      - Very long sessions with no work evidence: +10 each (up to 30)
      - High non-work screenshots: +15
      - Large activity gap (timesheet vs screenshots): +15
      - Contradictions between sources: +10 each (up to 30)
      - Very low overall activity: +10
      - High suspicious hours percentage: +15

    Risk levels:
      - confirmed_fraud: >= 80
      - high_risk: 60-79
      - needs_review: 40-59
      - low_risk: < 40
      - invalid_bundle: data is fundamentally broken

    Confidence capped at 0.4 if zero screenshots analyzed.
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
            )

    # ── Rule-based risk scoring ──────────────────────────────────────────
    risk_score = 0.0
    findings: list[str] = []

    # --- Screenshot-derived signals (highest weight) ---
    if screenshot_analysis:
        # FIX 2: Repeated identical frames — strongest fraud signal
        n_repeated = len(screenshot_analysis.repeated_frames)
        if n_repeated > 0:
            pts = min(60, n_repeated * 30)
            risk_score += pts
            findings.append(
                f"CRITICAL: {n_repeated} repeated identical frame pair(s) detected "
                f"(+{pts} points) — same frozen screen reappearing across time gaps"
            )

        # FIX 4: Tab-switching loop
        if screenshot_analysis.tab_switching_analysis and screenshot_analysis.tab_switching_analysis.loop_detected:
            risk_score += 25
            findings.append(
                f"Tab-switching loop detected ({screenshot_analysis.tab_switching_analysis.loop_count} loops) "
                f"— mechanical behavior suggesting automated simulation (+25 points)"
            )

        # FIX 6: Monitor configuration change
        if screenshot_analysis.monitor_inconsistencies:
            risk_score += 10
            findings.append(
                f"Monitor configuration changed on {len(screenshot_analysis.monitor_inconsistencies)} day(s) "
                f"— may indicate different person at screen (+10 points)"
            )

        # FIX 7: Unauthorized site access
        if screenshot_analysis.unauthorized_access_events:
            risk_score += 15
            findings.append(
                f"{len(screenshot_analysis.unauthorized_access_events)} unauthorized site access event(s) "
                f"detected (+15 points)"
            )

        # FIX 8: Third-party account
        if screenshot_analysis.third_party_accounts:
            risk_score += 20
            findings.append(
                f"SECURITY: {len(screenshot_analysis.third_party_accounts)} third-party account(s) "
                f"found logged in on screen (+20 points)"
            )

        # Zero real productive work across all screenshots
        if screenshot_analysis.total_analyzed > 0 and screenshot_analysis.work_count == 0:
            risk_score += 15
            findings.append(
                f"Zero work screenshots out of {screenshot_analysis.total_analyzed} analyzed "
                f"— no productive activity detected (+15 points)"
            )

        # High non-work screenshots
        if screenshot_analysis.non_work_pct > 30:
            risk_score += 15
            findings.append(f"High non-work screenshots: {screenshot_analysis.non_work_pct:.0f}% (+15 points)")
        elif screenshot_analysis.non_work_pct > 15:
            risk_score += 8

        # FIX 10: Suspicious sites
        if screenshot_analysis.suspicious_sites:
            risk_score += min(10, len(screenshot_analysis.suspicious_sites) * 5)
            findings.append(
                f"{len(screenshot_analysis.suspicious_sites)} suspicious site(s) detected on screen"
            )

    # --- Cross-analysis signals ---
    if cross_analysis:
        # Activity gap
        if cross_analysis.activity_gap > 30:
            risk_score += 15
            findings.append(f"Large activity gap: {cross_analysis.activity_gap:.0f}pp between timesheet and screenshots (+15 points)")
        elif cross_analysis.activity_gap > 15:
            risk_score += 8

        # Contradictions
        n_contradictions = len(cross_analysis.contradictions)
        if n_contradictions > 0:
            pts = min(30, n_contradictions * 10)
            risk_score += pts
            findings.append(f"{n_contradictions} contradiction(s) between data sources (+{pts} points)")

    # --- Timesheet-only signals ---
    if timesheet_analysis:
        # Very long sessions with no screenshot work evidence
        if timesheet_analysis.very_long_sessions > 0:
            no_work_evidence = (
                screenshot_analysis is None
                or screenshot_analysis.total_analyzed == 0
                or screenshot_analysis.work_pct < 20
            )
            if no_work_evidence:
                pts = min(30, timesheet_analysis.very_long_sessions * 10)
                risk_score += pts
                findings.append(
                    f"{timesheet_analysis.very_long_sessions} very long session(s) (>6h) "
                    f"with no/low screenshot work evidence (+{pts} points)"
                )

        # Very low overall activity
        if timesheet_analysis.overall_activity_pct < 40:
            risk_score += 10
            findings.append(f"Very low overall activity: {timesheet_analysis.overall_activity_pct:.0f}% (+10 points)")

        # FIX 5: High suspicious hours percentage
        if timesheet_analysis.suspicious_pct > 40:
            risk_score += 15
            findings.append(
                f"Suspicious hours: {timesheet_analysis.suspicious_hours_total} "
                f"({timesheet_analysis.suspicious_pct:.0f}% of total) (+15 points)"
            )
        elif timesheet_analysis.suspicious_pct > 20:
            risk_score += 8

        # Anomaly severity weighting
        high_sev = sum(1 for a in timesheet_analysis.anomalies if a.severity == "high")
        med_sev = sum(1 for a in timesheet_analysis.anomalies if a.severity == "medium")
        anomaly_pts = min(15, high_sev * 5 + med_sev * 2)
        if anomaly_pts > 0:
            risk_score += anomaly_pts
            findings.append(f"{len(timesheet_analysis.anomalies)} timesheet anomalies (+{anomaly_pts} points)")

    risk_score = min(100.0, max(0.0, risk_score))

    # ── Determine risk level from score ──────────────────────────────────
    if risk_score >= 80:
        risk_level = RiskLevel.CONFIRMED_FRAUD
    elif risk_score >= 60:
        risk_level = RiskLevel.HIGH_RISK
    elif risk_score >= 40:
        risk_level = RiskLevel.NEEDS_REVIEW
    else:
        risk_level = RiskLevel.LOW_RISK

    # ── Confidence calculation ───────────────────────────────────────────
    # Confidence reflects how much data supports the assessment
    screenshots_analyzed = screenshot_analysis.total_analyzed if screenshot_analysis else 0
    has_timesheet = timesheet_analysis is not None
    has_cross = cross_analysis is not None

    base_confidence = 0.3
    if has_timesheet:
        base_confidence += 0.2
    if screenshots_analyzed > 0:
        base_confidence += min(0.3, screenshots_analyzed * 0.03)  # Up to +0.3 for 10+ screenshots
    if has_cross:
        base_confidence += 0.1
    if len(findings) > 3:
        base_confidence += 0.1  # More evidence = more confidence

    # CRITICAL: Cap at 0.4 if zero screenshots analyzed
    if screenshots_analyzed == 0:
        base_confidence = min(0.4, base_confidence)

    confidence = min(1.0, max(0.0, base_confidence))

    # ── LLM reasoning ────────────────────────────────────────────────────
    if llm is None:
        llm = get_llm(temperature=0.0, max_tokens=4000)

    # Build rich timesheet payload
    ts_payload = {}
    if timesheet_analysis:
        anomaly_list = [
            {"date": a.session_date, "type": a.anomaly_type, "desc": a.description, "severity": a.severity}
            for a in timesheet_analysis.anomalies
        ]
        ts_payload = {
            "total_sessions": timesheet_analysis.total_sessions,
            "total_hours": timesheet_analysis.total_duration_hours,
            "active_hours": timesheet_analysis.total_active_hours,
            "overall_activity_pct": timesheet_analysis.overall_activity_pct,
            "avg_session_min": timesheet_analysis.avg_session_duration_min,
            "avg_activity_pct": timesheet_analysis.avg_activity_pct,
            "min_activity_pct": timesheet_analysis.min_activity_pct,
            "max_activity_pct": timesheet_analysis.max_activity_pct,
            "sessions_below_50_pct": timesheet_analysis.sessions_below_50_pct,
            "very_short_sessions": timesheet_analysis.very_short_sessions,
            "very_long_sessions": timesheet_analysis.very_long_sessions,
            "daily_breakdown": timesheet_analysis.daily_breakdown,
            "anomalies": anomaly_list,
            "suspicious_hours_total": timesheet_analysis.suspicious_hours_total,
            "suspicious_pct": timesheet_analysis.suspicious_pct,
            "llm_reasoning": timesheet_analysis.reasoning,
        }

    # Build rich screenshot payload — include advanced analysis results
    ss_payload = {"summary": "No screenshots analyzed"}
    if screenshot_analysis:
        top_classifications = [
            {"ts": c.timestamp, "cat": c.category.value, "conf": c.confidence, "desc": c.description}
            for c in screenshot_analysis.classifications[:30]
        ]
        repeated_summary = [
            {"first": r.first_occurrence, "repeat": r.repeat_occurrence,
             "gap_min": r.time_gap_minutes, "similarity": r.similarity_score}
            for r in screenshot_analysis.repeated_frames
        ]
        ss_payload = {
            "total_analyzed": screenshot_analysis.total_analyzed,
            "work_count": screenshot_analysis.work_count,
            "work_pct": screenshot_analysis.work_pct,
            "non_work_count": screenshot_analysis.non_work_count,
            "non_work_pct": screenshot_analysis.non_work_pct,
            "idle_count": screenshot_analysis.idle_count,
            "idle_pct": screenshot_analysis.idle_pct,
            "uncertain_count": screenshot_analysis.uncertain_count,
            "summary": screenshot_analysis.summary,
            "sample_classifications": top_classifications,
            "repeated_frames": repeated_summary,
            "tab_switching": screenshot_analysis.tab_switching_analysis.model_dump() if screenshot_analysis.tab_switching_analysis else None,
            "monitor_inconsistencies": len(screenshot_analysis.monitor_inconsistencies),
            "unauthorized_access_events": len(screenshot_analysis.unauthorized_access_events),
            "third_party_accounts": len(screenshot_analysis.third_party_accounts),
            "suspicious_sites": [s.site_name for s in screenshot_analysis.suspicious_sites],
        }

    # Build cross-analysis payload
    ca_payload = {}
    if cross_analysis:
        ca_payload = {
            "contradictions": cross_analysis.contradictions,
            "consistencies": cross_analysis.consistencies,
            "screenshot_work_pct": cross_analysis.screenshot_work_pct,
            "timesheet_activity_pct": cross_analysis.timesheet_activity_pct,
            "activity_gap": cross_analysis.activity_gap,
            "reasoning": cross_analysis.reasoning,
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
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        data = json.loads(text)

        return FinalRiskAssessment(
            risk_score=risk_score,
            risk_level=risk_level,
            confidence=confidence,
            reasoning=data.get("reasoning", ""),
            key_findings=data.get("key_findings", findings),
            facts=data.get("facts", []),
            interpretations=data.get("interpretations", []),
            fraud_assessment=data.get("fraud_assessment", ""),
        )

    except Exception as e:
        logger.error(f"LLM risk assessment failed: {e}")
        return FinalRiskAssessment(
            risk_score=risk_score,
            risk_level=risk_level,
            confidence=confidence,
            reasoning=f"Rule-based assessment (LLM unavailable: {e}). Score driven by: {'; '.join(findings)}",
            key_findings=findings,
            facts=findings,
            interpretations=["LLM reasoning unavailable — assessment based on rules only."],
        )
