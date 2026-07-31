from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

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


def test_voice_assignment_and_request_id_are_deterministic():
    text = "Guten Morgen."
    voice = audio.voice_for(123, 0)
    assert audio.GEMINI_VOICES == ("Kore", "Charon")
    assert audio.voice_for("123", 0) == voice
    assert audio.voice_for(123, 1) != voice
    assert audio.voice_for(123, 2) == voice
    assert audio.request_id(text, voice) == audio.request_id(text, voice)
    assert audio.request_id(text, voice) == audio.canonical_hash({
        "spoken_text": text,
        "voice": voice,
        "config": audio.GEMINI_CONFIG,
    })


def test_pommes_frites_override_changes_only_matching_request_ids():
    voice = "Kore"
    affected = "Für Pommes frites braucht man Kartoffeln."
    unaffected = "Eine Wurst mit Pommes, bitte."
    legacy_affected = audio.canonical_hash({
        "spoken_text": affected,
        "voice": voice,
        "config": audio.GEMINI_CONFIG,
    })
    legacy_unaffected = audio.canonical_hash({
        "spoken_text": unaffected,
        "voice": voice,
        "config": audio.GEMINI_CONFIG,
    })

    assert audio.request_id(affected, voice) != legacy_affected
    assert audio.request_id(affected, voice) == audio.canonical_hash({
        "spoken_text": affected,
        "voice": voice,
        "config": audio.GEMINI_CONFIG,
        "pronunciation_override": {
            "phrase": "Pommes frites",
            "version": 1,
        },
    })
    assert audio.request_id(unaffected, voice) == legacy_unaffected


def test_reviewed_pronunciation_audio_is_content_addressed_and_voice_scoped():
    text = "Für Pommes frites braucht man Kartoffeln."
    reviewed = audio.reviewed_pronunciation_audio(text, "Charon")

    assert reviewed is not None
    assert reviewed["generation_text"] == (
        "Für Pomm fritt braucht man Kartoffeln."
    )
    assert reviewed["model"] == "gemini-3.1-flash-tts-preview"
    assert reviewed["sha256"] == (
        "5efa0da14e280ea761d3f5571148dc1c812b7f8cb3d65d5973154b717b79ffbb"
    )
    assert audio.reviewed_pronunciation_audio(text, "Kore") is None
    assert audio.request_id(text, "Charon") == audio.canonical_hash({
        "spoken_text": text,
        "voice": "Charon",
        "config": audio.GEMINI_CONFIG,
        "pronunciation_override": {
            "phrase": "Pommes frites",
            "version": 1,
        },
        "reviewed_pronunciation_audio": {
            "version": 1,
            "engine": "gemini-native-tts",
            "model": "gemini-3.1-flash-tts-preview",
            "prompt_version": 3,
            "sha256": reviewed["sha256"],
        },
    })


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
    assert audio.media_name_for(item["sha256"]) == (
        "_goethe_example_gemini_" + "a" * 64 + ".mp3"
    )
    assert audio.GEMINI_CONFIG["voices"] == ["Kore", "Charon"]
    assert audio.EXPECTED_UNIQUE == 4992
    assert all(
        audio.GEMINI_CONFIG[key] == value
        for key, value in audio.gemini_tts.CONFIG.items()
    )


def test_audio_field_equivalence_accepts_anki_boolean_serialisation_only():
    canonical = (
        '<audio class="gw-example-player" controls preload="none" '
        'src="example.mp3"></audio>'
    )
    anki = canonical.replace(" controls ", ' controls="" ')
    assert audio.audio_field_equivalent(anki, canonical)
    assert not audio.audio_field_equivalent(
        anki.replace("example.mp3", "other.mp3"), canonical
    )


def record_examples(note_id: int, level: str, sentences: list[str]) -> dict:
    fields = {"CEFR": level}
    goethe_examples.render_fields(fields, [
        {"de": sentence, "en": sentence, "audio": ""}
        for sentence in sentences
    ])
    return {
        "model": audio.MODEL, "fields": fields, "tags": [],
        "cards": [{"cardId": note_id * 2}, {"cardId": note_id * 2 + 1}],
    }


def record(note_id: int, level: str, sentence: str) -> dict:
    return record_examples(note_id, level, [sentence])


