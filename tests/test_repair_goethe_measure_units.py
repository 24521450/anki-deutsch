from __future__ import annotations

import argparse
import asyncio
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import repair_goethe_measure_units as repair  # noqa: E402


def completion_manifest():
    records = {}
    for note_id, (_old, new, current_ref, merged_ref) in repair.UPDATE_TARGETS.items():
        fields = {name: "" for name in repair.gw.FIELDS}
        fields.update({"Lemma": new, "SourceRefs": "|".join(dict.fromkeys((current_ref, merged_ref)))})
        records[str(note_id)] = {"note_id": note_id, "is_new": False, "fields": fields}
    deletions = [
        {"note_id": note_id, "survivor": survivor}
        for note_id, survivor in repair.DELETE_TO_SURVIVOR.items()
    ]
    return {"records": records, "deletions": deletions}


def baseline_state():
    notes = {}
    cards = {}
    next_card = 1
    for note_id, (old, _new, current_ref, _merged_ref) in repair.UPDATE_TARGETS.items():
        word_audio = "[sound:_goethe_word_edge_old.mp3]"
        if note_id == 1584887177160:
            word_audio = "[sound:_goethe_word_wiktionary_eins.mp3]"
        card_ids = [next_card, next_card + 1]
        next_card += 2
        notes[note_id] = {
            "model": repair.MODEL, "tags": [], "card_ids": card_ids,
            "fields": {"Lemma": old, "SourceRefs": current_ref, "CEFR": "A1", "WordAudio": word_audio},
        }
        for card_id in card_ids:
            cards[card_id] = {"cardId": card_id, "note": note_id, "reps": 0}
    for note_id, survivor in repair.DELETE_TO_SURVIVOR.items():
        card_ids = [next_card, next_card + 1]
        next_card += 2
        notes[note_id] = {
            "model": repair.MODEL, "tags": [], "card_ids": card_ids,
            "fields": {
                "Lemma": "duplicate", "SourceRefs": repair.MEASURE_SURVIVORS[survivor][3],
                "CEFR": "B1", "WordAudio": "[sound:_goethe_word_edge_duplicate.mp3]",
            },
        }
        for card_id in card_ids:
            cards[card_id] = {"cardId": card_id, "note": note_id, "reps": 0}
    return {"notes": notes, "cards": cards}


def test_completion_manifest_requires_exact_survivors_and_delete_routes():
    desired = repair.desired_from_completion(completion_manifest())

    assert set(desired) == set(repair.UPDATE_TARGETS)
    assert desired[1584887177258]["Lemma"] == "Gramm"
    assert "B1-WG-0255" in repair.split_refs(desired[1584887177160]["SourceRefs"])
    assert desired[1784075690361]["Lemma"] == "1 km/h"


def test_completion_manifest_fails_closed_when_provenance_is_missing():
    manifest = completion_manifest()
    manifest["records"]["1584887177258"]["fields"]["SourceRefs"] = "A1-WG-0102"

    with pytest.raises(repair.RepairError, match="completion survivor fields not ready"):
        repair.desired_from_completion(manifest)


def test_completion_manifest_rejects_unexpected_new_notes_or_deletions():
    manifest = completion_manifest()
    manifest["records"]["new:unexpected"] = {
        "note_id": None, "is_new": True, "fields": {},
    }
    with pytest.raises(repair.RepairError, match="unexpected new notes"):
        repair.desired_from_completion(manifest)

    manifest = completion_manifest()
    manifest["deletions"].append({"note_id": 999, "survivor": 1})
    with pytest.raises(repair.RepairError, match="deletion set differs"):
        repair.desired_from_completion(manifest)


def test_live_baseline_guards_exact_ids_edge_audio_and_zero_rep_duplicates():
    state = baseline_state()
    repair.validate_live_baseline(state)

    duplicate = next(iter(repair.DELETE_TO_SURVIVOR))
    card_id = state["notes"][duplicate]["card_ids"][0]
    state["cards"][card_id]["reps"] = 1
    with pytest.raises(repair.RepairError, match="review history"):
        repair.validate_live_baseline(state)


def test_live_baseline_requires_phrase_survivors_to_keep_edge_audio():
    state = baseline_state()
    phrase_id = next(iter(repair.PHRASE_SURVIVORS))
    state["notes"][phrase_id]["fields"]["WordAudio"] = "[sound:_goethe_word_commons_phrase.mp3]"

    with pytest.raises(repair.RepairError, match="phrase no longer has"):
        repair.validate_live_baseline(state)


