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
    NEEDS_REVIEW = "needs_review"
    HIGH_RISK = "high_risk"
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


# ── Timesheet Analysis ───────────────────────────────────────────────────────

class SessionAnomaly(BaseModel):
    """A detected anomaly in a work session."""
    session_date: str
    session_time: str
    anomaly_type: str
    description: str
    severity: str  # low, medium, high


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


class AuditReport(BaseModel):
    """Complete audit report combining all analysis."""
    bundle_id: str
    employee: str
    date_range: str
    validation: ValidationResult
    timesheet_analysis: Optional[TimesheetAnalysisResult] = None
    screenshot_analysis: Optional[ScreenshotAnalysisResult] = None
    cross_analysis: Optional[CrossAnalysisResult] = None
    risk_assessment: FinalRiskAssessment
    work_summary: str = ""
    generated_at: datetime = Field(default_factory=datetime.now)

    def to_summary(self) -> str:
        lines = [
            "=" * 60,
            f"  AUDIT REPORT: {self.employee}",
            "=" * 60,
            f"Period: {self.date_range}",
            f"Generated: {self.generated_at.isoformat()}",
            "",
        ]

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
        lines.append("")
        lines.append("Reasoning:")
        lines.append(f"  {self.risk_assessment.reasoning}")
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
