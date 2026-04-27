"""
Screenshot analysis module.
Uses LLM vision to classify each screenshot, then aggregates results.
The LLM only classifies — it does not make fraud determinations.
"""

from __future__ import annotations

import base64
import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from PIL import Image

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from models import (
    ScreenshotAnalysisResult,
    ScreenshotCategory,
    ScreenshotClassification,
    ScreenshotReport,
)
from config import get_llm, get_settings
from prompts import SCREENSHOT_CLASSIFIER_PROMPT, build_screenshot_classifier_prompt

logger = logging.getLogger(__name__)


def _build_vision_message(
    image_bytes: bytes,
    timestamp: str,
    ocr_text: str = "",
    detail: str = "high",
) -> HumanMessage:
    """Build a multimodal message with the screenshot image.
    Uses detail=high by default to accurately read URLs, tab titles, and app names.
    Optionally includes OCR-extracted text as additional signal for the LLM.
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    text_parts = [f"Screenshot taken at: {timestamp}"]
    if ocr_text:
        # Provide OCR as grounding context — helps LLM read partially obscured text
        text_parts.append(
            f"OCR text extracted from this screenshot (may be partial):\n{ocr_text[:600]}"
        )
    text_parts.append("Classify this screenshot.")

    msg = HumanMessage(
        content=[
            {
                "type": "text",
                "text": "\n\n".join(text_parts),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": detail},
            },
        ]
    )
    del b64
    return msg


def _parse_classification(raw: str, timestamp: str) -> ScreenshotClassification:
    """Parse the LLM JSON response into a ScreenshotClassification."""
    from helpers import safe_parse_llm_json

    data = safe_parse_llm_json(raw)
    if not data:
        logger.warning(f"Failed to parse LLM classification response: {raw[:200]}")
        return ScreenshotClassification(
            timestamp=timestamp,
            category=ScreenshotCategory.UNCERTAIN,
            confidence=0.3,
            description="Failed to parse LLM classification response.",
            applications_visible=[],
            reasoning="Parse error — treating as uncertain.",
        )

    category_str = data.get("category", "uncertain").lower().strip()
    category_map = {
        "work": ScreenshotCategory.WORK,
        "non_work": ScreenshotCategory.NON_WORK,
        "non-work": ScreenshotCategory.NON_WORK,
        "idle": ScreenshotCategory.IDLE,
        "uncertain": ScreenshotCategory.UNCERTAIN,
    }
    category = category_map.get(category_str, ScreenshotCategory.UNCERTAIN)

    return ScreenshotClassification(
        timestamp=timestamp,
        category=category,
        confidence=min(1.0, max(0.0, float(data.get("confidence", 0.5)))),
        description=data.get("description", ""),
        applications_visible=data.get("applications_visible", []),
        reasoning=data.get("reasoning", ""),
    )


def _classify_single(
    llm: ChatOpenAI,
    image_bytes: bytes,
    timestamp: str,
    ocr_text: str = "",
    detail: str = "high",
    department: str = "developer",
) -> ScreenshotClassification:
    """Classify a single screenshot (thread-safe).
    ocr_text: pre-extracted OCR text to include as grounding context.
    detail: vision detail level ("high" reads text/URLs accurately).
    department: employee department for context-aware classification.
    """
    try:
        msg = _build_vision_message(image_bytes, timestamp, ocr_text=ocr_text, detail=detail)
        classifier_prompt = build_screenshot_classifier_prompt(department)
        response = llm.invoke([
            SystemMessage(content=classifier_prompt),
            msg,
        ])
        # Extract text and immediately discard the full response object
        response_text = response.content
        del response, msg
        classification = _parse_classification(response_text, timestamp)
        del response_text
        logger.debug(
            f"  [{timestamp}] → {classification.category.value} "
            f"(conf: {classification.confidence:.0%})"
        )
        return classification
    except Exception as e:
        logger.error(f"Error classifying screenshot at {timestamp}: {e}")
        return ScreenshotClassification(
            timestamp=timestamp,
            category=ScreenshotCategory.UNCERTAIN,
            confidence=0.0,
            description=f"Error during classification: {str(e)}",
            applications_visible=[],
            reasoning="Classification failed due to error.",
        )


def _classify_batch(
    llm: ChatOpenAI,
    entries: list[tuple[bytes, str]],
    ocr_map: Optional[dict[int, str]] = None,
    detail: str = "high",
    max_workers: int = 4,
    department: str = "developer",
) -> list[ScreenshotClassification]:
    """Classify screenshots concurrently using a thread pool.
    ocr_map: dict mapping entry index → pre-extracted OCR text (optional).
    detail: vision detail level passed to each classification call.
    department: employee department for context-aware classification.
    """
    if not entries:
        return []

    results: dict[int, ScreenshotClassification] = {}
    ocr_map = ocr_map or {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                _classify_single,
                llm,
                img,
                ts,
                ocr_map.get(idx, ""),
                detail,
                department,
            ): idx
            for idx, (img, ts) in enumerate(entries)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            logger.info(f"Screenshot {idx + 1}/{len(entries)} classified.")

    return [results[i] for i in range(len(entries))]


def analyze_screenshots(
    report: ScreenshotReport,
    llm: Optional[ChatOpenAI] = None,
    employee_email: str = "",
    assigned_domains: Optional[list[str]] = None,
    department: str = "developer",
) -> ScreenshotAnalysisResult:
    """
    Analyze all screenshots in the report.
    Step 1: LLM vision classification (work/non-work/idle/uncertain)
    Step 2: Advanced analysis (repeated frames, OCR, monitor changes, etc.)
    """
    import gc
    from advanced_screenshot_analysis import run_advanced_analysis

    settings = get_settings()

    if llm is None:
        llm = get_llm(temperature=0.0, max_tokens=800)

    entries_to_analyze = report.entries
    if settings.max_screenshots > 0:
        entries_to_analyze = entries_to_analyze[: settings.max_screenshots]

    logger.info(f"Analyzing {len(entries_to_analyze)} screenshots with LLM vision...")

    # ── Step 1: Run advanced analysis BEFORE freeing image bytes ──────────
    # Advanced analysis needs the raw images for hashing, OCR, etc.
    # It also returns an ocr_texts dict {image_index → text} for LLM injection.
    advanced = run_advanced_analysis(
        entries=entries_to_analyze,
        employee_email=employee_email,
        assigned_domains=assigned_domains,
        department=department,
    )
    ocr_texts: dict[int, str] = advanced.pop("ocr_texts", {})
    gc.collect()

    # ── Step 2: LLM classification ───────────────────────────────────────
    # Prepare batch: filter out entries without image data.
    # Build a parallel ocr_map keyed by batch index (not image_index).
    batch: list[tuple[bytes, str]] = []
    batch_ocr_map: dict[int, str] = {}
    for entry in entries_to_analyze:
        if entry.image_bytes:
            batch_idx = len(batch)
            batch.append((entry.image_bytes, entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")))
            ocr = ocr_texts.get(entry.image_index, "")
            if ocr:
                batch_ocr_map[batch_idx] = ocr
        else:
            logger.warning(f"Skipping screenshot at {entry.timestamp} — no image data")

    # Free image bytes from the report — they're now copied into the batch
    for entry in report.entries:
        entry.image_bytes = None

    classifications = _classify_batch(
        llm,
        batch,
        ocr_map=batch_ocr_map,
        detail=settings.screenshot_detail,
        department=department,
    )

    # ── Step 2b: Generate thumbnails for report display ─────────────────
    for i, classification in enumerate(classifications):
        if i < len(batch):
            try:
                img = Image.open(io.BytesIO(batch[i][0]))
                img.thumbnail((400, 280))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=55)
                classification.image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                img.close()
                buf.close()
            except Exception:
                pass

    # Free the batch (raw image bytes) now that classification is done
    batch.clear()
    del batch
    gc.collect()

    # ── Step 3: Aggregate ────────────────────────────────────────────────
    work_count = sum(1 for c in classifications if c.category == ScreenshotCategory.WORK)
    non_work_count = sum(1 for c in classifications if c.category == ScreenshotCategory.NON_WORK)
    idle_count = sum(1 for c in classifications if c.category == ScreenshotCategory.IDLE)
    uncertain_count = sum(1 for c in classifications if c.category == ScreenshotCategory.UNCERTAIN)
    total = len(classifications) or 1

    result = ScreenshotAnalysisResult(
        total_analyzed=len(classifications),
        work_count=work_count,
        non_work_count=non_work_count,
        idle_count=idle_count,
        uncertain_count=uncertain_count,
        work_pct=round(work_count / total * 100, 1),
        non_work_pct=round(non_work_count / total * 100, 1),
        idle_pct=round(idle_count / total * 100, 1),
        classifications=classifications,
        summary=(
            f"Analyzed {len(classifications)} screenshots: "
            f"{work_count} work ({work_count/total*100:.0f}%), "
            f"{non_work_count} non-work ({non_work_count/total*100:.0f}%), "
            f"{idle_count} idle ({idle_count/total*100:.0f}%), "
            f"{uncertain_count} uncertain ({uncertain_count/total*100:.0f}%)"
        ),
        # Merge advanced analysis results
        repeated_frames=advanced.get("repeated_frames", []),
        tab_switching_analysis=advanced.get("tab_switching_analysis"),
        monitor_inconsistencies=advanced.get("monitor_inconsistencies", []),
        unauthorized_access_events=advanced.get("unauthorized_access_events", []),
        third_party_accounts=advanced.get("third_party_accounts", []),
        suspicious_sites=advanced.get("suspicious_sites", []),
    )

    logger.info(f"Screenshot analysis complete: {result.summary}")
    if result.repeated_frames:
        logger.info(f"  FRAUD SIGNAL: {len(result.repeated_frames)} repeated frame pairs detected!")
    return result
