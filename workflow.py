"""
LangGraph orchestrator for the employee work audit system.
Defines nodes, edges, and the complete workflow graph.
"""

from __future__ import annotations

import gc
import logging
import uuid
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from screenshot_analysis import analyze_screenshots
from timesheet_analysis import analyze_timesheet
from evidence_fusion import fuse_evidence, generate_risk_assessment
from screenshot_parser import parse_screenshot_pdf
from sql_parser import parse_timesheet
from models import (
    AuditReport,
    EvidenceBundle,
    FinalRiskAssessment,
    RiskLevel,
    ValidationResult,
)

logger = logging.getLogger(__name__)


# ── State Definition ─────────────────────────────────────────────────────────

class AuditState(TypedDict, total=False):
    """TypedDict state for LangGraph. Mirrors GraphState but as a dict."""
    timesheet_path: str
    screenshot_path: str
    evidence_bundle: dict | None
    _full_bundle: Any  # Keeps parsed ScreenshotReport with image bytes for reuse
    validation_result: dict | None
    timesheet_analysis: dict | None
    screenshot_analysis: dict | None
    cross_analysis: dict | None
    risk_assessment: dict | None
    audit_report: dict | None
    error: str | None
    should_abort: bool


# ── Node Functions ───────────────────────────────────────────────────────────

def node_parse_inputs(state: AuditState) -> dict:
    """
    NODE 1: Parse both input files and create the EvidenceBundle.
    """
    logger.info("=" * 60)
    logger.info("NODE: parse_inputs")
    logger.info("=" * 60)

    timesheet_path = state.get("timesheet_path", "")
    screenshot_path = state.get("screenshot_path", "")

    timesheet_data = None
    screenshot_data = None
    errors: list[str] = []

    # Parse timesheet
    if timesheet_path:
        try:
            timesheet_data = parse_timesheet(timesheet_path)
            logger.info(
                f"Parsed timesheet: {timesheet_data.employee}, "
                f"{len(timesheet_data.sessions)} sessions"
            )
        except Exception as e:
            errors.append(f"Timesheet parse error: {e}")
            logger.error(f"Failed to parse timesheet: {e}")
    else:
        errors.append("No timesheet file provided.")

    # Parse screenshot PDF
    if screenshot_path:
        try:
            screenshot_data = parse_screenshot_pdf(screenshot_path)
            logger.info(
                f"Parsed screenshots: {screenshot_data.employee}, "
                f"{screenshot_data.total_screenshots} screenshots"
            )
        except Exception as e:
            errors.append(f"Screenshot parse error: {e}")
            logger.error(f"Failed to parse screenshots: {e}")
    else:
        errors.append("No screenshot file provided.")

    bundle = EvidenceBundle(
        timesheet=timesheet_data,
        screenshot_report=screenshot_data,
        bundle_id=str(uuid.uuid4())[:8],
        created_at=datetime.now(),
    )

    if errors and not timesheet_data and not screenshot_data:
        return {
            "evidence_bundle": bundle.model_dump(exclude={"screenshot_report": {"entries": {"__all__": {"image_bytes"}}}}),
            "error": "; ".join(errors),
            "should_abort": True,
        }

    return {
        "evidence_bundle": bundle.model_dump(exclude={"screenshot_report": {"entries": {"__all__": {"image_bytes"}}}}),
        # Keep the full bundle in a side channel for screenshot analysis
        "_full_bundle": bundle,
    }


