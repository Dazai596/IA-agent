"""
Parser for HiveDesk timesheet exports.
Handles both HTML-based .xls exports and real .csv/.sql files.
Normalizes all data into the unified TimesheetData schema.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd

from models import TimesheetData, WorkSession

logger = logging.getLogger(__name__)


def _parse_duration(dur_str) -> timedelta:
    """Parse HH:MM:SS duration string into timedelta.
    Also handles pandas Timedelta/Timestamp objects and time objects.
    """
    # Handle non-string types that pandas might produce
    if isinstance(dur_str, timedelta):
        return dur_str
    if pd.isna(dur_str) if not isinstance(dur_str, str) else False:
        return timedelta()
    if hasattr(dur_str, 'total_seconds'):
        return timedelta(seconds=dur_str.total_seconds())
    if hasattr(dur_str, 'hour') and hasattr(dur_str, 'minute'):
        # datetime.time object
        return timedelta(hours=dur_str.hour, minutes=dur_str.minute, seconds=dur_str.second)

    dur_str = str(dur_str).strip()

    # Handle pandas Timedelta string format like "0 days 04:32:35"
    if "days" in dur_str.lower():
        match = re.match(r"(\d+)\s*days?\s+(\d+):(\d+):(\d+)", dur_str)
        if match:
            return timedelta(
                days=int(match.group(1)),
                hours=int(match.group(2)),
                minutes=int(match.group(3)),
                seconds=int(match.group(4)),
            )

    # Handle HH:MM:SS.mmm (with milliseconds)
    parts = dur_str.split(":")
    if len(parts) == 3:
        sec_part = parts[2].split(".")[0]  # Strip milliseconds
        return timedelta(
            hours=int(parts[0]),
            minutes=int(parts[1]),
            seconds=int(sec_part),
        )
    if len(parts) == 2:
        return timedelta(hours=int(parts[0]), minutes=int(parts[1]))

    # Handle fractional hours like "1.5h"
    frac_match = re.match(r"^(\d+(?:\.\d+)?)\s*h(?:ours?)?$", dur_str, re.IGNORECASE)
    if frac_match:
        return timedelta(hours=float(frac_match.group(1)))

    raise ValueError(f"Cannot parse duration: {dur_str}")


def _parse_activity_pct(val) -> float:
    """Parse activity percentage like '93 %' or '80%' into float."""
    if not isinstance(val, str) and pd.isna(val):
        return 0.0
    cleaned = str(val).replace("%", "").replace("\n", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        logger.warning(f"Failed to parse activity percentage: {repr(val)}")
        return 0.0


def _is_na(val) -> bool:
    """Universal NaN/None/NaT check that handles both pandas and Python types."""
    if val is None:
        return True
    if isinstance(val, str):
        return val.strip().lower() in ("nan", "", "nat", "none")
    try:
        return pd.isna(val)
    except (TypeError, ValueError):
        return False


def _detect_file_type(path: Path) -> str:
    """Detect whether the file is HTML-xls, real xlsx, CSV, or SQL.
    Content-based detection FIRST, then extension fallback.
    HiveDesk exports .xls files that are actually HTML — extension alone is unreliable.
    """
    with open(path, "rb") as f:
        header = f.read(200)
    # 1. Content-based detection (always wins over extension)
    if b"<html" in header.lower() or b"<table" in header.lower():
        return "html"
    if header[:4] == b"PK\x03\x04":
        return "xlsx"
    if b"CREATE TABLE" in header or b"INSERT INTO" in header:
        return "sql"
    # 2. Extension fallback only if content detection didn't match
    if path.suffix.lower() in (".xlsx", ".xls"):
        return "xlsx"
    return "csv"


def _find_header_row(df: pd.DataFrame) -> int:
    """
    Search through the first rows of a DataFrame to find the actual header row.
    HiveDesk HTML exports have title/metadata rows before the real column headers.
    Returns the row index containing the header, or -1 if not found.
    """
    required_keywords = {"duration", "activity", "active"}
    helpful_keywords = {"project", "task", "member", "worksession", "time start", "time end"}

    for idx in range(min(15, len(df))):
        row_values = [str(v).strip().lower() for v in df.iloc[idx].values]
        row_text = " ".join(row_values)

        required_found = sum(1 for kw in required_keywords if kw in row_text)
        helpful_found = sum(1 for kw in helpful_keywords if kw in row_text)

        if required_found >= 2 and helpful_found >= 1:
            return idx

    return -1


def _map_columns(df: pd.DataFrame) -> dict:
    """
    Map DataFrame columns to known field names by partial match.
    Returns a dict like {"project": "Project", "duration": "Duration", ...}
    """
    col_map = {}
    date_cols_found = 0

    for col in df.columns:
        col_lower = str(col).lower().strip()

        # Use more specific patterns to avoid false matches
        if re.match(r"^project$|^project\s*name$", col_lower) and "project" not in col_map:
            col_map["project"] = col
        elif ("team member" in col_lower or col_lower == "member") and "employee" not in col_map:
            col_map["employee"] = col
        elif re.match(r"^task$|^task\s*name$", col_lower) and "task" not in col_map:
            col_map["task"] = col
        elif "date start" in col_lower:
            col_map["date_start"] = col
        elif "date end" in col_lower:
            col_map["date_end"] = col
        elif "worksession date" in col_lower or (
            "date" in col_lower
            and "start" not in col_lower
            and "end" not in col_lower
            and "range" not in col_lower
        ):
            if date_cols_found == 0:
                col_map["date_start"] = col
            else:
                col_map["date_end"] = col
            date_cols_found += 1
        elif "time start" in col_lower and "time_start" not in col_map:
            col_map["time_start"] = col
        elif "time end" in col_lower and "time_end" not in col_map:
            col_map["time_end"] = col
        elif col_lower in ("type", "worksession type", "session type") or (
            "type" in col_lower
            and "time" not in col_lower
            and "date" not in col_lower
            and "session_type" not in col_map
        ):
            col_map["session_type"] = col
        elif "duration" in col_lower and "duration" not in col_map:
            col_map["duration"] = col
        elif "active time" in col_lower or (
            "active" in col_lower
            and "activity" not in col_lower
            and "active_time" not in col_map
        ):
            col_map["active_time"] = col
        elif "activity" in col_lower and "activity" not in col_map:
            col_map["activity"] = col
        elif "cost" in col_lower and "cost" not in col_map:
            col_map["cost"] = col

    # Fallback: if strict regex didn't match, try looser substring matching
    if "project" not in col_map:
        for col in df.columns:
            if "project" in str(col).lower() and "project" not in col_map:
                col_map["project"] = col
                break
    if "task" not in col_map:
        for col in df.columns:
            if "task" in str(col).lower() and "task" not in col_map:
                col_map["task"] = col
                break
    if "employee" not in col_map:
        for col in df.columns:
            if "member" in str(col).lower() and "employee" not in col_map:
                col_map["employee"] = col
                break

    return col_map


def _has_critical_columns(col_map: dict) -> bool:
    """Check that at least duration + one of (active_time, activity) were found."""
    return "duration" in col_map and ("active_time" in col_map or "activity" in col_map)


def _is_summary_row(row) -> bool:
    """
    Check if a row is a summary/total/footer row.
    Only matches cells where the ENTIRE value (stripped) is 'Total', 'Average',
    or starts with '*'. Does NOT match substrings inside real data.
    """
    for val in row:
        s = str(val).strip()
        s_lower = s.lower()
        if s_lower in ("total", "average", "avg", "totals"):
            return True
        if s.startswith("*"):
            return True
        if re.match(r"^\d+%?\s*\(avg\)", s_lower):
            return True
    return False


# ── Shared session builder (eliminates duplication) ─────────────────────────


def _build_sessions_from_df(
    df: pd.DataFrame,
    col_map: dict,
) -> tuple[list[WorkSession], str]:
    """
    Build WorkSession list from a DataFrame with mapped columns.
    Returns (sessions, employee_name).
    Shared by HTML, XLSX, and CSV parsers.
    """
    sessions: list[WorkSession] = []
    employee_name = ""

    for _, row in df.iterrows():
        project = str(row.get(col_map.get("project", ""), "--")).strip()
        employee = str(row.get(col_map.get("employee", ""), "")).strip()
        if employee and employee not in ("nan", ""):
            employee_name = employee

        task = str(row.get(col_map.get("task", ""), "--")).strip()
        date_start = str(row.get(col_map.get("date_start", ""), "")).strip()
        date_end = str(row.get(col_map.get("date_end", ""), "")).strip()
        time_start = str(row.get(col_map.get("time_start", ""), "")).strip()
        time_end = str(row.get(col_map.get("time_end", ""), "")).strip()
        session_type = str(row.get(col_map.get("session_type", ""), "")).strip()

        # Get raw values — use _is_na for universal NaN detection
        dur_raw = row.get(col_map.get("duration", ""), "00:00:00")
        active_raw = row.get(col_map.get("active_time", ""), "00:00:00")
        act_pct_raw = row.get(col_map.get("activity", ""), "0")

        if _is_na(dur_raw):
            dur_raw = "00:00:00"
        if _is_na(active_raw):
            active_raw = "00:00:00"
        if _is_na(act_pct_raw):
            act_pct_raw = "0"

        try:
            duration = _parse_duration(dur_raw)
            active_time = _parse_duration(active_raw)
            activity_pct = _parse_activity_pct(act_pct_raw)
        except (ValueError, IndexError) as e:
            logger.warning(f"Skipping row due to parse error: {e}")
            continue

        # Clean up nan strings
        if task in ("nan", "--", ""):
            task = "--"
        if date_end in ("nan", ""):
            date_end = date_start
        if time_end in ("nan", ""):
            time_end = ""

        # Parse cost if available
        cost_raw = row.get(col_map.get("cost", ""), None)
        cost = None
        if cost_raw is not None and not _is_na(cost_raw):
            try:
                cost = float(str(cost_raw).replace("$", "").replace(",", "").strip())
            except (ValueError, TypeError):
                pass

        sessions.append(
            WorkSession(
                project=project if project != "nan" else "--",
                employee=employee_name,
                task=task,
                date_start=date_start,
                date_end=date_end,
                time_start=time_start,
                time_end=time_end,
                session_type=session_type if session_type != "nan" else "",
                duration=duration,
                active_time=active_time,
                activity_pct=activity_pct,
                cost=cost,
            )
        )

    return sessions, employee_name


def _build_timesheet_data(
    sessions: list[WorkSession],
    employee_name: str,
    date_range_start: str = "",
    date_range_end: str = "",
    timezone: str = "Unknown",
) -> TimesheetData:
    """Build TimesheetData from sessions list. Shared by all parsers."""
    total_duration = sum((s.duration for s in sessions), timedelta())
    total_active = sum((s.active_time for s in sessions), timedelta())
    avg_activity = (
        sum(s.activity_pct for s in sessions) / len(sessions) if sessions else 0.0
    )

    logger.info(
        f"Parsed {len(sessions)} sessions: "
        f"total={total_duration}, active={total_active}, avg_activity={avg_activity:.1f}%"
    )

    return TimesheetData(
        employee=employee_name,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        timezone=timezone,
        sessions=sessions,
        total_duration=total_duration,
        total_active=total_active,
        avg_activity_pct=avg_activity,
    )


def _prepare_df(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Flatten columns, find header row, map columns, filter summary rows."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            col[-1] if isinstance(col, tuple) else col
            for col in df.columns
        ]

    col_map = _map_columns(df)
    if not _has_critical_columns(col_map):
        header_idx = _find_header_row(df)
        if header_idx >= 0:
            new_cols = [str(v).strip() for v in df.iloc[header_idx].values]
            df = df.iloc[header_idx + 1:].reset_index(drop=True)
            df.columns = new_cols
            col_map = _map_columns(df)

    if not _has_critical_columns(col_map):
        logger.error(
            f"CRITICAL: Could not find Duration/Active/Activity columns! "
            f"Available columns: {df.columns.tolist()}, Mapped: {col_map}"
        )

    df = df[~df.apply(_is_summary_row, axis=1)]
    df = df.dropna(how="all")

    logger.info(f"Column mapping: {col_map}")
    return df, col_map


# ── Format-specific parsers ─────────────────────────────────────────────────


def _parse_html_xls(path: Path) -> TimesheetData:
    """Parse HiveDesk HTML-based .xls export."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_html = f.read()

    tz_match = re.search(r"Timezone:\s*([^<]+)", raw_html)
    timezone = tz_match.group(1).strip() if tz_match else "Unknown"

    dr_match = re.search(r"Date Range:\s*([^<]+)", raw_html)
    date_range_str = dr_match.group(1).strip() if dr_match else ""

    date_range_start = ""
    date_range_end = ""
    if " - " in date_range_str:
        parts = date_range_str.split(" - ")
        date_range_start = parts[0].strip()
        date_range_end = parts[1].strip()

    dfs = pd.read_html(path)
    if not dfs:
        raise ValueError(f"No tables found in {path}")

    # Find the table with timesheet data
    df = None
    for candidate_df in dfs:
        prepared, col_map = _prepare_df(candidate_df.copy())
        if _has_critical_columns(col_map):
            df = prepared
            break

    if df is None:
        df, col_map = _prepare_df(dfs[0].copy())

    sessions, employee_name = _build_sessions_from_df(df, col_map)
    return _build_timesheet_data(
        sessions, employee_name,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        timezone=timezone,
    )