def patch_baseline(monkeypatch, records: dict[int, dict], unique: int) -> None:
    notes_by_level = {
        level: sum(item["fields"]["CEFR"] == level for item in records.values())
        for level in audio.scope.LEVELS
    }
    cards_by_level = {
        level: sum(
            len(item["cards"])
            for item in records.values()
            if item["fields"]["CEFR"] == level
        )
        for level in audio.scope.LEVELS
    }
    occurrences_by_level = {
        level: sum(
            len(goethe_examples.parse_fields(item["fields"]))
            for item in records.values()
            if item["fields"]["CEFR"] == level
        )
        for level in audio.scope.LEVELS
    }
    monkeypatch.setattr(audio, "EXPECTED_NOTES", len(records))
    monkeypatch.setattr(
        audio, "EXPECTED_CARDS", sum(len(item["cards"]) for item in records.values())
    )
    monkeypatch.setattr(
        audio, "EXPECTED_OCCURRENCES", sum(occurrences_by_level.values())
    )
    monkeypatch.setattr(audio, "EXPECTED_UNIQUE", unique)
    monkeypatch.setattr(audio, "EXPECTED_NOTES_BY_LEVEL", notes_by_level)
    monkeypatch.setattr(audio, "EXPECTED_CARDS_BY_LEVEL", cards_by_level)
    monkeypatch.setattr(
        audio, "EXPECTED_OCCURRENCES_BY_LEVEL", occurrences_by_level
    )


def test_manifest_deduplicates_across_levels_and_pilot_covers_all_levels(monkeypatch):
    records = {
        1: record(1, "A1", "Guten Tag."),
        2: record(2, "A2", "Auf Wiedersehen."),
        4: record(4, "B1", "Guten Tag."),
    }
    assert audio.voice_for(1, 0) == audio.voice_for(4, 0)
    patch_baseline(monkeypatch, records, unique=2)
    manifest = audio.build_manifest(records)
    shared = next(item for item in manifest["unique"].values() if item["spoken_text"] == "Guten Tag.")
    assert shared["levels"] == ["A1", "B1"]
    assert shared["occurrences"] == 2
    assert {manifest["notes"][str(note_id)]["level"] for note_id in manifest["pilot_note_ids"]} == {"A1", "A2", "B1"}


def test_multi_example_note_always_uses_both_voices(monkeypatch):
    records = {
        1: record_examples(1, "A1", ["Satz eins.", "Satz zwei.", "Satz drei."]),
        2: record(2, "A2", "Satz vier."),
        3: record(3, "B1", "Satz fünf."),
    }
    patch_baseline(monkeypatch, records, unique=5)
    manifest = audio.build_manifest(records)
    occurrences = manifest["notes"]["1"]["occurrences"]
    assert [item["voice"] for item in occurrences] == [
        audio.voice_for(1, index) for index in range(3)
    ]
    assert {item["voice"] for item in occurrences} == {"Kore", "Charon"}


def test_same_text_with_different_voice_is_not_deduplicated(monkeypatch):
    records = {
        1: record(1, "A1", "Guten Tag."),
        2: record(2, "A2", "Guten Tag."),
        3: record(3, "B1", "Ein anderer Satz."),
    }
    assert audio.voice_for(1, 0) != audio.voice_for(2, 0)
    patch_baseline(monkeypatch, records, unique=3)
    manifest = audio.build_manifest(records)
    matching = [
        item for item in manifest["unique"].values()
        if item["spoken_text"] == "Guten Tag."
    ]
    assert {item["voice"] for item in matching} == {"Kore", "Charon"}


def test_manifest_reuses_unaffected_cache_but_rejects_legacy_override_id(
    monkeypatch,
):
    affected = "Wir essen Pommes frites."
    records = {
        1: record(1, "A1", affected),
        2: record(2, "A2", "Eine Wurst mit Pommes, bitte."),
        3: record(3, "B1", "Guten Tag."),
    }
    patch_baseline(monkeypatch, records, unique=3)
    current = audio.build_manifest(records)
    affected_voice = audio.voice_for(1, 0)
    legacy_affected_id = audio.canonical_hash({
        "spoken_text": affected,
        "voice": affected_voice,
        "config": audio.GEMINI_CONFIG,
    })
    previous_unique = {
        legacy_affected_id: {"status": "ok"},
    }
    for item in current["unique"].values():
        if item["spoken_text"] != affected:
            previous_unique[item["audio_id"]] = {"status": "ok"}
    previous = {
        "schema_version": audio.MANIFEST_SCHEMA_VERSION,
        "levels": list(audio.scope.LEVELS),
        "config": audio.GEMINI_CONFIG,
        "unique": previous_unique,
    }

    rebuilt = audio.build_manifest(records, previous)
    statuses = {
        item["spoken_text"]: item["status"]
        for item in rebuilt["unique"].values()
    }
    assert statuses[affected] == "pending"
    assert statuses["Eine Wurst mit Pommes, bitte."] == "ok"
    assert statuses["Guten Tag."] == "ok"


