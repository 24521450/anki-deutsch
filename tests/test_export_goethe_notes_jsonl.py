from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import export_goethe_notes_jsonl as export  # noqa: E402


def test_export_contract_reflects_six_reviewed_a1_a2_duplicate_deletions():
    assert export.EXPECTED_NOTES == 1524
    assert export.EXPECTED_CARDS == 3048


def note():
    values = {name: "" for name in export.gw.FIELDS}
    values.update({
        "Lemma": "Viertel", "MeaningEN": "quarter", "CEFR": "A1",
        "AcceptedAnswersDE": "Viertel|das Viertel", "AcceptedArticlesDE": "das",
        "AcceptedFullAnswersDE": "das Viertel", "ProductionEnabled": "1",
        "ProductionHint": "Germany/Austria", "ExampleTargetSpansJSON": "[[[0,7]]]",
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
    assert row["accepted_full_answers_de"] == ["das Viertel"]
    assert row["production_enabled"] is True
    assert row["production_hint"] == "Germany/Austria"
    assert row["example_target_spans"] == [[[0, 7]]]
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


def test_load_live_rows_filters_b1_before_fetching_cards(monkeypatch):
    a1 = note()
    b1 = note()
    b1["noteId"] = 11
    b1["fields"]["CEFR"]["value"] = "B1"
    b1["fields"]["SourceID"]["value"] = "B1-X"
    a1["cards"] = [20, 21]
    b1["cards"] = [22, 23]
    calls = []

    def fake_anki(action, **params):
        calls.append((action, params))
        if action == "findNotes":
            return [10, 11]
        if action == "notesInfo":
            return [a1, b1]
        if action == "cardsInfo":
            assert params["cards"] == [20, 21]
            return cards()
        raise AssertionError(action)

    monkeypatch.setattr(export.gw, "anki", fake_anki)
    monkeypatch.setattr(export, "validate_rows", lambda rows: None)

    rows = export.load_live_rows()

    assert [row["cefr"] for row in rows] == ["A1"]
    assert [action for action, _ in calls] == ["findNotes", "notesInfo", "cardsInfo"]
