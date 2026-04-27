"""
Department-specific configuration for the audit system.

Central single-source-of-truth for all per-department rules:
  - allowed tools / domains
  - work patterns
  - suspicious patterns
  - activity thresholds for scoring
  - role description injected into LLM prompts

Extend by adding a new key to DEPARTMENT_RULES.
"""

from __future__ import annotations

from typing import TypedDict


# ── Tools / domains allowed for EVERY department ─────────────────────────────
# These are NEVER flagged as non-work regardless of department.

GLOBAL_ALLOWED_TOOLS: list[str] = [
    # Project management & communication
    "ClickUp", "Telegram", "WhatsApp", "Slack", "Microsoft Teams", "Discord",
    "Zoom", "Google Meet",
    # Google suite
    "Gmail", "Google Drive", "Google Docs", "Google Sheets", "Google Slides",
    "Google Calendar", "Google Chrome",
    # Microsoft Office
    "Microsoft Word", "Microsoft Excel", "Microsoft PowerPoint",
    "Word", "Excel", "PowerPoint", "OneDrive",
    # AI tools — always work-related
    "ChatGPT", "Claude", "Gemini", "Copilot", "Perplexity", "Midjourney",
    # Work tracking
    "HiveDesk", "Trello", "Asana", "Notion", "Monday.com",
    # Generic browsers doing work
    "Google Search",
]

GLOBAL_ALLOWED_DOMAINS: list[str] = [
    # Communication
    "clickup.com",
    "telegram.org", "web.telegram.org", "t.me",
    "web.whatsapp.com", "whatsapp.com",
    "slack.com",
    "teams.microsoft.com",
    "zoom.us",
    "meet.google.com",
    # Google
    "gmail.com", "mail.google.com",
    "drive.google.com", "docs.google.com", "sheets.google.com",
    "slides.google.com", "calendar.google.com",
    "google.com", "google.",
    # Microsoft
    "office.com", "microsoft.com", "onedrive.live.com",
    "outlook.com", "outlook.office.com",
    # AI tools
    "chatgpt.com", "chat.openai.com", "openai.com",
    "claude.ai",
    "gemini.google.com",
    "copilot.microsoft.com",
    "perplexity.ai",
    "midjourney.com",
    # PM tools
    "notion.so", "trello.com", "asana.com", "monday.com",
    "hivedesk.com",
    # Video
    "youtube.com",  # context-dependent — flagged if entertainment but allowed if tutorial
]

# Domains that are never in the suspicious-site blocklist regardless of department
GLOBALLY_SAFE_DOMAINS: set[str] = {
    "clickup.com", "telegram.org", "web.telegram.org",
    "web.whatsapp.com", "slack.com", "teams.microsoft.com",
    "gmail.com", "mail.google.com", "drive.google.com",
    "docs.google.com", "sheets.google.com", "office.com",
    "microsoft.com", "chat.openai.com", "chatgpt.com",
    "claude.ai", "gemini.google.com", "hivedesk.com",
    "notion.so", "trello.com", "asana.com", "monday.com",
    "zoom.us", "meet.google.com",
}


# ── Per-department rule schema ────────────────────────────────────────────────

class DepartmentRule(TypedDict):
    role_description: str          # injected into LLM prompts
    work_tools: list[str]          # expected apps / software
    work_domains: list[str]        # expected browser domains
    work_patterns: list[str]       # textual descriptions for LLM
    suspicious_patterns: list[str] # textual descriptions for LLM
    activity_note: str             # LLM context on expected activity %
    low_activity_threshold: float  # below this % → lower-activity warning
    idle_ratio_threshold: float    # above this ratio → high-idle warning


# ── Department rule definitions ───────────────────────────────────────────────

