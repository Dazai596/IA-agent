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


def _parse_duration(dur_str: str) -> timedelta:
    """Parse HH:MM:SS duration string into timedelta."""
    dur_str = dur_str.strip()
    parts = dur_str.split(":")
    if len(parts) == 3:
        return timedelta(
            hours=int(parts[0]),
            minutes=int(parts[1]),
            seconds=int(parts[2]),
        )
    if len(parts) == 2:
        return timedelta(hours=int(parts[0]), minutes=int(parts[1]))
    raise ValueError(f"Cannot parse duration: {dur_str}")


def _parse_activity_pct(val: str) -> float:
    """Parse activity percentage like '93 %' or '80%' into float."""
    cleaned = str(val).replace("%", "").replace("\n", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


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


def _parse_html_xls(path: Path) -> TimesheetData:
    """Parse HiveDesk HTML-based .xls export."""
    # Read raw HTML to extract metadata
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_html = f.read()

    # Extract timezone from metadata row
    tz_match = re.search(r"Timezone:\s*([^<]+)", raw_html)
    timezone = tz_match.group(1).strip() if tz_match else "Unknown"

    # Extract date range
    dr_match = re.search(r"Date Range:\s*([^<]+)", raw_html)
    date_range_str = dr_match.group(1).strip() if dr_match else ""

    date_range_start = ""
    date_range_end = ""
    if " - " in date_range_str:
        parts = date_range_str.split(" - ")
        date_range_start = parts[0].strip()
        date_range_end = parts[1].strip()

    # Parse HTML tables with pandas
    dfs = pd.read_html(path)
    if not dfs:
        raise ValueError(f"No tables found in {path}")

    df = dfs[0]

    # Flatten multi-level columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[-1] if isinstance(col, tuple) else col for col in df.columns]

    logger.info(f"Parsed timesheet with columns: {df.columns.tolist()}")

    # Find relevant columns by partial match
    col_map = {}
    date_cols_found = 0  # Track multiple "Worksession Date" columns
    for col in df.columns:
        col_lower = str(col).lower()
        if "project" in col_lower:
            col_map["project"] = col
        elif "team member" in col_lower or "member" in col_lower:
            col_map["employee"] = col
        elif "task" in col_lower:
            col_map["task"] = col
        elif "date start" in col_lower:
            col_map["date_start"] = col
        elif "date end" in col_lower:
            col_map["date_end"] = col
        elif "worksession date" in col_lower or ("date" in col_lower and "start" not in col_lower and "end" not in col_lower and "range" not in col_lower):
            # HiveDesk has two "Worksession Date" columns (start, end)
            if date_cols_found == 0:
                col_map["date_start"] = col
            else:
                col_map["date_end"] = col
            date_cols_found += 1
        elif "time start" in col_lower:
            col_map["time_start"] = col
        elif "time end" in col_lower:
            col_map["time_end"] = col
        elif "type" in col_lower:
            col_map["session_type"] = col
        elif "duration" in col_lower:
            col_map["duration"] = col
        elif "activity" in col_lower:
            col_map["activity"] = col
        elif "active" in col_lower:
            col_map["active_time"] = col
        elif "cost" in col_lower:
            col_map["cost"] = col

    # Filter out total/summary/footer rows
    df = df[~df.apply(
        lambda row: row.astype(str).str.contains(
            r"Total|Average|^\*", case=False, regex=True
        ).any(), axis=1
    )]
    df = df.dropna(how="all")

    logger.info(f"Column mapping: {col_map}")

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

        # Handle empty/nan values safely
        dur_str = str(row.get(col_map.get("duration", ""), "00:00:00")).strip()
        active_str = str(row.get(col_map.get("active_time", ""), "00:00:00")).strip()
        act_pct_str = str(row.get(col_map.get("activity", ""), "0")).strip()

        if dur_str in ("nan", "", "NaT"):
            dur_str = "00:00:00"
        if active_str in ("nan", "", "NaT"):
            active_str = "00:00:00"
        if act_pct_str in ("nan", ""):
            act_pct_str = "0"

        try:
            duration = _parse_duration(dur_str)
            active_time = _parse_duration(active_str)
            activity_pct = _parse_activity_pct(act_pct_str)
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
            )
        )

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


