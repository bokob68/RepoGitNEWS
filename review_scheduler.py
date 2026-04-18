#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run make_review.py on a fixed daily schedule defined in review_automation.ini.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "review_automation.ini"
DEFAULT_SCRIPT_PATH = SCRIPT_DIR / "make_review.py"


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.getLogger().handlers.clear()
    logging.getLogger().setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logging.getLogger().addHandler(stream_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logging.getLogger().addHandler(file_handler)


def load_schedule_config(config_path: Path) -> tuple[list[str], int, bool, Path]:
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw_times = parser.get("schedule", "times", fallback="08:00")
    times = [value.strip() for value in raw_times.split(",") if value.strip()]
    if not times:
        raise ValueError("No schedule times configured.")

    for value in times:
        datetime.strptime(value, "%H:%M")

    poll_seconds = parser.getint("schedule", "poll_seconds", fallback=20)
    catch_up = parser.getboolean("schedule", "catch_up_on_start", fallback=True)
    log_file = parser.get("schedule", "log_file", fallback="logs/review_scheduler.log").strip()
    resolved_log = (config_path.parent / log_file).resolve()
    return times, poll_seconds, catch_up, resolved_log


def run_make_review(config_path: Path, script_path: Path) -> int:
    logging.info("Starting scheduled generation run.")
    result = subprocess.run([sys.executable, str(script_path), "--config", str(config_path)])
    if result.returncode == 0:
        logging.info("Scheduled generation finished successfully.")
    else:
        logging.error("Scheduled generation failed with exit code %s.", result.returncode)
    return result.returncode


def should_run_now(now: datetime, configured_time: str) -> bool:
    return now.strftime("%H:%M") == configured_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily scheduler for make_review.py.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to INI config file.")
    parser.add_argument("--once", action="store_true", help="Run make_review.py once immediately and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()
    times, poll_seconds, catch_up_on_start, log_file = load_schedule_config(config_path)
    setup_logging(log_file)
    script_path = DEFAULT_SCRIPT_PATH

    if args.once:
        return run_make_review(config_path, script_path)

    logging.info("Scheduler started. Times=%s", ", ".join(times))
    already_run: set[str] = set()
    if catch_up_on_start:
        now = datetime.now()
        todays_due = [value for value in times if value <= now.strftime("%H:%M")]
        if todays_due:
            key = f"{now:%Y-%m-%d}:{todays_due[-1]}"
            already_run.add(key)
            run_make_review(config_path, script_path)

    while True:
        now = datetime.now()
        for scheduled_time in times:
            key = f"{now:%Y-%m-%d}:{scheduled_time}"
            if key in already_run:
                continue
            if should_run_now(now, scheduled_time):
                already_run.add(key)
                run_make_review(config_path, script_path)

        # Keep the in-memory history short and day-scoped.
        today_prefix = f"{now:%Y-%m-%d}:"
        already_run = {value for value in already_run if value.startswith(today_prefix)}
        time.sleep(max(5, poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