DEPARTMENT_RULES: dict[str, DepartmentRule] = {

    # ── Developer ─────────────────────────────────────────────────────────────
    "developer": {
        "role_description": (
            "a WEB / SOFTWARE DEVELOPER (frontend, backend, or full-stack). "
            "Their normal work involves coding, debugging, code review, reading technical "
            "documentation, testing APIs, managing deployments, and collaborating with teams via "
            "code-review tools, chat, and issue trackers."
        ),
        "work_tools": [
            "VS Code", "Cursor", "WebStorm", "IntelliJ IDEA", "PyCharm",
            "Sublime Text", "Vim", "Neovim", "Android Studio", "Xcode",
            "Terminal", "Command Line", "PowerShell", "Bash", "iTerm",
            "GitHub", "GitLab", "Bitbucket",
            "Postman", "Insomnia", "Swagger UI", "Thunder Client",
            "Docker Desktop", "Kubernetes dashboard",
            "AWS Console", "GCP Console", "Azure Portal",
            "Vercel", "Netlify", "Cloudflare",
            "Figma", "Storybook",
            "pgAdmin", "DBeaver", "TablePlus", "MongoDB Compass",
            "CodeSandbox", "StackBlitz",
            "Jenkins", "CircleCI", "GitHub Actions",
            "localhost", "local dev server",
        ],
        "work_domains": [
            "github.com", "gitlab.com", "bitbucket.org",
            "stackoverflow.com", "stackexchange.com",
            "developer.mozilla.org", "mdn.mozilla.org",
            "npmjs.com", "yarnpkg.com", "pypi.org",
            "vercel.com", "netlify.com", "heroku.com", "render.com",
            "aws.amazon.com", "console.aws.amazon.com",
            "console.cloud.google.com",
            "portal.azure.com",
            "figma.com",
            "codepen.io", "codesandbox.io", "stackblitz.com", "replit.com",
            "dev.to", "medium.com",
            "news.ycombinator.com",
            "jira.atlassian.com", "confluence.atlassian.com",
            "localhost", "127.0.0.1",
            "reactjs.org", "nextjs.org", "vuejs.org", "angular.io",
            "tailwindcss.com", "nodejs.org", "python.org",
        ],
        "work_patterns": [
            "IDE or code editor with source code visible",
            "terminal or command line running commands",
            "GitHub / GitLab repository, pull request, or issue tracker",
            "browser with developer tools (DevTools, Network tab, Console) open",
            "browser showing technical documentation, MDN, Stack Overflow, npm",
            "API testing tool (Postman, Insomnia, Swagger)",
            "database client (pgAdmin, DBeaver, TablePlus)",
            "code review session",
            "deployment dashboard (Vercel, Netlify, AWS, GCP, Azure)",
            "local development server (localhost / 127.0.0.1)",
            "Docker or Kubernetes interface",
        ],
        "suspicious_patterns": [
            "personal social media feeds (Facebook timeline, Instagram, TikTok personal scroll)",
            "entertainment YouTube (music videos, vlogs, gaming streams)",
            "online shopping (Amazon product pages, eBay, AliExpress)",
            "gaming websites or launchers",
            "streaming platforms for personal entertainment (Netflix, Twitch gaming)",
            "dating apps or sites",
            "sports / entertainment news unrelated to work",
            "gambling or betting sites",
        ],
        "activity_note": (
            "Long focused sessions (4-8 hours) with moderate activity (35-60%) are COMPLETELY "
            "NORMAL for developers. Developers spend significant time READING code, debugging, "
            "reviewing PRs, and thinking — all of which show low keyboard/mouse input. "
            "Activity variance is expected: coding sessions are intense, reading sessions are quiet. "
            "Do NOT penalize developers for moderate activity during long sessions."
        ),
        "low_activity_threshold": 35.0,
        "idle_ratio_threshold": 0.65,
    },

    # ── Backoffice / Construction / Estimator ─────────────────────────────────
    "backoffice": {
        "role_description": (
            "a BACKOFFICE / CONSTRUCTION / ESTIMATOR employee. "
            "Their normal work involves processing documents, preparing estimates, working in "
            "spreadsheets, reviewing PDFs, handling company files, construction plans, "
            "project files, and internal email / admin tasks."
        ),
        "work_tools": [
            "Microsoft Excel", "Google Sheets",
            "Microsoft Word", "Google Docs",
            "Adobe Acrobat", "PDF reader", "Foxit PDF",
            "AutoCAD", "SketchUp", "construction software",
            "Bluebeam Revu", "Procore", "Buildertrend",
            "Outlook", "Gmail",
            "CRM software",
            "Estimation software", "Buildxact", "Planswift",
            "File Explorer", "Windows Explorer",
            "Scanner / scanner software",
            "Smartsheet",
        ],
        "work_domains": [
            "docs.google.com", "sheets.google.com", "drive.google.com",
            "office.com", "onedrive.live.com",
            "outlook.com", "outlook.office.com",
            "adobe.com", "acrobat.adobe.com",
            "smartsheet.com",
            "procore.com", "buildertrend.com",
            "buildxact.com", "planswift.com",
            "dropbox.com",
            "box.com",
        ],
        "work_patterns": [
            "spreadsheet (Excel or Google Sheets) open with data / estimates / project info",
            "Word document or Google Doc open for report or correspondence",
            "PDF viewer showing plans, drawings, estimates, or construction documents",
            "email client (Outlook, Gmail) composing or reviewing work emails",
            "file manager browsing company project folders or shared drives",
            "CRM or project management interface",
            "construction or estimation software interface",
            "data entry or form filling in admin tools",
            "multiple documents open for comparison or referencing",
            "scanning or uploading company documents",
        ],
        "suspicious_patterns": [
            "personal social media feeds (Facebook timeline, Instagram, TikTok personal scroll)",
            "entertainment YouTube (music, vlogs, gaming)",
            "online shopping",
            "gaming",
            "streaming for personal entertainment",
            "dating apps",
            "sports / entertainment sites unrelated to work",
        ],
        "activity_note": (
            "Backoffice work involves significant document reading, plan review, and data "
            "entry that produces moderate keyboard input. Activity of 30-70% is normal. "
            "Long periods spent reading PDFs, reviewing construction plans, or working in "
            "Excel with careful checking produce LOWER keyboard activity — this is LEGITIMATE. "
            "Do NOT flag document-heavy work, PDF review, or spreadsheet work as suspicious. "
            "Admin tasks naturally have more reading time than coding."
        ),
        "low_activity_threshold": 30.0,
        "idle_ratio_threshold": 0.70,
    },

    # ── Telemarketing / Sales ─────────────────────────────────────────────────
    "telemarketing": {
        "role_description": (
            "a TELEMARKETING / SALES employee. "
            "Their normal work involves CRM usage, email outreach, lead generation, "
            "Meta / Facebook business tools, LinkedIn prospecting, customer communication, "
            "and managing the full sales / outreach workflow."
        ),
        "work_tools": [
            "HubSpot", "Salesforce", "Pipedrive", "Monday CRM",
            "Gmail", "Outlook",
            "Meta Business Suite", "Facebook Ads Manager",
            "LinkedIn", "LinkedIn Sales Navigator",
            "WhatsApp Business",
            "Dialpad", "RingCentral", "VoIP software",
            "Google Sheets", "Excel",
            "Apollo.io", "Hunter.io", "Lemlist", "Reply.io",
            "Zoom", "Google Meet",
        ],
        "work_domains": [
            "hubspot.com", "salesforce.com", "pipedrive.com",
            "mail.google.com", "gmail.com",
            "outlook.com", "outlook.office.com",
            "business.facebook.com", "facebook.com",
            "ads.facebook.com", "adsmanager.facebook.com",
            "instagram.com",          # may be business usage for telemarketing
            "linkedin.com",
            "apollo.io", "hunter.io",
            "lemlist.com", "reply.io",
            "ringcentral.com", "dialpad.com",
            "sheets.google.com", "docs.google.com",
            "monday.com",
        ],
        "work_patterns": [
            "CRM interface open (HubSpot, Salesforce, Pipedrive, Monday CRM)",
            "email client composing or reading outreach messages",
            "Meta Business Suite or Facebook Ads Manager dashboard",
            "LinkedIn profile, search results, or Sales Navigator",
            "lead generation or outreach tool (Apollo, Hunter, Lemlist)",
            "WhatsApp Business or messaging conversation with a contact",
            "spreadsheet tracking leads, contacts, or pipeline",
            "VoIP / calling software ready or in use",
            "customer or prospect profile open in CRM",
            "Facebook or Instagram business page management",
            "calendar or scheduling tool for call / follow-up planning",
        ],
        "suspicious_patterns": [
            "personal social media feeds (TikTok, Twitter/X personal scrolling, Instagram personal feed)",
            "entertainment YouTube (music, vlogs, gaming)",
            "online shopping",
            "gaming",
            "streaming for personal entertainment (Netflix, Spotify music not related to work)",
            "dating apps",
            "sports / entertainment news unrelated to work",
            "gambling or betting sites",
        ],
        "activity_note": (
            "Telemarketing involves both active writing (email drafting, CRM entry, messaging) "
            "and passive reading (lead research, reviewing profiles, waiting on calls). "
            "Activity of 40-75% is normal. "
            "Facebook / Meta usage is EXPECTED and LEGITIMATE for telemarketing — "
            "do NOT flag it as suspicious. "
            "LinkedIn browsing is legitimate for lead research. "
            "Instagram business usage can be legitimate for outreach. "
            "Do NOT penalize communication-heavy work or social-platform outreach."
        ),
        "low_activity_threshold": 40.0,
        "idle_ratio_threshold": 0.65,
    },
}