def _parse_xlsx(path: Path) -> TimesheetData:
    """Parse a real .xlsx Excel file using pandas/openpyxl."""
    df = pd.read_excel(path, engine="openpyxl")
    logger.info(f"XLSX columns: {df.columns.tolist()}")

    df, col_map = _prepare_df(df)
    sessions, employee_name = _build_sessions_from_df(df, col_map)
    return _build_timesheet_data(sessions, employee_name)


def _parse_csv(path: Path) -> TimesheetData:
    """Parse a CSV timesheet export using DuckDB for efficient loading,
    then use standard column mapping (not positional indices)."""
    try:
        with duckdb.connect() as con:
            # Use parameterized path to avoid injection
            con.execute(
                "CREATE TABLE timesheet AS SELECT * FROM read_csv_auto(?)",
                [str(path)],
            )
            rows = con.execute("SELECT * FROM timesheet").fetchdf()
    except Exception as e:
        logger.warning(f"DuckDB CSV parse failed ({e}), falling back to pandas.")
        rows = pd.read_csv(path)

    logger.info(f"CSV columns: {rows.columns.tolist()}")

    # Use the same column mapping logic as HTML/XLSX parsers
    rows, col_map = _prepare_df(rows)
    sessions, employee_name = _build_sessions_from_df(rows, col_map)
    return _build_timesheet_data(sessions, employee_name)


def parse_timesheet(path: str) -> TimesheetData:
    """
    Main entry point: detect format and parse timesheet file.
    Supports HTML-xls (HiveDesk), CSV, and SQL dump formats.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Timesheet file not found: {path}")

    file_type = _detect_file_type(file_path)
    logger.info(f"Detected timesheet format: {file_type} for {path}")

    if file_type == "html":
        return _parse_html_xls(file_path)
    elif file_type == "xlsx":
        return _parse_xlsx(file_path)
    elif file_type == "csv":
        return _parse_csv(file_path)
    elif file_type == "sql":
        raise NotImplementedError("SQL dump parsing not yet implemented — export as CSV instead.")
    else:
        raise ValueError(f"Unknown file type for: {path}")
