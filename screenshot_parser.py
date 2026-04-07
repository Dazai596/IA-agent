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
    ts_total = 0
    ts_failed = 0
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text()
        matches = TIMESTAMP_PATTERN.findall(text)
        for ts_str in matches:
            ts_total += 1
            try:
                dt = _parse_hivedesk_timestamp(ts_str)
                all_timestamps.append(dt)
            except ValueError:
                ts_failed += 1
                logger.warning(f"Failed to parse timestamp: {ts_str}")

    # Abort if too many timestamps fail to parse (>10% failure rate)
    if ts_total > 0 and ts_failed / ts_total > 0.10:
        failure_pct = ts_failed / ts_total * 100
        logger.error(
            f"High timestamp parse failure rate: {ts_failed}/{ts_total} ({failure_pct:.0f}%). "
            f"PDF may be in an unsupported format."
        )
        raise ValueError(
            f"Too many timestamp parse failures: {ts_failed}/{ts_total} ({failure_pct:.0f}%). "
            f"Check that the PDF is a valid HiveDesk screenshot report."
        )
    elif ts_failed > 0:
        logger.info(f"Timestamp parse: {ts_failed}/{ts_total} failed ({ts_failed/ts_total*100:.1f}%)")

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
    Parse a HiveDesk screenshot PDF report.
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


# ── Folder-based screenshot loading ─────────────────────────────────────────

# Common timestamp patterns in screenshot filenames
_FOLDER_TS_PATTERNS = [
    # 2025-09-27_14-30-00.png
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})[_T](\d{2})-(\d{2})-(\d{2})"), "%Y-%m-%d %H:%M:%S"),
    # screenshot_20250927_143000.png
    (re.compile(r"(\d{4})(\d{2})(\d{2})[_](\d{2})(\d{2})(\d{2})"), "%Y%m%d %H%M%S"),
    # Sep 27, 2025 02_18_36 AM.png  (HiveDesk-style folder export)
    (re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})\s+"
        r"(\d{1,2})[_\-](\d{2})[_\-](\d{2})\s*(AM|PM)?",
        re.IGNORECASE,
    ), None),
]


def _parse_filename_timestamp(filename: str) -> datetime | None:
    """Extract a datetime from a screenshot filename."""
    stem = Path(filename).stem

    # Pattern 1: YYYY-MM-DD_HH-MM-SS
    m = _FOLDER_TS_PATTERNS[0][0].search(stem)
    if m:
        return datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
        )

    # Pattern 2: YYYYMMDD_HHMMSS
    m = _FOLDER_TS_PATTERNS[1][0].search(stem)
    if m:
        return datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
        )

    # Pattern 3: Month Day, Year HH_MM_SS AM/PM
    m = _FOLDER_TS_PATTERNS[2][0].search(stem)
    if m:
        try:
            return _parse_hivedesk_timestamp(
                f"{m.group(1)} {m.group(2)}, {m.group(3)} "
                f"{m.group(4)}:{m.group(5)}:{m.group(6)} {m.group(7) or 'AM'}"
            )
        except ValueError:
            pass

    return None


def parse_screenshot_folder(
    folder_path: str,
    employee: str = "Unknown",
) -> ScreenshotReport:
    """
    Load screenshots from a folder of image files.
    Each image filename should contain a timestamp.
    Supported formats: .png, .jpg, .jpeg, .bmp, .tiff
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise NotADirectoryError(f"Screenshot folder not found: {folder_path}")

    image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}
    image_files = sorted(
        [f for f in folder.iterdir() if f.suffix.lower() in image_extensions]
    )

    if not image_files:
        raise ValueError(f"No image files found in {folder_path}")

    logger.info(f"Found {len(image_files)} image files in {folder_path}")

    entries: list[ScreenshotEntry] = []
    date_start = ""
    date_end = ""

    for idx, img_path in enumerate(image_files):
        ts = _parse_filename_timestamp(img_path.name)
        if ts is None:
            # Fallback: use file modification time
            ts = datetime.fromtimestamp(img_path.stat().st_mtime)
            logger.debug(f"No timestamp in filename {img_path.name}, using mtime: {ts}")

        try:
            image_bytes = img_path.read_bytes()
            from PIL import Image as PILImage
            with PILImage.open(img_path) as img:
                width, height = img.size
        except Exception as e:
            logger.warning(f"Failed to read image {img_path}: {e}")
            continue

        entries.append(ScreenshotEntry(
            timestamp=ts,
            page_number=0,
            image_index=idx,
            image_bytes=image_bytes,
            width=width,
            height=height,
        ))

    # Sort by timestamp
    entries.sort(key=lambda e: e.timestamp)

    if entries:
        date_start = entries[0].timestamp.strftime("%b %d, %Y")
        date_end = entries[-1].timestamp.strftime("%b %d, %Y")

    return ScreenshotReport(
        employee=employee,
        date_range_start=date_start,
        date_range_end=date_end,
        total_screenshots=len(entries),
        work_session_count=0,
        entries=entries,
    )


def parse_screenshots(path: str, employee: str = "Unknown") -> ScreenshotReport:
    """
    Unified entry point: detect whether the path is a PDF file or a folder
    of images, and dispatch to the appropriate parser.
    """
    p = Path(path)
    if p.is_dir():
        return parse_screenshot_folder(path, employee=employee)
    elif p.is_file() and p.suffix.lower() == ".pdf":
        return parse_screenshot_pdf(path)
    elif p.is_file():
        # Try to treat any other file as a PDF
        return parse_screenshot_pdf(path)
    else:
        raise FileNotFoundError(f"Screenshot path not found: {path}")
