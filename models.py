"""
Pydantic models for the employee work audit system.
All data flows through these typed schemas.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    VALID_WORK = "valid_work"
    LOW_RISK = "low_risk"
    NEEDS_REVIEW = "needs_review"
    HIGH_RISK = "high_risk"
    CONFIRMED_FRAUD = "confirmed_fraud"
    INVALID_BUNDLE = "invalid_bundle"


class ScreenshotCategory(str, Enum):
    WORK = "work"
    NON_WORK = "non_work"
    IDLE = "idle"
    UNCERTAIN = "uncertain"


# ── Timesheet Models ──────────────────────────────────────────────────────────

class WorkSession(BaseModel):
    """A single work session from the timesheet."""
    project: str
    employee: str
    task: str
    date_start: str
    date_end: str
    time_start: str
    time_end: str
    session_type: str
    duration: timedelta
    active_time: timedelta
    activity_pct: float
    cost: Optional[float] = None

    @property
    def duration_minutes(self) -> float:
        return self.duration.total_seconds() / 60

    @property
    def active_minutes(self) -> float:
        return self.active_time.total_seconds() / 60

    @property
    def idle_minutes(self) -> float:
        return self.duration_minutes - self.active_minutes


class TimesheetData(BaseModel):
    """Parsed timesheet containing all work sessions."""
    employee: str
    date_range_start: str
    date_range_end: str
    timezone: str
    sessions: list[WorkSession]
    total_duration: timedelta
    total_active: timedelta
    avg_activity_pct: float


# ── Screenshot Models ─────────────────────────────────────────────────────────

class ScreenshotEntry(BaseModel):
    """A single screenshot with its timestamp and image data."""
    timestamp: datetime
    page_number: int
    image_index: int
    image_bytes: Optional[bytes] = Field(default=None, exclude=True, repr=False)
    width: int = 0
    height: int = 0


class ScreenshotReport(BaseModel):
    """Parsed screenshot report."""
    employee: str
    date_range_start: str
    date_range_end: str
    total_screenshots: int
    work_session_count: int
    entries: list[ScreenshotEntry]


# ── Evidence Bundle ───────────────────────────────────────────────────────────

class EvidenceBundle(BaseModel):
    """Unified container for all evidence to be analyzed."""
    timesheet: Optional[TimesheetData] = None
    screenshot_report: Optional[ScreenshotReport] = None
    bundle_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


# ── Validation ────────────────────────────────────────────────────────────────

class ValidationResult(BaseModel):
    """Result of the validation gate."""
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    employee_match: Optional[bool] = None
    date_overlap: Optional[bool] = None
    timezone_info: str = ""


# ── Screenshot Analysis ──────────────────────────────────────────────────────

class ScreenshotClassification(BaseModel):
    """LLM classification of a single screenshot."""
    timestamp: str
    category: ScreenshotCategory
    confidence: float = Field(ge=0.0, le=1.0)
    description: str
    applications_visible: list[str] = Field(default_factory=list)
    reasoning: str
    image_b64: str = Field(default="", repr=False)


class RepeatedFrame(BaseModel):
    """Two screenshots that are near-identical but far apart in time."""
    first_occurrence: str
    repeat_occurrence: str
    time_gap_minutes: float
    similarity_score: float
    visible_content: str = ""


class TabSwitchingAnalysis(BaseModel):
    """Result of mechanical tab-switching loop detection."""
    loop_detected: bool = False
    loop_count: int = 0
    max_tabs_visible: int = 0
    tab_sequence: list[str] = Field(default_factory=list)
    sessions_affected: list[str] = Field(default_factory=list)


class UnauthorizedAccessEvent(BaseModel):
    """A domain accessed that is NOT in the assigned tasks list."""
    timestamp: str
    domain: str
    page_title: str = ""


class ThirdPartyAccount(BaseModel):
    """An email address on screen that does not match the employee."""
    timestamp: str
    email_found: str
    expected_email: str = ""
    severity: str = "critical"


class SuspiciousSite(BaseModel):
    """A site from the blocklist detected on screen."""
    timestamp: str
    site_name: str
    category: str = ""
    reason: str = ""


class MonitorInconsistency(BaseModel):
    """Day where both single and dual-monitor screenshots appear."""
    date: str
    single_monitor_count: int = 0
    dual_monitor_count: int = 0
    timestamps_single: list[str] = Field(default_factory=list)
    timestamps_dual: list[str] = Field(default_factory=list)
    severity: str = "high"


class ScreenshotAnalysisResult(BaseModel):
    """Aggregate result of all screenshot analysis."""
    total_analyzed: int
    work_count: int = 0
    non_work_count: int = 0
    idle_count: int = 0
    uncertain_count: int = 0
    work_pct: float = 0.0
    non_work_pct: float = 0.0
    idle_pct: float = 0.0
    classifications: list[ScreenshotClassification] = Field(default_factory=list)
    summary: str = ""
    repeated_frames: list[RepeatedFrame] = Field(default_factory=list)
    tab_switching_analysis: Optional[TabSwitchingAnalysis] = None
    unauthorized_access_events: list[UnauthorizedAccessEvent] = Field(default_factory=list)
    third_party_accounts: list[ThirdPartyAccount] = Field(default_factory=list)
    suspicious_sites: list[SuspiciousSite] = Field(default_factory=list)
    monitor_inconsistencies: list[MonitorInconsistency] = Field(default_factory=list)


# ── Timesheet Analysis ───────────────────────────────────────────────────────

class SessionAnomaly(BaseModel):
    """A detected anomaly in a work session."""
    session_date: str
    session_time: str
    anomaly_type: str
    description: str
    severity: str  # low, medium, high


class SuspiciousWindow(BaseModel):
    """A specific time window flagged as suspicious."""
    start: str
    end: str
    duration: str  # HH:MM:SS
    duration_seconds: float = 0.0
    reason: str = ""
    session_date: str = ""


class TimesheetAnalysisResult(BaseModel):
    """Result of timesheet statistical analysis."""
    total_sessions: int
    total_duration_hours: float
    total_active_hours: float
    overall_activity_pct: float
    avg_session_duration_min: float
    avg_activity_pct: float
    min_activity_pct: float
    max_activity_pct: float
    sessions_below_50_pct: int
    very_short_sessions: int     # sessions < 5 min
    very_long_sessions: int      # sessions > 6 hours
    anomalies: list[SessionAnomaly] = Field(default_factory=list)
    daily_breakdown: dict[str, float] = Field(default_factory=dict)
    reasoning: str = ""
    suspicious_hours_total: str = "00:00:00"
    suspicious_pct: float = 0.0
    suspicious_windows: list[SuspiciousWindow] = Field(default_factory=list)
    # Enhanced stats
    activity_std_dev: float = 0.0
    duration_std_dev: float = 0.0
    p25_duration_min: float = 0.0
    p75_duration_min: float = 0.0
    round_duration_pct: float = 0.0      # % of sessions with round-hour durations
    duplicate_session_count: int = 0
    overlapping_session_count: int = 0
    max_daily_hours: float = 0.0
    days_over_12h: int = 0
    start_time_std_min: float = 0.0      # std dev of start times in minutes
    avg_gap_between_sessions_min: float = 0.0
    gap_std_dev_min: float = 0.0


# ── Cross Analysis ────────────────────────────────────────────────────────────

class CrossAnalysisResult(BaseModel):
    """Result of comparing screenshots vs timesheet data."""
    contradictions: list[str] = Field(default_factory=list)
    consistencies: list[str] = Field(default_factory=list)
    screenshot_work_pct: float = 0.0
    timesheet_activity_pct: float = 0.0
    activity_gap: float = 0.0  # difference between reported and observed
    reasoning: str = ""


# ── Final Output ──────────────────────────────────────────────────────────────

class SessionFinding(BaseModel):
    """A single finding within a session report."""
    timestamp: str = ""
    finding_type: str = ""
    description: str = ""
    severity: str = "low"  # low, medium, high, critical


class SessionReport(BaseModel):
    """Per-session audit with individual verdict."""
    session_date: str
    start_time: str = ""
    end_time: str = ""
    duration: str = ""  # HH:MM:SS
    duration_hours: float = 0.0
    screenshots_in_session: int = 0
    findings: list[SessionFinding] = Field(default_factory=list)
    session_verdict: str = "legitimate"  # no_work_detected | suspicious | legitimate | confirmed_fraud
    session_risk_score: float = 0.0


class RiskScoreBreakdown(BaseModel):
    """Breakdown of what contributed to the risk score."""
    signal_name: str
    points: float
    description: str


class FinalRiskAssessment(BaseModel):
    """The final risk scoring output."""
    risk_score: float = Field(ge=0.0, le=100.0)
    risk_level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    key_findings: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    interpretations: list[str] = Field(default_factory=list)
    fraud_assessment: str = ""
    score_breakdown: list[RiskScoreBreakdown] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    """Complete audit report combining all analysis."""
    bundle_id: str
    employee: str
    date_range: str
    timezone: str = ""
    validation: ValidationResult
    timesheet_analysis: Optional[TimesheetAnalysisResult] = None
    screenshot_analysis: Optional[ScreenshotAnalysisResult] = None
    cross_analysis: Optional[CrossAnalysisResult] = None
    risk_assessment: FinalRiskAssessment
    work_summary: str = ""
    session_reports: list[SessionReport] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.now)
    # Raw input data for display in report
    input_sessions: list[dict] = Field(default_factory=list)
    input_screenshot_count: int = 0
    input_screenshot_date_range: str = ""

    def to_summary(self) -> str:
        lines = [
            "=" * 60,
            f"  AUDIT REPORT: {self.employee}",
            "=" * 60,
            f"Period: {self.date_range}",
            f"Timezone: {self.timezone}" if self.timezone else "",
            f"Generated: {self.generated_at.isoformat()}",
            "",
        ]

        # Input data summary
        if self.input_sessions:
            lines.append("-" * 60)
            lines.append("  INPUT DATA — WORK SESSIONS")
            lines.append("-" * 60)
            lines.append(f"  {'Date':<14} {'Start':<12} {'End':<12} {'Duration':<10} {'Activity':<10} {'Project'}")
            lines.append(f"  {'─'*14} {'─'*12} {'─'*12} {'─'*10} {'─'*10} {'─'*20}")
            for s in self.input_sessions:
                lines.append(
                    f"  {s.get('date', ''):<14} "
                    f"{s.get('time_start', ''):<12} "
                    f"{s.get('time_end', ''):<12} "
                    f"{s.get('duration', ''):<10} "
                    f"{s.get('activity_pct', ''):<10} "
                    f"{s.get('project', '')}"
                )
            lines.append(f"  Total: {len(self.input_sessions)} sessions")
            lines.append("")
        if self.input_screenshot_count > 0:
            lines.append(f"  Screenshots provided: {self.input_screenshot_count} "
                         f"({self.input_screenshot_date_range})")
            lines.append("")

        # Work summary (the ~100 word narrative)
        if self.work_summary:
            lines.append("-" * 60)
            lines.append("  WORK SUMMARY")
            lines.append("-" * 60)
            lines.append(self.work_summary)
            lines.append("")

        # Risk assessment
        lines.append("-" * 60)
        lines.append("  RISK ASSESSMENT")
        lines.append("-" * 60)
        lines.append(f"Score: {self.risk_assessment.risk_score:.1f}/100")
        lines.append(f"Level: {self.risk_assessment.risk_level.value}")
        lines.append(f"Confidence: {self.risk_assessment.confidence:.0%}")
        lines.append("")
        lines.append("Key findings:")
        for f in self.risk_assessment.key_findings:
            lines.append(f"  - {f}")
        if self.risk_assessment.fraud_assessment:
            lines.append("")
            lines.append("Fraud Assessment:")
            lines.append(f"  {self.risk_assessment.fraud_assessment}")
        # Risk score breakdown
        if self.risk_assessment.score_breakdown:
            lines.append("")
            lines.append("Score Breakdown:")
            for b in self.risk_assessment.score_breakdown:
                lines.append(f"  +{b.points:.0f}  {b.signal_name}: {b.description}")

        lines.append("")
        lines.append("Reasoning:")
        lines.append(f"  {self.risk_assessment.reasoning}")

        # Recommendations
        if self.risk_assessment.recommendations:
            lines.append("")
            lines.append("-" * 60)
            lines.append("  RECOMMENDATIONS")
            lines.append("-" * 60)
            for r in self.risk_assessment.recommendations:
                lines.append(f"  - {r}")

        # Contradictions
        if self.cross_analysis and self.cross_analysis.contradictions:
            lines.append("")
            lines.append("-" * 60)
            lines.append("  CONTRADICTIONS")
            lines.append("-" * 60)
            for c in self.cross_analysis.contradictions:
                lines.append(f"  ! {c}")

        # Session reports summary
        if self.session_reports:
            lines.append("")
            lines.append("-" * 60)
            lines.append("  SESSION REPORTS")
            lines.append("-" * 60)
            for sr in self.session_reports:
                lines.append(
                    f"  {sr.session_date} {sr.start_time}-{sr.end_time} "
                    f"[{sr.session_verdict}] score={sr.session_risk_score:.0f} "
                    f"({sr.screenshots_in_session} screenshots, {len(sr.findings)} findings)"
                )

        lines.append("=" * 60)
        return "\n".join(lines)


# ── LangGraph State ──────────────────────────────────────────────────────────

class GraphState(BaseModel):
    """State object that flows through the LangGraph workflow."""
    # Inputs
    timesheet_path: str = ""
    screenshot_path: str = ""

    # Parsed data
    evidence_bundle: Optional[EvidenceBundle] = None

    # Validation
    validation_result: Optional[ValidationResult] = None

    # Analysis results
    timesheet_analysis: Optional[TimesheetAnalysisResult] = None
    screenshot_analysis: Optional[ScreenshotAnalysisResult] = None
    cross_analysis: Optional[CrossAnalysisResult] = None

    # Final output
    risk_assessment: Optional[FinalRiskAssessment] = None
    audit_report: Optional[AuditReport] = None

    # Control
    error: Optional[str] = None
    should_abort: bool = False

    class Config:
        arbitrary_types_allowed = True
