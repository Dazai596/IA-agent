"""
Screenshot analysis module.
Uses LLM vision to classify each screenshot, then aggregates results.
The LLM only classifies — it does not make fraud determinations.
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from models import (
    ScreenshotAnalysisResult,
    ScreenshotCategory,
    ScreenshotClassification,
    ScreenshotReport,
)
from config import get_llm, get_settings
from prompts import SCREENSHOT_CLASSIFIER_PROMPT

logger = logging.getLogger(__name__)


def _build_vision_message(image_bytes: bytes, timestamp: str) -> HumanMessage:
    """Build a multimodal message with the screenshot image.
    Uses detail=low to reduce token usage and memory on the API side.
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    msg = HumanMessage(
        content=[
            {
                "type": "text",
                "text": f"Screenshot taken at: {timestamp}\n\nClassify this screenshot.",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
            },
        ]
    )
    del b64  # Free the base64 string immediately
    return msg


def _parse_classification(raw: str, timestamp: str) -> ScreenshotClassification:
    """Parse the LLM JSON response into a ScreenshotClassification."""
    import json

    # Try to extract JSON from the response
    text = raw.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM response as JSON: {text[:200]}")
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
) -> ScreenshotClassification:
    """Classify a single screenshot (thread-safe)."""
    try:
        msg = _build_vision_message(image_bytes, timestamp)
        response = llm.invoke([
            SystemMessage(content=SCREENSHOT_CLASSIFIER_PROMPT),
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
    max_workers: int = 4,
) -> list[ScreenshotClassification]:
    """Classify screenshots concurrently using a thread pool."""
    if not entries:
        return []

    results: dict[int, ScreenshotClassification] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_classify_single, llm, img, ts): idx
            for idx, (img, ts) in enumerate(entries)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            logger.info(f"Screenshot {idx + 1}/{len(entries)} classified.")

    # Return in original order
    return [results[i] for i in range(len(entries))]


def analyze_screenshots(
    report: ScreenshotReport,
    llm: Optional[ChatOpenAI] = None,
    employee_email: str = "",
    assigned_domains: Optional[list[str]] = None,
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
    advanced = run_advanced_analysis(
        entries=entries_to_analyze,
        employee_email=employee_email,
        assigned_domains=assigned_domains,
    )

    # ── Step 2: LLM classification ───────────────────────────────────────
    # Prepare batch: filter out entries without image data
    batch: list[tuple[bytes, str]] = []
    for entry in entries_to_analyze:
        if entry.image_bytes:
            batch.append((entry.image_bytes, entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")))
        else:
            logger.warning(f"Skipping screenshot at {entry.timestamp} — no image data")

    # Free image bytes from the report — they're now copied into the batch
    for entry in report.entries:
        entry.image_bytes = None

    classifications = _classify_batch(llm, batch)

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
