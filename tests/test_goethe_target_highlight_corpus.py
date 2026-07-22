from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_target_highlights as highlights  # noqa: E402
import goethe_werkstatt_migrate as gw  # noqa: E402


EXPORT = ROOT / "data" / "build" / "anki_notes.jsonl"
REPAIRS = ROOT / "review" / "goethe_target_highlight_repairs_v2.json"


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


def test_python_and_card_javascript_match_all_reviewed_verb_examples(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        return
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line]
    blank = set(highlights.verb_policy()["blank_pos_verb_source_ids"])
    payload = []
    for row in rows:
        if row["pos"].lower().rstrip(".") != "v" and row["source_id"] not in blank:
            continue
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
    assert len(payload) == 728
    payload_path = tmp_path / "verb_examples.json"
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


def test_reviewed_repair_manifest_is_the_complete_corpus_delta() -> None:
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line]
    manifest = json.loads(REPAIRS.read_text(encoding="utf-8"))
    repairs = manifest["repairs"]

    assert manifest["schema_version"] == 1
    assert manifest["expected_changed_notes"] == 141 == len(repairs)
    assert len({item["source_id"] for item in repairs}) == len(repairs)
    assert sum(
        before != after
        for item in repairs
        for before, after in zip(item["before"], item["after"])
    ) == manifest["expected_changed_examples"] == 166
    assert manifest["expected_added_ranges"] == 195
    assert manifest["expected_removed_ranges"] == 8

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
