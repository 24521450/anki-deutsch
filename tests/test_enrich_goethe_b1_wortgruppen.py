from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import enrich_goethe_b1_wortgruppen as enrichment  # noqa: E402


def test_numeric_phrase_preserves_decimal_comma() -> None:
    assert enrichment.classify("1,15 m", "1.12 Maße und Gewichte") == (
        "1,15 m", "phrase", "", "", "", "", "",
    )


def test_numeric_phrase_preserves_unit_slash() -> None:
    assert enrichment.classify("1 km/h", "1.12 Maße und Gewichte") == (
        "1 km/h", "phrase", "", "", "", "", "",
    )


def test_numeric_noun_form_suffix_is_still_removed() -> None:
    assert enrichment.classify("1 Euro, -s", "1.12 Maße und Gewichte")[0] == "1 Euro"
