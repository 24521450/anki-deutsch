from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_example_cleanup as cleanup  # noqa: E402
import goethe_examples  # noqa: E402
import goethe_source_examples as source_examples  # noqa: E402


BOUNDARY_MERGES = {
    "A1-MAIN-0052": "Du brauchst den Schlüssel nicht. Die Wohnung ist auf.",
    "A1-MAIN-0074": "Du musst nichts machen. Das geht automatisch.",
    "A1-MAIN-0112": "Die Jacke kostet nur 10 Euro! Die ist aber billig!",
    "A1-MAIN-0144": "Wir sprechen gerade über Paul. Da kommt er ja gerade.",
    "A1-MAIN-0146": "Du kennst doch die Post. Daneben ist die Bank.",
    "A1-MAIN-0160": "Meine Tochter ist krank. Wir gehen zum Doktor.",
    "A1-MAIN-0171": "Hast du etwas zu trinken? Ich habe großen Durst.",
    "A1-MAIN-0179": "Ich nehme ein Bier. Willst du auch eins?",
    "A1-MAIN-0228": "Ich esse gern Fisch. Fleisch mag ich nicht.",
    "A1-MAIN-0231": "Ich fliege nicht gern. Deshalb fahre ich mit dem Zug.",
    "A1-MAIN-0238": "Er möchte Sie etwas fragen. Wann kommen Sie?",
    "A1-MAIN-0304": "Hallo Inge! Wie geht’s?",
    "A1-MAIN-0323": "Hilfe! Bitte helfen Sie mir!",
    "A1-MAIN-0329": "Hör mal! Was ist das?",
    "A1-MAIN-0332": "Ich habe Hunger! Wann ist das Essen fertig?",
    "A1-MAIN-0341": "Zieh dir eine Jacke an. Es ist kalt.",
    "A1-MAIN-0346": "Claudia ist 21.<br>– Was? Noch so jung?",
    "A1-MAIN-0347": "Ich habe zwei Kinder. Einen Jungen und ein Mädchen.",
    "A1-MAIN-0349": "Das Glas war teuer. Es geht sehr leicht kaputt.",
    "A1-MAIN-0357": "Wir sind neu hier. Wir möchten Sie kennenlernen.",
    "A1-MAIN-0380": "Ich bin kulturell interessiert. Ich gehe oft ins Museum.",
    "A1-MAIN-0382": "Einen Moment, bitte. Ich habe eine Kundin.",
    "A1-MAIN-0392": "Nicht so laut! Das Baby schläft.",
    "A1-MAIN-0396": "Sind Sie verheiratet?<br>– Nein. Ledig.",
    "A1-MAIN-0400": "Leider kann ich nicht kommen. Ich muss zum Arzt.",
    "A1-MAIN-0401": "Seid leise. Die anderen schlafen schon.",
    "A1-MAIN-0417": "Frau Mertens ist lustig. Sie lacht immer.",
    "A1-MAIN-0433": "Ich gehe einkaufen. Soll ich dir was mitbringen?",
    "A1-MAIN-0434": "Ich gehe ins Kino. Kommst du mit?",
    "A1-MAIN-0444": "Ich bin müde. Ich gehe schlafen.",
    "A1-MAIN-0451": "Heute gibt es Hähnchen. Das nehme ich.",
    "A1-MAIN-0455": "Hier kaufe ich nichts. Der Laden gefällt mir nicht.",
    "A1-MAIN-0458": "75 kg. Sein Gewicht ist normal.",
    "A1-MAIN-0582": "Es gibt heute keinen Bus mehr. Er fährt mit dem Taxi.",
    "A1-MAIN-0599": "Die Toilette? Die Treppe hoch und dann links.",
    "A1-MAIN-0625": "Unser Vermieter heißt Huber. Er wohnt auch hier.",
    "A1-MAIN-0633": "Vorsicht! Da kommt ein Auto.",
    "A1-MAIN-0644": "Ich muss zum Arzt. Mein Bein tut weh.",
}


