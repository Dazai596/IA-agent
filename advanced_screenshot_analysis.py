"""
Advanced screenshot analysis module.
Implements fraud detection beyond basic LLM classification:
  - Repeated/identical frame detection (perceptual hashing)
  - Tab-switching loop detection (OCR)
  - Monitor configuration change detection
  - Unauthorized site/URL detection (OCR)
  - Third-party account detection (OCR)
  - Suspicious site blocklist flagging (OCR)
"""

from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

from PIL import Image

from models import (
    MonitorInconsistency,
    RepeatedFrame,
    ScreenshotEntry,
    SuspiciousSite,
    TabSwitchingAnalysis,
    ThirdPartyAccount,
    UnauthorizedAccessEvent,
)

logger = logging.getLogger(__name__)

# ── Optional dependency imports ─────────────────────────────────────────────

try:
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False
    logger.warning("imagehash not installed — repeated frame detection disabled.")

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    import easyocr
    HAS_EASYOCR = True
except ImportError:
    HAS_EASYOCR = False

if not HAS_TESSERACT and not HAS_EASYOCR:
    logger.warning(
        "Neither pytesseract nor easyocr installed — "
        "OCR-based detection (tabs, URLs, accounts) disabled."
    )

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ── Lazy-initialized EasyOCR reader ────────────────────────────────────────

_easyocr_reader = None


def _get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None and HAS_EASYOCR:
        _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _easyocr_reader


# ── Suspicious site blocklist ──────────────────────────────────────────────

SUSPICIOUS_SITE_BLOCKLIST = {
    # Identity / fake data generators
    "fakename": ("Identity generator", "personal_tools"),
    "fakenamegenerator": ("Identity generator", "personal_tools"),
    "randomuser": ("Random user generator", "personal_tools"),
    "tempmail": ("Temporary email", "personal_tools"),
    "guerrillamail": ("Disposable email", "personal_tools"),
    "10minutemail": ("Disposable email", "personal_tools"),
    "throwaway": ("Disposable email", "personal_tools"),
    # Entertainment / streaming
    "netflix": ("Streaming service", "entertainment"),
    "twitch.tv": ("Streaming/gaming", "entertainment"),
    "hulu": ("Streaming service", "entertainment"),
    "disneyplus": ("Streaming service", "entertainment"),
    "primevideo": ("Streaming service", "entertainment"),
    "crunchyroll": ("Anime streaming", "entertainment"),
    "spotify": ("Music streaming", "entertainment"),
    # Social media (non-work)
    "facebook.com": ("Social media", "social"),
    "instagram.com": ("Social media", "social"),
    "tiktok.com": ("Social media", "social"),
    "snapchat": ("Social media", "social"),
    "pinterest": ("Social media", "social"),
    # reddit.com removed — many web devs use tech subreddits for work research
    # Dating
    "tinder": ("Dating app", "personal"),
    "bumble": ("Dating app", "personal"),
    "hinge": ("Dating app", "personal"),
    # Shopping (amazon excluded — too many false positives with AWS)
    "ebay.com": ("Shopping", "shopping"),
    "aliexpress": ("Shopping", "shopping"),
    "wish.com": ("Shopping", "shopping"),
    "etsy.com": ("Shopping", "shopping"),
    "amazon.com/gp": ("Amazon shopping", "shopping"),
    "amazon.com/dp": ("Amazon product page", "shopping"),
    # Gaming
    "steampowered": ("Gaming platform", "gaming"),
    "store.steampowered": ("Gaming store", "gaming"),
    "epicgames": ("Gaming platform", "gaming"),
    "roblox": ("Gaming", "gaming"),
    "minecraft": ("Gaming", "gaming"),
    # Sports / betting
    "bet365": ("Gambling", "gambling"),
    "draftkings": ("Sports betting", "gambling"),
    "fanduel": ("Sports betting", "gambling"),
}

