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
    """Parse HH:MM:SS duration string into timedelta.
    Also handles pandas Timedelta/Timestamp objects and time objects.
    """
    # Handle non-string types that pandas might produce
    if isinstance(dur_str, timedelta):
        return dur_str
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


def _find_header_row(df: pd.DataFrame) -> int:
    """
    Search through the first rows of a DataFrame to find the actual header row.
    HiveDesk HTML exports have title/metadata rows before the real column headers.
    Returns the row index containing the header, or -1 if not found.
    """
    # Known column names that MUST appear in the real header row
    required_keywords = {"duration", "activity", "active"}
    helpful_keywords = {"project", "task", "member", "worksession", "time start", "time end"}

    for idx in range(min(15, len(df))):
        row_values = [str(v).strip().lower() for v in df.iloc[idx].values]
        row_text = " ".join(row_values)

        required_found = sum(1 for kw in required_keywords if kw in row_text)
        helpful_found = sum(1 for kw in helpful_keywords if kw in row_text)

        # Need at least 2 required keywords + 1 helpful keyword to confirm header
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

        if "project" in col_lower and "project" not in col_map:
            col_map["project"] = col
        elif ("team member" in col_lower or "member" in col_lower) and "employee" not in col_map:
            col_map["employee"] = col
        elif "task" in col_lower and "task" not in col_map:
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
            # HiveDesk has two "Worksession Date" columns (start, end)
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
        # Match patterns like "74% (Avg)" in summary rows
        if re.match(r"^\d+%?\s*\(avg\)", s_lower):
            return True
    return False


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

    # Try each table to find the one with timesheet data
    df = None
    for candidate_df in dfs:
        # Flatten multi-level columns
        if isinstance(candidate_df.columns, pd.MultiIndex):
            candidate_df.columns = [
                col[-1] if isinstance(col, tuple) else col
                for col in candidate_df.columns
            ]

        # Check if this table's columns already contain known names
        test_map = _map_columns(candidate_df)
        if _has_critical_columns(test_map):
            df = candidate_df
            break

        # Columns don't match — search rows for the actual header
        header_idx = _find_header_row(candidate_df)
        if header_idx >= 0:
            new_cols = [str(v).strip() for v in candidate_df.iloc[header_idx].values]
            candidate_df = candidate_df.iloc[header_idx + 1:].reset_index(drop=True)
            candidate_df.columns = new_cols
            test_map = _map_columns(candidate_df)
            if _has_critical_columns(test_map):
                df = candidate_df
                break

    if df is None:
        # Last resort: use first table with header-row detection
        df = dfs[0]
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                col[-1] if isinstance(col, tuple) else col
                for col in df.columns
            ]
        header_idx = _find_header_row(df)
        if header_idx >= 0:
            new_cols = [str(v).strip() for v in df.iloc[header_idx].values]
            df = df.iloc[header_idx + 1:].reset_index(drop=True)
            df.columns = new_cols

    logger.info(f"Parsed timesheet with columns: {df.columns.tolist()}")

    col_map = _map_columns(df)

    # Validate that critical columns were found
    if not _has_critical_columns(col_map):
        logger.error(
            f"CRITICAL: Could not find Duration/Active/Activity columns! "
            f"Available columns: {df.columns.tolist()}, Mapped: {col_map}"
        )

    # Filter out total/summary/footer rows (exact match, not substring)
    df = df[~df.apply(_is_summary_row, axis=1)]
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

        # Handle empty/nan values safely — pass raw values to _parse_duration
        # so it can handle pandas Timedelta/time objects directly
        dur_raw = row.get(col_map.get("duration", ""), "00:00:00")
        active_raw = row.get(col_map.get("active_time", ""), "00:00:00")
        act_pct_str = str(row.get(col_map.get("activity", ""), "0")).strip()

        dur_str_check = str(dur_raw).strip()
        active_str_check = str(active_raw).strip()

        if dur_str_check in ("nan", "", "NaT", "None"):
            dur_raw = "00:00:00"
        if active_str_check in ("nan", "", "NaT", "None"):
            active_raw = "00:00:00"
        if act_pct_str in ("nan", "", "None"):
            act_pct_str = "0"

        try:
            duration = _parse_duration(dur_raw)
            active_time = _parse_duration(active_raw)
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

    # Check if columns look right; if not, find the header row
    col_map = _map_columns(df)
    if not _has_critical_columns(col_map):
        header_idx = _find_header_row(df)
        if header_idx >= 0:
            new_cols = [str(v).strip() for v in df.iloc[header_idx].values]
            df = df.iloc[header_idx + 1:].reset_index(drop=True)
            df.columns = new_cols
            logger.info(f"XLSX: Found header at row {header_idx}, new columns: {df.columns.tolist()}")

    col_map = _map_columns(df)

    if not _has_critical_columns(col_map):
        logger.error(
            f"CRITICAL: Could not find Duration/Active/Activity columns in XLSX! "
            f"Available columns: {df.columns.tolist()}, Mapped: {col_map}"
        )

    # Filter out total/summary/footer rows (exact match, not substring)
    df = df[~df.apply(_is_summary_row, axis=1)]
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

        # Pass raw values to _parse_duration for pandas Timedelta/time handling
        dur_raw = row.get(col_map.get("duration", ""), "00:00:00")
        active_raw = row.get(col_map.get("active_time", ""), "00:00:00")
        act_pct_str = str(row.get(col_map.get("activity", ""), "0")).strip()

        dur_str_check = str(dur_raw).strip()
        active_str_check = str(active_raw).strip()

        if dur_str_check in ("nan", "", "NaT", "None"):
            dur_raw = "00:00:00"
        if active_str_check in ("nan", "", "NaT", "None"):
            active_raw = "00:00:00"
        if act_pct_str in ("nan", "", "None"):
            act_pct_str = "0"

        try:
            duration = _parse_duration(dur_raw)
            active_time = _parse_duration(active_raw)
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
