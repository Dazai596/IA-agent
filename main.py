"""
Employee Work Audit System — Main Entry Point

Usage:
    python src/main.py --timesheet <path> --screenshots <path>
    python src/main.py --timesheet timesheet.xls --screenshots report.pdf
    python src/main.py --timesheet timesheet.xls   # timesheet only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


from dotenv import load_dotenv

load_dotenv()

from workflow import run_audit
from config import get_settings


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI-powered employee work audit system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --timesheet data/timesheet.xls --screenshots data/report.pdf
  python main.py --timesheet data/timesheet.xls
  python main.py --timesheet data/timesheet.xls --screenshots data/report.pdf --output report.json
        """,
    )
    parser.add_argument(
        "--timesheet", "-t",
        type=str,
        default="",
        help="Path to timesheet export file (.xls, .csv)",
    )
    parser.add_argument(
        "--screenshots", "-s",
        type=str,
        default="",
        help="Path to screenshot report PDF",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="",
        help="Path to save JSON report (default: prints to stdout)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    parser.add_argument(
        "--employee-email",
        type=str,
        default="",
        help="Employee email for third-party account detection",
    )
    parser.add_argument(
        "--assigned-domains",
        type=str,
        nargs="*",
        default=[],
        help="List of allowed domains (e.g., github.com jira.atlassian.net)",
    )

    args = parser.parse_args()

    if not args.timesheet and not args.screenshots:
        parser.error("At least one of --timesheet or --screenshots is required.")

    settings = get_settings()
    log_level = "DEBUG" if args.verbose else settings.log_level
    setup_logging(log_level)

    logger = logging.getLogger("main")

    # Validate file paths
    if args.timesheet and not Path(args.timesheet).exists():
        logger.error(f"Timesheet file not found: {args.timesheet}")
        sys.exit(1)
    if args.screenshots and not Path(args.screenshots).exists():
        logger.error(f"Screenshot file not found: {args.screenshots}")
        sys.exit(1)

    if not settings.openai_api_key and args.screenshots:
        logger.warning(
            "No OPENAI_API_KEY set. Screenshot analysis and LLM reasoning will fail. "
            "Set it in .env or as an environment variable."
        )

    # Run the audit
    logger.info("=" * 70)
    logger.info("  EMPLOYEE WORK AUDIT SYSTEM")
    logger.info("=" * 70)

    report = run_audit(
        timesheet_path=args.timesheet,
        screenshot_path=args.screenshots,
        employee_email=args.employee_email,
        assigned_domains=args.assigned_domains,
    )

    # Output
    print("\n")
    print(report.to_summary())
    print("\n")

    # Save JSON report
    report_json = report.model_dump(mode="json")
    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(report_json, indent=2, default=str))
        logger.info(f"Full report saved to: {output_path}")
    else:
        # Always save a timestamped report
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_output = Path(f"audit_report_{ts}.json")
        default_output.write_text(json.dumps(report_json, indent=2, default=str))
        logger.info(f"Full report saved to: {default_output}")


if __name__ == "__main__":
    main()
