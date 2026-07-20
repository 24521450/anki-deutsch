from __future__ import annotations

import argparse
import base64
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_example_audio as audio  # noqa: E402
import goethe_examples  # noqa: E402


def test_spoken_text_normalizes_only_tts_punctuation():
    assert audio.spoken_text("  –  Woher kommen Sie? / Aus Frankreich. ") == "Woher kommen Sie? — Aus Frankreich."
    assert audio.spoken_text("Das kostet 10 Euro (inklusive).") == "Das kostet 10 Euro (inklusive)."


def test_spoken_text_converts_html_dialogue_break_to_a_pause():
    assert audio.spoken_text(
        "Willst du diese Jacke?<br>– Nein, ich möchte die andere."
    ) == "Willst du diese Jacke? Nein, ich möchte die andere."


def test_voice_and_request_id_are_deterministic():
    text = "Guten Morgen."
    voice = audio.voice_for(text)
    assert voice in audio.EDGE_CONFIG["voices"]
    assert audio.voice_for(text) == voice
    assert audio.request_id(text, voice) == audio.request_id(text, voice)


def test_expected_audio_fields_covers_overflow():
    fields: dict[str, str] = {}
    rows = [{"de": f"Satz {i}", "en": f"Sentence {i}", "audio": "old"} for i in range(1, 6)]
    goethe_examples.render_fields(fields, rows)
    occurrences = []
    unique = {}
    for index, row in enumerate(rows, 1):
        key = f"id-{index}"
        occurrences.append({"index": index, "de": row["de"], "en": row["en"], "audio_id": key})
        unique[key] = {"media_name": f"edge-{index}.mp3"}
    manifest = {"notes": {"1": {"occurrences": occurrences}}, "unique": unique}
    rendered = audio.expected_audio_fields(1, manifest, fields)
    assert "edge-4.mp3" in rendered["Example4Audio"]
    assert goethe_examples.parse_overflow(rendered["MoreExamplesHTML"])[0]["audio"].endswith('edge-5.mp3"></audio>')


def test_media_name_is_content_hash_scoped():
    item = {"sha256": "a" * 64}
    assert f"_goethe_example_edge_{item['sha256']}.mp3" == "_goethe_example_edge_" + "a" * 64 + ".mp3"


def record(note_id: int, level: str, sentence: str) -> dict:
    fields = {"CEFR": level}
    goethe_examples.render_fields(fields, [{"de": sentence, "en": sentence, "audio": ""}])
    return {
        "model": audio.MODEL, "fields": fields, "tags": [],
        "cards": [{"cardId": note_id * 2}, {"cardId": note_id * 2 + 1}],
    }


def test_manifest_deduplicates_across_levels_and_pilot_covers_all_levels(monkeypatch):
    monkeypatch.setattr(audio, "EXPECTED_NOTES", 3)
    monkeypatch.setattr(audio, "EXPECTED_CARDS", 6)
    monkeypatch.setattr(audio, "EXPECTED_OCCURRENCES", 3)
    monkeypatch.setattr(audio, "EXPECTED_UNIQUE", 2)
    monkeypatch.setattr(audio, "EXPECTED_NOTES_BY_LEVEL", {"A1": 1, "A2": 1, "B1": 1})
    monkeypatch.setattr(audio, "EXPECTED_CARDS_BY_LEVEL", {"A1": 2, "A2": 2, "B1": 2})
    monkeypatch.setattr(audio, "EXPECTED_OCCURRENCES_BY_LEVEL", {"A1": 1, "A2": 1, "B1": 1})
    manifest = audio.build_manifest({
        1: record(1, "A1", "Guten Tag."),
        2: record(2, "A2", "Auf Wiedersehen."),
        3: record(3, "B1", "Guten Tag."),
    })
    shared = next(item for item in manifest["unique"].values() if item["spoken_text"] == "Guten Tag.")
    assert shared["levels"] == ["A1", "B1"]
    assert shared["occurrences"] == 2
    assert {manifest["notes"][str(note_id)]["level"] for note_id in manifest["pilot_note_ids"]} == {"A1", "A2", "B1"}


def test_example_manifest_rejects_pre_b1_schema():
    try:
        audio.validate_manifest({"schema_version": 1})
    except audio.ExampleAudioError as exc:
        assert "schema is stale" in str(exc)
    else:
        raise AssertionError("stale manifest was accepted")


def test_ensure_media_verifies_hash_after_store(monkeypatch, tmp_path):
    payload = b"test mp3 payload"
    path = tmp_path / "audio.mp3"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    item = {"path": str(path), "size": len(payload), "sha256": digest, "media_name": "example.mp3"}
    calls = []

    def fake_anki(action, **params):
        calls.append(action)
        if action == "retrieveMediaFile":
            return "" if calls.count("retrieveMediaFile") == 1 else base64.b64encode(payload).decode("ascii")
        if action == "storeMediaFile":
            return "example.mp3"
        raise AssertionError(action)

    monkeypatch.setattr(audio.word_audio, "validate_audio", lambda *args: (len(payload), digest))
    monkeypatch.setattr(audio.gw, "anki", fake_anki)
    audio.ensure_media(item)
    assert calls == ["retrieveMediaFile", "storeMediaFile", "retrieveMediaFile"]


def test_apply_dry_run_never_stores_media_or_updates_notes(monkeypatch):
    manifest = {"notes": {"1": {"occurrences": []}}, "pilot_note_ids": [1]}
    fields = {name: "old" for name in audio.AUDIO_FIELDS}
    records = {1: {"fields": fields}}
    snapshot = {"notes": {"1": {"fields": fields}}}
    monkeypatch.setattr(audio, "load_ready", lambda: (manifest, snapshot))
    monkeypatch.setattr(audio, "live_records", lambda: records)
    monkeypatch.setattr(audio, "verify_baseline", lambda *args: None)
    monkeypatch.setattr(audio, "expected_audio_fields", lambda *args: {name: "new" for name in audio.AUDIO_FIELDS})
    monkeypatch.setattr(audio, "ensure_media", lambda *args: (_ for _ in ()).throw(AssertionError("stored media")))
    monkeypatch.setattr(audio, "update_notes", lambda *args: (_ for _ in ()).throw(AssertionError("updated note")))
    audio.command_apply(argparse.Namespace(scope="pilot", dry_run=True, confirmation=None))
