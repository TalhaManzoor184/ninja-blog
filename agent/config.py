"""Loads config.yaml with environment overrides."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "progress.json"
BACKUP_DIR = STATE_DIR / "backups"
REPORT_FILE = STATE_DIR / "report.md"
OUTPUT_DIR = ROOT / "output"
PRIORITY_FILE = ROOT / "content" / "priority.txt"


def _bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def cfg() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    m = data["models"]
    m["text"] = os.getenv("GEMINI_TEXT_MODEL") or m["text"]
    m["image"] = os.getenv("GEMINI_IMAGE_MODEL") or m["image"]
    data["site"]["url"] = (os.getenv("WP_URL") or data["site"]["url"]).rstrip("/")
    r = data["run"]
    if os.getenv("POSTS_PER_DAY", "").strip():
        r["posts_per_day"] = int(os.environ["POSTS_PER_DAY"])
    if os.getenv("PUBLISH", "").strip():
        r["publish"] = _bool(os.environ["PUBLISH"])
    if os.getenv("USE_SEARCH", "").strip():
        r["use_search"] = _bool(os.environ["USE_SEARCH"])
    if os.getenv("DUPLICATE_ACTION", "").strip():
        data["duplicates"]["action"] = os.environ["DUPLICATE_ACTION"].strip().lower()
    return data


def env(name: str, required: bool = True) -> str:
    val = os.getenv(name, "").strip()
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val
