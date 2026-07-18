"""Safely repair Goethe measurement-unit notes and their exact B1 duplicates."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_completion as completion
import goethe_word_audio as audio
import goethe_werkstatt_migrate as gw


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_measure_units"
SNAPSHOT_PATH = STATE / "snapshot.json"
CONFIRMATION = "REPAIR_GOETHE_MEASURE_UNITS"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
EXPORT_TIMEOUT_SECONDS = 180

# note_id: (current lemma, desired lemma, current A1 source ref, merged B1 ref)
MEASURE_SURVIVORS = {
    1584887177249: ("ein Meter", "Meter", "A1-WG-0093", "B1-WG-0241"),
    1584887177250: ("ein Zentimeter", "Zentimeter", "A1-WG-0094", "B1-WG-0242"),
    1584887177251: ("ein Meter fünfzehn", "ein Meter fünfzehn", "A1-WG-0095", "B1-WG-0243"),
    1584887177252: ("zweihundert Kilometer", "Kilometer", "A1-WG-0096", "B1-WG-0244"),
    1584887177253: ("ein Quadratmeter", "Quadratmeter", "A1-WG-0097", "B1-WG-0245"),
    1584887177254: ("ein Grad unter Null/minus ein Grad", "ein Grad unter Null/minus ein Grad", "A1-WG-0098", "B1-WG-0246"),
    1584887177255: ("vier Grad über Null/plus vier Grad", "vier Grad über Null/plus vier Grad", "A1-WG-0099", "B1-WG-0247"),
    1584887177256: ("ein Prozent", "Prozent", "A1-WG-0100", "B1-WG-0248"),
    1584887177257: ("ein Liter", "Liter", "A1-WG-0101", "B1-WG-0249"),
    1584887177258: ("ein Gramm", "Gramm", "A1-WG-0102", "B1-WG-0250"),
    1584887177259: ("ein Pfund", "Pfund", "A1-WG-0103", "B1-WG-0251"),
    1584887177260: ("ein Kilo(gramm)", "Kilogramm", "A1-WG-0104", "B1-WG-0252"),
}
EXTRA_UPDATES = {
    # B1-WG-0255 was incorrectly coalesced into the disposable B1-WG-0243 note.
    1584887177160: ("eins", "eins", "A1-WG-0001", "B1-WG-0255"),
    # Keep this phrase as a B1 note, but repair the truncated slash unit and audio.
    1784075690361: ("1 km", "1 km/h", "B1-WG-0254", "B1-WG-0254"),
}
UPDATE_TARGETS = {**MEASURE_SURVIVORS, **EXTRA_UPDATES}

DELETE_TO_SURVIVOR = {
    1784075689145: 1584887177249,
    1784075689238: 1584887177250,
    1784075689331: 1584887177251,
    1784075689425: 1584887177252,
    1784075689517: 1584887177253,
    1784075689611: 1584887177254,
    1784075689705: 1584887177255,
    1784075689797: 1584887177256,
    1784075689892: 1584887177257,
    1784075689985: 1584887177258,
    1784075690077: 1584887177259,
    1784075690172: 1584887177260,
}

COMMONS_TARGETS = {
    1584887177249: "Meter",
    1584887177250: "Zentimeter",
    1584887177252: "Kilometer",
    1584887177253: "Quadratmeter",
    1584887177256: "Prozent",
    1584887177257: "Liter",
    1584887177258: "Gramm",
    1584887177259: "Pfund",
    1584887177260: "Kilogramm",
}
EDGE_TARGETS = {1784075690361: "ein Kilometer pro Stunde"}
PHRASE_SURVIVORS = {1584887177251, 1584887177254, 1584887177255}


class RepairError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_refs(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").split("|") if part.strip()}


def require_anki() -> None:
    if gw.anki("version") != 6:
        raise RepairError("AnkiConnect v6 is required")
    if gw.anki("modelFieldNames", modelName=MODEL) != gw.FIELDS:
        raise RepairError("Goethe Werkstatt field schema differs from the repository")


def note_hash(record: dict[str, Any]) -> str:
    return audio.canonical_hash({
        "model": record["model"], "tags": record["tags"], "fields": record["fields"],
    })


def collect_model_state() -> dict[str, Any]:
    note_ids = sorted(map(int, gw.anki("findNotes", query=f'note:"{MODEL}"')))
    notes_info: list[dict[str, Any]] = []
    for batch in gw.chunks(note_ids):
        notes_info.extend(gw.anki("notesInfo", notes=batch))
    if {int(note["noteId"]) for note in notes_info} != set(note_ids):
        raise RepairError("Anki returned an incomplete Goethe note inventory")

    card_ids = sorted({int(card_id) for note in notes_info for card_id in note.get("cards", [])})
    cards_info: list[dict[str, Any]] = []
    for batch in gw.chunks(card_ids):
        cards_info.extend(gw.anki("cardsInfo", cards=batch))
    cards = {int(card["cardId"]): audio.schedule_projection(card) for card in cards_info}
    if set(cards) != set(card_ids):
        raise RepairError("Anki returned an incomplete Goethe card inventory")

    notes = {}
    for note in notes_info:
        note_id = int(note["noteId"])
        if note.get("modelName") != MODEL:
            raise RepairError(f"unexpected model for note {note_id}")
        note_card_ids = sorted(map(int, note.get("cards", [])))
        notes[note_id] = {
            "model": note["modelName"],
            "tags": sorted(note.get("tags", [])),
            "fields": audio.field_values(note),
            "card_ids": note_card_ids,
        }
    return {"notes": notes, "cards": cards}


def validate_live_baseline(state: dict[str, Any]) -> None:
    notes, cards = state["notes"], state["cards"]
    required = set(UPDATE_TARGETS) | set(DELETE_TO_SURVIVOR)
    if not required <= set(notes):
        raise RepairError(f"target notes missing: {sorted(required - set(notes))}")

    for note_id, (old_lemma, _new_lemma, current_ref, _merged_ref) in UPDATE_TARGETS.items():
        record = notes[note_id]
        fields = record["fields"]
        if fields.get("Lemma") != old_lemma or current_ref not in split_refs(fields.get("SourceRefs", "")):
            raise RepairError(f"live survivor drift: {note_id}")
        if len(record["card_ids"]) != 2 or any(card_id not in cards for card_id in record["card_ids"]):
            raise RepairError(f"survivor must have exactly two cards: {note_id}")
    for note_id in COMMONS_TARGETS:
        if "_goethe_word_edge_" not in notes[note_id]["fields"].get("WordAudio", ""):
            raise RepairError(f"noun no longer has the expected Edge baseline: {note_id}")
    for note_id in PHRASE_SURVIVORS:
        if "_goethe_word_edge_" not in notes[note_id]["fields"].get("WordAudio", ""):
            raise RepairError(f"phrase no longer has the expected Edge baseline: {note_id}")
    if "_goethe_word_edge_" not in notes[1784075690361]["fields"].get("WordAudio", ""):
        raise RepairError("B1-WG-0254 no longer has the expected Edge baseline")

    for note_id, survivor_id in DELETE_TO_SURVIVOR.items():
        record = notes[note_id]
        expected_ref = MEASURE_SURVIVORS[survivor_id][3]
        if record["fields"].get("CEFR") != "B1" or expected_ref not in split_refs(record["fields"].get("SourceRefs", "")):
            raise RepairError(f"duplicate note drift: {note_id}")
        if len(record["card_ids"]) != 2:
            raise RepairError(f"duplicate must have exactly two cards: {note_id}")
        if any(int(cards[card_id].get("reps") or 0) != 0 for card_id in record["card_ids"]):
            raise RepairError(f"duplicate unexpectedly has review history: {note_id}")


def load_completion_manifest() -> dict[str, Any]:
    if not completion.MANIFEST.exists():
        raise RepairError("completion manifest missing; run goethe_completion.py build")
    return json.loads(completion.MANIFEST.read_text(encoding="utf-8"))


def desired_from_completion(manifest: dict[str, Any]) -> dict[int, dict[str, str]]:
    new_records = [
        key for key, record in manifest.get("records", {}).items()
        if record.get("is_new")
    ]
    if new_records:
        raise RepairError(f"completion manifest has unexpected new notes: {new_records[:5]}")

    raw_deletions = manifest.get("deletions", [])
    deletion_ids = [int(item["note_id"]) for item in raw_deletions]
    if len(deletion_ids) != len(set(deletion_ids)) or set(deletion_ids) != set(DELETE_TO_SURVIVOR):
        raise RepairError("completion deletion set differs from the exact reviewed 12 notes")

    records = {
        int(record["note_id"]): record
        for record in manifest.get("records", {}).values()
        if not record.get("is_new")
    }
    desired: dict[int, dict[str, str]] = {}
    for note_id, (_old_lemma, new_lemma, current_ref, merged_ref) in UPDATE_TARGETS.items():
        record = records.get(note_id)
        if not record:
            raise RepairError(f"completion survivor missing: {note_id}")
        fields = record.get("fields", {})
        refs = split_refs(fields.get("SourceRefs", ""))
        if fields.get("Lemma") != new_lemma or current_ref not in refs or merged_ref not in refs:
            raise RepairError(f"completion survivor fields not ready: {note_id}")
        desired[note_id] = {name: str(fields.get(name, "")) for name in gw.FIELDS}

    deletions = {int(item["note_id"]): item for item in raw_deletions}
    for note_id, survivor_id in DELETE_TO_SURVIVOR.items():
        item = deletions.get(note_id)
        if not item or int(item.get("survivor", 0)) != survivor_id:
            raise RepairError(f"completion deletion mapping not ready: {note_id}")
    return desired


async def prepare_media(desired: dict[int, dict[str, str]]) -> dict[int, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    request_keys: dict[int, str] = {}
    for note_id, lemma in COMMONS_TARGETS.items():
        fields = desired[note_id]
        key = audio.canonical_hash({"text": lemma, "pos": fields.get("POS", ""), "gender": fields.get("Gender", "")})
        request_keys[note_id] = key
        groups[key] = {
            "request_key": key, "spoken_text": lemma, "pos": fields.get("POS", ""),
            "gender": fields.get("Gender", ""), "note_ids": [note_id], "skip_duden": True,
        }
    unresolved = {"items": {key: {"status": "unresolved"} for key in groups}}
    commons = await audio.prepare_commons(groups, unresolved)
    assignments: dict[int, dict[str, Any]] = {}
    for note_id, lemma in COMMONS_TARGETS.items():
        item = commons.get("items", {}).get(request_keys[note_id], {})
        if item.get("status") != "ok":
            raise RepairError(f"exact Commons audio unavailable for {lemma}: {item.get('reason')}")
        if item.get("title") != f"File:De-{lemma}.ogg":
            raise RepairError(f"unexpected Commons file for {lemma}: {item.get('title')}")
        if audio.clean(item.get("artist", "")).casefold() != "jeuwre" or item.get("license_short_name") != "CC BY-SA 4.0":
            raise RepairError(f"unexpected Commons attribution for {lemma}")
        assignments[note_id] = audio.assignment("commons", Path(item["path"]), detail=item)

    for note_id, spoken_text in EDGE_TARGETS.items():
        fields = desired[note_id]
        key = audio.canonical_hash({"text": spoken_text, "pos": fields.get("POS", ""), "gender": fields.get("Gender", "")})
        group = {key: {
            "request_key": key, "spoken_text": spoken_text, "pos": fields.get("POS", ""),
            "gender": fields.get("Gender", ""), "note_ids": [note_id], "skip_duden": True,
        }}
        unavailable = {"items": {key: {"status": "unresolved"}}}
        edge = await audio.prepare_edge(group, unavailable, unavailable, unavailable)
        item = edge.get("items", {}).get(audio.edge_audio_id(spoken_text), {})
        if item.get("status") != "ok" or item.get("spoken_text") != spoken_text:
            raise RepairError(f"correct Edge phrase audio unavailable for note {note_id}")
        assignments[note_id] = audio.assignment("edge", Path(item["path"]), detail=item)
    return assignments


def validate_backup(path: Path, expected_sha256: str | None = None) -> str:
    if not path.exists():
        raise RepairError(f"APKG backup missing: {path}")
    sha256 = gw.sha256_file(path)
    if expected_sha256 and sha256 != expected_sha256:
        raise RepairError("APKG backup hash changed")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if archive.testzip() is not None or not names.intersection({"collection.anki2", "collection.anki21"}):
                raise RepairError("APKG backup is not a valid Anki package")
    except zipfile.BadZipFile as exc:
        raise RepairError("APKG backup is not a ZIP archive") from exc
    return sha256


def export_scheduled_backup(path: Path) -> bool:
    return bool(gw.anki(
        "exportPackage", deck=PARENT_DECK, path=path.resolve().as_posix(),
        includeSched=True, request_timeout=EXPORT_TIMEOUT_SECONDS,
    ))


def make_snapshot(
    state: dict[str, Any], desired: dict[int, dict[str, str]], assignments: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_pre_measure_units_{stamp}.apkg"
    exported = export_scheduled_backup(backup)
    if not exported:
        raise RepairError("scheduled APKG backup failed")
    backup_sha256 = validate_backup(backup)

    final_fields = {note_id: dict(fields) for note_id, fields in desired.items()}
    for note_id in UPDATE_TARGETS:
        # Completion rebuilds may contain stale audio. Preserve live audio except
        # for the ten explicitly prepared assignments below.
        final_fields[note_id]["WordAudio"] = state["notes"][note_id]["fields"].get("WordAudio", "")
    for note_id, assignment in assignments.items():
        final_fields[note_id]["WordAudio"] = f"[sound:{assignment['media_name']}]"

    delete_card_ids = sorted(
        card_id for note_id in DELETE_TO_SURVIVOR for card_id in state["notes"][note_id]["card_ids"]
    )
    surviving_card_ids = sorted(set(state["cards"]) - set(delete_card_ids))
    snapshot = {
        "schema_version": 1,
        "created_utc": now_utc(),
        "completion_manifest": str(completion.MANIFEST),
        "completion_manifest_sha256": gw.sha256_file(completion.MANIFEST),
        "backup": str(backup),
        "backup_sha256": backup_sha256,
        "baseline_counts": {"notes": len(state["notes"]), "cards": len(state["cards"])},
        "note_hashes": {str(note_id): note_hash(record) for note_id, record in state["notes"].items()},
        "cards": {str(card_id): card for card_id, card in state["cards"].items()},
        "delete_card_ids": delete_card_ids,
        "surviving_reviews_sha256": audio.canonical_hash(audio.all_reviews(surviving_card_ids)),
        "model": audio.model_snapshot(),
        "target_baseline": {str(note_id): state["notes"][note_id] for note_id in set(UPDATE_TARGETS) | set(DELETE_TO_SURVIVOR)},
        "desired_fields": {str(note_id): fields for note_id, fields in final_fields.items()},
        "assignments": {str(note_id): item for note_id, item in assignments.items()},
    }
    audio.atomic_json(SNAPSHOT_PATH, snapshot)
    return snapshot


def load_snapshot(*, require_manifest_unchanged: bool = True) -> dict[str, Any]:
    snapshot = audio.load_json(SNAPSHOT_PATH, None)
    if not snapshot or snapshot.get("schema_version") != 1:
        raise RepairError("repair snapshot missing; run audit")
    if require_manifest_unchanged and (
        gw.sha256_file(completion.MANIFEST) != snapshot.get("completion_manifest_sha256")
    ):
        raise RepairError("completion manifest changed after audit")
    validate_backup(Path(snapshot["backup"]), snapshot.get("backup_sha256"))
    return snapshot


def verify_baseline_since_audit(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected_notes = set(map(int, snapshot["note_hashes"]))
    expected_cards = {int(card_id): value for card_id, value in snapshot["cards"].items()}
    if set(state["notes"]) != expected_notes or state["cards"] != expected_cards:
        raise RepairError("note/card inventory or scheduling changed after audit")
    hashes = {note_id: note_hash(record) for note_id, record in state["notes"].items()}
    if hashes != {int(note_id): value for note_id, value in snapshot["note_hashes"].items()}:
        raise RepairError("note fields or tags changed after audit")
    surviving = sorted(set(expected_cards) - set(map(int, snapshot["delete_card_ids"])))
    if audio.canonical_hash(audio.all_reviews(surviving)) != snapshot["surviving_reviews_sha256"]:
        raise RepairError("review history changed after audit")
    if audio.model_snapshot() != snapshot["model"]:
        raise RepairError("model/templates/styling changed after audit")


def update_fields(snapshot: dict[str, Any]) -> None:
    actions = [{
        "action": "updateNoteFields",
        "params": {"note": {"id": note_id, "fields": snapshot["desired_fields"][str(note_id)]}},
    } for note_id in sorted(UPDATE_TARGETS)]
    completion.anki_multi(actions)


def restore_fields(snapshot: dict[str, Any]) -> None:
    baseline = snapshot["target_baseline"]
    rollback = [{
        "action": "updateNoteFields",
        "params": {"note": {"id": note_id, "fields": baseline[str(note_id)]["fields"]}},
    } for note_id in sorted(UPDATE_TARGETS)]
    completion.anki_multi(rollback)


def target_fields_are_ready(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    for note_id in UPDATE_TARGETS:
        if state["notes"][note_id]["fields"] != snapshot["desired_fields"][str(note_id)]:
            raise RepairError(f"survivor update verification failed: {note_id}")
    for note_id in DELETE_TO_SURVIVOR:
        before = snapshot["target_baseline"][str(note_id)]
        if note_hash(state["notes"][note_id]) != note_hash(before):
            raise RepairError(f"duplicate changed before deletion: {note_id}")
        if state["notes"][note_id]["card_ids"] != before["card_ids"]:
            raise RepairError(f"duplicate card IDs changed before deletion: {note_id}")
        for card_id in before["card_ids"]:
            if state["cards"].get(card_id) != snapshot["cards"].get(str(card_id)):
                raise RepairError(f"duplicate scheduling changed before deletion: {note_id}")


def verify_state(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or load_snapshot(require_manifest_unchanged=False)
    require_anki()
    state = collect_model_state()
    baseline_notes = set(map(int, snapshot["note_hashes"]))
    baseline_cards = {int(card_id): value for card_id, value in snapshot["cards"].items()}
    expected_notes = baseline_notes - set(DELETE_TO_SURVIVOR)
    expected_cards = {card_id: value for card_id, value in baseline_cards.items() if card_id not in set(map(int, snapshot["delete_card_ids"]))}
    if set(state["notes"]) != expected_notes:
        raise RepairError("post-repair note inventory differs from the audited -12 delta")
    if state["cards"] != expected_cards:
        raise RepairError("post-repair card inventory/scheduling differs from the audited -24 delta")

    baseline_hashes = {int(note_id): value for note_id, value in snapshot["note_hashes"].items()}
    for note_id, record in state["notes"].items():
        if note_id in UPDATE_TARGETS:
            if record["fields"] != snapshot["desired_fields"][str(note_id)]:
                raise RepairError(f"wrong repaired fields: {note_id}")
            before = snapshot["target_baseline"][str(note_id)]
            if record["model"] != before["model"] or record["tags"] != before["tags"]:
                raise RepairError(f"target metadata changed: {note_id}")
        elif note_hash(record) != baseline_hashes[note_id]:
            raise RepairError(f"unrelated note changed: {note_id}")

    card_ids = sorted(expected_cards)
    if audio.canonical_hash(audio.all_reviews(card_ids)) != snapshot["surviving_reviews_sha256"]:
        raise RepairError("surviving review history changed")
    if audio.model_snapshot() != snapshot["model"]:
        raise RepairError("model/templates/styling changed")

    for note_id, assignment in ((int(key), value) for key, value in snapshot["assignments"].items()):
        expected = f"[sound:{assignment['media_name']}]"
        if state["notes"][note_id]["fields"].get("WordAudio") != expected:
            raise RepairError(f"wrong repaired audio field: {note_id}")
        encoded = gw.anki("retrieveMediaFile", filename=assignment["media_name"])
        if not encoded or hashlib.sha256(base64.b64decode(encoded)).hexdigest() != assignment["sha256"]:
            raise RepairError(f"missing or corrupt Anki media: {assignment['media_name']}")
    if any("_goethe_word_edge_" in state["notes"][note_id]["fields"].get("WordAudio", "") for note_id in COMMONS_TARGETS):
        raise RepairError("a noun survivor still uses Edge word audio")
    if any("_goethe_word_edge_" not in state["notes"][note_id]["fields"].get("WordAudio", "") for note_id in PHRASE_SURVIVORS | set(EDGE_TARGETS)):
        raise RepairError("a phrase unexpectedly lost its full-phrase Edge audio")

    result = {
        "notes": len(state["notes"]),
        "cards": len(state["cards"]),
        "note_delta": len(state["notes"]) - int(snapshot["baseline_counts"]["notes"]),
        "card_delta": len(state["cards"]) - int(snapshot["baseline_counts"]["cards"]),
        "updated": len(UPDATE_TARGETS),
        "deleted": len(DELETE_TO_SURVIVOR),
        "human_audio": len(COMMONS_TARGETS),
    }
    if result["note_delta"] != -12 or result["card_delta"] != -24:
        raise RepairError(f"unexpected inventory delta: {result['note_delta']}/{result['card_delta']}")
    return result


def command_audit(_: argparse.Namespace) -> None:
    require_anki()
    manifest = load_completion_manifest()
    desired = desired_from_completion(manifest)
    state = collect_model_state()
    validate_live_baseline(state)
    assignments = asyncio.run(prepare_media(desired))
    if set(assignments) != set(COMMONS_TARGETS) | set(EDGE_TARGETS):
        raise RepairError("prepared audio target set differs")
    snapshot = make_snapshot(state, desired, assignments)
    print(json.dumps({
        "status": "AUDITED", "backup": snapshot["backup"], "updates": len(UPDATE_TARGETS),
        "deletions": len(DELETE_TO_SURVIVOR), "audio": len(assignments),
    }, ensure_ascii=False, indent=2))


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != CONFIRMATION:
        raise RepairError(f"confirmation must equal {CONFIRMATION}")
    snapshot = load_snapshot()
    require_anki()
    state = collect_model_state()
    verify_baseline_since_audit(state, snapshot)
    for assignment in snapshot["assignments"].values():
        audio.ensure_media({"assignment": assignment})
    try:
        update_fields(snapshot)
        after_update = collect_model_state()
        target_fields_are_ready(after_update, snapshot)
    except Exception as exc:
        try:
            restore_fields(snapshot)
        except Exception as rollback_exc:
            raise RepairError(
                f"survivor update failed and rollback also failed: {rollback_exc}"
            ) from exc
        raise
    gw.anki("deleteNotes", notes=sorted(DELETE_TO_SURVIVOR))
    print(json.dumps({"status": "APPLIED", **verify_state(snapshot)}, indent=2))


def command_verify(_: argparse.Namespace) -> None:
    print(json.dumps({"status": "PASS", **verify_state()}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=command_audit)
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    sub.add_parser("verify").set_defaults(func=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (RepairError, completion.CompletionError, audio.WordAudioError, gw.MigrationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