def test_prepare_media_uses_only_nine_exact_commons_files_and_one_phrase_edge(monkeypatch, tmp_path: Path):
    desired = repair.desired_from_completion(completion_manifest())
    commons_groups = {}
    edge_groups = {}

    async def fake_commons(groups, _duden):
        commons_groups.update(groups)
        items = {}
        for key, group in groups.items():
            lemma = group["spoken_text"]
            items[key] = {
                "status": "ok", "title": f"File:De-{lemma}.ogg", "artist": "Jeuwre",
                "license_short_name": "CC BY-SA 4.0", "path": str(tmp_path / f"{lemma}.mp3"),
            }
        return {"items": items}

    async def fake_edge(groups, _duden, _commons, _wiktionary):
        edge_groups.update(groups)
        spoken = next(iter(groups.values()))["spoken_text"]
        return {"items": {repair.audio.edge_audio_id(spoken): {
            "status": "ok", "spoken_text": spoken, "path": str(tmp_path / "phrase.mp3"),
        }}}

    def fake_assignment(source, path, *, detail):
        return {"source": source, "path": str(path), "media_name": f"{source}-{path.name}", "sha256": "abc", "size": 1}

    monkeypatch.setattr(repair.audio, "prepare_commons", fake_commons)
    monkeypatch.setattr(repair.audio, "prepare_edge", fake_edge)
    monkeypatch.setattr(repair.audio, "assignment", fake_assignment)

    assignments = asyncio.run(repair.prepare_media(desired))

    assert len(commons_groups) == 9
    assert {group["spoken_text"] for group in commons_groups.values()} == set(repair.COMMONS_TARGETS.values())
    assert {item["source"] for note_id, item in assignments.items() if note_id in repair.COMMONS_TARGETS} == {"commons"}
    assert [group["spoken_text"] for group in edge_groups.values()] == ["ein Kilometer pro Stunde"]
    assert set(assignments) == set(repair.COMMONS_TARGETS) | set(repair.EDGE_TARGETS)


def test_validate_backup_requires_a_scheduled_anki_collection(tmp_path: Path):
    valid = tmp_path / "valid.apkg"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("collection.anki21", b"anki")
        archive.writestr("media", b"{}")
    assert repair.validate_backup(valid) == repair.gw.sha256_file(valid)

    invalid = tmp_path / "invalid.apkg"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr("media", b"{}")
    with pytest.raises(repair.RepairError, match="valid Anki package"):
        repair.validate_backup(invalid)


def test_scheduled_backup_uses_an_extended_http_timeout(monkeypatch, tmp_path: Path):
    calls = []

    def fake_anki(action, **params):
        calls.append((action, params))
        return True

    monkeypatch.setattr(repair.gw, "anki", fake_anki)
    backup = tmp_path / "backup.apkg"

    assert repair.export_scheduled_backup(backup) is True
    assert calls == [("exportPackage", {
        "deck": repair.PARENT_DECK,
        "path": backup.resolve().as_posix(),
        "includeSched": True,
        "request_timeout": repair.EXPORT_TIMEOUT_SECONDS,
    })]


def test_post_apply_snapshot_load_does_not_depend_on_rebuilt_completion_manifest(monkeypatch):
    snapshot = {"schema_version": 1, "backup": "backup.apkg"}
    monkeypatch.setattr(repair.audio, "load_json", lambda path, default: snapshot)
    monkeypatch.setattr(
        repair.gw, "sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError("manifest hash must not be read")),
    )
    monkeypatch.setattr(repair, "validate_backup", lambda path, expected=None: "ok")

    assert repair.load_snapshot(require_manifest_unchanged=False) is snapshot


def test_apply_is_confirmation_gated_before_any_live_action(monkeypatch):
    monkeypatch.setattr(repair, "load_snapshot", lambda: (_ for _ in ()).throw(AssertionError("must not load")))

    with pytest.raises(repair.RepairError, match=repair.CONFIRMATION):
        repair.command_apply(argparse.Namespace(confirmation="wrong"))


def test_apply_installs_media_updates_fields_then_deletes_exact_notes_last(monkeypatch):
    events = []
    snapshot = {"assignments": {"1": {"media_name": "human.mp3"}}}

    monkeypatch.setattr(repair, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(repair, "require_anki", lambda: events.append("guard"))
    monkeypatch.setattr(repair, "collect_model_state", lambda: {})
    monkeypatch.setattr(repair, "verify_baseline_since_audit", lambda state, snap: events.append("baseline"))
    monkeypatch.setattr(repair.audio, "ensure_media", lambda item: events.append("media"))
    monkeypatch.setattr(repair, "update_fields", lambda snap: events.append("update"))
    monkeypatch.setattr(repair, "target_fields_are_ready", lambda state, snap: events.append("ready"))
    monkeypatch.setattr(repair, "verify_state", lambda snap: events.append("verify") or {"note_delta": -12, "card_delta": -24})

    def fake_anki(action, **params):
        assert action == "deleteNotes"
        assert params["notes"] == sorted(repair.DELETE_TO_SURVIVOR)
        events.append("delete")

    monkeypatch.setattr(repair.gw, "anki", fake_anki)

    repair.command_apply(argparse.Namespace(confirmation=repair.CONFIRMATION))

    assert events == ["guard", "baseline", "media", "update", "ready", "delete", "verify"]


def test_apply_rolls_back_survivor_fields_if_pre_delete_verification_fails(monkeypatch):
    events = []
    snapshot = {"assignments": {}}

    monkeypatch.setattr(repair, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(repair, "require_anki", lambda: None)
    monkeypatch.setattr(repair, "collect_model_state", lambda: {})
    monkeypatch.setattr(repair, "verify_baseline_since_audit", lambda state, snap: None)
    monkeypatch.setattr(repair, "update_fields", lambda snap: events.append("update"))
    monkeypatch.setattr(
        repair, "target_fields_are_ready",
        lambda state, snap: (_ for _ in ()).throw(repair.RepairError("verification failed")),
    )
    monkeypatch.setattr(repair, "restore_fields", lambda snap: events.append("restore"))
    monkeypatch.setattr(
        repair.gw, "anki",
        lambda action, **params: (_ for _ in ()).throw(AssertionError("must not delete")),
    )

    with pytest.raises(repair.RepairError, match="verification failed"):
        repair.command_apply(argparse.Namespace(confirmation=repair.CONFIRMATION))

    assert events == ["update", "restore"]
