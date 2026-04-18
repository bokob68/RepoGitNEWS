#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scheduler for the local repo sync workflow around Codex app automation.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "review_automation.ini"
SYNC_SCRIPT_PATH = SCRIPT_DIR / "repo_sync.py"
PID_FILE = SCRIPT_DIR / "repo_sync_scheduler.pid"


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def resolve_path(base_dir: Path, raw_value: str) -> Path:
    raw_value = raw_value.strip()
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_schedule_config(config_path: Path) -> tuple[list[str], int, int, bool, Path]:
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw_times = parser.get("schedule", "push_times", fallback="07:00,17:30")
    push_times = [value.strip() for value in raw_times.split(",") if value.strip()]
    if not push_times:
        raise ValueError("No push_times configured.")
    for value in push_times:
        datetime.strptime(value, "%H:%M")

    poll_seconds = parser.getint("schedule", "poll_seconds", fallback=20)
    review_poll_seconds = parser.getint("schedule", "review_poll_seconds", fallback=300)
    catch_up = parser.getboolean("schedule", "catch_up_on_start", fallback=True)
    log_path = resolve_path(config_path.parent, parser.get("schedule", "log_file", fallback="logs/repo_sync.log"))
    return push_times, poll_seconds, review_poll_seconds, catch_up, log_path


def run_sync(config_path: Path, *extra_args: str) -> int:
    result = subprocess.run([sys.executable, str(SYNC_SCRIPT_PATH), "--config", str(config_path), *extra_args])
    return result.returncode


def write_pid_file() -> None:
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def remove_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scheduler for repo_sync.py.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to INI config file.")
    parser.add_argument("--once-push", action="store_true", help="Run only the news push once and exit.")
    parser.add_argument("--once-pull", action="store_true", help="Run only the review pull once and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()
    push_times, poll_seconds, review_poll_seconds, catch_up_on_start, log_file = load_schedule_config(config_path)
    setup_logging(log_file)
    write_pid_file()

    try:
        if args.once_push:
            return run_sync(config_path, "--push-news")
        if args.once_pull:
            return run_sync(config_path, "--pull-review")

        logging.info("Repo sync scheduler started. push_times=%s", ", ".join(push_times))
        last_review_pull_minute: str | None = None
        already_run: set[str] = set()

        if catch_up_on_start:
            now = datetime.now()
            todays_due = [value for value in push_times if value <= now.strftime("%H:%M")]
            if todays_due:
                key = f"{now:%Y-%m-%d}:{todays_due[-1]}"
                already_run.add(key)
                run_sync(config_path, "--push-news")
        
        while True:
            now = datetime.now()
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            for push_time in push_times:
                key = f"{now:%Y-%m-%d}:{push_time}"
                if key in already_run:
                    continue
                if now.strftime("%H:%M") == push_time:
                    already_run.add(key)
                    logging.info("Scheduled news push for %s", push_time)
                    run_sync(config_path, "--push-news")

            if last_review_pull_minute != minute_key and now.second < max(5, poll_seconds):
                if now.timestamp() % max(60, review_poll_seconds) < poll_seconds:
                    last_review_pull_minute = minute_key
                    logging.info("Periodic review pull check.")
                    run_sync(config_path, "--pull-review")

            today_prefix = f"{now:%Y-%m-%d}:"
            already_run = {value for value in already_run if value.startswith(today_prefix)}
            time.sleep(max(5, poll_seconds))
    finally:
        remove_pid_file()


if __name__ == "__main__":
    raise SystemExit(main())
