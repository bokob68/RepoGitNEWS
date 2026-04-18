#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate "Преглед на световния печат" from a news.txt-style input file.

The script is intentionally separate from yt_live_watch_noapi.py.
It reads configuration from review_automation.ini by default.
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "review_automation.ini"
ALLOWED_SECTIONS = {"Политика", "Икономика", "Свят", "Технологии"}
FORBIDDEN_PATTERNS = (
    "[1]",
    "[2]",
    "[^1]",
    "Sources",
    "References",
    "Източници:",
    "Бележки:",
)


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str


@dataclass
class AppConfig:
    config_path: Path
    input_file: Path
    rules_file: Path
    sample_output_file: Path | None
    output_file: Path
    archive_dir: Path | None
    write_archive: bool
    log_file: Path
    mirror_outputs: list[Path]
    model: str
    timeout_seconds: int
    max_output_tokens: int
    enable_web_search: bool
    api_base_url: str
    temperature: float | None
    git_enabled: bool
    repo_path: Path | None
    repo_branch: str
    pull_before_run: bool
    push_after_run: bool
    commit_message: str


def resolve_path(base_dir: Path, raw_value: str) -> Path:
    raw_value = raw_value.strip()
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_config(config_path: Path) -> AppConfig:
    parser = configparser.ConfigParser(interpolation=None)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    parser.read(config_path, encoding="utf-8")
    base_dir = config_path.parent

    def get_path(section: str, option: str, fallback: str = "") -> Path | None:
        value = parser.get(section, option, fallback=fallback).strip()
        if not value:
            return None
        return resolve_path(base_dir, value)

    input_file = get_path("paths", "input_file", "news.txt")
    rules_file = get_path("paths", "rules_file", "PROMPT_RULES.md")
    output_file = get_path("paths", "output_file", "review_latest.txt")
    archive_dir = get_path("paths", "archive_dir", "output")
    log_file = get_path("paths", "log_file", "logs/make_review.log")
    sample_output_file = get_path("paths", "sample_output_file", "news1.txt")
    if input_file is None or rules_file is None or output_file is None or log_file is None:
        raise ValueError("Config must define input_file, rules_file, output_file, and log_file.")

    mirrors: list[Path] = []
    if parser.has_section("mirror_outputs"):
        for _, value in parser.items("mirror_outputs"):
            value = value.strip()
            if value:
                mirrors.append(resolve_path(base_dir, value))

    repo_path = get_path("repo", "repo_path") if parser.has_section("repo") else None
    return AppConfig(
        config_path=config_path,
        input_file=input_file,
        rules_file=rules_file,
        sample_output_file=sample_output_file,
        output_file=output_file,
        archive_dir=archive_dir,
        write_archive=parser.getboolean("paths", "write_archive", fallback=True),
        log_file=log_file,
        mirror_outputs=mirrors,
        model=parser.get("openai", "model", fallback=os.environ.get("OPENAI_MODEL", "gpt-5")).strip(),
        timeout_seconds=parser.getint("openai", "timeout_seconds", fallback=180),
        max_output_tokens=parser.getint("openai", "max_output_tokens", fallback=6000),
        enable_web_search=parser.getboolean("openai", "enable_web_search", fallback=True),
        api_base_url=parser.get("openai", "api_base_url", fallback="https://api.openai.com/v1/responses").strip(),
        temperature=_parse_optional_float(parser.get("openai", "temperature", fallback="")),
        git_enabled=parser.getboolean("repo", "enabled", fallback=False),
        repo_path=repo_path,
        repo_branch=parser.get("repo", "branch", fallback="main").strip(),
        pull_before_run=parser.getboolean("repo", "pull_before_run", fallback=False),
        push_after_run=parser.getboolean("repo", "push_after_run", fallback=False),
        commit_message=parser.get("repo", "commit_message", fallback="Update generated review").strip(),
    )


def _parse_optional_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    return float(value)


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


def read_stable_text_file(path: Path, retries: int = 5, pause_seconds: float = 1.0) -> str:
    previous: str | None = None
    for attempt in range(1, retries + 1):
        first = path.read_text(encoding="utf-8")
        time.sleep(pause_seconds)
        second = path.read_text(encoding="utf-8")
        if first == second:
            return second
        previous = second
        logging.warning("Input file changed during read attempt %s: %s", attempt, path)
        time.sleep(pause_seconds)
    raise RuntimeError(f"Input file did not stabilize after {retries} attempts: {path}")


def parse_news_items(text: str) -> list[NewsItem]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Input file is empty after removing blank lines.")
    if len(lines) % 2 != 0:
        raise ValueError("Input file has an odd number of non-empty lines.")

    items: list[NewsItem] = []
    for index in range(0, len(lines), 2):
        items.append(NewsItem(title=lines[index], url=lines[index + 1]))
    return items


