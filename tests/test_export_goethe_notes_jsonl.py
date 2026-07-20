from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import export_goethe_notes_jsonl as export  # noqa: E402


def test_export_contract_covers_all_three_canonical_decks():
    assert export.scope.LEVELS == ("A1", "A2", "B1")
    assert export.scope.EXPECTED_NOTES_BY_LEVEL == {"A1": 818, "A2": 707, "B1": 1968}
    assert export.EXPECTED_NOTES == export.scope.EXPECTED_NOTES == 3493
    assert export.EXPECTED_CARDS == export.scope.EXPECTED_CARDS == 6986
    assert export.scope.DUDEN_ROWS == {"A1": 685, "A2": 1147, "B1": 2969}


def note():
    values = {name: "" for name in export.gw.FIELDS}
    values.update({
        "Lemma": "Viertel", "MeaningEN": "quarter", "CEFR": "A1",
        "AcceptedAnswersDE": "Viertel|das Viertel", "AcceptedArticlesDE": "das",
        "AcceptedFullAnswersDE": "das Viertel", "ProductionEnabled": "1",
        "ProductionHint": "Germany/Austria", "ExampleTargetSpansJSON": "[[[0,7]]]",
        "Example1DE": "Viertel nach zwei", "Example1EN": "quarter past two",
        "Example1Audio": "<audio src=\"example.mp3\"></audio>",
        "WordAudio": "[sound:word.mp3]",
        "SourceID": "A1-X", "SourceRefs": "A1-X|A1-WG-X", "OriginalOrder": "10",
    })
    return {
        "noteId": 10, "modelName": export.gw.MODEL,
        "tags": ["z", "a", export.scope.ENGLISH_AUDITED_TAG],
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
    assert row["examples"] == [{
        "de": "Viertel nach zwei", "en": "quarter past two",
        "audio": '<audio src="example.mp3"></audio>',
    }]
    assert row["source_refs"] == ["A1-X", "A1-WG-X"]
    assert row["tags"] == ["a", export.scope.ENGLISH_AUDITED_TAG, "z"]
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


def test_load_live_rows_includes_b1_and_fetches_all_cards(monkeypatch):
    a1 = note()
    b1 = copy.deepcopy(note())
    b1["noteId"] = 11
    b1["fields"]["CEFR"]["value"] = "B1"
    b1["fields"]["SourceID"]["value"] = "B1-X"
    b1["fields"]["SourceRefs"]["value"] = "B1-X"
    a1["cards"] = [20, 21]
    b1["cards"] = [22, 23]
    b1_cards = [
        {"cardId": 23, "note": 11, "ord": 1, "deckName": export.scope.LEVEL_DECK["B1"]},
        {"cardId": 22, "note": 11, "ord": 0, "deckName": export.scope.LEVEL_DECK["B1"]},
    ]
    calls = []

    def fake_anki(action, **params):
        calls.append((action, params))
        if action == "version":
            return 6
        if action == "findNotes":
            return [10, 11]
        if action == "notesInfo":
            return [a1, b1]
        if action == "cardsInfo":
            assert params["cards"] == [20, 21, 22, 23]
            return cards() + b1_cards
        raise AssertionError(action)

    monkeypatch.setattr(export.gw, "anki", fake_anki)
    monkeypatch.setattr(export, "validate_rows", lambda rows: None)
    monkeypatch.setattr(export, "load_audit_entries", lambda: {})
    monkeypatch.setattr(export, "validate_audited_content", lambda rows, entries: None)

    rows = export.load_live_rows()

    assert [row["cefr"] for row in rows] == ["A1", "B1"]
    assert [action for action, _ in calls] == ["version", "findNotes", "notesInfo", "cardsInfo"]


def test_serialize_note_fails_closed_on_deck_and_source_identity_drift():
    wrong_deck = copy.deepcopy(cards())
    wrong_deck[0]["deckName"] = export.scope.LEVEL_DECK["A2"]
    with pytest.raises(export.ExportError, match="invalid card identity or deck"):
        export.serialize_note(note(), wrong_deck)

    wrong_source = copy.deepcopy(note())
    wrong_source["fields"]["SourceRefs"]["value"] = "A1-OTHER"
    with pytest.raises(export.ExportError, match="invalid source identity"):
        export.serialize_note(wrong_source, cards())


def test_export_rejects_content_drift_even_when_audit_tag_remains():
    row = export.serialize_note(note(), cards())
    entry = {
        "source_id": row["source_id"], "stable_guid": row["guid"],
        "source_refs": row["source_refs"], "lemma": row["lemma"],
        "cefr": row["cefr"],
        "desired_meaning_en": row["meaning_en"],
        "desired_examples": [{"de": item["de"], "en": item["en"]} for item in row["examples"]],
    }
    export.validate_audited_content([row], {row["source_id"]: entry})
    drifted = copy.deepcopy(row)
    drifted["meaning_en"] = "tampered"
    with pytest.raises(export.ExportError, match="English meaning drift"):
        export.validate_audited_content([drifted], {row["source_id"]: entry})


def test_export_canonicalises_historical_source_alias_by_guid():
    current = note()
    current["fields"]["SourceID"]["value"] = "A1-LEGACY"
    current["fields"]["SourceRefs"]["value"] = "A1-X|A1-LEGACY"
    current["fields"]["LegacyGUID"]["value"] = "goethe:A1-X"
    row = export.serialize_note(current, cards())
    entry = {
        "source_id": "A1-X", "stable_guid": "goethe:A1-X",
        "source_refs": ["A1-X", "A1-LEGACY"], "lemma": row["lemma"],
        "cefr": row["cefr"], "desired_meaning_en": row["meaning_en"],
        "desired_examples": [{"de": item["de"], "en": item["en"]} for item in row["examples"]],
    }
    export.validate_audited_content([row], {entry["source_id"]: entry})
    assert row["source_id"] == "A1-X"
    assert row["source_refs"] == ["A1-X", "A1-LEGACY"]


def test_export_rejects_alias_not_present_in_audited_provenance():
    current = note()
    current["fields"]["SourceID"]["value"] = "A1-LEGACY"
    current["fields"]["SourceRefs"]["value"] = "A1-X|A1-LEGACY"
    current["fields"]["LegacyGUID"]["value"] = "goethe:A1-X"
    row = export.serialize_note(current, cards())
    entry = {
        "source_id": "A1-X", "stable_guid": "goethe:A1-X",
        "source_refs": ["A1-X"], "lemma": row["lemma"], "cefr": row["cefr"],
        "desired_meaning_en": row["meaning_en"],
        "desired_examples": [{"de": item["de"], "en": item["en"]} for item in row["examples"]],
    }
    with pytest.raises(export.ExportError, match="stable identity drift"):
        export.validate_audited_content([row], {entry["source_id"]: entry})


def test_validate_rows_enforces_per_level_counts_and_canonical_order(monkeypatch):
    rows = []
    for offset, level in enumerate(export.scope.LEVELS):
        current_note = copy.deepcopy(note())
        note_id = 10 + offset
        current_note["noteId"] = note_id
        current_note["fields"]["CEFR"]["value"] = level
        current_note["fields"]["SourceID"]["value"] = f"{level}-X"
        current_note["fields"]["SourceRefs"]["value"] = f"{level}-X"
        current_cards = [
            {
                "cardId": 20 + offset * 2 + ord_,
                "note": note_id,
                "ord": ord_,
                "deckName": export.scope.LEVEL_DECK[level],
            }
            for ord_ in (0, 1)
        ]
        rows.append(export.serialize_note(current_note, current_cards))

    monkeypatch.setattr(export, "EXPECTED_NOTES", 3)
    monkeypatch.setattr(export, "EXPECTED_CARDS", 6)
    monkeypatch.setattr(export.scope, "EXPECTED_NOTES_BY_LEVEL", {
        "A1": 1, "A2": 1, "B1": 1,
    })
    monkeypatch.setattr(export.scope, "EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL", {
        "A1": 1, "A2": 1, "B1": 1,
    })
    monkeypatch.setattr(export.scope, "EXPECTED_EMPTY_NOTES_BY_LEVEL", {
        "A1": 0, "A2": 0, "B1": 0,
    })
    export.validate_rows(rows)
    with pytest.raises(export.ExportError, match="canonical A1-A2-B1 order"):
        export.validate_rows([rows[1], rows[0], rows[2]])

    pending = copy.deepcopy(rows)
    pending[2]["tags"] = [export.scope.ENGLISH_REVIEW_TAG]
    with pytest.raises(export.ExportError, match="English audit v4 is not applied"):
        export.validate_rows(pending)


def test_validate_rows_rejects_duplicate_canonical_source_ids(monkeypatch):
    rows = []
    for offset, level in enumerate(export.scope.LEVELS):
        current_note = copy.deepcopy(note())
        note_id = 100 + offset
        current_note["noteId"] = note_id
        current_note["fields"]["CEFR"]["value"] = level
        current_note["fields"]["SourceID"]["value"] = f"{level}-X"
        current_note["fields"]["SourceRefs"]["value"] = f"{level}-X"
        current_cards = [
            {"cardId": 200 + offset * 2 + ord_, "note": note_id, "ord": ord_,
             "deckName": export.scope.LEVEL_DECK[level]}
            for ord_ in (0, 1)
        ]
        rows.append(export.serialize_note(current_note, current_cards))
    monkeypatch.setattr(export, "EXPECTED_NOTES", 3)
    monkeypatch.setattr(export, "EXPECTED_CARDS", 6)
    monkeypatch.setattr(export.scope, "EXPECTED_NOTES_BY_LEVEL", {"A1": 1, "A2": 1, "B1": 1})
    monkeypatch.setattr(export.scope, "EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL", {"A1": 1, "A2": 1, "B1": 1})
    monkeypatch.setattr(export.scope, "EXPECTED_EMPTY_NOTES_BY_LEVEL", {"A1": 0, "A2": 0, "B1": 0})
    rows[1]["source_id"] = rows[0]["source_id"]
    with pytest.raises(export.ExportError, match="duplicate canonical source ID"):
        export.validate_rows(rows)
