"""Shared corpus contract for the canonical Goethe A1-B1 decks."""
from __future__ import annotations

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

EXPECTED_NOTES_BY_LEVEL = {"A1": 818, "A2": 707, "B1": 1968}
EXPECTED_CARDS_BY_LEVEL = {
    level: count * 2 for level, count in EXPECTED_NOTES_BY_LEVEL.items()
}
EXPECTED_NOTES = sum(EXPECTED_NOTES_BY_LEVEL.values())
EXPECTED_CARDS = sum(EXPECTED_CARDS_BY_LEVEL.values())

DUDEN_ROWS = {"A1": 685, "A2": 1147, "B1": 2969}

# Canonical post-v4 example inventory. The reviewed A1 Lieblings- note retains
# its additional Goethe A2 usage sentence, hence 995 A1 occurrences.
EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL = {"A1": 995, "A2": 1015, "B1": 2308}
EXPECTED_EXAMPLE_OCCURRENCES = sum(EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL.values())
EXPECTED_EMPTY_NOTES_BY_LEVEL = {"A1": 53, "A2": 4, "B1": 199}
EXPECTED_EMPTY_NOTES = sum(EXPECTED_EMPTY_NOTES_BY_LEVEL.values())
EXPECTED_UNIQUE_EXAMPLE_AUDIO = 4153

ENGLISH_AUDITED_TAG = "goethe::quality::english_audited::v4::british"
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