def is_valid_url(url: str) -> bool:
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    parsed = urllib.parse.urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def filter_valid_items(items: Iterable[NewsItem]) -> list[NewsItem]:
    valid: list[NewsItem] = []
    for item in items:
        if not item.title.strip():
            logging.warning("Skipping item with empty title: %r", item)
            continue
        if not is_valid_url(item.url):
            logging.warning("Skipping item with invalid URL: %s | %s", item.title, item.url)
            continue
        valid.append(item)
    if not valid:
        raise ValueError("No valid news items found in input file.")
    return valid


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        if item.url in seen:
            logging.info("Skipping duplicate URL: %s", item.url)
            continue
        seen.add(item.url)
        deduped.append(item)
    return deduped


def build_prompt(rules_text: str, items: list[NewsItem], sample_output: str | None) -> str:
    items_block = "\n".join(
        f"{idx}. TITLE: {item.title}\n   URL: {item.url}" for idx, item in enumerate(items, start=1)
    )
    sample_block = ""
    if sample_output:
        sample_block = (
            "\n\nIMPORTANT OUTPUT SHAPE\n"
            "Match the practical output style of this reference as closely as possible.\n"
            "Use a Bulgarian month name in the main title, append 'г.' after the year, append 'ч.' after the hour,\n"
            "and print the source URL on its own line as a Markdown link in the form [URL](URL).\n\n"
            f"{sample_output.strip()}\n"
        )

    return (
        "You are generating a finished Bulgarian editorial text file.\n"
        "Return only the final review text. Do not wrap it in code fences. Do not add explanations.\n\n"
        "RULES\n"
        f"{rules_text.strip()}\n"
        f"{sample_block}\n"
        "ADDITIONAL HARD REQUIREMENTS\n"
        "- Use only the news items listed below.\n"
        "- Do not skip any valid URL.\n"
        "- Do not add any external URLs.\n"
        "- Use only the sections: Политика, Икономика, Свят, Технологии.\n"
        "- Do not add an introduction or conclusion.\n"
        "- Keep a blank line between stories.\n"
        "- Each story must contain exactly one source URL line.\n\n"
        "NEWS ITEMS\n"
        f"{items_block}\n"
    )