VALID_DEPARTMENTS: list[str] = list(DEPARTMENT_RULES.keys())
DEFAULT_DEPARTMENT: str = "developer"

DEPARTMENT_DISPLAY_NAMES: dict[str, str] = {
    "developer": "Developer",
    "backoffice": "Backoffice / Construction / Estimator",
    "telemarketing": "Telemarketing / Sales",
}


# ── Helper functions ──────────────────────────────────────────────────────────

def get_department_rule(department: str) -> DepartmentRule:
    """Return the rule set for the given department; falls back to developer."""
    return DEPARTMENT_RULES.get(department, DEPARTMENT_RULES[DEFAULT_DEPARTMENT])


def get_department_display_name(department: str) -> str:
    """Human-readable department label."""
    return DEPARTMENT_DISPLAY_NAMES.get(department, department.replace("_", " ").title())


def get_all_work_domains(department: str) -> set[str]:
    """Return a merged set of global + department-specific work domains."""
    rule = get_department_rule(department)
    return set(GLOBAL_ALLOWED_DOMAINS) | set(rule["work_domains"])


def is_globally_safe_domain(domain: str) -> bool:
    """Return True if the domain is in the always-safe global list."""
    domain_lower = domain.lower()
    return any(safe in domain_lower for safe in GLOBALLY_SAFE_DOMAINS)


def get_department_thresholds(department: str) -> dict:
    """Return activity / idle thresholds for a department."""
    rule = get_department_rule(department)
    return {
        "low_activity_threshold": rule["low_activity_threshold"],
        "idle_ratio_threshold": rule["idle_ratio_threshold"],
    }