# Domains that look like blocklist matches but are legitimate work tools
# Extended for web developers — these are essential developer resources
_WORK_DOMAIN_ALLOWLIST = {
    # Cloud / hosting
    "aws.amazon.com", "console.aws.amazon",
    "docs.aws.amazon", "s3.amazonaws",
    # Developer resources commonly visited by web devs
    "github.com", "gitlab.com", "bitbucket.org",
    "stackoverflow.com", "stackexchange.com",
    "npmjs.com", "yarnpkg.com", "pypi.org",
    "developer.mozilla.org", "mdn.mozilla.org",
    "w3schools.com", "css-tricks.com",
    "dev.to", "medium.com",  # Tech blogs
    "hackernews", "news.ycombinator.com",
    # Design / API / DevOps tools
    "figma.com", "postman.com", "insomnia.rest",
    "vercel.com", "netlify.com", "heroku.com",
    "cloudflare.com", "digitalocean.com",
    "docker.com", "hub.docker.com",
    # Documentation sites
    "reactjs.org", "nextjs.org", "vuejs.org", "angular.io",
    "tailwindcss.com", "getbootstrap.com",
    "typescriptlang.org", "nodejs.org",
    "python.org", "docs.python.org",
    "laravel.com", "djangoproject.com",
    # Code playgrounds
    "codepen.io", "codesandbox.io",
    "stackblitz.com", "jsfiddle.net",
    "replit.com",
}

# ── OCR cache for reuse across detections ─────────────────────────────────

_ocr_cache: dict[int, str] = {}  # image_index -> OCR text
_ocr_top_cache: dict[int, str] = {}  # image_index -> top-region OCR text


# ══════════════════════════════════════════════════════════════════════════════
#  IMAGE UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _load_image(entry: ScreenshotEntry) -> Optional[Image.Image]:
    """Load a PIL Image from a ScreenshotEntry's raw bytes."""
    if entry.image_bytes:
        try:
            return Image.open(io.BytesIO(entry.image_bytes))
        except Exception as e:
            logger.warning(f"Failed to load image for entry at {entry.timestamp}: {e}")
    return None


def _ocr_image(image: Image.Image, region: Optional[tuple] = None) -> str:
    """
    Run OCR on an image (or a cropped region of it).
    Tries pytesseract first, falls back to easyocr.
    region: (left, top, right, bottom) to crop before OCR.
    """
    if region:
        image = image.crop(region)

    if HAS_TESSERACT:
        try:
            text = pytesseract.image_to_string(image)
            return text.strip()
        except Exception as e:
            logger.debug(f"pytesseract failed: {e}")

    if HAS_EASYOCR:
        try:
            reader = _get_easyocr_reader()
            if reader is None:
                return ""
            # Convert PIL to numpy array for easyocr
            import numpy as np
            img_array = np.array(image)
            results = reader.readtext(img_array, detail=0)
            return " ".join(results).strip()
        except Exception as e:
            logger.debug(f"easyocr failed: {e}")

    return ""


def _ocr_top_region(image: Image.Image, height_pct: float = 0.08, cache_key: int = -1) -> str:
    """OCR just the top portion of a screenshot (address bar / tab bar area).
    Uses cache if cache_key is provided and result is already cached."""
    if cache_key >= 0 and cache_key in _ocr_top_cache:
        return _ocr_top_cache[cache_key]
    w, h = image.size
    top_region = (0, 0, w, int(h * height_pct))
    result = _ocr_image(image, region=top_region)
    if cache_key >= 0:
        _ocr_top_cache[cache_key] = result
    return result


def _ocr_full(image: Image.Image, cache_key: int = -1) -> str:
    """OCR the full screenshot. Uses cache if available."""
    if cache_key >= 0 and cache_key in _ocr_cache:
        return _ocr_cache[cache_key]
    result = _ocr_image(image)
    if cache_key >= 0:
        _ocr_cache[cache_key] = result
    return result


def clear_ocr_cache() -> None:
    """Clear OCR caches between runs."""
    _ocr_cache.clear()
    _ocr_top_cache.clear()


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 2: REPEATED FRAME DETECTION (perceptual hashing)
# ══════════════════════════════════════════════════════════════════════════════