def call_openai(config: AppConfig, prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    payload: dict[str, object] = {
        "model": config.model,
        "input": prompt,
        "max_output_tokens": config.max_output_tokens,
    }
    if config.enable_web_search:
        payload["tools"] = [{"type": "web_search"}]
    if config.temperature is not None:
        payload["temperature"] = config.temperature

    request = urllib.request.Request(
        config.api_base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            raw_data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API HTTP error {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    data = json.loads(raw_data)
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        content = item.get("content", [])
        parts: list[str] = []
        for chunk in content:
            if chunk.get("type") == "output_text" and chunk.get("text"):
                parts.append(chunk["text"])
        if parts:
            return "\n".join(parts).strip()

    raise RuntimeError("OpenAI API response did not contain output text.")


def normalize_markdown_links(text: str) -> str:
    return re.sub(r"\[(https?://[^\]]+)\]\((https?://[^)]+)\)", r"\2", text)


def extract_urls(text: str) -> list[str]:
    normalized = normalize_markdown_links(text)
    return re.findall(r"https?://[^\s)>\]]+", normalized)


def validate_output(output_text: str, input_items: list[NewsItem]) -> None:
    if not output_text.strip():
        raise ValueError("Output is empty.")
    if not output_text.startswith("# Преглед на световния печат за "):
        raise ValueError("Output is missing the required main title.")

    for pattern in FORBIDDEN_PATTERNS:
        if pattern in output_text:
            raise ValueError(f"Output contains forbidden pattern: {pattern}")

    sections = re.findall(r"^##\s+(.+?)\s*$", output_text, flags=re.MULTILINE)
    if not sections:
        raise ValueError("Output contains no sections.")
    invalid_sections = [section for section in sections if section not in ALLOWED_SECTIONS]
    if invalid_sections:
        raise ValueError(f"Output contains forbidden sections: {', '.join(invalid_sections)}")

    input_urls = [item.url for item in input_items]
    input_url_set = set(input_urls)
    extracted_urls = extract_urls(output_text)
    output_url_set = set(extracted_urls)

    missing = [url for url in input_urls if url not in output_url_set]
    if missing:
        raise ValueError(f"Output is missing input URLs: {missing}")

    extra = sorted(url for url in output_url_set if url not in input_url_set)
    if extra:
        raise ValueError(f"Output contains extra URLs: {extra}")

    for url in input_urls:
        count = output_text.count(url)
        if count not in (1, 2):
            raise ValueError(f"URL {url} appears an unexpected number of times: {count}")
        if count == 2 and f"[{url}]({url})" not in output_text:
            raise ValueError(f"URL {url} is duplicated outside the expected Markdown link format.")

    story_count = len(re.findall(r"^###\s+.+$", output_text, flags=re.MULTILINE))
    if story_count != len(input_items):
        raise ValueError(f"Output stories ({story_count}) do not match input items ({len(input_items)}).")

    source_count = len(re.findall(r"^Източник:\s+.+$", output_text, flags=re.MULTILINE))
    if source_count != len(input_items):
        raise ValueError(f"Output source lines ({source_count}) do not match input items ({len(input_items)}).")


def write_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_archive_copy(config: AppConfig, text: str) -> Path | None:
    if not config.write_archive or config.archive_dir is None:
        return None
    config.archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("review_%Y-%m-%d_%H-%M.txt")
    archive_path = config.archive_dir / stamp
    write_output(archive_path, text)
    return archive_path


def mirror_outputs(text: str, targets: Iterable[Path]) -> list[Path]:
    written: list[Path] = []
    for target in targets:
        write_output(target, text)
        written.append(target)
    return written


def run_git_pull(config: AppConfig) -> None:
    if not config.git_enabled or not config.pull_before_run or config.repo_path is None:
        return
    logging.info("Running git pull in %s", config.repo_path)
    subprocess.run(
        ["git", "-C", str(config.repo_path), "pull", "--ff-only", "origin", config.repo_branch],
        check=True,
        capture_output=True,
        text=True,
    )


def _collect_repo_relative_paths(config: AppConfig) -> list[str]:
    if not config.repo_path:
        return []
    repo_root = config.repo_path.resolve()
    paths = [config.output_file, *config.mirror_outputs]
    result: list[str] = []
    for path in paths:
        try:
            rel = path.resolve().relative_to(repo_root)
        except ValueError:
            continue
        result.append(str(rel))
    return result


def run_git_push(config: AppConfig) -> None:
    if not config.git_enabled or not config.push_after_run or config.repo_path is None:
        return
    repo_relative_paths = _collect_repo_relative_paths(config)
    if not repo_relative_paths:
        logging.info("No repo-local output files to commit.")
        return
    logging.info("Committing generated review files in %s", config.repo_path)
    subprocess.run(["git", "-C", str(config.repo_path), "add", "--", *repo_relative_paths], check=True)
    status = subprocess.run(
        ["git", "-C", str(config.repo_path), "status", "--porcelain", "--", *repo_relative_paths],
        check=True,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        logging.info("No git changes detected after generation.")
        return
    subprocess.run(
        ["git", "-C", str(config.repo_path), "commit", "-m", config.commit_message],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(config.repo_path), "push", "origin", config.repo_branch],
        check=True,
        capture_output=True,
        text=True,
    )


def run_generation(config: AppConfig) -> None:
    run_git_pull(config)
    logging.info("Reading input from %s", config.input_file)
    news_text = read_stable_text_file(config.input_file)
    items = dedupe_items(filter_valid_items(parse_news_items(news_text)))

    logging.info("Reading rules from %s", config.rules_file)
    rules_text = config.rules_file.read_text(encoding="utf-8")
    sample_output = None
    if config.sample_output_file and config.sample_output_file.is_file():
        sample_output = config.sample_output_file.read_text(encoding="utf-8")

    prompt = build_prompt(rules_text, items, sample_output)
    logging.info("Generating review via OpenAI model %s", config.model)
    output_text = call_openai(config, prompt)
    validate_output(output_text, items)

    write_output(config.output_file, output_text)
    archive_path = write_archive_copy(config, output_text)
    mirror_paths = mirror_outputs(output_text, config.mirror_outputs)
    run_git_push(config)

    logging.info("Saved main output: %s", config.output_file)
    if archive_path:
        logging.info("Saved archive copy: %s", archive_path)
    for mirror_path in mirror_paths:
        logging.info("Mirrored output: %s", mirror_path)


def run_self_tests() -> None:
    sample_with_blanks = """Title one

https://example.com/one

Title two

https://example.com/two
"""
    parsed = parse_news_items(sample_with_blanks)
    assert len(parsed) == 2
    assert parsed[0].url == "https://example.com/one"

    invalid_filtered = filter_valid_items(
        parse_news_items(
            """Title one
not-a-url
Title two
https://example.com/two
"""
        )
    )
    assert len(invalid_filtered) == 1
    assert invalid_filtered[0].title == "Title two"

    deduped = dedupe_items(
        [
            NewsItem("A", "https://example.com/one"),
            NewsItem("B", "https://example.com/one"),
        ]
    )
    assert len(deduped) == 1

    valid_output = """# Преглед на световния печат за 18 април 2026 г., 08:09 ч.

## Свят

### Заглавие едно

Текст. Текст. Текст. Текст.

Източник: Example

[https://example.com/one](https://example.com/one)

### Заглавие две

Текст. Текст. Текст. Текст.

Източник: Example

[https://example.com/two](https://example.com/two)
"""
    validate_output(
        valid_output,
        [
            NewsItem("One", "https://example.com/one"),
            NewsItem("Two", "https://example.com/two"),
        ],
    )

    try:
        validate_output(
            """# Преглед на световния печат за 18 април 2026 г., 08:09 ч.

## Спорт
""",
            [NewsItem("One", "https://example.com/one")],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected forbidden section validation failure.")

    print("Self-tests passed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate review_latest.txt from a news.txt-style input file.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to INI config file.")
    parser.add_argument("--self-test", action="store_true", help="Run built-in parser and validator tests.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        run_self_tests()
        return 0

    config = load_config(Path(args.config).resolve())
    setup_logging(config.log_file)
    try:
        run_generation(config)
    except Exception as exc:
        logging.exception("Review generation failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("OK: review generated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