def node_validate(state: AuditState) -> dict:
    """
    NODE 2: Validation gate — check data consistency.
    """
    logger.info("=" * 60)
    logger.info("NODE: validate")
    logger.info("=" * 60)

    bundle_data = state.get("evidence_bundle")
    if not bundle_data:
        return {
            "validation_result": ValidationResult(
                is_valid=False,
                errors=["No evidence bundle to validate."],
            ).model_dump(),
            "should_abort": True,
        }

    errors: list[str] = []
    warnings: list[str] = []
    employee_match = None
    date_overlap = None

    ts = bundle_data.get("timesheet")
    ss = bundle_data.get("screenshot_report")

    # Check data completeness
    if not ts:
        errors.append("Timesheet data is missing.")
    if not ss:
        warnings.append("Screenshot data is missing — analysis will be partial.")

    if ts and ss:
        # Date overlap check — both files must cover the same time period
        ts_start = ts.get("date_range_start", "")
        ts_end = ts.get("date_range_end", "")
        ss_start = ss.get("date_range_start", "")
        ss_end = ss.get("date_range_end", "")

        if ts_start and ss_start:
            from dateutil import parser as dateparser

            try:
                ts_s = dateparser.parse(ts_start)
                ts_e = dateparser.parse(ts_end) if ts_end else ts_s
                ss_s = dateparser.parse(ss_start)
                ss_e = dateparser.parse(ss_end) if ss_end else ss_s

                # Ranges overlap if one starts before the other ends
                if ts_s <= ss_e and ss_s <= ts_e:
                    date_overlap = True
                else:
                    date_overlap = False
                    errors.append(
                        f"Date mismatch: the timesheet covers {ts_start} – {ts_end} "
                        f"but the screenshots cover {ss_start} – {ss_end}. "
                        f"Please upload files from the same time period."
                    )
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not parse dates for overlap check: {e}")
                warnings.append("Could not verify date overlap — dates could not be parsed.")

        # Timezone info
        timezone_info = ts.get("timezone", "Unknown")
    else:
        timezone_info = ""

    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning(f"Validation failed: {errors}")

    result = ValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        employee_match=employee_match,
        date_overlap=date_overlap,
        timezone_info=timezone_info,
    )

    logger.info(
        f"Validation: valid={result.is_valid}, "
        f"employee_match={result.employee_match}, "
        f"date_overlap={result.date_overlap}, "
        f"warnings={len(result.warnings)}"
    )

    if not is_valid:
        return {"validation_result": result.model_dump(), "should_abort": True}

    return {"validation_result": result.model_dump()}


def node_analyze_timesheet(state: AuditState) -> dict:
    """
    NODE 3a: Statistical analysis of timesheet data.
    """
    logger.info("=" * 60)
    logger.info("NODE: analyze_timesheet")
    logger.info("=" * 60)

    bundle_data = state.get("evidence_bundle")
    if not bundle_data or not bundle_data.get("timesheet"):
        logger.warning("No timesheet data to analyze.")
        return {"timesheet_analysis": None}

    from models import TimesheetData
    ts_data = TimesheetData.model_validate(bundle_data["timesheet"])
    result = analyze_timesheet(ts_data)

    return {"timesheet_analysis": result.model_dump()}


def node_analyze_screenshots(state: AuditState) -> dict:
    """
    NODE 3b: LLM-based screenshot classification.
    """
    logger.info("=" * 60)
    logger.info("NODE: analyze_screenshots")
    logger.info("=" * 60)

    # Try to reuse already-parsed bundle to avoid expensive re-parse
    full_bundle = state.get("_full_bundle")
    screenshot_path = state.get("screenshot_path", "")

    if not screenshot_path:
        logger.warning("No screenshot path — skipping screenshot analysis.")
        return {"screenshot_analysis": None}

    try:
        if full_bundle and hasattr(full_bundle, "screenshot_report") and full_bundle.screenshot_report:
            logger.info("Reusing already-parsed screenshot data (skipping re-parse).")
            report = full_bundle.screenshot_report
        else:
            logger.info("No cached bundle — parsing screenshot PDF.")
            report = parse_screenshot_pdf(screenshot_path)

        result = analyze_screenshots(report)

        # Free the full bundle — image bytes are no longer needed
        del report
        return {"screenshot_analysis": result.model_dump(), "_full_bundle": None}
    except Exception as e:
        logger.error(f"Screenshot analysis failed: {e}")
        return {"screenshot_analysis": None, "_full_bundle": None}