def detect_repeated_frames(
    entries: list[ScreenshotEntry],
    similarity_threshold: float = 0.90,
    min_time_gap_minutes: float = 20.0,
) -> list[RepeatedFrame]:
    """
    Compare all screenshot pairs using perceptual hashing.
    Flag pairs that are >threshold similar and >min_time_gap apart.
    This is the strongest single indicator of fraud.
    """
    if not HAS_IMAGEHASH:
        logger.warning("imagehash not available — skipping repeated frame detection.")
        return []

    if not entries:
        return []

    # Compute perceptual hashes for all entries that have image data
    hashed: list[tuple[ScreenshotEntry, imagehash.ImageHash, str]] = []
    for entry in entries:
        img = _load_image(entry)
        if img is None:
            continue
        try:
            h = imagehash.phash(img, hash_size=16)  # 16x16 = 256-bit hash for accuracy
            # Also grab OCR text for visible_content if OCR available (uses cache)
            ocr_text = ""
            if HAS_TESSERACT or HAS_EASYOCR:
                try:
                    ocr_text = _ocr_top_region(img, cache_key=entry.image_index)[:200]
                except Exception:
                    pass
            hashed.append((entry, h, ocr_text))
        except Exception as e:
            logger.warning(f"Failed to hash screenshot at {entry.timestamp}: {e}")
        finally:
            img.close()

    if len(hashed) < 2:
        return []

    logger.info(f"Comparing {len(hashed)} screenshot hashes for repeated frames...")

    # hash_size=16 means 256 bits total
    max_bits = 16 * 16  # 256

    repeated: list[RepeatedFrame] = []
    for i in range(len(hashed)):
        for j in range(i + 1, len(hashed)):
            entry_a, hash_a, ocr_a = hashed[i]
            entry_b, hash_b, ocr_b = hashed[j]

            # Hamming distance
            distance = hash_a - hash_b
            similarity = 1.0 - (distance / max_bits)

            if similarity < similarity_threshold:
                continue

            # Time gap check
            time_gap_sec = abs((entry_b.timestamp - entry_a.timestamp).total_seconds())
            time_gap_min = time_gap_sec / 60.0

            if time_gap_min < min_time_gap_minutes:
                continue

            visible = ocr_a or ocr_b or ""
            repeated.append(RepeatedFrame(
                first_occurrence=entry_a.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                repeat_occurrence=entry_b.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                time_gap_minutes=round(time_gap_min, 1),
                similarity_score=round(similarity, 4),
                visible_content=visible[:300],
            ))

    logger.info(f"Found {len(repeated)} repeated frame pairs.")
    return repeated


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 4: TAB-SWITCHING LOOP DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _extract_tab_titles(image: Image.Image) -> list[str]:
    """
    Extract browser tab titles from the top of a screenshot.
    Reads the tab bar region and splits into individual tab titles.
    """
    w, h = image.size
    # Tab bar is typically in the top ~5% of the screen
    tab_region = (0, 0, w, int(h * 0.05))
    text = _ocr_image(image, region=tab_region)
    if not text:
        return []

    # Split by common tab separators — tabs often show as "Title1 | Title2" or separated by whitespace
    # Clean up and extract meaningful tokens
    titles = []
    # Split on pipe, dash clusters, or multiple spaces
    parts = re.split(r'\s*[\|]\s*|\s{3,}', text)
    for p in parts:
        p = p.strip()
        if len(p) > 2:  # Ignore tiny fragments
            titles.append(p)
    return titles


