from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_completion as gc  # noqa: E402
import goethe_source_examples as source_examples  # noqa: E402
import goethe_werkstatt_migrate as gw  # noqa: E402


def test_b1_source_contract_and_pdf_line_wraps_are_clean() -> None:
    rows = gw.parse_markdown(gw.SOURCE_B1)
    assert len(rows) == 2969
    assert all(row["pos"] and row["examples"] for row in rows)

    broken = []
    for row in rows:
        for sentence in row["examples"]:
            broken.extend(re.findall(r"\b[^\W\d_]+-\s+[^\W\d_]+\b", sentence))
    assert broken == []


def test_b1_wortgruppen_uses_enriched_schema() -> None:
    rows = gc.parse_wortgruppen(gc.WG_FILES["B1"])
    assert len(rows) == 355
    assert rows[0]["id"] == "B1-WG-0001"
    assert rows[-1]["id"] == "B1-WG-0355"


def test_level_rank_keeps_lowest_goethe_level() -> None:
    assert gc.lower_level("A1", "B1") == "A1"
    assert gc.lower_level("A2", "B1") == "A2"
    assert gc.lower_level("B1", "B1") == "B1"


def test_matcher_refuses_incompatible_homographs() -> None:
    adjective = gc.new_record("A1-X", "bar", "A1", "adj.")
    records = {"1": adjective}
    assert gc.find_record(records, gc.variant_index(records), "Bar", "n.", "f.") is None


def test_b1_examples_have_their_own_level_policy() -> None:
    allowed = source_examples.allowed_examples_by_level()
    assert set(allowed) == {"A1", "A2", "B1"}
    assert len(allowed["B1"]) > 4000


def test_b1_deck_and_level_style_exist_without_new_card_type() -> None:
    assert gw.B1_DECK == "Goethe Institute::B1 Wordlist"
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    assert '.gw-card[data-level="B1"]' in css
    assert "fonts.googleapis.com" not in css
    assert "gw-regional" in css
