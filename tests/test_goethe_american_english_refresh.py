from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_english_audit as audit  # noqa: E402


def test_reviewed_b1_audit_contains_no_british_english_terms() -> None:
    path = ROOT / "review" / "goethe_english_audit_v5.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    offenders = [
        row["source_id"]
        for row in rows
        if row.get("cefr") == "B1" and "non_american_english" in audit._review_entry_errors(row)
    ]
    assert not offenders


def test_b1_us_overrides_cover_can_sick_person_and_british_medical_term() -> None:
    path = ROOT / "review" / "goethe_b1_american_english_overrides.json"
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    assert records["B1-MAIN-0554"]["set"]["MeaningEN"] == "can"
    assert records["B1-MAIN-1404"]["set"]["MeaningEN"] == "sick person"
    for source_id in ("B1-MAIN-2691", "B1-MAIN-2692"):
        assert records[source_id]["set"]["MeaningEN"].endswith("substitute doctor")
        assert "Example1EN" in records[source_id]["set"]
        assert "Example2EN" not in records[source_id]["set"]
