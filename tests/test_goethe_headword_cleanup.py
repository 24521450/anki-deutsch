from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_headword_cleanup as cleanup  # noqa: E402


def note(note_id: int, lemma: str, meaning: str, refs: str = "A1-MAIN-0001", order: str = "1") -> dict:
    fields = {
        "Lemma": lemma, "MeaningEN": meaning, "CEFR": "A1", "POS": "prep.",
        "SourceRefs": refs, "OriginalOrder": order,
    }
    return {"noteId": note_id, "fields": {key: {"value": value} for key, value in fields.items()}, "cards": []}


def test_merged_glosses_are_unique_and_drop_unsupported_machen_sense():
    group = [note(1, "machen", "to do; to make", order="1"), note(2, "machen", "to come to", refs="A1-0002", order="2")]
    assert cleanup.merged_gloss(group) == "to do; to make"


def test_examples_are_deduplicated_in_source_order():
    first = note(1, "zu", "to", order="1")
    second = note(2, "zu", "at", refs="A1-0002", order="2")
    first["fields"].update({"Example1DE": {"value": "Er geht zu Hause."}, "Example1EN": {"value": "He goes home."}})
    second["fields"].update({"Example1DE": {"value": "Er geht zu Hause."}, "Example1EN": {"value": "He goes home."}, "Example2DE": {"value": "Die Tür ist zu."}, "Example2EN": {"value": "The door is closed."}})
    examples = cleanup.merged_examples([first, second])
    assert [item["de"] for item in examples] == ["Er geht zu Hause.", "Die Tür ist zu."]


def test_der_das_group_accepts_all_articles():
    assert set(cleanup.DER_DAS_IDS) == {1584886454605, 1584886454606, 1584886454607}