def detect_tab_switching(
    entries: list[ScreenshotEntry],
    min_loop_count: int = 2,
) -> TabSwitchingAnalysis:
    """
    Detect mechanical tab-switching behavior by analyzing tab title sequences.
    Looks for repeating left-to-right cycling patterns.
    """
    result = TabSwitchingAnalysis()

    if not (HAS_TESSERACT or HAS_EASYOCR):
        logger.warning("No OCR available — skipping tab switching detection.")
        return result

    if not entries:
        return result

    # Extract tab titles from each screenshot
    tab_sequences: list[tuple[ScreenshotEntry, list[str]]] = []
    for entry in entries:
        img = _load_image(entry)
        if img is None:
            continue
        try:
            titles = _extract_tab_titles(img)
            if titles:
                tab_sequences.append((entry, titles))
                result.max_tabs_visible = max(result.max_tabs_visible, len(titles))
        except Exception as e:
            logger.debug(f"Tab extraction failed for {entry.timestamp}: {e}")
        finally:
            img.close()

    if len(tab_sequences) < 4:
        return result

    # Detect cycling pattern: look for the same sequence of active tabs repeating
    # We look at the FIRST visible tab title as "active tab" for each screenshot
    active_tabs = [seq[1][0] if seq[1] else "" for seq in tab_sequences]

    # Look for repeating subsequences
    loop_count = 0
    sessions_affected: set[str] = set()
    seq_len = min(len(active_tabs), 20)  # Look at reasonable window

    for window_size in range(3, min(seq_len // 2 + 1, 10)):
        for start in range(len(active_tabs) - window_size * 2 + 1):
            pattern = active_tabs[start:start + window_size]
            next_chunk = active_tabs[start + window_size:start + window_size * 2]
            if pattern == next_chunk and all(p for p in pattern):
                loop_count += 1
                for idx in range(start, start + window_size * 2):
                    if idx < len(tab_sequences):
                        entry = tab_sequences[idx][0]
                        sessions_affected.add(entry.timestamp.strftime("%Y-%m-%d %H:%M"))
                break  # Found a loop at this window size, move on

    result.loop_detected = loop_count >= min_loop_count
    result.loop_count = loop_count
    result.tab_sequence = active_tabs[:20]  # Store first 20 for reference
    result.sessions_affected = sorted(sessions_affected)

    # Flag excessive tab preloading
    if result.max_tabs_visible > 15:
        logger.info(f"Excessive tab preloading detected: {result.max_tabs_visible} tabs visible.")

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 6: MONITOR CONFIGURATION CHANGE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def detect_monitor_changes(
    entries: list[ScreenshotEntry],
    dual_monitor_ratio: float = 3.0,
) -> list[MonitorInconsistency]:
    """
    Detect when screen resolution / monitor layout changes within the same day.
    Ultra-wide aspect ratio (>3.0:1) = dual monitor. Normal (~16:9 or 21:9) = single.
    Threshold raised to 3.0 to avoid flagging ultrawide monitors (21:9 = 2.33:1).
    """
    if not entries:
        return []

    # Group by date and classify each screenshot
    daily: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"single": [], "dual": []})

    for entry in entries:
        date_str = entry.timestamp.strftime("%Y-%m-%d")
        ts_str = entry.timestamp.strftime("%H:%M:%S")

        w = entry.width
        h = entry.height

        # Also check from image_bytes if dimensions not stored
        if (w == 0 or h == 0) and entry.image_bytes:
            try:
                img = Image.open(io.BytesIO(entry.image_bytes))
                w, h = img.size
                img.close()
            except Exception:
                continue

        if h == 0:
            continue

        ratio = w / h
        if ratio >= dual_monitor_ratio:
            daily[date_str]["dual"].append(ts_str)
        else:
            daily[date_str]["single"].append(ts_str)

    # Flag days where BOTH single and dual appear
    inconsistencies: list[MonitorInconsistency] = []
    for date_str, counts in daily.items():
        if counts["single"] and counts["dual"]:
            inconsistencies.append(MonitorInconsistency(
                date=date_str,
                single_monitor_count=len(counts["single"]),
                dual_monitor_count=len(counts["dual"]),
                timestamps_single=counts["single"][:10],
                timestamps_dual=counts["dual"][:10],
                severity="high",
            ))

    return inconsistencies


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 7: UNAUTHORIZED SITE / URL DETECTION
# ══════════════════════════════════════════════════════════════════════════════

# Common URL / domain patterns for OCR text
_URL_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})+)',
    re.IGNORECASE,
)


def _extract_domains_from_text(text: str) -> list[str]:
    """Extract domain names from OCR'd text."""
    domains = set()
    for match in _URL_PATTERN.finditer(text):
        domain = match.group(1).lower().strip(".")
        # Filter out very short or obvious non-domains
        if len(domain) > 4 and "." in domain:
            domains.add(domain)
    return list(domains)


