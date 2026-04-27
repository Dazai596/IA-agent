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
/* ═══════════════════════════════════════════════════════════════
   WHITE · CRIMSON · GOLD  —  Professional Audit Theme
═══════════════════════════════════════════════════════════════ */

/* ── Design tokens ── */
:root {
    --cream:          #FDFAF3;
    --white:          #FFFFFF;
    --card-bg:        #FFFDF8;
    --gold:           #C9A84C;
    --gold-bright:    #E8C96A;
    --gold-dark:      #8A6918;
    --gold-border:    #D6BA7A;
    --gold-pale:      #F7EDD4;
    --gold-subtle:    #F2E4BC;
    --crimson:        #9B1515;
    --crimson-dark:   #680808;
    --crimson-mid:    #C42020;
    --crimson-pale:   #FDF0F0;
    --green-ok:       #186840;
    --green-pale:     #EDF7F1;
    --amber:          #B97310;
    --amber-pale:     #FFF6E6;
    --ink:            #1A1208;
    --ink-mid:        #4A3820;
    --ink-light:      #7A6040;
    --ink-faint:      #A89070;
    --border:         #E2D0A8;
    --border-light:   #EEE2C8;
    --shadow-sm:      rgba(130, 85, 10, 0.08);
    --shadow-md:      rgba(130, 85, 10, 0.16);
    --shadow-lg:      rgba(130, 85, 10, 0.28);
}

/* ── Hide Streamlit chrome ── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* ── Lock sidebar — only hide the collapse button (inside the sidebar) ── */
[data-testid="stSidebarCollapseButton"],
button[aria-label="Close sidebar"] {
    display: none !important;
}

/* ── Global background & text ── */
.stApp,
[data-testid="stAppViewContainer"] {
    background: var(--cream) !important;
    color: var(--ink) !important;
}
.block-container {
    padding-top: 2.5rem;
    padding-bottom: 3rem;
    max-width: 1120px;
}

/* ── Native Streamlit text elements ── */
.stMarkdown, .stMarkdown p, .stMarkdown li,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4,
[data-testid="stText"], [data-testid="stCaption"],
.stCaption, p, h1, h2, h3, h4, h5, h6 {
    color: var(--ink) !important;
}
.stCaption, [data-testid="stCaption"] {
    color: var(--ink-light) !important;
}

/* ── Sidebar — dark ink with gold accents ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1A1208 0%, #120D04 100%) !important;
    border-right: 1px solid var(--gold-dark) !important;
}

/* Every text node inside the sidebar gets the warm light colour */
[data-testid="stSidebar"] *:not(button):not(svg):not(path) {
    color: #D6C9B0 !important;
}

/* Headings in gold */
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4,
[data-testid="stSidebar"] h5,
[data-testid="stSidebar"] h6,
[data-testid="stSidebar"] strong {
    color: var(--gold) !important;
    letter-spacing: 0.05em;
}

/* Radio option labels */
[data-testid="stSidebar"] [data-testid="stRadio"] label,
[data-testid="stSidebar"] [data-testid="stRadio"] label p,
[data-testid="stSidebar"] [data-testid="stRadio"] span {
    color: #D6C9B0 !important;
}

/* Slider value / label text */
[data-testid="stSidebar"] [data-testid="stSlider"] label,
[data-testid="stSidebar"] [data-testid="stSlider"] p,
[data-testid="stSidebar"] [data-testid="stSlider"] span,
[data-testid="stSidebar"] .stSlider [data-testid="stTickBar"] {
    color: #D6C9B0 !important;
}

/* File uploader text */
[data-testid="stSidebar"] [data-testid="stFileUploader"] label,
[data-testid="stSidebar"] [data-testid="stFileUploader"] span,
[data-testid="stSidebar"] [data-testid="stFileUploader"] p,
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] span {
    color: #D6C9B0 !important;
}

/* Caption / help text */
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stCaption"],
[data-testid="stSidebar"] small {
    color: #A89070 !important;
}

/* ── Primary run button ── */
.stButton > button[kind="primary"],
.stButton > button[type="submit"] {
    background: linear-gradient(135deg, var(--crimson) 0%, var(--crimson-dark) 100%) !important;
    border: 1px solid var(--crimson-dark) !important;
    color: #FFFFFF !important;
    font-weight: 700 !important;
    letter-spacing: 0.06em !important;
    border-radius: 6px !important;
    transition: box-shadow 0.2s, transform 0.15s !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 4px 18px rgba(155, 21, 21, 0.40) !important;
    transform: translateY(-1px) !important;
}