def test_example_manifest_rejects_stale_schema():
    try:
        audio.validate_manifest({"schema_version": audio.MANIFEST_SCHEMA_VERSION - 1})
    except audio.ExampleAudioError as exc:
        assert "schema is stale" in str(exc)
    else:
        raise AssertionError("stale manifest was accepted")


def test_cache_requires_passing_qa_metadata(monkeypatch, tmp_path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"mp3")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        audio.word_audio,
        "validate_audio",
        lambda *args: (path.stat().st_size, digest),
    )
    item = {
        "status": "ok",
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": digest,
        "media_name": audio.media_name_for(digest),
        "voice": "Kore",
        "duration_seconds": 1.5,
        "qa_status": "exact",
        "asr_transcript": "Guten Tag.",
    }
    assert audio.validate_cached(item)
    assert not audio.validate_cached({**item, "qa_status": "mismatch"})
    assert not audio.validate_cached({**item, "asr_transcript": ""})
    assert not audio.validate_cached({**item, "duration_seconds": 0})


def test_generate_one_uses_verified_gemini_adapter(monkeypatch, tmp_path):
    calls = []
    payload = b"generated mp3"
    digest = hashlib.sha256(payload).hexdigest()

    async def fake_generate(text, voice, purpose, target):
        calls.append((text, voice, purpose, target))
        target.write_bytes(payload)
        return {
            "status": "ok",
            "path": str(target),
            "size": len(payload),
            "sha256": digest,
            "duration_seconds": 1.25,
            "qa_status": "verified_equivalent",
            "asr_transcript": text,
            "voice": voice,
            "created_utc": "2026-07-29T00:00:00+00:00",
        }

    monkeypatch.setattr(audio, "GEMINI_DIR", tmp_path / "gemini")
    monkeypatch.setattr(
        audio.word_audio,
        "validate_audio",
        lambda *args: (len(payload), digest),
    )
    monkeypatch.setattr(
        audio.gemini_tts,
        "generate_verified_mp3",
        fake_generate,
    )
    item = {
        "audio_id": "request-id",
        "spoken_text": "Guten Tag.",
        "voice": "Kore",
        "status": "pending",
    }
    result = asyncio.run(audio.generate_one(item, asyncio.Semaphore(1)))
    assert calls == [(
        "Guten Tag.",
        "Kore",
        "example",
        tmp_path / "gemini" / "request-id.mp3",
    )]
    assert result["media_name"] == audio.media_name_for(digest)
    assert audio.validate_cached(result)


def test_generate_scope_checkpoints_successes_before_stopping_on_failure(
    monkeypatch,
):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    manifest = {
        "pilot_audio_ids": ["a", "b", "c"],
        "unique": {
            "a": {"audio_id": "a", "status": "pending"},
            "b": {"audio_id": "b", "status": "pending"},
            "c": {"audio_id": "c", "status": "pending"},
        },
    }
    checkpoints = []

    async def fake_generate(item, semaphore):
        if item["audio_id"] == "b":
            raise audio.ExampleAudioError("quota exhausted")
        return {**item, "status": "ok"}

    monkeypatch.setattr(audio, "validate_manifest", lambda value: None)
    monkeypatch.setattr(
        audio,
        "validate_cached",
        lambda item: item.get("status") == "ok",
    )
    monkeypatch.setattr(audio, "generate_one", fake_generate)
    monkeypatch.setattr(
        audio.word_audio,
        "atomic_json",
        lambda path, value: checkpoints.append(value["unique"]["a"]["status"]),
    )

    with pytest.raises(audio.ExampleAudioError, match="checkpointing 2 successful"):
        asyncio.run(audio.generate_scope(manifest, "pilot"))

    assert manifest["unique"]["a"]["status"] == "ok"
    assert manifest["unique"]["b"]["status"] == "pending"
    assert manifest["unique"]["c"]["status"] == "ok"
    assert checkpoints[-1] == "ok"