def detect_unauthorized_sites(
    entries: list[ScreenshotEntry],
    assigned_domains: list[str],
) -> list[UnauthorizedAccessEvent]:
    """
    OCR address bars and page titles to find domains not in the assigned list.
    """
    if not (HAS_TESSERACT or HAS_EASYOCR):
        logger.warning("No OCR available — skipping unauthorized site detection.")
        return []

    if not assigned_domains or not entries:
        return []

    # Normalize assigned domains
    allowed = set()
    for d in assigned_domains:
        d = d.lower().strip()
        d = d.replace("https://", "").replace("http://", "").replace("www.", "")
        d = d.split("/")[0]  # Just the domain part
        allowed.add(d)

    events: list[UnauthorizedAccessEvent] = []

    for entry in entries:
        img = _load_image(entry)
        if img is None:
            continue
        try:
            # OCR the top region (address bar / tabs) with caching
            top_text = _ocr_top_region(img, height_pct=0.10, cache_key=entry.image_index)
            if not top_text:
                continue

            domains = _extract_domains_from_text(top_text)
            for domain in domains:
                # Skip known work domains
                if any(wd in domain for wd in _WORK_DOMAIN_ALLOWLIST):
                    continue
                # Check if domain is authorized (proper suffix matching)
                is_allowed = False
                for a in allowed:
                    # Proper domain matching: "github.com" matches "api.github.com"
                    # but not "github.com.phishing.io"
                    if domain == a or domain.endswith("." + a):
                        is_allowed = True
                        break
                if not is_allowed:
                    events.append(UnauthorizedAccessEvent(
                        timestamp=entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        domain=domain,
                        page_title=top_text[:100],
                    ))
        except Exception as e:
            logger.debug(f"URL detection failed for {entry.timestamp}: {e}")
        finally:
            img.close()

    return events


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 8: THIRD-PARTY ACCOUNT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_EMAIL_PATTERN = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
    re.IGNORECASE,
)


def detect_third_party_accounts(
    entries: list[ScreenshotEntry],
    employee_email: str = "",
) -> list[ThirdPartyAccount]:
    """
    OCR screenshots for email addresses visible in Gmail/Outlook dashboards
    or browser profile icons. Flag any that don't match employee_email.
    """
    if not (HAS_TESSERACT or HAS_EASYOCR):
        logger.warning("No OCR available — skipping third-party account detection.")
        return []

    if not employee_email or not entries:
        return []

    employee_email_lower = employee_email.lower().strip()
    employee_domain = employee_email_lower.split("@")[1] if "@" in employee_email_lower else ""

    found: list[ThirdPartyAccount] = []
    seen_emails: set[str] = set()

    for entry in entries:
        img = _load_image(entry)
        if img is None:
            continue
        try:
            # OCR the full screenshot with caching
            full_text = _ocr_full(img, cache_key=entry.image_index)
            if not full_text:
                continue

            emails = _EMAIL_PATTERN.findall(full_text)
            for email in emails:
                email = email.lower().strip()
                # Skip the employee's own email or already-flagged ones
                if email == employee_email_lower or email in seen_emails:
                    continue
                # Skip same-domain emails (coworkers)
                if employee_domain and email.endswith("@" + employee_domain):
                    continue
                # Skip common system/no-reply addresses
                if any(x in email for x in ["noreply", "no-reply", "mailer-daemon", "notification", "support@", "info@", "admin@"]):
                    continue
                seen_emails.add(email)
                found.append(ThirdPartyAccount(
                    timestamp=entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    email_found=email,
                    expected_email=employee_email,
                    severity="critical",
                ))
        except Exception as e:
            logger.debug(f"Account detection failed for {entry.timestamp}: {e}")
        finally:
            img.close()

    return found


# ══════════════════════════════════════════════════════════════════════════════
#  FIX 10: SUSPICIOUS SITE FLAGGING (blocklist)
# ══════════════════════════════════════════════════════════════════════════════

