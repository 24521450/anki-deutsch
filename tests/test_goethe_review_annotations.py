from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFRESH_BATCH = "american_english_and_abbreviation_review_2026_09"


def _records() -> dict[str, dict[str, object]]:
    data = json.loads((ROOT / "review" / "goethe_record_overrides.json").read_text(encoding="utf-8"))
    return data["records"]


def test_every_abbreviation_headword_has_an_explicit_reviewed_note() -> None:
    source_ids = {
        "A1-84886454584", "A1-84886454586", "A1-84886454922", "A1-84886455046", "A1-WG-0039",
        "A2-WG-0002", "A2-WG-0003", "A2-WG-0005", "A2-WG-0006", "A2-WG-0008",
        "B1-MAIN-0585", "B1-MAIN-2492", "B1-WG-0004", "B1-WG-0007", "B1-WG-0008",
        "B1-WG-0009", "B1-WG-0011", "B1-WG-0014", "B1-WG-0015", "B1-WG-0016",
        "B1-WG-0018", "B1-WG-0020", "B1-WG-0032", "B1-WG-0072", "B1-WG-0123",
        "B1-WG-0124", "B1-WG-0239", "B1-WG-0240", "B1-WG-0253", "B1-WG-0254", "B1-WG-0279",
        "B1-WG-0283", "B1-WG-0287",
    }
    records = _records()
    missing = []
    for source_id in source_ids:
        record = records.get(source_id, {})
        note = record.get("set", {}).get("FormOrVariantNote", "")
        if record.get("refresh_batch") != REFRESH_BATCH or not note:
            missing.append(source_id)
    assert not missing, f"abbreviation notes missing for {sorted(missing)}"

    assert "Lastkraftwagen" in records["A1-84886454922"]["set"]["FormOrVariantNote"]
    assert "das heißt" in records["A2-WG-0002"]["set"]["FormOrVariantNote"]
    assert all(term in records["B1-WG-0008"]["set"]["FormOrVariantNote"] for term in ("EG", "OG", "UG"))
    assert "Kraftfahrzeug" in records["B1-WG-0011"]["set"]["FormOrVariantNote"]
    assert "km/h" in records["B1-WG-0254"]["set"]["FormOrVariantNote"]
    assert "dkg = Dekagramm" in records["B1-WG-0253"]["set"]["FormOrVariantNote"]
    assert "dag" in records["B1-WG-0253"]["set"]["FormOrVariantNote"]


def test_dose_and_redundant_gloss_fixes_are_in_the_reviewed_refresh_batch() -> None:
    records = _records()
    dose = records["B1-MAIN-0554"]
    assert dose["set"]["RegionalVariants"] == "D, CH: Büchse"
    assert "standard German" in dose["set"]["UsageNoteEN"]
    assert dose["refresh_batch"] == REFRESH_BATCH

    english = json.loads(
        (ROOT / "review" / "goethe_b1_american_english_overrides.json").read_text(encoding="utf-8")
    )["records"]
    assert english["B1-MAIN-0554"]["set"]["MeaningEN"] == "can"
    assert english["B1-MAIN-1404"]["set"]["MeaningEN"] == "sick person"
    for source_id in ("B1-MAIN-2691", "B1-MAIN-2692"):
        assert "Example1EN" in english[source_id]["set"]
        assert "Example2EN" not in english[source_id]["set"]
