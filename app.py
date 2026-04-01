"""
Employee Work Audit System — Streamlit UI
Clean, simple, professional interface.
Works in both light and dark Streamlit themes.
"""

from __future__ import annotations

import os
import sys


import json
import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from workflow import run_audit
from models import AuditReport, RiskLevel
from config import get_settings

# ── Page Config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Work Audit System",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS — theme-adaptive via CSS variables ────────────────────────────

st.markdown("""
<style>
    /* ── Theme-adaptive custom properties ── */
    :root {
        --audit-bg:          #ffffff;
        --audit-bg-secondary:#f9fafb;
        --audit-text:        #1f2937;
        --audit-text-muted:  #6b7280;
        --audit-text-faint:  #9ca3af;
        --audit-border:      #e5e7eb;
        --audit-border-light:#f3f4f6;
        --audit-card-shadow: rgba(0,0,0,0.06);
    }

    @media (prefers-color-scheme: dark) {
        :root {
            --audit-bg:          #1e1e2e;
            --audit-bg-secondary:#262637;
            --audit-text:        #e0e0e0;
            --audit-text-muted:  #a0a0a0;
            --audit-text-faint:  #777777;
            --audit-border:      #383850;
            --audit-border-light:#2e2e42;
            --audit-card-shadow: rgba(0,0,0,0.3);
        }
    }

    [data-testid="stAppViewContainer"][data-theme="dark"],
    .stApp[data-theme="dark"],
    html[data-theme="dark"] {
        --audit-bg:          #1e1e2e;
        --audit-bg-secondary:#262637;
        --audit-text:        #e0e0e0;
        --audit-text-muted:  #a0a0a0;
        --audit-text-faint:  #777777;
        --audit-border:      #383850;
        --audit-border-light:#2e2e42;
        --audit-card-shadow: rgba(0,0,0,0.3);
    }

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1100px;
    }

    /* ── Metric cards ── */
    .metric-card {
        background: var(--audit-bg-secondary);
        border: 1px solid var(--audit-border);
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        text-align: center;
        transition: box-shadow 0.2s;
    }
    .metric-card:hover {
        box-shadow: 0 4px 12px var(--audit-card-shadow);
    }
    .metric-label {
        font-size: 0.75rem;
        font-weight: 600;
        color: var(--audit-text-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.25rem;
    }
    .metric-value {
        font-size: 1.75rem;
        font-weight: 700;
        color: var(--audit-text);
        line-height: 1.2;
    }
    .metric-sub {
        font-size: 0.8rem;
        color: var(--audit-text-faint);
        margin-top: 0.2rem;
    }

    /* ── Risk badges ── */
    .risk-badge {
        display: inline-block;
        padding: 0.35rem 1rem;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 600;
        letter-spacing: 0.03em;
        text-transform: uppercase;
    }
    .risk-valid   { background: #064e3b; color: #6ee7b7; border: 1px solid #065f46; }
    .risk-review  { background: #78350f; color: #fcd34d; border: 1px solid #92400e; }
    .risk-high    { background: #7f1d1d; color: #fca5a5; border: 1px solid #991b1b; }
    .risk-invalid { background: #374151; color: #d1d5db; border: 1px solid #4b5563; }

    /* ── Score ring ── */
    .score-ring {
        position: relative;
        width: 140px;
        height: 140px;
    }
    .score-ring svg {
        transform: rotate(-90deg);
    }
    .score-number {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        font-size: 2rem;
        font-weight: 700;
    }

    /* ── Section headers ── */
    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--audit-text);
        padding-bottom: 0.5rem;
        border-bottom: 2px solid var(--audit-border-light);
        margin: 1.5rem 0 1rem 0;
    }

    /* ── Finding cards ── */
    .finding-card {
        background: var(--audit-bg-secondary);
        border-left: 3px solid var(--audit-border);
        padding: 0.75rem 1rem;
        margin-bottom: 0.5rem;
        border-radius: 0 8px 8px 0;
        font-size: 0.9rem;
        color: var(--audit-text);
    }
    .finding-card.fact      { border-left-color: #60a5fa; }
    .finding-card.interpret  { border-left-color: #a78bfa; }
    .finding-card.warning    { border-left-color: #fbbf24; }
    .finding-card.error      { border-left-color: #f87171; }
    .finding-card.positive   { border-left-color: #34d399; }

    /* ── Work summary box ── */
    .summary-box {
        background: var(--audit-bg-secondary);
        border: 1px solid var(--audit-border);
        border-radius: 12px;
        padding: 1.5rem 2rem;
        font-size: 1rem;
        line-height: 1.7;
        color: var(--audit-text);
        margin: 0.5rem 0 1rem 0;
    }

    /* ── Anomaly rows ── */
    .anomaly-row {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.6rem 0;
        border-bottom: 1px solid var(--audit-border-light);
        font-size: 0.85rem;
        color: var(--audit-text);
    }
    .severity-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
    }
    .severity-low    { background: #fbbf24; }
    .severity-medium { background: #f97316; }
    .severity-high   { background: #ef4444; }

    /* ── Divider ── */
    .divider {
        height: 1px;
        background: var(--audit-border-light);
        margin: 1.5rem 0;
    }

    /* ── Guide box ── */
    .guide-box {
        background: var(--audit-bg-secondary);
        border: 1px solid var(--audit-border);
        border-radius: 12px;
        padding: 1.5rem 2rem;
        color: var(--audit-text);
        line-height: 1.7;
    }
    .guide-box h4 {
        margin-top: 0;
        color: var(--audit-text);
    }
    .guide-step {
        display: flex;
        align-items: flex-start;
        gap: 0.75rem;
        margin-bottom: 1rem;
    }
    .guide-num {
        background: #6366f1;
        color: white;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 0.85rem;
        flex-shrink: 0;
        margin-top: 2px;
    }
    .guide-text {
        color: var(--audit-text);
        font-size: 0.92rem;
    }
    .guide-text strong {
        color: var(--audit-text);
    }
    .guide-text .muted {
        color: var(--audit-text-muted);
        font-size: 0.82rem;
    }

    /* ── Landing page ── */
    .landing-title {
        color: var(--audit-text);
        font-weight: 700;
        margin-bottom: 0.5rem;
    }
    .landing-desc {
        color: var(--audit-text-muted);
        max-width: 500px;
        margin: 0 auto;
        line-height: 1.6;
    }

    /* ── Inline text helpers ── */
    .txt        { color: var(--audit-text); }
    .txt-muted  { color: var(--audit-text-muted); }
    .txt-faint  { color: var(--audit-text-faint); }
</style>
""", unsafe_allow_html=True)