def fields(level: str, rows: list[dict[str, str]]) -> dict[str, str]:
    result = {"CEFR": level}
    goethe_examples.render_fields(result, rows)
    return result


def test_reviewed_overrides_define_canonical_level_whitelists():
    allowed = source_examples.allowed_examples_by_level()
    assert len(allowed["A1"]) == 837
    assert len(allowed["A2"]) == 1835
    assert len(allowed["B1"]) == 4554
    assert source_examples.sentence_key("Im Zug fahre ich immer 2. Klasse.") in allowed["A1"]
    assert source_examples.sentence_key("Im Zug fahre ich immer 2.") not in allowed["A1"]
    assert source_examples.sentence_key("Ich finde den Film schrecklich. Er macht mir Angst.") in allowed["A2"]


def test_filter_is_strictly_level_specific_and_preserves_retained_objects():
    allowed = source_examples.allowed_examples_by_level()
    keep = {"de": "Hast du die Tür abgeschlossen?", "en": "Did you lock the door?", "audio": "edge-1"}
    remove = {"de": "Darf ich Ihnen ein Stück Kuchen anbieten?", "en": "May I offer you cake?", "audio": "edge-2"}
    assert source_examples.filter_examples("A2", [remove, keep], allowed) == [keep]
    assert source_examples.filter_examples("A1", [keep], allowed) == []


def test_cleanup_compacts_slots_and_allows_zero_examples():
    allowed = {"A1": {source_examples.sentence_key("Satz 5"): "Satz 5"}, "A2": {}}
    rows = [
        {"de": f"Satz {index}", "en": f"Sentence {index}", "audio": f"audio-{index}"}
        for index in range(1, 6)
    ]
    desired, kept, removed = cleanup.desired_example_fields(fields("A1", rows), allowed)
    assert [item["de"] for item in kept] == ["Satz 5"]
    assert len(removed) == 4
    assert desired["Example1DE"] == "Satz 5"
    assert desired["Example1Audio"] == "audio-5"
    assert desired["Example2DE"] == ""
    assert desired["MoreExamplesHTML"] == ""
    empty, kept, _ = cleanup.desired_example_fields(fields("A2", rows), allowed)
    assert kept == []
    assert empty["Example1DE"] == ""