def test_generate_scope_sliding_window_does_not_wait_for_slow_batch(
    monkeypatch,
):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    manifest = {
        "pilot_audio_ids": ["a", "b", "c"],
        "unique": {
            key: {"audio_id": key, "status": "pending"}
            for key in ("a", "b", "c")
        },
    }
    release_slow = asyncio.Event()

    async def fake_generate(item, semaphore):
        async with semaphore:
            if item["audio_id"] == "b":
                await release_slow.wait()
            if item["audio_id"] == "c":
                release_slow.set()
            return {**item, "status": "ok"}

    monkeypatch.setattr(audio, "validate_manifest", lambda value: None)
    monkeypatch.setattr(
        audio,
        "validate_cached",
        lambda item: item.get("status") == "ok",
    )
    monkeypatch.setattr(audio, "generate_one", fake_generate)
    monkeypatch.setattr(audio.word_audio, "atomic_json", lambda path, value: None)

    asyncio.run(
        asyncio.wait_for(audio.generate_scope(manifest, "pilot"), timeout=0.5)
    )

    assert all(item["status"] == "ok" for item in manifest["unique"].values())


def test_checkpoint_manifest_retries_windows_replace_race(monkeypatch):
    calls = 0
    delays = []

    def fake_atomic(path, value):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError("temporarily locked")

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(audio.word_audio, "atomic_json", fake_atomic)
    monkeypatch.setattr(audio.asyncio, "sleep", fake_sleep)

    asyncio.run(audio.checkpoint_manifest({"unique": {}}))

    assert calls == 3
    assert delays == [0.05, 0.1]


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


def test_audit_counts_gemini_and_historical_edge_media(monkeypatch, capsys):
    records = {
        1: record(1, "A1", "Satz eins."),
        2: record(2, "A2", "Satz zwei."),
        3: record(3, "B1", "Satz drei."),
    }
    media = {
        1: '<audio src="_goethe_example_gemini_hash.mp3"></audio>',
        2: '<audio src="_goethe_example_edge_hash.mp3"></audio>',
    }
    for note_id, value in media.items():
        rows = goethe_examples.parse_fields(records[note_id]["fields"])
        rows[0]["audio"] = value
        goethe_examples.render_fields(records[note_id]["fields"], rows)
    patch_baseline(monkeypatch, records, unique=3)
    monkeypatch.setattr(audio, "live_records", lambda: records)
    audio.command_audit(argparse.Namespace())
    result = json.loads(capsys.readouterr().out)
    assert result["sources"]["gemini-example"] == 1
    assert result["sources"]["edge-example"] == 1
    assert result["sources"]["blank"] == 1


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


def test_example_baseline_ignores_word_audio_owned_by_word_workflow(
    monkeypatch,
):
    before_fields = {
        **{name: "example-old" for name in audio.AUDIO_FIELDS},
        "WordAudio": "[sound:word-old.mp3]",
        "Lemma": "Bahnhof",
    }
    live_fields = {
        **before_fields,
        "WordAudio": "[sound:word-new.mp3]",
    }
    records = {
        1: {
            "fields": live_fields,
            "model": {"id": 1},
            "tags": ["A1"],
        }
    }
    snapshot = {
        "notes": {
            "1": {
                "fields": before_fields,
                "model": {"id": 1},
                "tags": ["A1"],
            }
        }
    }
    monkeypatch.setattr(
        audio,
        "expected_audio_fields",
        lambda *args: {name: "example-old" for name in audio.AUDIO_FIELDS},
    )

    audio.verify_baseline(records, {"notes": {"1": {}}}, snapshot)


def test_example_verify_allows_schedule_change_only_with_appended_review():
    before = {
        key: 0
        for key in audio.gw.SCHEDULE_KEYS
    }
    before.update({"cardId": 10, "note": 20})
    after = {**before, "reps": 1}
    card = after
    snapshot = {
        "cards": {"10": before},
        "reviews": {"10": [{"id": 1}]},
    }
    reviews = {"10": [{"id": 1}, {"id": 2}]}

    assert audio.verify_review_progress([card], reviews, snapshot) == [20]


def test_example_verify_rejects_schedule_change_without_appended_review():
    before = {
        key: 0
        for key in audio.gw.SCHEDULE_KEYS
    }
    before.update({"cardId": 10, "note": 20})
    after = {**before, "reps": 1}
    card = after
    snapshot = {
        "cards": {"10": before},
        "reviews": {"10": [{"id": 1}]},
    }

    with pytest.raises(audio.ExampleAudioError, match="scheduling changed"):
        audio.verify_review_progress(
            [card],
            {"10": [{"id": 1}]},
            snapshot,
        )