# ── Helper Functions ─────────────────────────────────────────────────────────

def get_risk_color(level: RiskLevel) -> str:
    return {
        RiskLevel.VALID_WORK: "#34d399",
        RiskLevel.NEEDS_REVIEW: "#fbbf24",
        RiskLevel.HIGH_RISK: "#f87171",
        RiskLevel.INVALID_BUNDLE: "#9ca3af",
    }.get(level, "#9ca3af")


def get_risk_css_class(level: RiskLevel) -> str:
    return {
        RiskLevel.VALID_WORK: "risk-valid",
        RiskLevel.NEEDS_REVIEW: "risk-review",
        RiskLevel.HIGH_RISK: "risk-high",
        RiskLevel.INVALID_BUNDLE: "risk-invalid",
    }.get(level, "risk-invalid")


def get_risk_label(level: RiskLevel) -> str:
    return {
        RiskLevel.VALID_WORK: "Valid Work",
        RiskLevel.NEEDS_REVIEW: "Needs Review",
        RiskLevel.HIGH_RISK: "High Risk",
        RiskLevel.INVALID_BUNDLE: "Invalid Bundle",
    }.get(level, "Unknown")


def render_metric(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="metric-sub">{sub}</div>' if sub else ""
    return f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {sub_html}
    </div>
    """


def render_score_ring(score: float, color: str) -> str:
    circumference = 2 * 3.14159 * 54
    offset = circumference - (score / 100) * circumference
    return f"""
    <div class="score-ring">
        <svg width="140" height="140">
            <circle cx="70" cy="70" r="54" fill="none" stroke="var(--audit-border)" stroke-width="10"/>
            <circle cx="70" cy="70" r="54" fill="none" stroke="{color}" stroke-width="10"
                    stroke-dasharray="{circumference}" stroke-dashoffset="{offset}"
                    stroke-linecap="round"/>
        </svg>
        <div class="score-number" style="color: {color}">{score:.0f}</div>
    </div>
    """


def save_uploaded_file(uploaded_file) -> str:
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getbuffer())
        return tmp.name


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🔍 Work Audit")
    st.markdown(
        '<p class="txt-muted" style="font-size:0.85rem;margin-top:-0.5rem;">'
        'AI-powered freelancer activity analysis</p>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    st.markdown("##### Upload Files")

    timesheet_file = st.file_uploader(
        "Timesheet Export",
        type=["xls", "xlsx", "csv"],
        help="HiveDesk timesheet export (.xls, .csv)",
    )

    screenshot_file = st.file_uploader(
        "Screenshot Report",
        type=["pdf"],
        help="HiveDesk screenshot report (.pdf)",
    )

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    st.markdown("##### Settings")

    max_screenshots = st.slider(
        "Max Screenshots to Analyze",
        min_value=0,
        max_value=50,
        value=10,
        help="0 = analyze all (can be slow & costly)",
    )

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    run_disabled = not timesheet_file and not screenshot_file
    run_button = st.button(
        "▶  Run Audit",
        use_container_width=True,
        disabled=run_disabled,
        type="primary",
    )

    if run_disabled:
        st.caption("Upload at least one file to begin.")


# ── Main Content ─────────────────────────────────────────────────────────────

if "report" not in st.session_state:
    st.session_state.report = None

# ── Landing state: show user guide ───────────────────────────────────────────

if not run_button and st.session_state.report is None:
    st.markdown("")
    col_center = st.columns([1, 2, 1])[1]
    with col_center:
        st.markdown("### Freelancer Work Audit System")
        st.caption("Upload a timesheet and/or screenshot report to analyze work patterns and generate a detailed audit report.")

    st.divider()

    st.markdown("#### How to run an audit")
    st.markdown("""
1. **Export your files from HiveDesk** — Go to HiveDesk > Reports > export the **Timesheet Detail** as .xls or .csv. If you have screenshots enabled, export the **Screenshot Report** as .pdf.
2. **Upload the files in the sidebar** — Use the file uploaders on the left panel. You can upload both files for a full cross-analysis, or just the timesheet for a timesheet-only audit.
3. **Click "Run Audit"** — The system will parse your files, analyze the data, cross-reference the sources, and generate a full report with a work summary and risk assessment.
4. **Read the report** — The report starts with a plain-language **Work Summary** that describes what the freelancer did during the period. Below it you will find detailed metrics, charts, anomalies, and a downloadable JSON report.
""")

    st.divider()

    st.markdown("#### What does the report tell you?")
    st.caption(
        "The system analyzes whether the tracked hours and session patterns are compatible "
        "with normal productive work. It checks activity levels, session durations, idle time, "
        "and — if screenshots are provided — what was actually on screen. "
        "It does not judge working hours: freelancers can work at any time they choose. "
        "The final summary gives you a clear, neutral conclusion based purely on the data."
    )

    st.stop()


# ── Run Audit ────────────────────────────────────────────────────────────────

if run_button:
    if max_screenshots:
        os.environ["MAX_SCREENSHOTS"] = str(max_screenshots)

    from config import get_settings
    get_settings.cache_clear()

    ts_path = ""
    ss_path = ""

    if timesheet_file:
        ts_path = save_uploaded_file(timesheet_file)
    if screenshot_file:
        ss_path = save_uploaded_file(screenshot_file)

    progress = st.progress(0, text="Initializing audit...")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    def update_progress(pct, msg):
        progress.progress(min(pct, 100), text=msg)

    try:
        report = run_audit(
            timesheet_path=ts_path,
            screenshot_path=ss_path,
            progress_callback=update_progress,
        )
        st.session_state.report = report
        progress.progress(100, text="Complete!")
        time.sleep(0.3)
        progress.empty()

    except Exception as e:
        progress.empty()
        st.error(f"Audit failed: {e}")
        st.stop()

    if ts_path:
        Path(ts_path).unlink(missing_ok=True)
    if ss_path:
        Path(ss_path).unlink(missing_ok=True)

    st.rerun()


# ── Display Report ───────────────────────────────────────────────────────────

report: AuditReport = st.session_state.report
if report is None:
    st.stop()

risk = report.risk_assessment
color = get_risk_color(risk.risk_level)

# ── Report Header ────────────────────────────────────────────────────────────

col_title, col_badge = st.columns([3, 1])
with col_title:
    st.markdown(
        f'<h2 class="txt" style="margin:0;">Audit Report</h2>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="txt-muted" style="margin-top:0.2rem;">'
        f'{report.employee} &nbsp;·&nbsp; {report.date_range}'
        f'</p>',
        unsafe_allow_html=True,
    )
with col_badge:
    badge_class = get_risk_css_class(risk.risk_level)
    st.markdown(
        f'<div style="text-align:right;padding-top:0.75rem;">'
        f'<span class="risk-badge {badge_class}">{get_risk_label(risk.risk_level)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Work Summary (the main narrative the user reads first)
# ══════════════════════════════════════════════════════════════════════════════

if report.work_summary:
    st.markdown('<div class="section-header">📝 Detailed Audit Report</div>', unsafe_allow_html=True)
    st.markdown(report.work_summary)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Risk Score overview
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-header">⚡ Risk Assessment</div>', unsafe_allow_html=True)

col_ring, col_info = st.columns([1, 2])

with col_ring:
    st.markdown(render_score_ring(risk.risk_score, color), unsafe_allow_html=True)
    st.markdown(
        '<div style="text-align:center;margin-top:0.5rem;">'
        '<span class="txt-faint" style="font-size:0.8rem;">Risk Score</span>'
        '</div>',
        unsafe_allow_html=True,
    )

with col_info:
    st.markdown(
        f'<div style="padding:0.5rem 0;">'
        f'<div class="txt-muted" style="font-size:0.75rem;text-transform:uppercase;'
        f'letter-spacing:0.05em;font-weight:600;">Confidence</div>'
        f'<div class="txt" style="font-size:1.5rem;font-weight:700;">{risk.confidence:.0%}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if risk.key_findings:
        st.markdown(
            '<div class="txt-muted" style="font-size:0.75rem;text-transform:uppercase;'
            'letter-spacing:0.05em;font-weight:600;margin-top:0.5rem;">Key Findings</div>',
            unsafe_allow_html=True,
        )
        for finding in risk.key_findings:
            st.markdown(f'<div class="finding-card">{finding}</div>', unsafe_allow_html=True)

# ── Fraud Assessment (if available) ─────────────────────────────────────────
if risk.fraud_assessment:
    st.markdown('<div class="section-header">🚨 Fraud Assessment</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="finding-card warning" style="font-size:1rem;padding:1rem 1.25rem;border-left-width:4px;">'
        f'{risk.fraud_assessment}</div>',
        unsafe_allow_html=True,
    )

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Timesheet Metrics
# ══════════════════════════════════════════════════════════════════════════════

if report.timesheet_analysis:
    ta = report.timesheet_analysis

    st.markdown('<div class="section-header">📊 Timesheet Analysis</div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            render_metric("Total Hours", f"{ta.total_duration_hours:.1f}h", f"{ta.total_sessions} sessions"),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            render_metric("Active Hours", f"{ta.total_active_hours:.1f}h", f"{ta.overall_activity_pct:.0f}% active"),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            render_metric("Avg Session", f"{ta.avg_session_duration_min:.0f}m", f"{ta.avg_activity_pct:.0f}% avg activity"),
            unsafe_allow_html=True,
        )
    with c4:
        anomaly_count = len(ta.anomalies)
        st.markdown(
            render_metric("Anomalies", str(anomaly_count), "detected"),
            unsafe_allow_html=True,
        )

    # Daily breakdown chart
    if ta.daily_breakdown:
        st.markdown("")
        import pandas as pd
        daily_df = pd.DataFrame(
            list(ta.daily_breakdown.items()),
            columns=["Date", "Hours"],
        )
        st.bar_chart(daily_df.set_index("Date"), color=color, height=220)

    # Anomalies
    if ta.anomalies:
        with st.expander(f"View {len(ta.anomalies)} Anomalies", expanded=False):
            for a in ta.anomalies:
                sev_class = f"severity-{a.severity}"
                st.markdown(
                    f'<div class="anomaly-row">'
                    f'<span class="severity-dot {sev_class}"></span>'
                    f'<span class="txt-muted" style="min-width:90px;">{a.session_date}</span>'
                    f'<span class="txt-faint" style="min-width:80px;">{a.session_time[:8]}</span>'
                    f'<span class="txt" style="flex:1;">{a.description}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Screenshot Analysis
# ══════════════════════════════════════════════════════════════════════════════

if report.screenshot_analysis:
    sa = report.screenshot_analysis

    st.markdown('<div class="section-header">🖼️ Screenshot Analysis</div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            render_metric("Analyzed", str(sa.total_analyzed), "screenshots"),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            render_metric("Work", f"{sa.work_pct:.0f}%", f"{sa.work_count} screenshots"),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            render_metric("Non-Work", f"{sa.non_work_pct:.0f}%", f"{sa.non_work_count} screenshots"),
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            render_metric("Idle", f"{sa.idle_pct:.0f}%", f"{sa.idle_count} screenshots"),
            unsafe_allow_html=True,
        )

    # Category distribution
    if sa.total_analyzed > 0:
        import pandas as pd
        cat_df = pd.DataFrame({
            "Category": ["Work", "Non-Work", "Idle", "Uncertain"],
            "Count": [sa.work_count, sa.non_work_count, sa.idle_count, sa.uncertain_count],
        })
        cat_df = cat_df[cat_df["Count"] > 0]
        st.bar_chart(cat_df.set_index("Category"), height=200)

    # Individual classifications
    if sa.classifications:
        with st.expander(f"View {len(sa.classifications)} Classifications", expanded=False):
            for c in sa.classifications:
                cat_colors = {
                    "work": "#34d399",
                    "non_work": "#f87171",
                    "idle": "#9ca3af",
                    "uncertain": "#fbbf24",
                }
                cat_color = cat_colors.get(c.category.value, "#9ca3af")
                st.markdown(
                    f'<div class="anomaly-row">'
                    f'<span class="txt-faint" style="min-width:140px;">{c.timestamp}</span>'
                    f'<span style="color:{cat_color};font-weight:600;min-width:80px;">{c.category.value}</span>'
                    f'<span class="txt-muted" style="min-width:50px;">{c.confidence:.0%}</span>'
                    f'<span class="txt" style="flex:1;">{c.description[:100]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Cross Analysis
# ══════════════════════════════════════════════════════════════════════════════

if report.cross_analysis:
    ca = report.cross_analysis

    st.markdown('<div class="section-header">⚖️ Cross Analysis</div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            render_metric("Timesheet Activity", f"{ca.timesheet_activity_pct:.0f}%", "reported"),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            render_metric("Screenshot Work", f"{ca.screenshot_work_pct:.0f}%", "observed"),
            unsafe_allow_html=True,
        )
    with c3:
        gap_color = "#34d399" if ca.activity_gap < 15 else "#fbbf24" if ca.activity_gap < 30 else "#f87171"
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-label">Activity Gap</div>
                <div class="metric-value" style="color:{gap_color};">{ca.activity_gap:.0f}pp</div>
                <div class="metric-sub">percentage points</div>
            </div>""",
            unsafe_allow_html=True,
        )

    if ca.contradictions:
        st.markdown("")
        for item in ca.contradictions:
            st.markdown(f'<div class="finding-card warning">{item}</div>', unsafe_allow_html=True)

    if ca.consistencies:
        for item in ca.consistencies:
            st.markdown(f'<div class="finding-card positive">{item}</div>', unsafe_allow_html=True)

    if ca.reasoning:
        with st.expander("AI Cross-Analysis Reasoning", expanded=False):
            st.markdown(ca.reasoning)

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Validation warnings/errors
# ══════════════════════════════════════════════════════════════════════════════