def detect_suspicious_sites(
    entries: list[ScreenshotEntry],
) -> list[SuspiciousSite]:
    """
    Check OCR'd text against the suspicious site blocklist.
    Non-definitive on their own — contribute to risk score.
    """
    if not (HAS_TESSERACT or HAS_EASYOCR):
        logger.warning("No OCR available — skipping suspicious site detection.")
        return []

    if not entries:
        return []

    found: list[SuspiciousSite] = []
    # Track which sites we already flagged to avoid duplicates
    flagged: set[str] = set()

    for entry in entries:
        img = _load_image(entry)
        if img is None:
            continue
        try:
            top_text = _ocr_top_region(img, height_pct=0.10, cache_key=entry.image_index)
            if not top_text:
                continue

            text_lower = top_text.lower()
            for keyword, (reason, category) in SUSPICIOUS_SITE_BLOCKLIST.items():
                if keyword in text_lower and keyword not in flagged:
                    # Skip if the match is in a work-context domain
                    if any(wd in text_lower for wd in _WORK_DOMAIN_ALLOWLIST):
                        continue
                    flagged.add(keyword)
                    found.append(SuspiciousSite(
                        timestamp=entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        site_name=keyword,
                        category=category,
                        reason=reason,
                    ))
        except Exception as e:
            logger.debug(f"Suspicious site check failed for {entry.timestamp}: {e}")
        finally:
            img.close()

    return found


# ══════════════════════════════════════════════════════════════════════════════
#  MASTER FUNCTION: Run all advanced analyses
# ══════════════════════════════════════════════════════════════════════════════

def detect_frozen_screens(
    entries: list[ScreenshotEntry],
    min_consecutive: int = 3,
) -> list[RepeatedFrame]:
    """
    Detect consecutive screenshots that are nearly identical (frozen screen).
    Unlike detect_repeated_frames which compares all pairs, this specifically
    looks for sequences of consecutive identical frames — stronger fraud signal.
    """
    if not HAS_IMAGEHASH or len(entries) < min_consecutive:
        return []

    # Compute hashes for all entries
    hashed: list[tuple[ScreenshotEntry, "imagehash.ImageHash"]] = []
    for entry in entries:
        img = _load_image(entry)
        if img is None:
            continue
        try:
            h = imagehash.phash(img, hash_size=16)
            hashed.append((entry, h))
        except Exception:
            pass
        finally:
            img.close()

    if len(hashed) < min_consecutive:
        return []

    max_bits = 16 * 16
    frozen: list[RepeatedFrame] = []
    streak_start = 0

    for i in range(1, len(hashed)):
        entry_a, hash_a = hashed[i - 1]
        entry_b, hash_b = hashed[i]
        distance = hash_a - hash_b
        similarity = 1.0 - (distance / max_bits)

        if similarity < 0.95:
            # Streak broken — check if it was long enough
            streak_len = i - streak_start
            if streak_len >= min_consecutive:
                first_entry = hashed[streak_start][0]
                last_entry = hashed[i - 1][0]
                gap_sec = abs((last_entry.timestamp - first_entry.timestamp).total_seconds())
                frozen.append(RepeatedFrame(
                    first_occurrence=first_entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    repeat_occurrence=last_entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    time_gap_minutes=round(gap_sec / 60, 1),
                    similarity_score=0.99,
                    visible_content=f"Frozen screen: {streak_len} consecutive identical frames",
                ))
            streak_start = i

    # Check final streak
    streak_len = len(hashed) - streak_start
    if streak_len >= min_consecutive:
        first_entry = hashed[streak_start][0]
        last_entry = hashed[-1][0]
        gap_sec = abs((last_entry.timestamp - first_entry.timestamp).total_seconds())
        frozen.append(RepeatedFrame(
            first_occurrence=first_entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            repeat_occurrence=last_entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            time_gap_minutes=round(gap_sec / 60, 1),
            similarity_score=0.99,
            visible_content=f"Frozen screen: {streak_len} consecutive identical frames",
        ))

    if frozen:
        logger.info(f"Found {len(frozen)} frozen screen sequences.")
    return frozen