def node_cross_analyze(state: AuditState) -> dict:
    """
    NODE 4: Cross-reference screenshots vs timesheet.
    """
    logger.info("=" * 60)
    logger.info("NODE: cross_analyze")
    logger.info("=" * 60)

    from models import (
        CrossAnalysisResult,
        ScreenshotAnalysisResult,
        TimesheetAnalysisResult,
    )

    ts_data = state.get("timesheet_analysis")
    ss_data = state.get("screenshot_analysis")
    val_data = state.get("validation_result")

    if not ts_data or not ss_data:
        logger.warning("Missing data for cross-analysis — skipping.")
        return {"cross_analysis": None}

    ts_result = TimesheetAnalysisResult.model_validate(ts_data)
    ss_result = ScreenshotAnalysisResult.model_validate(ss_data)
    val_result = ValidationResult.model_validate(val_data) if val_data else ValidationResult(is_valid=True)

    result = fuse_evidence(ts_result, ss_result, val_result)
    return {"cross_analysis": result.model_dump()}


def node_risk_scoring(state: AuditState) -> dict:
    """
    NODE 5: Generate final risk assessment.
    """
    logger.info("=" * 60)
    logger.info("NODE: risk_scoring")
    logger.info("=" * 60)

    from models import (
        CrossAnalysisResult,
        ScreenshotAnalysisResult,
        TimesheetAnalysisResult,
    )

    val_data = state.get("validation_result")
    ts_data = state.get("timesheet_analysis")
    ss_data = state.get("screenshot_analysis")
    ca_data = state.get("cross_analysis")

    validation = ValidationResult.model_validate(val_data) if val_data else ValidationResult(is_valid=True)
    ts_result = TimesheetAnalysisResult.model_validate(ts_data) if ts_data else None
    ss_result = ScreenshotAnalysisResult.model_validate(ss_data) if ss_data else None
    ca_result = CrossAnalysisResult.model_validate(ca_data) if ca_data else None

    risk = generate_risk_assessment(validation, ts_result, ss_result, ca_result)
    return {"risk_assessment": risk.model_dump()}


