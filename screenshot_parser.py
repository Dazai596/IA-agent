"""
Parser for HiveDesk screenshot PDF reports.
Extracts individual screenshots and their timestamps using PyMuPDF.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF

from models import ScreenshotEntry, ScreenshotReport

logger = logging.getLogger(__name__)

# HiveDesk timestamp format: "Sep 27, 2025 02:18:36 AM"
# NOTE: HiveDesk uses a non-standard format where hours can be 24h-style
# but still appended with AM/PM (e.g. "23:55:01 PM", "00:58:01 AM").
TIMESTAMP_PATTERN = re.compile(
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)"
)

def _parse_hivedesk_timestamp(ts_str: str) -> datetime:
    """
    Parse HiveDesk's non-standard timestamps.
    HiveDesk sometimes uses 24h hours with AM/PM suffix:
      "Sep 26, 2025 23:55:01 PM"  -> hour 23
      "Sep 27, 2025 00:58:01 AM"  -> hour 0
    Standard strptime %I:%M:%S %p can't handle hours > 12 or hour 0.
    """
    # Try standard 12-hour parse first
    try:
        return datetime.strptime(ts_str, "%b %d, %Y %I:%M:%S %p")
    except ValueError:
        pass

    # Manual parse for non-standard format
    match = re.match(
        r"(\w+ \d+, \d{4}) (\d{1,2}):(\d{2}):(\d{2}) ([AP]M)", ts_str
    )
    if not match:
        raise ValueError(f"Cannot parse timestamp: {ts_str}")

    date_part = match.group(1)
    hour = int(match.group(2))
    minute = int(match.group(3))
    second = int(match.group(4))

    base_date = datetime.strptime(date_part, "%b %d, %Y")
    return base_date.replace(hour=hour, minute=minute, second=second)


WORKSESSION_PATTERN = re.compile(r"WorkSession\s+(\d+)")
EMPLOYEE_PATTERN = re.compile(r"Team Member Name\s*:\s*(.+?)(?:\s*null)?$", re.MULTILINE)
DATE_RANGE_PATTERN = re.compile(
    r"From\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4})\s+to\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4})"
)


def _extract_metadata(doc: fitz.Document) -> tuple[str, str, str, int]:
    """Extract employee name, date range, and work session count from the first page."""
    first_page_text = doc[0].get_text()

    employee = "Unknown"
    emp_match = EMPLOYEE_PATTERN.search(first_page_text)
    if emp_match:
        employee = emp_match.group(1).strip()

    date_start = ""
    date_end = ""
    dr_match = DATE_RANGE_PATTERN.search(first_page_text)
    if dr_match:
        date_start = dr_match.group(1)
        date_end = dr_match.group(2)

    # Count work sessions across all pages
    all_text = "".join(doc[i].get_text() for i in range(len(doc)))
    work_sessions = WORKSESSION_PATTERN.findall(all_text)
    session_count = len(work_sessions)

    return employee, date_start, date_end, session_count


def _extract_timestamps_and_images(
    doc: fitz.Document,
) -> list[ScreenshotEntry]:
    """
    Extract all screenshots and their associated timestamps.

    The PDF layout has 6 screenshots per page, each with a timestamp label.
    Timestamps are in reverse chronological order.
    """
    # Collect all timestamps across all pages in order
    all_timestamps: list[datetime] = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text()
        matches = TIMESTAMP_PATTERN.findall(text)
        for ts_str in matches:
            try:
                dt = _parse_hivedesk_timestamp(ts_str)
                all_timestamps.append(dt)
            except ValueError:
                logger.warning(f"Failed to parse timestamp: {ts_str}")

    # Collect all unique images (by xref) maintaining order
    seen_xrefs: set[int] = set()
    ordered_images: list[tuple[int, int, int]] = []  # (xref, page_idx, img_index_on_page)

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        images = page.get_images()
        img_idx_on_page = 0
        for img in images:
            xref = img[0]
            if xref not in seen_xrefs:
                seen_xrefs.add(xref)
                ordered_images.append((xref, page_idx, img_idx_on_page))
                img_idx_on_page += 1

    logger.info(
        f"Found {len(all_timestamps)} timestamps and {len(ordered_images)} unique images"
    )

    # Match timestamps to images (both lists should be same length and in order)
    entries: list[ScreenshotEntry] = []
    count = min(len(all_timestamps), len(ordered_images))

    for i in range(count):
        xref, page_idx, img_idx = ordered_images[i]

        # Extract image bytes
        try:
            pix = fitz.Pixmap(doc, xref)
            if pix.n > 4:  # CMYK — convert to RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            image_bytes = pix.tobytes("png")
            width = pix.width
            height = pix.height
            pix = None
        except Exception as e:
            logger.warning(f"Failed to extract image xref={xref}: {e}")
            image_bytes = None
            width = 0
            height = 0

        entries.append(
            ScreenshotEntry(
                timestamp=all_timestamps[i],
                page_number=page_idx + 1,
                image_index=i,
                image_bytes=image_bytes,
                width=width,
                height=height,
            )
        )

    return entries


def parse_screenshot_pdf(path: str) -> ScreenshotReport:
    """
    Main entry point: parse a HiveDesk screenshot PDF report.
    Extracts all screenshots with their timestamps.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Screenshot PDF not found: {path}")

    logger.info(f"Opening screenshot PDF: {path}")
    with fitz.open(str(file_path)) as doc:
        employee, date_start, date_end, session_count = _extract_metadata(doc)
        logger.info(
            f"PDF metadata: employee={employee}, range={date_start} to {date_end}, "
            f"sessions={session_count}, pages={len(doc)}"
        )

        entries = _extract_timestamps_and_images(doc)

    return ScreenshotReport(
        employee=employee,
        date_range_start=date_start,
        date_range_end=date_end,
        total_screenshots=len(entries),
        work_session_count=session_count,
        entries=entries,
    )