def test_abschliessen_projection_keeps_only_two_a2_source_examples():
    row = next(
        json.loads(line) for line in (ROOT / "data" / "build" / "anki_notes.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["lemma"] == "abschließen"
    )
    kept = source_examples.filter_examples("A2", row["examples"])
    assert [item["de"] for item in kept] == [
        "Hast du die Tür abgeschlossen?",
        "Ich schließe dieses Jahr mein Studium/meine Ausbildung ab.",
    ]


def test_update_notes_sends_only_partial_example_fields(monkeypatch):
    calls = []

    def fake_anki(action, **params):
        calls.append((action, params))
        return [{"result": None, "error": None}]

    monkeypatch.setattr(cleanup.gw, "anki", fake_anki)
    payload = {name: "" for name in cleanup.EXAMPLE_FIELDS}
    cleanup.update_notes({123: payload})
    assert calls[0][0] == "multi"
    note = calls[0][1]["actions"][0]["params"]["note"]
    assert note == {"id": 123, "fields": payload}
    assert len(note["fields"]) == 13


def test_example_audio_baseline_matches_cleanup_projection():
    import goethe_example_audio

    assert (
        goethe_example_audio.EXPECTED_OCCURRENCES
        == cleanup.EXPECTED_REMAINING
        == cleanup.scope.EXPECTED_EXAMPLE_OCCURRENCES
        == 5080
    )
    assert (
        goethe_example_audio.EXPECTED_UNIQUE
        == cleanup.scope.EXPECTED_UNIQUE_EXAMPLE_AUDIO
        == 4992
    )
    assert cleanup.EXPECTED_EMPTY_BY_LEVEL == cleanup.scope.EXPECTED_EMPTY_NOTES_BY_LEVEL
    assert cleanup.EXPECTED_EMPTY_BY_LEVEL["B1"] == 0


def test_exported_examples_obey_the_level_source_policy():
    allowed = cleanup.reviewed_allowed_examples()
    rows = [
        json.loads(line)
        for line in (ROOT / "data" / "build" / "anki_notes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    examples = [(row["cefr"], item["de"]) for row in rows for item in row["examples"]]
    assert len(rows) == cleanup.scope.EXPECTED_NOTES
    assert {row["cefr"] for row in rows} == set(cleanup.scope.LEVELS)
    assert len(examples) == cleanup.EXPECTED_REMAINING
    assert all(source_examples.sentence_key(sentence) in allowed[level] for level, sentence in examples)


def test_heimat_boundary_is_combined_in_the_a1_export_and_policy():
    row = next(
        json.loads(line)
        for line in (ROOT / "data" / "build" / "anki_notes.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if json.loads(line)["source_id"] == "A1-84886454802"
    )
    combined = "Ich komme aus der Schweiz. Das ist meine Heimat."
    allowed = cleanup.reviewed_allowed_examples()["A1"]

    assert row["cefr"] == "A1"
    assert row["source_refs"] == [
        "A1-84886454802",
        "A1-MAIN-0313",
        "A2-MAIN-0444",
        "B1-MAIN-1139",
    ]
    assert source_examples.sentence_key(combined) in allowed
    for excluded in ("Ich komme aus der Schweiz.", "Das ist meine Heimat."):
        assert source_examples.sentence_key(excluded) not in allowed
    assert source_examples.sentence_key(
        "Mein Heimatland ist Italien. Dort bin ich geboren."
    ) in allowed
    assert source_examples.sentence_key(
        "Ich lebe jetzt hier in Deutschland. Das ist meine neue Heimat."
    ) not in allowed
    assert [(item["de"], item["en"]) for item in row["examples"]] == [
        (combined, "I come from Switzerland. This is my home."),
        ("Mein Heimatland ist Italien. Dort bin ich geboren.",
         "My home country is Italy. I was born there."),
        ("Jetzt lebe ich in Deutschland, das ist meine neue Heimat.",
         "Now I live in Germany, this is my new home."),
    ]
    assert row["example_target_spans"] == [
        [[41, 47]],
        [[5, 11]],
        [[50, 56]],
    ]
    assert source_examples.filter_examples("A1", row["examples"], {"A1": allowed}) == row["examples"]


def test_all_38_boundary_decisions_are_synced_across_source_review_translation_and_build():
    source = {
        f"A1-MAIN-{row['row']:04d}": row
        for row in cleanup.gw.parse_markdown(cleanup.gw.SOURCE_A1)
    }
    audit_text = (
        ROOT / "review" / "goethe_example_boundary_audit.md"
    ).read_text(encoding="utf-8")
    assert "pending" not in audit_text.casefold()

    audit_entries = [
        json.loads(line)
        for line in (
                ROOT / "review" / "goethe_english_audit_v5.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    translations = json.loads(
        (ROOT / "review" / "goethe_completion_translations.json").read_text(
            encoding="utf-8"
        )
    )
    build_rows = [
        json.loads(line)
        for line in (
            ROOT / "data" / "build" / "anki_notes.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]

    assert len(BOUNDARY_MERGES) == 38
    for source_id, combined in BOUNDARY_MERGES.items():
        assert combined in source[source_id]["examples"]
        assert f"| `{source_id}` " in audit_text
        assert f"| `MERGE` | {combined} |" in audit_text

        entries = [
            entry
            for entry in audit_entries
            if source_id in entry.get("source_refs", [])
        ]
        assert len(entries) == 1
        desired = [
            item for item in entries[0]["desired_examples"]
            if item["de"] == combined
        ]
        assert len(desired) == 1
        # The source registry remains the pre-v5 translation baseline; v5 may
        # intentionally Americanize the reviewed English while preserving DE.
        assert combined in translations

        exported = [
            row for row in build_rows if source_id in row["source_refs"]
        ]
        assert len(exported) == 1
        assert [(item["de"], item["en"]) for item in exported[0]["examples"]
                if item["de"] == combined] == [
            (combined, desired[0]["en"])
        ]
