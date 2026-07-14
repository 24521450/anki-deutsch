"""Canonical per-level Goethe example policy shared by cleanup and rebuilds."""
from __future__ import annotations

import html
import json
import re
import unicodedata
from typing import Any

import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
SOURCE_PATHS = {"A1": gw.SOURCE_A1, "A2": gw.SOURCE_A2}
OVERRIDES_PATH = ROOT / "review" / "goethe_source_text_overrides.json"


def sentence_key(value: str) -> str:
    value = html.unescape(str(value or "")).strip()
    value = re.sub(r"\s+", " ", value)
    value = unicodedata.normalize("NFC", value).replace("’", "'").replace("–", "-")
    return value.casefold()


def load_overrides() -> dict[str, list[str]]:
    if not OVERRIDES_PATH.exists():
        return {}
    data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("examples"), dict):
        raise ValueError("unsupported Goethe source-text override schema")
    return {str(key): list(value) for key, value in data["examples"].items()}


def allowed_examples_by_level() -> dict[str, dict[str, str]]:
    overrides = load_overrides()
    allowed: dict[str, dict[str, str]] = {"A1": {}, "A2": {}}
    for level, path in SOURCE_PATHS.items():
        for row in gw.parse_markdown(path):
            ref = f"{level}-MAIN-{row['row']:04d}"
            for sentence in overrides.get(ref, row["examples"]):
                allowed[level].setdefault(sentence_key(sentence), sentence)
    return allowed


def filter_examples(
    level: str,
    examples: list[dict[str, Any]],
    allowed: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    policy = allowed or allowed_examples_by_level()
    if level not in policy:
        raise ValueError(f"unsupported Goethe level: {level}")
    keys = policy[level]
    return [example for example in examples if sentence_key(str(example.get("de") or "")) in keys]
