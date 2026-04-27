"""
Application configuration loaded from environment / .env file.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    openai_api_key: str = ""
    llm_model: str = "gpt-4o"
    max_screenshots: int = 0          # 0 = analyze ALL screenshots (was 10)
    screenshot_detail: str = "high"   # "high"|"low"|"auto" — high reads URLs/titles accurately
    log_level: str = "INFO"

    # Analysis thresholds — calibrated for web developers
    # Web devs have long focused sessions with lower keyboard activity
    # during debugging, code review, documentation reading, etc.
    low_activity_threshold: float = 35.0       # Was 50. Devs reading code/docs can be 35-50%
    very_short_session_min: float = 3.0        # Was 5. Quick terminal commands / git ops are normal
    very_long_session_min: float = 480.0       # Was 360 (6h). 8h focused coding sprints are normal
    screenshot_batch_size: int = 4             # screenshots per LLM call
    idle_ratio_threshold: float = 0.65         # Was hardcoded 0.5. Devs can be idle 60%+ during debugging
    excessive_daily_hours: float = 14.0        # Was hardcoded 12. Long days happen during deadlines

    # Advanced analysis settings
    employee_email: str = ""
    assigned_domains: str = ""  # comma-separated list of allowed domains
    phash_similarity_threshold: float = 0.90
    phash_min_time_gap_minutes: float = 20.0

    # Jiggler / automation detection — more lenient for devs
    # Devs in "flow state" can have genuinely consistent activity
    activity_stability_threshold: float = 2.0  # std dev below this = suspicious (was hardcoded 3.0)
    start_time_regularity_threshold: float = 10.0  # std dev in minutes (was hardcoded 15)
    gap_regularity_threshold: float = 3.0      # std dev in minutes (was hardcoded 5)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_llm(temperature: float = 0.0, max_tokens: int = 1500) -> ChatOpenAI:
    """Create a ChatOpenAI client with the requested temperature and max_tokens.

    A new instance is created per call so that callers get the exact temperature
    and max_tokens they request.  ChatOpenAI instances are lightweight wrappers;
    the underlying httpx connection pool is managed by the openai library and is
    shared automatically across instances with the same api_key.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set.")
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def close_llm() -> None:
    """No-op kept for backward compatibility — connection pool is managed by openai library."""
    pass