/* ── Download buttons ── */
.stDownloadButton > button {
    background: linear-gradient(135deg, var(--gold-dark) 0%, #5C4010 100%) !important;
    border: 1px solid var(--gold-dark) !important;
    color: var(--gold-pale) !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em !important;
    border-radius: 6px !important;
    transition: box-shadow 0.2s, transform 0.15s !important;
}
.stDownloadButton > button:hover {
    background: linear-gradient(135deg, var(--gold) 0%, var(--gold-dark) 100%) !important;
    box-shadow: 0 4px 18px rgba(201, 168, 76, 0.38) !important;
    transform: translateY(-1px) !important;
}

/* ── Secondary / reset button ── */
.stButton > button:not([kind="primary"]) {
    background: var(--white) !important;
    border: 1px solid var(--border) !important;
    color: var(--ink-mid) !important;
    font-weight: 600 !important;
    border-radius: 6px !important;
    transition: border-color 0.2s, box-shadow 0.2s !important;
}
.stButton > button:not([kind="primary"]):hover {
    border-color: var(--gold-border) !important;
    box-shadow: 0 2px 10px var(--shadow-sm) !important;
}

/* ── Metric cards ── */
.metric-card {
    background: var(--white);
    border: 1px solid var(--border);
    border-top: 3px solid var(--gold);
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    text-align: center;
    box-shadow: 0 2px 8px var(--shadow-sm);
    transition: box-shadow 0.25s, transform 0.2s;
}
.metric-card:hover {
    box-shadow: 0 6px 22px var(--shadow-md);
    transform: translateY(-2px);
}
.metric-label {
    font-size: 0.68rem;
    font-weight: 800;
    color: var(--gold-dark);
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 0.35rem;
}
.metric-value {
    font-size: 1.75rem;
    font-weight: 800;
    color: var(--ink);
    line-height: 1.15;
    letter-spacing: -0.01em;
}
.metric-sub {
    font-size: 0.76rem;
    color: var(--ink-faint);
    margin-top: 0.25rem;
    font-style: italic;
}

/* ── Risk badges ── */
.risk-badge {
    display: inline-block;
    padding: 0.38rem 1.1rem;
    border-radius: 4px;
    font-size: 0.74rem;
    font-weight: 800;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    box-shadow: 0 2px 8px rgba(0,0,0,0.18);
}
.risk-valid   {
    background: linear-gradient(135deg, #0D4A28, #0A3A20);
    color: #6EDDA8;
    border: 1px solid #1A6840;
}
.risk-review  {
    background: linear-gradient(135deg, #5A3A00, #3D2700);
    color: var(--gold-bright);
    border: 1px solid var(--gold-dark);
}
.risk-high    {
    background: linear-gradient(135deg, var(--crimson-dark), #3A0000);
    color: #FFA8A8;
    border: 1px solid var(--crimson);
}
.risk-fraud   {
    background: linear-gradient(135deg, #3A0000, #1A0000);
    color: #FF9090;
    border: 2px solid var(--crimson-mid);
    font-weight: 900;
    letter-spacing: 0.14em;
    box-shadow: 0 0 12px rgba(196, 32, 32, 0.45);
}
.risk-invalid {
    background: linear-gradient(135deg, #2A2015, #1A1408);
    color: var(--ink-faint);
    border: 1px solid var(--border);
}

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
    font-weight: 800;
    letter-spacing: -0.02em;
}

/* ── Section headers ── */
.section-header {
    font-size: 0.72rem;
    font-weight: 900;
    color: var(--gold-dark);
    text-transform: uppercase;
    letter-spacing: 0.14em;
    padding-bottom: 0.55rem;
    border-bottom: 2px solid var(--gold-border);
    margin: 1.75rem 0 1rem 0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

/* ── Finding cards ── */
.finding-card {
    background: var(--white);
    border-left: 3px solid var(--border);
    padding: 0.7rem 1rem;
    margin-bottom: 0.45rem;
    border-radius: 0 8px 8px 0;
    font-size: 0.875rem;
    color: var(--ink-mid);
    box-shadow: 0 1px 5px var(--shadow-sm);
    transition: box-shadow 0.2s;
}
.finding-card:hover {
    box-shadow: 0 3px 12px var(--shadow-md);
}
.finding-card.fact      { border-left-color: var(--gold);     background: #FFFCF4; }
.finding-card.interpret { border-left-color: #7C5CBF;         background: #FAF8FF; }
.finding-card.warning   { border-left-color: var(--amber);    background: var(--amber-pale); }
.finding-card.error     { border-left-color: var(--crimson);  background: var(--crimson-pale); }
.finding-card.positive  { border-left-color: var(--green-ok); background: var(--green-pale); }

/* ── Work summary box ── */
.summary-box {
    background: var(--white);
    border: 1px solid var(--border);
    border-left: 4px solid var(--gold);
    border-radius: 0 10px 10px 0;
    padding: 1.25rem 1.75rem;
    font-size: 0.92rem;
    line-height: 1.75;
    color: var(--ink-mid);
    margin: 0.5rem 0 1rem 0;
    box-shadow: 0 2px 10px var(--shadow-sm);
}

/* ── Anomaly rows ── */
.anomaly-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 0.5rem;
    border-bottom: 1px solid var(--border-light);
    font-size: 0.84rem;
    color: var(--ink-mid);
    transition: background 0.15s;
}
.anomaly-row:hover {
    background: var(--gold-pale);
    border-radius: 6px;
}
.severity-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}
.severity-low    { background: var(--gold); }
.severity-medium { background: var(--amber); }
.severity-high   { background: var(--crimson); }

/* ── Divider — gold gradient ── */
.divider {
    height: 1px;
    background: linear-gradient(to right, transparent, var(--gold-border) 25%, var(--gold-border) 75%, transparent);
    margin: 1.75rem 0;
    border: none;
}

/* ── Guide / how-to box ── */
.guide-box {
    background: var(--white);
    border: 1px solid var(--border);
    border-top: 3px solid var(--gold);
    border-radius: 10px;
    padding: 1.5rem 2rem;
    color: var(--ink-mid);
    line-height: 1.7;
    box-shadow: 0 2px 10px var(--shadow-sm);
}
.guide-box h4 {
    margin-top: 0;
    color: var(--ink);
}
.guide-step {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 1rem;
}
.guide-num {
    background: linear-gradient(135deg, var(--gold) 0%, var(--gold-dark) 100%);
    color: var(--white);
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 0.85rem;
    flex-shrink: 0;
    margin-top: 2px;
    box-shadow: 0 2px 6px rgba(138, 105, 24, 0.40);
}
.guide-text {
    color: var(--ink-mid);
    font-size: 0.91rem;
}
.guide-text strong { color: var(--ink); }
.guide-text .muted { color: var(--ink-light); font-size: 0.82rem; }

/* ── Landing page ── */
.landing-title {
    color: var(--ink);
    font-weight: 800;
    margin-bottom: 0.5rem;
}
.landing-desc {
    color: var(--ink-light);
    max-width: 500px;
    margin: 0 auto;
    line-height: 1.65;
}

/* ── Inline text helpers ── */
.txt       { color: var(--ink); }
.txt-muted { color: var(--ink-light); }
.txt-faint { color: var(--ink-faint); }

/* ── Streamlit expanders ── */
[data-testid="stExpander"] {
    background: var(--white) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    box-shadow: 0 1px 4px var(--shadow-sm) !important;
}
[data-testid="stExpander"] summary {
    color: var(--ink-mid) !important;
    font-weight: 600 !important;
}
[data-testid="stExpander"] summary:hover {
    color: var(--gold-dark) !important;
}

/* ── Streamlit dataframe ── */
[data-testid="stDataFrame"] {
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}

/* ── Progress bar ── */
.stProgress > div > div > div > div {
    background: linear-gradient(90deg, var(--gold-dark) 0%, var(--gold) 100%) !important;
}

/* ── Streamlit alerts ── */
.stAlert { border-radius: 8px !important; }

/* ── Radio buttons in sidebar ── */
[data-testid="stSidebar"] [data-testid="stRadio"] > label {
    color: var(--gold) !important;
    font-size: 0.78rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
}
</style>
""", unsafe_allow_html=True)


# ── Helper Functions ─────────────────────────────────────────────────────────

def get_risk_color(level: RiskLevel) -> str:
    return {
        RiskLevel.VALID_WORK: "#186840",
        RiskLevel.LOW_RISK: "#186840",
        RiskLevel.NEEDS_REVIEW: "#C9A84C",
        RiskLevel.HIGH_RISK: "#9B1515",
        RiskLevel.CONFIRMED_FRAUD: "#680808",
        RiskLevel.INVALID_BUNDLE: "#A89070",
    }.get(level, "#A89070")


def get_risk_css_class(level: RiskLevel) -> str:
    return {
        RiskLevel.VALID_WORK: "risk-valid",
        RiskLevel.LOW_RISK: "risk-valid",
        RiskLevel.NEEDS_REVIEW: "risk-review",
        RiskLevel.HIGH_RISK: "risk-high",
        RiskLevel.CONFIRMED_FRAUD: "risk-high",
        RiskLevel.INVALID_BUNDLE: "risk-invalid",
    }.get(level, "risk-invalid")


def get_risk_label(level: RiskLevel) -> str:
    return {
        RiskLevel.VALID_WORK: "Looks Good",
        RiskLevel.LOW_RISK: "Looks Good",
        RiskLevel.NEEDS_REVIEW: "Worth Reviewing",
        RiskLevel.HIGH_RISK: "Needs Attention",
        RiskLevel.CONFIRMED_FRAUD: "Needs Attention",
        RiskLevel.INVALID_BUNDLE: "Invalid Data",
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
            <circle cx="70" cy="70" r="54" fill="none" stroke="var(--border)" stroke-width="10"/>
            <circle cx="70" cy="70" r="54" fill="none" stroke="{color}" stroke-width="10"
                    stroke-dasharray="{circumference}" stroke-dashoffset="{offset}"
                    stroke-linecap="round"/>
        </svg>
        <div class="score-number" style="color: {color}">{score:.0f}</div>
    </div>
    """


def generate_pdf_report(report: AuditReport) -> bytes:
    """Generate a professional 4-page activity report matching the Mr Sagor report style."""
    from fpdf import FPDF

    risk = report.risk_assessment
    ss = report.structured_sections
    ta = report.timesheet_analysis

    # ── Colors ──────────────────────────────────────────────────────────
    C_DARK      = (26, 35, 50)      # dark navy header
    C_ACCENT    = (41, 128, 185)    # blue accent
    C_GREEN     = (39, 174, 96)
    C_ORANGE    = (243, 156, 18)
    C_RED       = (231, 76, 60)
    C_GRAY_LIGHT = (245, 246, 250)
    C_GRAY_TEXT  = (127, 140, 141)
    C_WHITE      = (255, 255, 255)
    C_TEXT       = (44, 62, 80)

    risk_colors = {
        "valid_work":      C_GREEN,
        "low_risk":        (46, 204, 113),
        "needs_review":    C_ORANGE,
        "high_risk":       (230, 126, 34),
        "confirmed_fraud": C_RED,
        "invalid_bundle":  (149, 165, 166),
    }
    risk_color = risk_colors.get(risk.risk_level.value, C_GRAY_TEXT)
    risk_label = get_risk_label(risk.risk_level)

    class PDF(FPDF):
        def footer(self):
            self.set_y(-13)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(*C_GRAY_TEXT)
            self.cell(0, 8, f"Page {self.page_no()}", align="C")

        @staticmethod
        def _c(text: str) -> str:
            return str(text).encode("latin-1", errors="replace").decode("latin-1")

        def section_band(self, title: str, r=41, g=128, b=185):
            self.set_fill_color(r, g, b)
            self.set_text_color(*C_WHITE)
            self.set_font("Helvetica", "B", 12)
            self.cell(0, 9, self._c(f"  {title}"), fill=True, new_x="LMARGIN", new_y="NEXT")
            self.ln(3)
            self.set_text_color(*C_TEXT)

        def kv_row(self, label: str, value: str, label_w: int = 55):
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*C_GRAY_TEXT)
            self.cell(label_w, 6, self._c(label + ":"))
            self.set_font("Helvetica", "", 10)
            self.set_text_color(*C_TEXT)
            self.multi_cell(0, 6, self._c(value))

        def bullet_item(self, text: str, indent: int = 5, bullet_char: str = "\x95"):
            self.set_font("Helvetica", "", 10)
            self.set_text_color(*C_TEXT)
            x = self.get_x()
            self.set_x(x + indent)
            self.cell(5, 5.5, bullet_char)
            self.multi_cell(0, 5.5, self._c(text))
            self.set_x(x)

        def stat_box(self, x: float, y: float, w: float, h: float,
                     label: str, value: str, sub: str = "",
                     bg=(245, 246, 250), val_color=None):
            self.set_fill_color(*bg)
            self.set_draw_color(220, 220, 220)
            self.rect(x, y, w, h, style="FD")
            val_color = val_color or C_ACCENT
            cy = y + 4
            self.set_xy(x + 3, cy)
            self.set_font("Helvetica", "B", 17)
            self.set_text_color(*val_color)
            self.cell(w - 6, 10, self._c(value), align="C")
            self.set_xy(x + 3, cy + 11)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*C_GRAY_TEXT)
            self.cell(w - 6, 5, self._c(label), align="C")
            if sub:
                self.set_xy(x + 3, cy + 16)
                self.set_font("Helvetica", "I", 7)
                self.cell(w - 6, 4, self._c(sub), align="C")

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(15, 15, 15)

    # ══════════════════════════════════════════════════════════════════════
    # PAGE 1: Cover — Employee Info + Activity Dot Matrix
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()

    # ── Header band ──────────────────────────────────────────────────────
    pdf.set_fill_color(*C_DARK)
    pdf.set_xy(0, 0)
    pdf.cell(0, 38, "", fill=True, new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(15, 8)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(120, 10, "Activity Report", new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(15, 21)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(180, 195, 210)
    from department_config import get_department_display_name as _pdf_dept_name
    _pdf_dept = _pdf_dept_name(report.department)
    pdf.cell(0, 8, pdf._c(f"{report.employee}  |  {report.date_range}  |  {_pdf_dept}"))

    # Risk badge (top right)
    badge_x = 148
    pdf.set_fill_color(*risk_color)
    pdf.set_draw_color(*risk_color)
    pdf.rect(badge_x, 10, 47, 16, style="F")
    pdf.set_xy(badge_x, 13)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(47, 10, pdf._c(risk_label), align="C")

    pdf.ln(15)

    # ── Employee info ─────────────────────────────────────────────────────
    pdf.set_text_color(*C_TEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(2)
    pdf.kv_row("Employee", report.employee)
    pdf.kv_row("Period", report.date_range)
    if report.timezone:
        pdf.kv_row("Timezone", report.timezone)
    pdf.kv_row("Generated", report.generated_at.strftime("%Y-%m-%d %H:%M"))
    pdf.ln(4)

    # ── Summary stat boxes ────────────────────────────────────────────────
    box_y = pdf.get_y()
    box_h = 30
    margin = 15
    total_w = 180
    box_w = (total_w - 3 * 4) / 4  # 4 boxes, 4mm gap between

    total_h_str = f"{ta.total_duration_hours:.1f}h" if ta else "N/A"
    active_h_str = f"{ta.total_active_hours:.1f}h" if ta else "N/A"
    activity_str = f"{ta.overall_activity_pct:.0f}%" if ta else "N/A"
    sessions_str = str(ta.total_sessions) if ta else "N/A"
    activity_col = C_GREEN if ta and ta.overall_activity_pct >= 60 else (C_ORANGE if ta and ta.overall_activity_pct >= 40 else C_RED)

    for i, (lbl, val, sub, col) in enumerate([
        ("Total Hours", total_h_str, "logged", C_ACCENT),
        ("Active Hours", active_h_str, "keyboard+mouse", C_GREEN),
        ("Activity Rate", activity_str, "avg across sessions", activity_col),
        ("Sessions", sessions_str, "work sessions", C_ACCENT),
    ]):
        bx = margin + i * (box_w + 4)
        pdf.stat_box(bx, box_y, box_w, box_h, lbl, val, sub, val_color=col)

    pdf.set_y(box_y + box_h + 6)

    # ── Activity Dot Matrix ───────────────────────────────────────────────
    pdf.section_band("Activity Overview", *C_DARK)

    if ta and ta.daily_breakdown:
        import re as _re

        # Build date → activity map from daily_breakdown and input_sessions
        date_activity: dict = {}
        for d, h in ta.daily_breakdown.items():
            date_activity[d] = h  # hours; we'll overlay with activity below

        # Build date → activity_pct from input_sessions
        day_pcts: dict = {}
        for s in report.input_sessions:
            d = s.get("date", "")
            if not d:
                continue
            raw_pct = s.get("activity_pct", "0")
            try:
                pct = float(str(raw_pct).replace("%", "").strip())
            except Exception:
                pct = 0.0
            if d not in day_pcts:
                day_pcts[d] = []
            day_pcts[d].append(pct)
        avg_day_pcts = {d: sum(v) / len(v) for d, v in day_pcts.items() if v}

        # If structured sections have daily_patterns, use those
        if ss and ss.daily_patterns:
            for dp in ss.daily_patterns:
                if dp.date and dp.activity_pct > 0:
                    avg_day_pcts[dp.date] = dp.activity_pct

        sorted_dates = sorted(date_activity.keys())

        dot_size = 7
        dot_gap = 2
        cols_per_row = 7  # 7 days per row (week)
        row_y = pdf.get_y()

        # Legend
        legend_x = margin
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_GRAY_TEXT)
        pdf.set_y(pdf.get_y())
        for lbl, col in [("High (>70%)", C_GREEN), ("Medium (30-70%)", C_ORANGE), ("Low (<30%)", C_RED), ("No data", (200, 200, 200))]:
            pdf.set_fill_color(*col)
            pdf.set_draw_color(*col)
            pdf.set_xy(legend_x, pdf.get_y())
            pdf.rect(legend_x, pdf.get_y() + 1, 4, 4, style="F")
            pdf.set_x(legend_x + 6)
            pdf.cell(22, 6, lbl)
            legend_x += 30
        pdf.ln(6)

        # Weekday headers
        row_y = pdf.get_y() + 1
        day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for di, dl in enumerate(day_labels):
            cx = margin + di * (dot_size + dot_gap)
            pdf.set_xy(cx, row_y)
            pdf.set_font("Helvetica", "B", 6)
            pdf.set_text_color(*C_GRAY_TEXT)
            pdf.cell(dot_size, 4, dl, align="C")
        pdf.ln(4)
        row_y = pdf.get_y() + 1

        for idx, d in enumerate(sorted_dates):
            col_idx = idx % cols_per_row
            row_idx = idx // cols_per_row
            cx = margin + col_idx * (dot_size + dot_gap)
            cy = row_y + row_idx * (dot_size + dot_gap)

            pct = avg_day_pcts.get(d, -1)
            if pct < 0:
                dot_col = (210, 210, 210)
            elif pct >= 70:
                dot_col = C_GREEN
            elif pct >= 30:
                dot_col = C_ORANGE
            else:
                dot_col = C_RED

            pdf.set_fill_color(*dot_col)
            pdf.set_draw_color(*dot_col)
            pdf.ellipse(cx, cy, dot_size, dot_size, style="F")

            # Date tooltip inside dot (tiny)
            try:
                day_num = str(int(d.split("-")[2]))
            except Exception:
                day_num = ""
            pdf.set_xy(cx, cy + 1)
            pdf.set_font("Helvetica", "", 5)
            pdf.set_text_color(*C_WHITE)
            pdf.cell(dot_size, dot_size - 2, day_num, align="C")

        n_rows = (len(sorted_dates) + cols_per_row - 1) // cols_per_row
        pdf.set_y(row_y + n_rows * (dot_size + dot_gap) + 4)
    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*C_GRAY_TEXT)
        pdf.cell(0, 8, "No timesheet data available for activity visualization.")
        pdf.ln(8)

    # ══════════════════════════════════════════════════════════════════════
    # PAGE 2: Key Observations
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_band("Key Observations")

    if ss:
        # ── Strengths ─────────────────────────────────────────────────────
        if ss.strengths:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*C_ACCENT)
            pdf.cell(0, 8, "Strengths", new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*C_ACCENT)
            pdf.line(15, pdf.get_y(), 195, pdf.get_y())
            pdf.ln(2)
            for item in ss.strengths:
                pdf.bullet_item(item)
            pdf.ln(3)

        # ── Consistency Indicators ────────────────────────────────────────
        if ss.consistency_indicators:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*C_ACCENT)
            pdf.cell(0, 8, "Consistency Indicators", new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*C_ACCENT)
            pdf.line(15, pdf.get_y(), 195, pdf.get_y())
            pdf.ln(2)
            for item in ss.consistency_indicators:
                pdf.bullet_item(item)
            pdf.ln(3)

        # ── Work Style Observations ───────────────────────────────────────
        if ss.work_style_observations:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*C_ACCENT)
            pdf.cell(0, 8, "Work Style Observations", new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*C_ACCENT)
            pdf.line(15, pdf.get_y(), 195, pdf.get_y())
            pdf.ln(2)
            for item in ss.work_style_observations:
                pdf.bullet_item(item)
            pdf.ln(3)

        # ── Overall Efficiency Assessment ─────────────────────────────────
        if ss.overall_efficiency_assessment:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*C_ACCENT)
            pdf.cell(0, 8, "Overall Efficiency Assessment", new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*C_ACCENT)
            pdf.line(15, pdf.get_y(), 195, pdf.get_y())
            pdf.ln(2)
            pdf.set_fill_color(*C_GRAY_LIGHT)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*C_TEXT)
            pdf.set_x(15)
            pdf.multi_cell(0, 6, pdf._c(ss.overall_efficiency_assessment), fill=True)
            pdf.ln(3)

        # ── Performance Overview ──────────────────────────────────────────
        if ss.performance_overview:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*C_ACCENT)
            pdf.cell(0, 8, "Performance Overview", new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*C_ACCENT)
            pdf.line(15, pdf.get_y(), 195, pdf.get_y())
            pdf.ln(2)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*C_TEXT)
            pdf.multi_cell(0, 6, pdf._c(ss.performance_overview))
            pdf.ln(3)

    else:
        # Fallback when no structured sections: use work_summary
        if report.work_summary:
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*C_TEXT)
            # Strip markdown formatting for PDF
            import re as _re
            clean = _re.sub(r'#+\s*', '', report.work_summary)
            clean = _re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
            clean = _re.sub(r'\*(.+?)\*', r'\1', clean)
            pdf.multi_cell(0, 5.5, pdf._c(clean[:3000]))

    # ── Risk Assessment box ───────────────────────────────────────────────
    pdf.ln(2)
    box_y2 = pdf.get_y()
    if box_y2 + 28 > 270:
        pdf.add_page()
        box_y2 = pdf.get_y()

    pdf.set_fill_color(*C_GRAY_LIGHT)
    pdf.set_draw_color(200, 200, 200)
    pdf.rect(15, box_y2, 180, 28, style="FD")
    pdf.set_xy(18, box_y2 + 3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*C_GRAY_TEXT)
    pdf.cell(60, 6, "Risk Assessment")
    pdf.set_xy(18, box_y2 + 10)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*risk_color)
    pdf.cell(60, 10, pdf._c(risk_label))
    pdf.set_xy(80, box_y2 + 3)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*C_GRAY_TEXT)
    pdf.cell(30, 6, f"Score: {risk.risk_score:.0f}/100")
    if risk.fraud_assessment:
        pdf.set_xy(80, box_y2 + 10)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_TEXT)
        pdf.multi_cell(112, 5, pdf._c(risk.fraud_assessment[:220]))

    pdf.set_y(box_y2 + 32)

    # ══════════════════════════════════════════════════════════════════════
    # PAGE 3: Daily Work Patterns
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_band("Daily Work Patterns")

    # Build daily data: prefer structured_sections, fallback to timesheet
    daily_rows = []
    if ss and ss.daily_patterns:
        for dp in ss.daily_patterns:
            daily_rows.append({
                "date": dp.date,
                "hours": dp.hours_worked,
                "activity": dp.activity_pct,
                "sessions": dp.session_count,
                "time_range": dp.time_range,
                "commentary": dp.commentary,
            })
    elif ta and ta.daily_breakdown:
        # Build from raw data
        day_hours = dict(ta.daily_breakdown)
        day_sessions: dict = {}
        day_start: dict = {}
        day_end: dict = {}
        day_pct2: dict = {}
        for s in report.input_sessions:
            d = s.get("date", "")
            if not d:
                continue
            day_sessions[d] = day_sessions.get(d, 0) + 1
            ts = s.get("time_start", "")
            te = s.get("time_end", "")
            if ts and (d not in day_start or ts < day_start[d]):
                day_start[d] = ts
            if te and (d not in day_end or te > day_end[d]):
                day_end[d] = te
            raw_pct = s.get("activity_pct", "0")
            try:
                pct = float(str(raw_pct).replace("%", "").strip())
            except Exception:
                pct = 0.0
            if d not in day_pct2:
                day_pct2[d] = []
            day_pct2[d].append(pct)

        for d in sorted(day_hours.keys()):
            avg_pct = sum(day_pct2.get(d, [0])) / max(len(day_pct2.get(d, [0])), 1)
            t_start = day_start.get(d, "")
            t_end = day_end.get(d, "")
            t_range = f"{t_start[:5]} - {t_end[:5]}" if t_start and t_end else ""
            daily_rows.append({
                "date": d,
                "hours": day_hours[d],
                "activity": avg_pct,
                "sessions": day_sessions.get(d, 0),
                "time_range": t_range,
                "commentary": "",
            })

    if daily_rows:
        # Table header
        col_ws = [32, 22, 25, 20, 30, 51]
        headers = ["Date", "Hours", "Activity", "Sessions", "Time Range", "Notes"]
        header_y = pdf.get_y()

        pdf.set_fill_color(*C_DARK)
        pdf.set_text_color(*C_WHITE)
        pdf.set_font("Helvetica", "B", 9)
        x = 15
        for hw, ht in zip(col_ws, headers):
            pdf.set_xy(x, header_y)
            pdf.cell(hw, 7, ht, fill=True, border=0, align="C")
            x += hw
        pdf.ln(7)

        for ri, row in enumerate(daily_rows):
            row_y3 = pdf.get_y()
            if row_y3 + 12 > 272:
                pdf.add_page()
                pdf.section_band("Daily Work Patterns (continued)")
                # Redraw header
                header_y = pdf.get_y()
                pdf.set_fill_color(*C_DARK)
                pdf.set_text_color(*C_WHITE)
                pdf.set_font("Helvetica", "B", 9)
                x = 15
                for hw, ht in zip(col_ws, headers):
                    pdf.set_xy(x, header_y)
                    pdf.cell(hw, 7, ht, fill=True, border=0, align="C")
                    x += hw
                pdf.ln(7)
                row_y3 = pdf.get_y()

            bg = C_WHITE if ri % 2 == 0 else C_GRAY_LIGHT
            pdf.set_fill_color(*bg)
            pdf.set_draw_color(225, 225, 225)
            pdf.rect(15, row_y3, 180, 9, style="FD")

            pct = row.get("activity", 0)
            act_col = C_GREEN if pct >= 60 else (C_ORANGE if pct >= 30 else C_RED)

            vals = [
                row.get("date", ""),
                f"{row.get('hours', 0):.1f}h",
                f"{pct:.0f}%",
                str(row.get("sessions", 0)),
                row.get("time_range", ""),
                row.get("commentary", ""),
            ]
            x = 15
            for vi, (vw, val) in enumerate(zip(col_ws, vals)):
                pdf.set_xy(x, row_y3 + 1)
                pdf.set_font("Helvetica", "", 8)
                # Activity column gets color
                if vi == 2:
                    pdf.set_text_color(*act_col)
                    pdf.set_font("Helvetica", "B", 8)
                else:
                    pdf.set_text_color(*C_TEXT)
                clipped = str(val)[:28] if vi == 5 else str(val)[:20]
                pdf.cell(vw, 7, pdf._c(clipped), align="C" if vi < 4 else "L")
                x += vw

            # Activity bar inside the activity cell
            bar_x = 15 + col_ws[0] + col_ws[1]
            bar_w = col_ws[2] - 4
            bar_fill = max(1, int(bar_w * pct / 100))
            pdf.set_fill_color(220, 220, 220)
            pdf.rect(bar_x + 2, row_y3 + 6, bar_w, 2, style="F")
            pdf.set_fill_color(*act_col)
            pdf.rect(bar_x + 2, row_y3 + 6, bar_fill, 2, style="F")

            pdf.ln(9)

    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*C_GRAY_TEXT)
        pdf.cell(0, 8, "No daily breakdown data available.")
        pdf.ln(8)

    # ══════════════════════════════════════════════════════════════════════
    # PAGE 4: Suspicious Activity
    # ══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_band("Suspicious Activity", *C_RED)

    # Collect suspicious data
    susp_rows = []
    if ss and ss.suspicious_sessions:
        for s in ss.suspicious_sessions:
            susp_rows.append({
                "date": s.get("date", ""),
                "started": s.get("started", ""),
                "ended": s.get("ended", ""),
                "total": s.get("total_time", ""),
                "reason": s.get("reason", ""),
            })
    # Also add temporal contradictions from cross_analysis
    if report.cross_analysis and report.cross_analysis.temporal_contradictions:
        for tc in report.cross_analysis.temporal_contradictions:
            susp_rows.append({
                "date": "",
                "started": "",
                "ended": "",
                "total": "",
                "reason": tc,
            })
    # Add anomalies flagged as high severity
    if ta and ta.anomalies:
        for a in ta.anomalies:
            if a.severity in ("high", "critical"):
                susp_rows.append({
                    "date": a.session_date,
                    "started": a.session_time[:5] if a.session_time else "",
                    "ended": "",
                    "total": "",
                    "reason": a.description,
                })

    if not susp_rows:
        pdf.set_fill_color(230, 255, 240)
        pdf.set_draw_color(39, 174, 96)
        pdf.rect(15, pdf.get_y(), 180, 18, style="FD")
        pdf.set_xy(15, pdf.get_y() + 4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*C_GREEN)
        pdf.cell(180, 10, "No suspicious activity detected.", align="C")
        pdf.ln(20)
    else:
        susp_col_ws = [28, 22, 22, 22, 86]
        susp_headers = ["Date", "Started", "Ended", "Total Time", "Reason"]

        # Table header
        hdr_y = pdf.get_y()
        pdf.set_fill_color(*C_RED)
        pdf.set_text_color(*C_WHITE)
        pdf.set_font("Helvetica", "B", 9)
        x = 15
        for hw, ht in zip(susp_col_ws, susp_headers):
            pdf.set_xy(x, hdr_y)
            pdf.cell(hw, 7, ht, fill=True, border=0, align="C")
            x += hw
        pdf.ln(7)

        for si, row in enumerate(susp_rows):
            row_y4 = pdf.get_y()
            if row_y4 + 10 > 272:
                pdf.add_page()
                pdf.section_band("Suspicious Activity (continued)", *C_RED)
                hdr_y = pdf.get_y()
                pdf.set_fill_color(*C_RED)
                pdf.set_text_color(*C_WHITE)
                pdf.set_font("Helvetica", "B", 9)
                x = 15
                for hw, ht in zip(susp_col_ws, susp_headers):
                    pdf.set_xy(x, hdr_y)
                    pdf.cell(hw, 7, ht, fill=True, border=0, align="C")
                    x += hw
                pdf.ln(7)
                row_y4 = pdf.get_y()

            # Alternating red-tinted rows
            bg = (255, 240, 240) if si % 2 == 0 else (255, 230, 230)
            pdf.set_fill_color(*bg)
            pdf.set_draw_color(240, 200, 200)
            needed_h = max(9, min(18, 9 + len(row.get("reason", "")) // 60 * 5))
            pdf.rect(15, row_y4, 180, needed_h, style="FD")

            vals = [
                row.get("date", ""),
                row.get("started", ""),
                row.get("ended", ""),
                row.get("total", ""),
                row.get("reason", ""),
            ]
            x = 15
            for vi, (vw, val) in enumerate(zip(susp_col_ws, vals)):
                pdf.set_xy(x, row_y4 + 1.5)
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(*C_RED if vi < 4 else C_TEXT)
                if vi == 4:
                    pdf.multi_cell(vw, 5, pdf._c(str(val)[:180]))
                    pdf.set_y(row_y4 + needed_h)
                else:
                    pdf.cell(vw, needed_h - 2, pdf._c(str(val)[:14]), align="C")
                x += vw

            pdf.set_y(row_y4 + needed_h + 1)

    # ── Recommendations at the bottom of page 4 ───────────────────────────
    if risk.recommendations:
        pdf.ln(3)
        if pdf.get_y() + 40 > 272:
            pdf.add_page()
        pdf.section_band("Recommendations", *C_ACCENT)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*C_TEXT)
        for rec in risk.recommendations:
            pdf.bullet_item(rec)

    return bytes(pdf.output())


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

    from department_config import DEPARTMENT_RULES, DEPARTMENT_DISPLAY_NAMES, DEFAULT_DEPARTMENT
    dept_options = list(DEPARTMENT_RULES.keys())
    dept_labels = [DEPARTMENT_DISPLAY_NAMES.get(d, d) for d in dept_options]
    selected_dept_label = st.radio(
        "Employee Department",
        options=dept_labels,
        index=0,
        help="Select the employee's department so the audit uses the right classification rules.",
    )
    selected_department = dept_options[dept_labels.index(selected_dept_label)]

    max_screenshots = st.slider(
        "Max Screenshots to Analyze",
        min_value=0,
        max_value=100,
        value=50,
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
            employee_email="",
            assigned_domains=[],
            progress_callback=update_progress,
            department=selected_department,
        )
        import gc
        gc.collect()

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
    from department_config import get_department_display_name as _dept_name
    dept_display = _dept_name(report.department)
    st.markdown(
        f'<h2 class="txt" style="margin:0;">Audit Report</h2>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="txt-muted" style="margin-top:0.2rem;">'
        f'{report.employee} &nbsp;·&nbsp; {report.date_range}'
        f' &nbsp;·&nbsp; <span style="color:#6366f1;font-weight:600;">{dept_display}</span>'
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
# SECTION 0 — Input Data (what the user provided)
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-header">📂 Input Data</div>', unsafe_allow_html=True)

# Employee info bar
info_parts = [f"<strong>{report.employee}</strong>"]
if report.date_range:
    info_parts.append(f"Period: {report.date_range}")
if report.timezone:
    info_parts.append(f"Timezone: {report.timezone}")
if report.input_screenshot_count > 0:
    info_parts.append(f"Screenshots: {report.input_screenshot_count} ({report.input_screenshot_date_range})")
st.markdown(
    f'<div class="summary-box" style="padding:1rem 1.5rem;">'
    f'{" &nbsp;·&nbsp; ".join(info_parts)}'
    f'</div>',
    unsafe_allow_html=True,
)

# Sessions table
if report.input_sessions:
    import pandas as pd
    sessions_df = pd.DataFrame(report.input_sessions)

    # Rename columns for display
    col_rename = {
        "date": "Date",
        "time_start": "Start",
        "time_end": "End",
        "duration": "Duration",
        "activity_pct": "Activity",
        "project": "Project",
        "task": "Task",
    }
    display_cols = [c for c in col_rename if c in sessions_df.columns]
    sessions_display = sessions_df[display_cols].rename(columns=col_rename)

    with st.expander(f"View {len(report.input_sessions)} Work Sessions (raw input)", expanded=False):
        st.dataframe(
            sessions_display,
            use_container_width=True,
            hide_index=True,
            height=min(400, 35 * len(sessions_display) + 38),
        )
elif report.input_screenshot_count == 0:
    st.caption("No timesheet sessions or screenshots were provided.")

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Work Summary (the main narrative the user reads first)
# ══════════════════════════════════════════════════════════════════════════════

if report.work_summary:
    st.markdown('<div class="section-header">📝 Detailed Audit Report</div>', unsafe_allow_html=True)
    st.markdown(report.work_summary)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1b — Structured Report Sections (Key Observations, Daily Patterns)
# ══════════════════════════════════════════════════════════════════════════════

if report.structured_sections:
    ss_ui = report.structured_sections

    st.markdown('<div class="section-header">📋 Key Observations</div>', unsafe_allow_html=True)

    col_left, col_right = st.columns(2)

    with col_left:
        if ss_ui.strengths:
            st.markdown("**Strengths**")
            for item in ss_ui.strengths:
                st.markdown(f'<div class="finding-card positive">{item}</div>', unsafe_allow_html=True)

        if ss_ui.consistency_indicators:
            st.markdown("**Consistency Indicators**")
            for item in ss_ui.consistency_indicators:
                st.markdown(f'<div class="finding-card fact">{item}</div>', unsafe_allow_html=True)

    with col_right:
        if ss_ui.work_style_observations:
            st.markdown("**Work Style Observations**")
            for item in ss_ui.work_style_observations:
                st.markdown(f'<div class="finding-card interpret">{item}</div>', unsafe_allow_html=True)

    if ss_ui.overall_efficiency_assessment:
        st.markdown("**Overall Efficiency Assessment**")
        st.markdown(
            f'<div class="summary-box" style="padding:1rem 1.5rem;">{ss_ui.overall_efficiency_assessment}</div>',
            unsafe_allow_html=True,
        )

    if ss_ui.performance_overview:
        st.markdown("**Performance Overview**")
        st.markdown(
            f'<div class="summary-box" style="padding:1rem 1.5rem;">{ss_ui.performance_overview}</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    # ── Daily patterns table ──
    if ss_ui.daily_patterns:
        st.markdown('<div class="section-header">📅 Daily Work Patterns</div>', unsafe_allow_html=True)

        import pandas as pd
        dp_rows = []
        for dp in ss_ui.daily_patterns:
            pct = dp.activity_pct
            act_label = "High" if pct >= 60 else ("Medium" if pct >= 30 else "Low")
            dp_rows.append({
                "Date": dp.date,
                "Hours": f"{dp.hours_worked:.1f}h",
                "Activity": f"{pct:.0f}% ({act_label})",
                "Sessions": dp.session_count,
                "Time Range": dp.time_range,
                "Notes": dp.commentary,
            })
        if dp_rows:
            dp_df = pd.DataFrame(dp_rows)
            st.dataframe(dp_df, use_container_width=True, hide_index=True)

        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    # ── Suspicious sessions table ──
    if ss_ui.suspicious_sessions:
        st.markdown('<div class="section-header">🚨 Suspicious Sessions</div>', unsafe_allow_html=True)
        for s in ss_ui.suspicious_sessions:
            st.markdown(
                f'<div class="finding-card error">'
                f'<strong>{s.get("date", "")} {s.get("started", "")} – {s.get("ended", "")}</strong>'
                f' &nbsp;·&nbsp; {s.get("total_time", "")}'
                f'<br><span style="font-size:0.9rem;">{s.get("reason", "")}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Risk Score overview
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-header">📊 Analysis Overview</div>', unsafe_allow_html=True)

if risk.key_findings:
    for finding in risk.key_findings:
        st.markdown(f'<div class="finding-card">{finding}</div>', unsafe_allow_html=True)

# ── Score Breakdown (what contributed to the risk score) ──────────────────
if risk.score_breakdown:
    with st.expander(f"Detailed Observations ({len(risk.score_breakdown)} patterns noted)", expanded=False):
        for b in risk.score_breakdown:
            st.markdown(
                f'<div class="anomaly-row">'
                f'<span class="txt-muted" style="min-width:160px;font-weight:600;">{b.signal_name}</span>'
                f'<span class="txt" style="flex:1;">{b.description}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

# ── Recommendations ───────────────────────────────────────────────────────
if risk.recommendations:
    st.markdown('<div class="section-header">📋 Recommendations</div>', unsafe_allow_html=True)
    for r in risk.recommendations:
        st.markdown(f'<div class="finding-card positive">{r}</div>', unsafe_allow_html=True)

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

    # Individual classifications with screenshot thumbnails
    if sa.classifications:
        with st.expander(f"View {len(sa.classifications)} Classifications", expanded=True):
            for c in sa.classifications:
                cat_colors = {
                    "work": "#186840",
                    "non_work": "#9B1515",
                    "idle": "#A89070",
                    "uncertain": "#C9A84C",
                }
                cat_color = cat_colors.get(c.category.value, "#A89070")

                # Show screenshot thumbnail + classification side by side
                if c.image_b64:
                    import base64 as b64mod
                    col_img, col_info = st.columns([1, 2])
                    with col_img:
                        try:
                            st.image(
                                b64mod.b64decode(c.image_b64),
                                caption=c.timestamp,
                                use_container_width=True,
                            )
                        except Exception:
                            st.caption(f"📷 {c.timestamp}")
                    with col_info:
                        st.markdown(
                            f'<div style="padding:0.5rem 0;">'
                            f'<span style="color:{cat_color};font-weight:700;font-size:1.1rem;">'
                            f'{c.category.value.upper().replace("_", " ")}</span>'
                            f' &nbsp; <span class="txt-muted">{c.confidence:.0%} confidence</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f'<div class="txt" style="font-size:0.9rem;line-height:1.5;">{c.description}</div>',
                            unsafe_allow_html=True,
                        )
                        if c.applications_visible:
                            apps_str = ", ".join(c.applications_visible)
                            st.markdown(
                                f'<div class="txt-muted" style="font-size:0.8rem;margin-top:0.3rem;">'
                                f'Apps: {apps_str}</div>',
                                unsafe_allow_html=True,
                            )
                        if c.reasoning:
                            st.markdown(
                                f'<div class="txt-faint" style="font-size:0.78rem;margin-top:0.3rem;">'
                                f'{c.reasoning}</div>',
                                unsafe_allow_html=True,
                            )
                    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
                else:
                    # No thumbnail available — show text-only row
                    st.markdown(
                        f'<div class="anomaly-row">'
                        f'<span class="txt-faint" style="min-width:140px;">{c.timestamp}</span>'
                        f'<span style="color:{cat_color};font-weight:600;min-width:80px;">{c.category.value}</span>'
                        f'<span class="txt-muted" style="min-width:50px;">{c.confidence:.0%}</span>'
                        f'<span class="txt" style="flex:1;">{c.description[:100]}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    # ── Advanced Analysis Sub-sections ──────────────────────────────────

    # Repeated frames (strongest fraud signal)
    if sa.repeated_frames:
        st.markdown(
            f'<div class="finding-card error" style="border-left-width:4px;padding:0.75rem 1rem;">'
            f'<strong>CRITICAL: {len(sa.repeated_frames)} repeated identical frame(s) detected</strong>'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.expander(f"View {len(sa.repeated_frames)} Repeated Frames", expanded=True):
            for rf in sa.repeated_frames:
                st.markdown(
                    f'<div class="anomaly-row">'
                    f'<span class="severity-dot severity-high"></span>'
                    f'<span class="txt" style="flex:1;">'
                    f'<strong>{rf.first_occurrence}</strong> → <strong>{rf.repeat_occurrence}</strong> '
                    f'({rf.time_gap_minutes:.0f} min gap, {rf.similarity_score:.0%} similar)'
                    f'</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if rf.visible_content:
                    st.caption(f"Visible: {rf.visible_content[:150]}")

    # Tab switching analysis
    if sa.tab_switching_analysis and sa.tab_switching_analysis.loop_detected:
        st.markdown(
            f'<div class="finding-card warning" style="border-left-width:4px;">'
            f'Tab-switching loop detected: {sa.tab_switching_analysis.loop_count} loops, '
            f'max {sa.tab_switching_analysis.max_tabs_visible} tabs visible'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Monitor inconsistencies
    if sa.monitor_inconsistencies:
        with st.expander(f"View {len(sa.monitor_inconsistencies)} Monitor Inconsistencies"):
            for mi in sa.monitor_inconsistencies:
                st.markdown(
                    f'<div class="finding-card warning">'
                    f'{mi.date}: {mi.single_monitor_count} single-monitor + {mi.dual_monitor_count} dual-monitor screenshots'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # Third-party accounts
    if sa.third_party_accounts:
        st.markdown(
            f'<div class="finding-card error" style="border-left-width:4px;">'
            f'<strong>SECURITY: {len(sa.third_party_accounts)} third-party account(s) detected</strong>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for tp in sa.third_party_accounts:
            st.markdown(
                f'<div class="anomaly-row">'
                f'<span class="severity-dot severity-high"></span>'
                f'<span class="txt-faint" style="min-width:140px;">{tp.timestamp}</span>'
                f'<span class="txt" style="flex:1;">Found: <strong>{tp.email_found}</strong> (expected: {tp.expected_email})</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Unauthorized access events
    if sa.unauthorized_access_events:
        with st.expander(f"View {len(sa.unauthorized_access_events)} Unauthorized Access Events"):
            for ua in sa.unauthorized_access_events:
                st.markdown(
                    f'<div class="anomaly-row">'
                    f'<span class="severity-dot severity-medium"></span>'
                    f'<span class="txt-faint" style="min-width:140px;">{ua.timestamp}</span>'
                    f'<span class="txt" style="flex:1;">{ua.domain}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # Suspicious sites
    if sa.suspicious_sites:
        with st.expander(f"View {len(sa.suspicious_sites)} Suspicious Sites"):
            for ss_site in sa.suspicious_sites:
                st.markdown(
                    f'<div class="anomaly-row">'
                    f'<span class="severity-dot severity-low"></span>'
                    f'<span class="txt-faint" style="min-width:140px;">{ss_site.timestamp}</span>'
                    f'<span class="txt" style="flex:1;"><strong>{ss_site.site_name}</strong> — {ss_site.reason}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4b — Suspicious Hours (FIX 5)
# ══════════════════════════════════════════════════════════════════════════════

if report.timesheet_analysis and report.timesheet_analysis.suspicious_windows:
    ta_s = report.timesheet_analysis
    st.markdown('<div class="section-header">⏱️ Suspicious Hours</div>', unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            render_metric("Suspicious Time", ta_s.suspicious_hours_total, f"{ta_s.suspicious_pct:.0f}% of total"),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            render_metric("Suspicious Windows", str(len(ta_s.suspicious_windows)), "time blocks flagged"),
            unsafe_allow_html=True,
        )

    with st.expander(f"View {len(ta_s.suspicious_windows)} Suspicious Windows", expanded=False):
        for sw in ta_s.suspicious_windows:
            st.markdown(
                f'<div class="anomaly-row">'
                f'<span class="severity-dot severity-high"></span>'
                f'<span class="txt-faint" style="min-width:120px;">{sw.start}</span>'
                f'<span class="txt-muted" style="min-width:80px;">{sw.duration}</span>'
                f'<span class="txt" style="flex:1;">{sw.reason}</span>'
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
        gap_color = "#186840" if ca.activity_gap < 15 else "#C9A84C" if ca.activity_gap < 30 else "#9B1515"
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
# SECTION 8b — Session Reports (FIX 9)
# ══════════════════════════════════════════════════════════════════════════════

if report.session_reports:
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-header">📋 Session-by-Session Reports</div>', unsafe_allow_html=True)

    for sr in report.session_reports:
        verdict_colors = {
            "legitimate": "#186840",
            "suspicious": "#C9A84C",
            "no_work_detected": "#9B1515",
            "confirmed_fraud": "#680808",
        }
        v_color = verdict_colors.get(sr.session_verdict, "#A89070")
        with st.expander(
            f"{sr.session_date}  —  {sr.duration} ({sr.duration_hours:.1f}h)  "
            f"| {sr.screenshots_in_session} screenshots  "
            f"| Score: {sr.session_risk_score:.0f}  "
            f"| {sr.session_verdict.upper().replace('_', ' ')}",
            expanded=(sr.session_verdict in ("confirmed_fraud", "suspicious")),
        ):
            st.markdown(
                f'<span style="color:{v_color};font-weight:700;font-size:1.1rem;">'
                f'{sr.session_verdict.upper().replace("_", " ")}</span>'
                f' &nbsp; Score: {sr.session_risk_score:.0f}/100',
                unsafe_allow_html=True,
            )
            if sr.findings:
                for f in sr.findings:
                    sev_class = f"severity-{f.severity}" if f.severity in ("low", "medium", "high") else "severity-high"
                    st.markdown(
                        f'<div class="anomaly-row">'
                        f'<span class="severity-dot {sev_class}"></span>'
                        f'<span class="txt-faint" style="min-width:120px;">{f.timestamp}</span>'
                        f'<span class="txt-muted" style="min-width:100px;">{f.finding_type}</span>'
                        f'<span class="txt" style="flex:1;">{f.description}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No findings — session appears legitimate.")

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Export & Reset
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

col_pdf, col_dl, col_reset = st.columns([1, 1, 1])
with col_pdf:
    pdf_bytes = generate_pdf_report(report)
    safe_name = report.employee.replace(" ", "_")
    safe_range = report.date_range.replace(" ", "_").replace("/", "-")
    st.download_button(
        "📄  Download PDF Report",
        data=pdf_bytes,
        file_name=f"audit_{safe_name}_{safe_range}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
with col_dl:
    report_json = json.dumps(
        report.model_dump(
            mode="json",
            exclude={"screenshot_analysis": {"classifications": {"__all__": {"image_b64"}}}},
        ),
        indent=2,
        default=str,
    )
    st.download_button(
        "📥  Download JSON Report",
        data=report_json,
        file_name=f"audit_{safe_name}_{safe_range}.json",
        mime="application/json",
        use_container_width=True,
    )
with col_reset:
    if st.button("🔄  New Audit", use_container_width=True):
        st.session_state.report = None
        st.rerun()
