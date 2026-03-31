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
            SystemMessage(content="You are a cross-referencing analyst. Respond with valid JSON only."),
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
    Generate the final risk assessment using rule-based scoring + LLM reasoning.
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

    # Rule-based risk scoring
    risk_score = 0.0
    findings: list[str] = []

    if cross_analysis:
        # Activity gap contributes to risk
        if cross_analysis.activity_gap > 30:
            risk_score += 25
            findings.append(f"Large activity gap: {cross_analysis.activity_gap:.0f}pp")
        elif cross_analysis.activity_gap > 15:
            risk_score += 10

        # Contradictions
        risk_score += min(30, len(cross_analysis.contradictions) * 10)
        if cross_analysis.contradictions:
            findings.append(f"{len(cross_analysis.contradictions)} contradiction(s) found")

    if timesheet_analysis:
        # Anomalies
        high_severity = [a for a in timesheet_analysis.anomalies if a.severity == "high"]
        med_severity = [a for a in timesheet_analysis.anomalies if a.severity == "medium"]
        low_severity = [a for a in timesheet_analysis.anomalies if a.severity == "low"]
        risk_score += len(high_severity) * 8 + len(med_severity) * 3 + len(low_severity) * 1
        if timesheet_analysis.anomalies:
            findings.append(f"{len(timesheet_analysis.anomalies)} timesheet anomalies")

        # Very low activity
        if timesheet_analysis.overall_activity_pct < 40:
            risk_score += 15
            findings.append(f"Very low activity: {timesheet_analysis.overall_activity_pct:.0f}%")

    if screenshot_analysis:
        # High non-work
        if screenshot_analysis.non_work_pct > 30:
            risk_score += 20
            findings.append(f"High non-work screenshots: {screenshot_analysis.non_work_pct:.0f}%")
        elif screenshot_analysis.non_work_pct > 15:
            risk_score += 10

    risk_score = min(100.0, max(0.0, risk_score))

    # Determine risk level from score
    if risk_score >= 60:
        risk_level = RiskLevel.HIGH_RISK
    elif risk_score >= 25:
        risk_level = RiskLevel.NEEDS_REVIEW
    else:
        risk_level = RiskLevel.VALID_WORK

    # LLM reasoning for the final assessment — send FULL data
    if llm is None:
        llm = get_llm(temperature=0.0, max_tokens=2500)

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
            "llm_reasoning": timesheet_analysis.reasoning,
        }

    # Build rich screenshot payload
    ss_payload = {"summary": "No screenshots analyzed"}
    if screenshot_analysis:
        top_classifications = [
            {"ts": c.timestamp, "cat": c.category.value, "conf": c.confidence, "desc": c.description}
            for c in screenshot_analysis.classifications[:30]
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
            SystemMessage(content="You are a risk assessment expert. Respond with valid JSON only."),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        del response  # Free full response object
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        data = json.loads(text)

        return FinalRiskAssessment(
            risk_score=risk_score,  # Use our rule-based score, not LLM's
            risk_level=risk_level,  # Use our rule-based level
            confidence=min(1.0, max(0.0, float(data.get("confidence", 0.5)))),
            reasoning=data.get("reasoning", ""),
            key_findings=data.get("key_findings", findings),
            facts=data.get("facts", []),
            interpretations=data.get("interpretations", []),
        )

    except Exception as e:
        logger.error(f"LLM risk assessment failed: {e}")
        return FinalRiskAssessment(
            risk_score=risk_score,
            risk_level=risk_level,
            confidence=0.4,
            reasoning=f"Rule-based assessment (LLM unavailable: {e}). Score driven by: {'; '.join(findings)}",
            key_findings=findings,
            facts=findings,
            interpretations=["LLM reasoning unavailable — assessment based on rules only."],
        )
