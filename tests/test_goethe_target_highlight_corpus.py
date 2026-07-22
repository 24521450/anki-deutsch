from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_target_highlights as highlights  # noqa: E402


EXPORT = ROOT / "data" / "build" / "anki_notes.jsonl"
REPAIRS = ROOT / "review" / "goethe_target_highlight_repairs.json"


def as_fields(row: dict) -> dict[str, str]:
    examples = row["examples"][:4]
    fields = {
        "Lemma": row["lemma"],
        "AcceptedAnswersDE": "|".join(row["accepted_answers_de"]),
        "NounFormsRaw": row["noun_forms_raw"],
        "VerbFormsRaw": row["verb_forms_raw"],
        "POS": row["pos"],
        "MoreExamplesHTML": row["more_examples_html"],
    }
    for index in range(1, 5):
        fields[f"Example{index}DE"] = examples[index - 1]["de"] if index <= len(examples) else ""
    return fields


def test_reviewed_repair_manifest_is_the_complete_corpus_delta() -> None:
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line]
    manifest = json.loads(REPAIRS.read_text(encoding="utf-8"))
    repairs = manifest["repairs"]

    assert manifest["schema_version"] == 1
    assert manifest["expected_changed_notes"] == 40 == len(repairs)
    assert len({item["source_id"] for item in repairs}) == len(repairs)
    assert sum(
        before != after
        for item in repairs
        for before, after in zip(item["before"], item["after"])
    ) == manifest["expected_changed_examples"] == 44

    by_source = {item["source_id"]: item for item in repairs}
    seen: set[str] = set()
    for row in rows:
        source_id = row["source_id"]
        stored = row["example_target_spans"]
        built = json.loads(highlights.build_spans(as_fields(row)))
        repair = by_source.get(source_id)
        if repair is None:
            assert built == stored, source_id
            continue
        seen.add(source_id)
        assert repair["note_id"] == row["anki_note_id"]
        assert repair["card_ids"] == row["card_ids"]
        assert repair["lemma"] == row["lemma"]
        assert repair["before"] != repair["after"]
        assert stored in (repair["before"], repair["after"])
        assert built == repair["after"]

    assert seen == set(by_source)