def _generate_work_summary(
    employee: str,
    date_range: str,
    timesheet_analysis,
    screenshot_analysis,
    cross_analysis,
    risk: FinalRiskAssessment,
) -> str:
    """Generate a ~100 word neutral work summary using the LLM."""
    from prompts import WORK_SUMMARY_PROMPT
    from langchain_core.messages import HumanMessage, SystemMessage
    from config import get_settings

    settings = get_settings()
    if not settings.openai_api_key:
        return ""

    # Build timesheet summary text
    ts_text = "No timesheet data."
    if timesheet_analysis:
        ts_text = (
            f"Total sessions: {timesheet_analysis.total_sessions}, "
            f"Total hours: {timesheet_analysis.total_duration_hours:.1f}h, "
            f"Active hours: {timesheet_analysis.total_active_hours:.1f}h, "
            f"Overall activity: {timesheet_analysis.overall_activity_pct:.1f}%, "
            f"Avg session: {timesheet_analysis.avg_session_duration_min:.0f} min, "
            f"Sessions below 50% activity: {timesheet_analysis.sessions_below_50_pct}, "
            f"Very short sessions (<5 min): {timesheet_analysis.very_short_sessions}, "
            f"Very long sessions (>6h): {timesheet_analysis.very_long_sessions}, "
            f"Daily breakdown: {timesheet_analysis.daily_breakdown}"
        )

    # Build screenshot summary text
    ss_text = "No screenshot data."
    if screenshot_analysis:
        ss_text = (
            f"Total analyzed: {screenshot_analysis.total_analyzed}, "
            f"Work: {screenshot_analysis.work_count} ({screenshot_analysis.work_pct:.0f}%), "
            f"Non-work: {screenshot_analysis.non_work_count} ({screenshot_analysis.non_work_pct:.0f}%), "
            f"Idle: {screenshot_analysis.idle_count} ({screenshot_analysis.idle_pct:.0f}%), "
            f"Uncertain: {screenshot_analysis.uncertain_count}"
        )

    # Build cross-analysis summary
    ca_text = "No cross-analysis (single source only)."
    if cross_analysis:
        ca_text = (
            f"Activity gap: {cross_analysis.activity_gap:.1f}pp, "
            f"Contradictions: {cross_analysis.contradictions}, "
            f"Consistencies: {cross_analysis.consistencies}"
        )

    # Build risk summary
    risk_text = (
        f"Score: {risk.risk_score:.0f}/100, "
        f"Level: {risk.risk_level.value}, "
        f"Confidence: {risk.confidence:.0%}"
    )

    prompt = WORK_SUMMARY_PROMPT.format(
        employee=employee,
        date_range=date_range,
        timesheet_summary=ts_text,
        screenshot_summary=ss_text,
        cross_summary=ca_text,
        risk_summary=risk_text,
    )

    try:
        from config import get_llm
        llm = get_llm(temperature=0.2, max_tokens=300)
        response = llm.invoke([
            SystemMessage(content="You are a neutral work reporter. Respond with plain text only."),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        del response
        return text
    except Exception as e:
        logger.error(f"Work summary generation failed: {e}")
        return ""


def node_generate_report(state: AuditState) -> dict:
    """
    NODE 6: Assemble the final AuditReport.
    """
    logger.info("=" * 60)
    logger.info("NODE: generate_report")
    logger.info("=" * 60)

    bundle_data = state.get("evidence_bundle", {})
    ts = bundle_data.get("timesheet", {}) if bundle_data else {}
    ss = bundle_data.get("screenshot_report", {}) if bundle_data else {}

    employee = ts.get("employee", "") or ss.get("employee", "") or "Unknown"
    date_range = ""
    if ts:
        date_range = f"{ts.get('date_range_start', '')} to {ts.get('date_range_end', '')}"
    elif ss:
        date_range = f"{ss.get('date_range_start', '')} to {ss.get('date_range_end', '')}"

    from models import (
        CrossAnalysisResult,
        ScreenshotAnalysisResult,
        TimesheetAnalysisResult,
    )

    risk_data = state.get("risk_assessment", {})
    risk = FinalRiskAssessment.model_validate(risk_data) if risk_data else FinalRiskAssessment(
        risk_score=0, risk_level=RiskLevel.INVALID_BUNDLE, confidence=0,
        reasoning="No risk assessment generated."
    )

    ts_result = TimesheetAnalysisResult.model_validate(state["timesheet_analysis"]) if state.get("timesheet_analysis") else None
    ss_result = ScreenshotAnalysisResult.model_validate(state["screenshot_analysis"]) if state.get("screenshot_analysis") else None
    ca_result = CrossAnalysisResult.model_validate(state["cross_analysis"]) if state.get("cross_analysis") else None

    # Generate the ~100 word work summary
    work_summary = _generate_work_summary(
        employee=employee,
        date_range=date_range,
        timesheet_analysis=ts_result,
        screenshot_analysis=ss_result,
        cross_analysis=ca_result,
        risk=risk,
    )

    report = AuditReport(
        bundle_id=bundle_data.get("bundle_id", "unknown") if bundle_data else "unknown",
        employee=employee,
        date_range=date_range,
        validation=ValidationResult.model_validate(state.get("validation_result", {"is_valid": False})),
        timesheet_analysis=ts_result,
        screenshot_analysis=ss_result,
        cross_analysis=ca_result,
        risk_assessment=risk,
        work_summary=work_summary,
    )

    logger.info(f"\n{report.to_summary()}")

    return {"audit_report": report.model_dump(mode="json")}


# ── Edge Conditions ──────────────────────────────────────────────────────────

def should_abort(state: AuditState) -> str:
    """Check if we should abort."""
    if state.get("should_abort"):
        return "abort"
    return "continue"


def should_abort_after_validate(state: AuditState) -> list[str]:
    """After validation, abort or fan out to both analyses."""
    if state.get("should_abort"):
        return ["generate_report"]
    return ["analyze_timesheet", "analyze_screenshots"]


def should_skip_screenshots(state: AuditState) -> str:
    """Check if screenshot analysis should be skipped."""
    if not state.get("screenshot_path"):
        return "skip"
    return "analyze"


# ── Graph Builder ────────────────────────────────────────────────────────────

def build_audit_graph() -> StateGraph:
    """
    Build the LangGraph workflow for the audit system.

    Graph structure:
        parse_inputs
            │
            ├─ [abort] → generate_report (with error) → END
            │
            └─ [continue] → validate
                                │
                                ├── analyze_timesheet ──┐
                                │                       │
                                ├── analyze_screenshots ┤
                                │                       │
                                └───────────────────────┘
                                            │
                                      cross_analyze
                                            │
                                      risk_scoring
                                            │
                                      generate_report
                                            │
                                           END
    """
    graph = StateGraph(AuditState)

    # Add nodes
    graph.add_node("parse_inputs", node_parse_inputs)
    graph.add_node("validate", node_validate)
    graph.add_node("analyze_timesheet", node_analyze_timesheet)
    graph.add_node("analyze_screenshots", node_analyze_screenshots)
    graph.add_node("cross_analyze", node_cross_analyze)
    graph.add_node("risk_scoring", node_risk_scoring)
    graph.add_node("generate_report", node_generate_report)

    # Set entry point
    graph.set_entry_point("parse_inputs")

    # Edges from parse_inputs
    graph.add_conditional_edges(
        "parse_inputs",
        should_abort,
        {
            "abort": "generate_report",
            "continue": "validate",
        },
    )

    # After validation, abort if dates don't match, otherwise run both analyses
    graph.add_conditional_edges(
        "validate",
        should_abort_after_validate,
        ["analyze_timesheet", "analyze_screenshots", "generate_report"],
    )

    # Both analyses feed into cross-analysis
    graph.add_edge("analyze_timesheet", "cross_analyze")
    graph.add_edge("analyze_screenshots", "cross_analyze")

    # Cross-analysis → risk scoring → report
    graph.add_edge("cross_analyze", "risk_scoring")
    graph.add_edge("risk_scoring", "generate_report")
    graph.add_edge("generate_report", END)

    return graph


_NODE_PROGRESS = {
    "parse_inputs":         (10, "Parsing input files..."),
    "validate":             (20, "Validating data consistency..."),
    "analyze_timesheet":    (35, "Analyzing timesheet patterns..."),
    "analyze_screenshots":  (55, "Classifying screenshots (this may take a moment)..."),
    "cross_analyze":        (75, "Cross-referencing evidence..."),
    "risk_scoring":         (85, "Calculating risk score..."),
    "generate_report":      (95, "Generating report..."),
}


def run_audit(
    timesheet_path: str = "",
    screenshot_path: str = "",
    progress_callback=None,
) -> AuditReport:
    """
    Execute the full audit workflow.
    Returns the complete AuditReport.
    If progress_callback(pct, msg) is provided, it is called as each node starts.
    """
    graph = build_audit_graph()
    app = graph.compile()

    initial_state: AuditState = {
        "timesheet_path": timesheet_path,
        "screenshot_path": screenshot_path,
        "should_abort": False,
    }

    logger.info("Starting audit workflow...")
    logger.info(f"  Timesheet: {timesheet_path}")
    logger.info(f"  Screenshots: {screenshot_path}")

    # Nodes that should trigger a gc.collect() after completion
    _heavy_nodes = {"parse_inputs", "analyze_screenshots", "analyze_timesheet"}

    final_state = None
    for event in app.stream(initial_state, stream_mode="updates"):
        for node_name in event:
            if progress_callback and node_name in _NODE_PROGRESS:
                pct, msg = _NODE_PROGRESS[node_name]
                progress_callback(pct, msg)
            # Force GC after memory-heavy nodes
            if node_name in _heavy_nodes:
                gc.collect()
        final_state = final_state or {}
        for node_name, updates in event.items():
            if isinstance(updates, dict):
                final_state.update(updates)

    if progress_callback:
        progress_callback(100, "Complete!")

    # Close the shared LLM client and force final GC
    from config import close_llm
    close_llm()
    gc.collect()

    report_data = final_state.get("audit_report") if final_state else None
    if report_data:
        return AuditReport.model_validate(report_data)

    # Fallback if report generation somehow failed
    return AuditReport(
        bundle_id="error",
        employee="Unknown",
        date_range="Unknown",
        validation=ValidationResult(is_valid=False, errors=["Workflow did not produce a report."]),
        risk_assessment=FinalRiskAssessment(
            risk_score=0,
            risk_level=RiskLevel.INVALID_BUNDLE,
            confidence=0,
            reasoning="Workflow failed to produce a report.",
        ),
    )
