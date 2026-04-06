"""
Application configuration loaded from environment / .env file.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    openai_api_key: str = ""
    llm_model: str = "gpt-4o"
    max_screenshots: int = 10  # max screenshots to classify via LLM (0 = all)
    log_level: str = "INFO"

    # Analysis thresholds
    low_activity_threshold: float = 50.0
    very_short_session_min: float = 5.0
    very_long_session_min: float = 360.0  # 6 hours
    screenshot_batch_size: int = 4  # screenshots per LLM call

    # Advanced analysis settings
    employee_email: str = ""
    assigned_domains: str = ""  # comma-separated list of allowed domains
    phash_similarity_threshold: float = 0.90
    phash_min_time_gap_minutes: float = 20.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ── Shared LLM client ────────────────────────────────────────────────────────
# Reuses a single ChatOpenAI instance (and its underlying httpx connection pool)
# across the entire audit instead of creating 6+ separate instances.

_shared_llm: Optional[ChatOpenAI] = None


def get_llm(temperature: float = 0.0, max_tokens: int = 1500) -> ChatOpenAI:
    """Get or create a shared ChatOpenAI client for the current audit."""
    global _shared_llm
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set.")
    if _shared_llm is None:
        _shared_llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    # Override per-call settings (ChatOpenAI supports this via bind)
    return _shared_llm.bind(temperature=temperature, max_tokens=max_tokens)


def close_llm() -> None:
    """Close the shared LLM client and release its connection pool."""
    global _shared_llm
    if _shared_llm is not None:
        try:
            # ChatOpenAI wraps an httpx client internally
            if hasattr(_shared_llm, "client") and hasattr(_shared_llm.client, "close"):
                _shared_llm.client.close()
            if hasattr(_shared_llm, "async_client") and hasattr(_shared_llm.async_client, "close"):
                _shared_llm.async_client.close()
        except Exception as e:
            logger.debug(f"Error closing LLM client: {e}")
        _shared_llm = None