if report.validation:
    v = report.validation
    has_issues = v.errors or v.warnings
    if has_issues:
        st.markdown('<div class="section-header">⚠️ Validation</div>', unsafe_allow_html=True)
        for e in v.errors:
            st.markdown(f'<div class="finding-card error">{e}</div>', unsafe_allow_html=True)
        for w in v.warnings:
            st.markdown(f'<div class="finding-card warning">{w}</div>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Facts & Interpretations
# ══════════════════════════════════════════════════════════════════════════════

if risk.facts or risk.interpretations:
    col_f, col_i = st.columns(2)

    with col_f:
        if risk.facts:
            st.markdown('<div class="section-header">📌 Facts</div>', unsafe_allow_html=True)
            for f in risk.facts:
                st.markdown(f'<div class="finding-card fact">{f}</div>', unsafe_allow_html=True)

    with col_i:
        if risk.interpretations:
            st.markdown('<div class="section-header">💡 Interpretations</div>', unsafe_allow_html=True)
            for i in risk.interpretations:
                st.markdown(f'<div class="finding-card interpret">{i}</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Full AI Reasoning (collapsible)
# ══════════════════════════════════════════════════════════════════════════════

if risk.reasoning:
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    with st.expander("Full AI Reasoning", expanded=False):
        st.markdown(risk.reasoning)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Export & Reset
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

col_dl, col_reset = st.columns([1, 1])
with col_dl:
    report_json = json.dumps(report.model_dump(mode="json"), indent=2, default=str)
    st.download_button(
        "📥  Download JSON Report",
        data=report_json,
        file_name=f"audit_{report.employee}_{report.date_range}.json",
        mime="application/json",
        use_container_width=True,
    )
with col_reset:
    if st.button("🔄  New Audit", use_container_width=True):
        st.session_state.report = None
        st.rerun()
