from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_example_cleanup as cleanup  # noqa: E402
import goethe_examples  # noqa: E402
import goethe_source_examples as source_examples  # noqa: E402


def fields(level: str, rows: list[dict[str, str]]) -> dict[str, str]:
    result = {"CEFR": level}
    goethe_examples.render_fields(result, rows)
    return result


def test_reviewed_overrides_define_canonical_level_whitelists():
    allowed = source_examples.allowed_examples_by_level()
    assert len(allowed["A1"]) == 887
    assert len(allowed["A2"]) == 1835
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

    assert goethe_example_audio.EXPECTED_OCCURRENCES == 1868
    assert goethe_example_audio.EXPECTED_UNIQUE == 1780


def test_exported_examples_obey_the_level_source_policy():
    allowed = source_examples.allowed_examples_by_level()
    rows = [
        json.loads(line)
        for line in (ROOT / "data" / "build" / "anki_notes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    examples = [(row["cefr"], item["de"]) for row in rows for item in row["examples"]]
    assert len(rows) == cleanup.EXPECTED_NOTES
    assert len(examples) == cleanup.EXPECTED_REMAINING
    assert all(source_examples.sentence_key(sentence) in allowed[level] for level, sentence in examples)
