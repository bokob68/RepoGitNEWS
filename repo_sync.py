#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local GitHub repo sync helpers for the Codex app workflow.

This script does not call the OpenAI API.
It only moves files between local paths and the GitHub repo clone.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "review_automation.ini"


@dataclass
class SyncConfig:
    config_path: Path
    source_news_file: Path
    repo_news_file: Path
    repo_review_file: Path
    review_output_file: Path
    mirror_outputs: list[Path]
    repo_path: Path
    repo_branch: str
    news_commit_message: str
    pull_before_review_sync: bool
    log_file: Path


def resolve_path(base_dir: Path, raw_value: str) -> Path:
    raw_value = raw_value.strip()
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_config(config_path: Path) -> SyncConfig:
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    base_dir = config_path.parent

    def read_path(section: str, option: str, fallback: str = "") -> Path:
        value = parser.get(section, option, fallback=fallback).strip()
        if not value:
            raise ValueError(f"Missing path config: [{section}] {option}")
        return resolve_path(base_dir, value)

    mirrors: list[Path] = []
    if parser.has_section("mirror_outputs"):
        for _, value in parser.items("mirror_outputs"):
            value = value.strip()
            if value:
                mirrors.append(resolve_path(base_dir, value))

    return SyncConfig(
        config_path=config_path,
        source_news_file=read_path("paths", "source_news_file"),
        repo_news_file=read_path("paths", "repo_news_file", "news.txt"),
        repo_review_file=read_path("paths", "repo_review_file", "review_latest.txt"),
        review_output_file=read_path("paths", "review_output_file", "review_latest.txt"),
        mirror_outputs=mirrors,
        repo_path=read_path("repo", "repo_path", "."),
        repo_branch=parser.get("repo", "branch", fallback="main").strip(),
        news_commit_message=parser.get("repo", "news_commit_message", fallback="Update news input").strip(),
        pull_before_review_sync=parser.getboolean("repo", "pull_before_review_sync", fallback=True),
        log_file=read_path("schedule", "log_file", "logs/repo_sync.log"),
    )


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


def write_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)
    return True


def run_git(repo_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def repo_relative(repo_path: Path, file_path: Path) -> str:
    return str(file_path.resolve().relative_to(repo_path.resolve()))


def sync_news_to_repo(config: SyncConfig) -> bool:
    if not config.source_news_file.is_file():
        raise FileNotFoundError(f"Source news file not found: {config.source_news_file}")

    source_text = config.source_news_file.read_text(encoding="utf-8")
    changed = write_if_changed(config.repo_news_file, source_text)
    if not changed:
        logging.info("Repo news file already matches local source.")
        return False

    rel_news = repo_relative(config.repo_path, config.repo_news_file)
    run_git(config.repo_path, "add", "--", rel_news)
    status = run_git(config.repo_path, "status", "--porcelain", "--", rel_news)
    if status.stdout.strip():
        run_git(config.repo_path, "commit", "-m", config.news_commit_message)
        run_git(config.repo_path, "push", "origin", config.repo_branch)
        logging.info("Pushed fresh news.txt to GitHub.")
        return True

    logging.info("No git changes detected for repo news file.")
    return False


def sync_review_from_repo(config: SyncConfig) -> bool:
    if config.pull_before_review_sync:
        run_git(config.repo_path, "fetch", "origin")
        run_git(config.repo_path, "pull", "--ff-only", "origin", config.repo_branch)
        logging.info("Pulled latest repo changes.")

    if not config.repo_review_file.is_file():
        raise FileNotFoundError(f"Repo review file not found: {config.repo_review_file}")

    review_text = config.repo_review_file.read_text(encoding="utf-8")
    changed = write_if_changed(config.review_output_file, review_text)
    mirror_changed = False
    for mirror_path in config.mirror_outputs:
        mirror_changed = write_if_changed(mirror_path, review_text) or mirror_changed

    if changed or mirror_changed:
        logging.info("Copied review_latest.txt to local output targets.")
    else:
        logging.info("Local review targets already match repo review_latest.txt.")
    return changed or mirror_changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync news.txt and review_latest.txt around a Codex app repo workflow.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to INI config file.")
    parser.add_argument("--push-news", action="store_true", help="Copy the fresh local news.txt into the repo and push it.")
    parser.add_argument("--pull-review", action="store_true", help="Pull the repo and copy review_latest.txt to local targets.")
    parser.add_argument("--full-cycle", action="store_true", help="Run push-news followed by pull-review.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config).resolve())
    setup_logging(config.log_file)

    push_news = args.push_news or args.full_cycle
    pull_review = args.pull_review or args.full_cycle
    if not push_news and not pull_review:
        print("Nothing to do. Use --push-news, --pull-review, or --full-cycle.", file=sys.stderr)
        return 1

    try:
        if push_news:
            sync_news_to_repo(config)
        if pull_review:
            sync_review_from_repo(config)
    except Exception as exc:
        logging.exception("Repo sync failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("OK: repo sync completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