def _parse_xlsx(path: Path) -> TimesheetData:
    """Parse a real .xlsx Excel file using pandas/openpyxl."""
    df = pd.read_excel(path, engine="openpyxl")
    logger.info(f"XLSX columns: {df.columns.tolist()}")

    # Flatten multi-level columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[-1] if isinstance(col, tuple) else col for col in df.columns]

    # Find relevant columns by partial match (same logic as HTML parser)
    col_map = {}
    date_cols_found = 0
    for col in df.columns:
        col_lower = str(col).lower()
        if "project" in col_lower:
            col_map["project"] = col
        elif "team member" in col_lower or "member" in col_lower:
            col_map["employee"] = col
        elif "task" in col_lower:
            col_map["task"] = col
        elif "date start" in col_lower:
            col_map["date_start"] = col
        elif "date end" in col_lower:
            col_map["date_end"] = col
        elif "worksession date" in col_lower or ("date" in col_lower and "start" not in col_lower and "end" not in col_lower and "range" not in col_lower):
            if date_cols_found == 0:
                col_map["date_start"] = col
            else:
                col_map["date_end"] = col
            date_cols_found += 1
        elif "time start" in col_lower:
            col_map["time_start"] = col
        elif "time end" in col_lower:
            col_map["time_end"] = col
        elif "type" in col_lower:
            col_map["session_type"] = col
        elif "duration" in col_lower:
            col_map["duration"] = col
        elif "activity" in col_lower:
            col_map["activity"] = col
        elif "active" in col_lower:
            col_map["active_time"] = col

    # Filter out total/summary/footer rows
    df = df[~df.apply(
        lambda row: row.astype(str).str.contains(
            r"Total|Average|^\*", case=False, regex=True
        ).any(), axis=1
    )]
    df = df.dropna(how="all")

    logger.info(f"XLSX column mapping: {col_map}")

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

        dur_str = str(row.get(col_map.get("duration", ""), "00:00:00")).strip()
        active_str = str(row.get(col_map.get("active_time", ""), "00:00:00")).strip()
        act_pct_str = str(row.get(col_map.get("activity", ""), "0")).strip()

        if dur_str in ("nan", "", "NaT"):
            dur_str = "00:00:00"
        if active_str in ("nan", "", "NaT"):
            active_str = "00:00:00"
        if act_pct_str in ("nan", ""):
            act_pct_str = "0"

        try:
            duration = _parse_duration(dur_str)
            active_time = _parse_duration(active_str)
            activity_pct = _parse_activity_pct(act_pct_str)
        except (ValueError, IndexError) as e:
            logger.warning(f"Skipping row due to parse error: {e}")
            continue

        if task in ("nan", "--", ""):
            task = "--"
        if date_end in ("nan", ""):
            date_end = date_start
        if time_end in ("nan", ""):
            time_end = ""

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
            )
        )

    total_duration = sum((s.duration for s in sessions), timedelta())
    total_active = sum((s.active_time for s in sessions), timedelta())
    avg_activity = (
        sum(s.activity_pct for s in sessions) / len(sessions) if sessions else 0.0
    )

    logger.info(
        f"XLSX parsed {len(sessions)} sessions: "
        f"total={total_duration}, active={total_active}, avg_activity={avg_activity:.1f}%"
    )

    return TimesheetData(
        employee=employee_name,
        date_range_start="",
        date_range_end="",
        timezone="Unknown",
        sessions=sessions,
        total_duration=total_duration,
        total_active=total_active,
        avg_activity_pct=avg_activity,
    )


def _parse_csv(path: Path) -> TimesheetData:
    """Parse a CSV timesheet export using DuckDB for efficient querying."""
    con = duckdb.connect()
    con.execute(f"CREATE TABLE timesheet AS SELECT * FROM read_csv_auto('{path}')")

    columns = [row[0] for row in con.execute("DESCRIBE timesheet").fetchall()]
    logger.info(f"CSV columns: {columns}")

    rows = con.execute("SELECT * FROM timesheet").fetchdf()
    con.close()

    # Delegate to the same logic as HTML parsing with a flat DataFrame
    # Adjust column names to match the expected format
    sessions: list[WorkSession] = []
    employee_name = ""

    for _, row in rows.iterrows():
        employee = str(row.iloc[1]).strip() if len(row) > 1 else ""
        if employee:
            employee_name = employee
        sessions.append(
            WorkSession(
                project=str(row.iloc[0]) if len(row) > 0 else "--",
                employee=employee,
                task=str(row.iloc[2]) if len(row) > 2 else "--",
                date_start=str(row.iloc[3]) if len(row) > 3 else "",
                date_end=str(row.iloc[4]) if len(row) > 4 else "",
                time_start=str(row.iloc[5]) if len(row) > 5 else "",
                time_end=str(row.iloc[6]) if len(row) > 6 else "",
                session_type=str(row.iloc[7]) if len(row) > 7 else "",
                duration=_parse_duration(str(row.iloc[8])) if len(row) > 8 else timedelta(),
                active_time=_parse_duration(str(row.iloc[9])) if len(row) > 9 else timedelta(),
                activity_pct=_parse_activity_pct(str(row.iloc[10])) if len(row) > 10 else 0.0,
            )
        )

    total_duration = sum((s.duration for s in sessions), timedelta())
    total_active = sum((s.active_time for s in sessions), timedelta())
    avg_activity = sum(s.activity_pct for s in sessions) / len(sessions) if sessions else 0.0

    return TimesheetData(
        employee=employee_name,
        date_range_start="",
        date_range_end="",
        timezone="Unknown",
        sessions=sessions,
        total_duration=total_duration,
        total_active=total_active,
        avg_activity_pct=avg_activity,
    )


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