def run_advanced_analysis(
    entries: list[ScreenshotEntry],
    employee_email: str = "",
    assigned_domains: Optional[list[str]] = None,
    department: str = "developer",
) -> dict:
    """
    Run all advanced screenshot analyses and return results as a dict
    that can be merged into ScreenshotAnalysisResult.

    Returns dict with keys:
        repeated_frames, tab_switching_analysis, monitor_inconsistencies,
        unauthorized_access_events, third_party_accounts, suspicious_sites
    """
    logger.info(f"Running advanced screenshot analysis on {len(entries)} entries...")

    # Clear OCR caches from any previous run
    clear_ocr_cache()

    # Read thresholds from config
    from config import get_settings
    settings = get_settings()

    # FIX 2: Repeated frames (highest fraud signal)
    repeated = detect_repeated_frames(
        entries,
        similarity_threshold=settings.phash_similarity_threshold,
        min_time_gap_minutes=settings.phash_min_time_gap_minutes,
    )
    logger.info(f"  Repeated frames: {len(repeated)} found")

    # Consecutive frozen screen detection
    frozen = detect_frozen_screens(entries)
    if frozen:
        logger.info(f"  Frozen screens: {len(frozen)} sequences found")
        # Merge with repeated frames (avoid duplicates)
        existing_pairs = {(r.first_occurrence, r.repeat_occurrence) for r in repeated}
        for f in frozen:
            if (f.first_occurrence, f.repeat_occurrence) not in existing_pairs:
                repeated.append(f)

    # FIX 6: Monitor changes
    monitor = detect_monitor_changes(entries)
    logger.info(f"  Monitor inconsistencies: {len(monitor)} days flagged")

    # FIX 4: Tab switching (requires OCR)
    tabs = detect_tab_switching(entries)
    logger.info(f"  Tab switching: loop_detected={tabs.loop_detected}, loops={tabs.loop_count}")

    # FIX 7: Unauthorized sites (requires OCR + assigned_domains)
    unauthorized = []
    if assigned_domains:
        unauthorized = detect_unauthorized_sites(entries, assigned_domains)
        logger.info(f"  Unauthorized sites: {len(unauthorized)} events")

    # FIX 8: Third-party accounts (requires OCR + employee_email)
    third_party = []
    if employee_email:
        third_party = detect_third_party_accounts(entries, employee_email)
        logger.info(f"  Third-party accounts: {len(third_party)} found")

    # FIX 10: Suspicious sites (requires OCR)
    suspicious = detect_suspicious_sites(entries)

    # Filter suspicious sites against the department's legitimate work domains.
    # e.g. facebook.com is suspicious for a developer but legitimate for telemarketing.
    if suspicious and department != "developer":
        from department_config import get_all_work_domains, is_globally_safe_domain
        dept_work_domains = get_all_work_domains(department)
        filtered_suspicious = []
        for site in suspicious:
            site_kw = site.site_name.lower()
            # Keep the entry only if it's NOT a known work domain for this department
            if not any(site_kw in wd or wd in site_kw for wd in dept_work_domains) \
                    and not is_globally_safe_domain(site_kw):
                filtered_suspicious.append(site)
        removed = len(suspicious) - len(filtered_suspicious)
        if removed:
            logger.info(f"  Removed {removed} suspicious site(s) that are work domains for {department}")
        suspicious = filtered_suspicious

    logger.info(f"  Suspicious sites: {len(suspicious)} flagged")

    # Export OCR cache so the LLM classifier can inject the text as grounding context.
    # Keyed by image_index, value is the full-page OCR string (may be empty for entries
    # where OCR was skipped or unavailable).
    ocr_results: dict[int, str] = {}
    for image_index, text in _ocr_cache.items():
        if text:
            ocr_results[image_index] = text
    # Also merge top-region cache (addresses / tab bars) if no full-page OCR available
    for image_index, text in _ocr_top_cache.items():
        if text and image_index not in ocr_results:
            ocr_results[image_index] = text

    # Clear caches to free memory
    clear_ocr_cache()

    return {
        "repeated_frames": repeated,
        "tab_switching_analysis": tabs,
        "monitor_inconsistencies": monitor,
        "unauthorized_access_events": unauthorized,
        "third_party_accounts": third_party,
        "suspicious_sites": suspicious,
        "ocr_texts": ocr_results,  # {image_index: ocr_text} for LLM injection
    }
