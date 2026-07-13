from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import export_goethe_notes_jsonl as export  # noqa: E402


def note():
    values = {name: "" for name in export.gw.FIELDS}
    values.update({
        "Lemma": "Viertel", "MeaningEN": "quarter", "CEFR": "A1",
        "AcceptedAnswersDE": "Viertel|das Viertel", "AcceptedArticlesDE": "das",
        "Example1DE": "Viertel nach zwei", "Example1EN": "quarter past two",
        "SourceID": "A1-X", "SourceRefs": "A1-X|A1-WG-X", "OriginalOrder": "10",
    })
    return {
        "noteId": 10, "modelName": export.gw.MODEL, "tags": ["z", "a"],
        "fields": {name: {"value": value} for name, value in values.items()},
    }


def cards():
    return [
        {"cardId": 21, "note": 10, "ord": 1, "deckName": "Goethe Institute::A1 Wordlist"},
        {"cardId": 20, "note": 10, "ord": 0, "deckName": "Goethe Institute::A1 Wordlist"},
    ]


def test_serialize_note_is_agent_readable_and_stable():
    row = export.serialize_note(note(), cards())
    assert row["guid"] == "goethe:A1-X"
    assert row["accepted_answers_de"] == ["Viertel", "das Viertel"]
    assert row["examples"] == [{"de": "Viertel nach zwei", "en": "quarter past two", "audio": ""}]
    assert row["source_refs"] == ["A1-X", "A1-WG-X"]
    assert row["tags"] == ["a", "z"]
    assert row["card_ids"] == [20, 21]


def test_write_jsonl_uses_one_utf8_json_object_per_line(tmp_path: Path):
    path = tmp_path / "notes.jsonl"
    export.write_jsonl([{"lemma": "grüßen"}, {"lemma": "Straße"}], path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["lemma"] for line in lines] == ["grüßen", "Straße"]
    assert "grüßen" in path.read_text(encoding="utf-8")


def test_overflow_examples_are_exposed_without_requiring_html_parsing():
    raw = (
        '<article class="gw-example"><div class="gw-example-main gw-example-de">'
        'Grüße aus Köln.</div><div class="gw-example-sub">Greetings from Cologne.</div></article>'
    )
    assert export.overflow_examples(raw) == [{
        "de": "Grüße aus Köln.", "en": "Greetings from Cologne.", "audio": "",
    }]
