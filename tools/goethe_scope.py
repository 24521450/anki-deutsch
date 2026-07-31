"""Shared corpus contract for the canonical Goethe A1-B1 decks."""
from __future__ import annotations

import html
from typing import Any, Mapping

import goethe_werkstatt_migrate as gw


LEVELS = ("A1", "A2", "B1")
LEVEL_RANK = {level: index for index, level in enumerate(LEVELS)}
LEVEL_DECK = {
    "A1": gw.A1_DECK,
    "A2": gw.A2_DECK,
    "B1": gw.B1_DECK,
}
LEVEL_TAG = {level: f"goethe::level::{level.casefold()}" for level in LEVELS}

EXPECTED_NOTES_BY_LEVEL = {"A1": 804, "A2": 680, "B1": 1941}
EXPECTED_CARDS_BY_LEVEL = {
    level: count * 2 for level, count in EXPECTED_NOTES_BY_LEVEL.items()
}
EXPECTED_NOTES = sum(EXPECTED_NOTES_BY_LEVEL.values())
EXPECTED_CARDS = sum(EXPECTED_CARDS_BY_LEVEL.values())

DUDEN_ROWS = {"A1": 685, "A2": 1147, "B1": 2969}

# Canonical post-v5 example inventory, including one reviewed supplemental
# sentence for every source-only Wortgruppen note and reviewed sense splits.
EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL = {"A1": 1579, "A2": 1002, "B1": 2499}
EXPECTED_EXAMPLE_OCCURRENCES = sum(EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL.values())
EXPECTED_EMPTY_NOTES_BY_LEVEL = {"A1": 0, "A2": 0, "B1": 0}
EXPECTED_EMPTY_NOTES = sum(EXPECTED_EMPTY_NOTES_BY_LEVEL.values())
EXPECTED_UNIQUE_EXAMPLE_AUDIO = 4992

ENGLISH_AUDITED_TAG = "goethe::quality::english_audited::v5::american"
ENGLISH_REVIEW_TAG = "goethe::quality::translation_review_needed"


class ScopeError(ValueError):
    pass


def stable_guid(fields: Mapping[str, Any]) -> str:
    """Return the durable note identity used by exports and review artifacts."""
    legacy_guid = str(fields.get("LegacyGUID", "") or "").strip()
    if legacy_guid:
        return legacy_guid
    source_id = str(fields.get("SourceID", "") or "").strip()
    if source_id:
        return f"goethe:{source_id}"
    raise ScopeError("note has neither LegacyGUID nor SourceID")


def guid_matches_expected(actual: Any, expected: Any) -> bool:
    """Accept the canonical GUID or its HTML-escaped Anki field representation."""
    actual_text = str(actual or "").strip()
    expected_text = str(expected or "").strip()
    return actual_text == expected_text or html.unescape(actual_text) == expected_text
