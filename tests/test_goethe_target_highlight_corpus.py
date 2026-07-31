from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_target_highlights as highlights  # noqa: E402
import goethe_werkstatt_migrate as gw  # noqa: E402


EXPORT = ROOT / "data" / "build" / "anki_notes.jsonl"
REPAIRS = ROOT / "review" / "goethe_target_highlight_repairs_v3.json"


def as_fields(row: dict) -> dict[str, str]:
    examples = row["examples"][:4]
    fields = {
        "Lemma": row["lemma"],
        "AcceptedAnswersDE": "|".join(row["accepted_answers_de"]),
        "NounFormsRaw": row["noun_forms_raw"],
        "VerbFormsRaw": row["verb_forms_raw"],
        "SourceNoteRaw": row["source_note_raw"],
        "SourceID": row["source_id"],
        "POS": row["pos"],
        "MoreExamplesHTML": row["more_examples_html"],
    }
    for index in range(1, 5):
        fields[f"Example{index}DE"] = examples[index - 1]["de"] if index <= len(examples) else ""
    return fields


def test_absolut_export_highlights_both_adverb_and_inflected_adjective():
    row = next(
        json.loads(line)
        for line in EXPORT.read_text(encoding="utf-8").splitlines()
        if '"source_id":"B1-MAIN-0030"' in line
    )

    assert row["pos"] == "adj., adv."
    assert json.loads(highlights.build_target_spans(as_fields(row))) == [
        [[22, 29]], [[9, 18]],
    ]


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("B1-MAIN-2784", [[[21, 26], [32, 36]]]),
        ("B1-MAIN-2752", [[[30, 35]], [[17, 22]]]),
    ],
)
def test_dictionary_notation_regression_fixtures(source_id: str, expected: list) -> None:
    row = next(
        json.loads(line)
        for line in EXPORT.read_text(encoding="utf-8").splitlines()
        if f'"source_id":"{source_id}"' in line
    )
    assert json.loads(highlights.build_spans(as_fields(row))) == expected


def test_python_and_card_javascript_match_all_reviewed_examples(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        return
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line]
    payload = []
    for row in rows:
        fields = as_fields(row)
        payload.append({
            "fields": {
                "gw-source-id": fields["SourceID"], "gw-lemma": fields["Lemma"],
                "gw-accepted-answers": fields["AcceptedAnswersDE"],
                "gw-noun-forms": fields["NounFormsRaw"], "gw-verb-forms": fields["VerbFormsRaw"],
                "gw-source-note-raw": fields["SourceNoteRaw"], "gw-pos": fields["POS"],
            },
            "texts": highlights.example_texts(fields),
            "expected": json.loads(highlights.build_spans(fields)),
        })
    assert len(payload) == 3425
    payload_path = tmp_path / "all_examples.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    back = gw.templates()["German → English"]["Back"]
    highlighter = back.split("</main>\n<script>\n", 1)[1].split("\n</script>\n<script>\n", 1)[0]
    script_path = tmp_path / "parity.js"
    script_path.write_text(r'''
const fs = require("fs");
const rows = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const fields = {};
globalThis.document = {getElementById:(id)=>({textContent:fields[id]||""}), querySelectorAll:()=>[]};
HIGHLIGHTER
const api = globalThis.goetheWerkstattTargetHighlighter;
for (const row of rows) {
  Object.keys(fields).forEach((key)=>delete fields[key]); Object.assign(fields,row.fields);
  const terms = api.terms();
  const actual = row.texts.map((source,index)=>api.rangesForExample(source,terms,index+1));
  if (JSON.stringify(actual)!==JSON.stringify(row.expected)) {
    throw new Error(fields["gw-source-id"]+" "+JSON.stringify(actual)+" != "+JSON.stringify(row.expected));
  }
}
'''.replace("HIGHLIGHTER", highlighter), encoding="utf-8")
    subprocess.run([node, str(script_path), str(payload_path)], check=True, capture_output=True, text=True)


def test_reviewed_repair_manifest_is_valid_and_current_corpus_is_in_sync() -> None:
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line]
    manifest = json.loads(REPAIRS.read_text(encoding="utf-8"))
    repairs = manifest["repairs"]

    assert manifest["schema_version"] == 1
    assert manifest["expected_changed_notes"] == 208 == len(repairs)
    assert len({item["source_id"] for item in repairs}) == len(repairs)
    assert sum(
        before != after
        for item in repairs
        for before, after in zip(item["before"], item["after"])
    ) == manifest["expected_changed_examples"] == 286
    assert manifest["expected_added_ranges"] == 317
    assert manifest["expected_removed_ranges"] == 34

    # The review manifest is the immutable record of the one-time migration.
    # Dedupe and source-owned example rebuilding may legitimately replace note
    # IDs, reorder examples, or remove old source IDs.  The current invariant is
    # that every exported note exactly matches a fresh span calculation.
    for row in rows:
        stored = row["example_target_spans"]
        built = json.loads(highlights.build_spans(as_fields(row)))
        assert built == stored, row["source_id"]


def test_every_empty_example_has_a_reviewed_non_repair_classification() -> None:
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line]
    cases = {
        (case["source_id"], case["example_index"]): case
        for case in highlights.highlight_policy()["cases"]
    }
    empty: set[tuple[str, int]] = set()
    for row in rows:
        spans = json.loads(highlights.build_spans(as_fields(row)))
        for index, (example, ranges) in enumerate(zip(row["examples"], spans), 1):
            if ranges:
                continue
            empty.add((row["source_id"], index))
            assert cases[(row["source_id"], index)]["status"] in {"needs_review", "valid_blank"}

    reviewed_blank = {
        key for key, case in cases.items()
        if case["status"] in {"needs_review", "valid_blank"}
    }
    assert empty == reviewed_blank
