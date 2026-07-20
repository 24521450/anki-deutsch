"""Safely repair reviewed Goethe noun articles and split Neujahr/Silvester."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import goethe_completion as completion
import goethe_example_audio as example_audio
import goethe_noun_policy as noun_policy
import goethe_scope as scope
import goethe_werkstatt_migrate as gw
import goethe_word_audio as word_audio


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_noun_articles"
SNAPSHOT_PATH = STATE / "snapshot.json"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
CONFIRMATION = "REPAIR_GOETHE_NOUN_ARTICLES"
NEW_SOURCE_ID = "A2-WG-0130-NEUJAHR"
SILVESTER_NOTE_ID = 1783863836345
EXPECTED_FINAL = {"notes": scope.EXPECTED_NOTES, "cards": scope.EXPECTED_CARDS}
# This one-off repair turns one combined identity into two notes, each with
# the model's two cards.  Its audited pre-apply inventory is therefore -1/-2
# from the shared post-split corpus contract.
EXPECTED_BASELINE = {
    "notes": EXPECTED_FINAL["notes"] - 1,
    "cards": EXPECTED_FINAL["cards"] - 2,
}

# note_id: (SourceID, current lemma)
TARGETS = {
    1783863835542: ("A2-WG-0099", "Biologie"),
    1783863835572: ("A2-WG-0100", "Chemie"),
    1783863835603: ("A2-WG-0101", "Deutsch"),
    1783863835635: ("A2-WG-0102", "Englisch"),
    1783863835664: ("A2-WG-0103", "Französisch"),
    1783863835692: ("A2-WG-0104", "Geografie"),
    1783863835727: ("A2-WG-0107", "Latein"),
    1783863835757: ("A2-WG-0108", "Mathematik"),
    1783863835787: ("A2-WG-0110", "Physik"),
    1783863835819: ("A2-WG-0111", "Religion"),
    1783863835851: ("A2-WG-0112", "Sozialkunde"),
    1783863836285: ("A2-WG-0128", "Ostern"),
    1783863836316: ("A2-WG-0129", "Weihnachten"),
    SILVESTER_NOTE_ID: ("A2-WG-0130", "Neujahr/Silvester"),
}

EXACT_DUDEN_AUDIO = {
    "Neujahr": {
        "page_url": "https://www.duden.de/rechtschreibung/Neujahr",
        "audio_url": "https://cdn.duden.de/_media_/audio/ID4520841_525444858.mp3",
        "size": 38242,
        "sha256": "9d97260960ebcd5396f5f17369b091362bb248d1aaa9d86a2c38d9cb432139ba",
    },
    "Silvester": {
        "page_url": "https://www.duden.de/rechtschreibung/Silvester_Tag",
        "audio_url": "https://cdn.duden.de/_media_/audio/ID4119780_151970064.mp3",
        "size": 26957,
        "sha256": "0c6af2335e0da1e0b22758add56181a68d9877cd4f464af6d1a608678f0645ff",
    },
}


class RepairError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def note_hash(record: dict[str, Any]) -> str:
    return word_audio.canonical_hash({"fields": record["fields"], "tags": record["tags"]})


def require_anki() -> None:
    if gw.anki("version") != 6:
        raise RepairError("AnkiConnect v6 is required")
    if gw.anki("modelFieldNames", modelName=MODEL) != gw.FIELDS:
        raise RepairError("Goethe Werkstatt field schema differs from the repository")


def collect_state() -> dict[str, Any]:
    records, _ = completion.load_live()
    notes: dict[int, dict[str, Any]] = {}
    cards: dict[int, dict[str, Any]] = {}
    for key, record in records.items():
        note_id = int(key)
        note_cards = sorted(record["cards"], key=lambda item: int(item["cardId"]))
        notes[note_id] = {
            "fields": dict(record["fields"]),
            "tags": sorted(record["tags"]),
            "card_ids": [int(card["cardId"]) for card in note_cards],
        }
        for card in note_cards:
            cards[int(card["cardId"])] = word_audio.schedule_projection(card)
    return {"notes": notes, "cards": cards}


def validate_live_baseline(state: dict[str, Any]) -> None:
    notes, cards = state["notes"], state["cards"]
    if {"notes": len(notes), "cards": len(cards)} != EXPECTED_BASELINE:
        raise RepairError(f"live baseline count drift: {len(notes)}/{len(cards)}")
    if not set(TARGETS) <= set(notes):
        raise RepairError(f"target notes missing: {sorted(set(TARGETS) - set(notes))}")
    if any(note["fields"].get("SourceID") == NEW_SOURCE_ID for note in notes.values()):
        raise RepairError(f"new split note already exists: {NEW_SOURCE_ID}")

    for note_id, (source_id, lemma) in TARGETS.items():
        record = notes[note_id]
        fields = record["fields"]
        if (
            fields.get("SourceID") != source_id
            or fields.get("Lemma") != lemma
            or fields.get("POS") != "n."
            or fields.get("Article", "").strip()
            or len(record["card_ids"]) != 2
            or any(card_id not in cards for card_id in record["card_ids"])
        ):
            raise RepairError(f"live target identity drift: {note_id}")
    silvester_cards = notes[SILVESTER_NOTE_ID]["card_ids"]
    if any(int(cards[card_id].get("reps") or 0) != 0 for card_id in silvester_cards):
        raise RepairError("combined holiday note unexpectedly has review history")
    if "_goethe_word_edge_" not in notes[SILVESTER_NOTE_ID]["fields"].get("WordAudio", ""):
        raise RepairError("combined holiday note no longer has its reviewed Edge baseline")


def load_completion_manifest() -> dict[str, Any]:
    if not completion.MANIFEST.exists():
        raise RepairError("completion manifest missing; run goethe_completion.py build")
    manifest = json.loads(completion.MANIFEST.read_text(encoding="utf-8"))
    completion.validate_manifest(manifest)
    return manifest


def desired_from_completion(manifest: dict[str, Any]) -> tuple[dict[int, dict[str, str]], dict[str, Any]]:
    if manifest.get("deletions"):
        raise RepairError("completion manifest unexpectedly deletes notes")
    new_records = [record for record in manifest["records"].values() if record.get("is_new")]
    if len(new_records) != 1 or new_records[0]["fields"].get("SourceID") != NEW_SOURCE_ID:
        raise RepairError("completion manifest must contain exactly the reviewed Neujahr child")
    existing = {
        int(record["note_id"]): record
        for record in manifest["records"].values() if not record.get("is_new")
    }
    if not set(TARGETS) <= set(existing):
        raise RepairError("completion manifest is missing an exact target")
    desired: dict[int, dict[str, str]] = {}
    for note_id in TARGETS:
        fields = {name: str(existing[note_id]["fields"].get(name, "")) for name in gw.FIELDS}
        completion.validate_noun_fields(fields)
        desired[note_id] = fields
    child = new_records[0]
    child_fields = {name: str(child["fields"].get(name, "")) for name in gw.FIELDS}
    completion.validate_noun_fields(child_fields)
    if (
        desired[SILVESTER_NOTE_ID]["Lemma"] != "Silvester"
        or desired[SILVESTER_NOTE_ID]["AcceptedFullAnswersDE"] != "das Silvester|der Silvester"
        or child_fields["Lemma"] != "Neujahr"
        or child_fields["AcceptedFullAnswersDE"] != "das Neujahr"
        or child_fields["Example1DE"] != "Neujahr fällt in diesem Jahr auf einen Mittwoch."
    ):
        raise RepairError("completion split content differs from the reviewed repair")
    return desired, {
        "fields": child_fields,
        "tags": sorted(child["tags"]),
        "deck": child["deck"],
    }


def exact_audio_path(lemma: str) -> Path:
    return STATE / "duden" / f"{lemma}.mp3"


def prepare_exact_duden_audio(lemma: str) -> dict[str, Any]:
    spec = EXACT_DUDEN_AUDIO[lemma]
    target = exact_audio_path(lemma)
    target.parent.mkdir(parents=True, exist_ok=True)
    valid = False
    if target.exists():
        try:
            size, sha256 = word_audio.validate_audio(target)
            valid = size == spec["size"] and sha256 == spec["sha256"]
        except word_audio.WordAudioError:
            valid = False
    if not valid:
        request = urllib.request.Request(
            spec["audio_url"], headers={"User-Agent": "anki-deutsch/1.0 (personal dictionary study)"},
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
        if len(data) != spec["size"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise RepairError(f"reviewed Duden audio changed: {lemma}")
        target.write_bytes(data)
    size, sha256 = word_audio.validate_audio(target, spec["sha256"], spec["size"])
    detail = {**spec, "lemma": lemma, "path": str(target), "status": "ok", "size": size, "sha256": sha256}
    return word_audio.assignment("duden_exact", target, detail=detail)


async def prepare_neujahr_example_audio() -> dict[str, Any]:
    try:
        import edge_tts
    except ImportError as exc:
        raise RepairError("edge-tts is not installed") from exc
    if version("edge-tts") != example_audio.EDGE_CONFIG["engine_version"]:
        raise RepairError(f"edge-tts {example_audio.EDGE_CONFIG['engine_version']} is required")
    text = example_audio.spoken_text("Neujahr fällt in diesem Jahr auf einen Mittwoch.")
    voice = example_audio.voice_for(text)
    voices = await edge_tts.list_voices()
    if not any(item.get("ShortName") == voice and item.get("Locale") == "de-DE" for item in voices):
        raise RepairError(f"Edge voice unavailable: {voice}")
    item = {
        "audio_id": example_audio.request_id(text, voice),
        "spoken_text": text,
        "voice": voice,
        "levels": ["A2"],
        "occurrences": 1,
        "status": "pending",
    }
    return await example_audio.generate_one(item, edge_tts, asyncio.Semaphore(1))


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
                raise RepairError("APKG backup is invalid")
    except zipfile.BadZipFile as exc:
        raise RepairError("APKG backup is not a ZIP archive") from exc
    return sha256


def make_snapshot(
    state: dict[str, Any], desired: dict[int, dict[str, str]], child: dict[str, Any],
    word_assignments: dict[str, dict[str, Any]], example_assignment: dict[str, Any],
) -> dict[str, Any]:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_pre_noun_articles_{stamp}.apkg"
    exported = gw.anki(
        "exportPackage", deck=PARENT_DECK, path=backup.resolve().as_posix(),
        includeSched=True, request_timeout=180,
    )
    if not exported:
        raise RepairError("scheduled APKG backup failed")
    backup_sha256 = validate_backup(backup)

    desired = {note_id: dict(fields) for note_id, fields in desired.items()}
    desired[SILVESTER_NOTE_ID]["WordAudio"] = f"[sound:{word_assignments['Silvester']['media_name']}]"
    child = {**child, "fields": dict(child["fields"])}
    child["fields"]["WordAudio"] = f"[sound:{word_assignments['Neujahr']['media_name']}]"
    child["fields"]["Example1Audio"] = example_audio.audio_html(example_assignment["media_name"])
    card_ids = sorted(state["cards"])
    snapshot = {
        "schema_version": 1,
        "created_utc": now_utc(),
        "completion_manifest_sha256": gw.sha256_file(completion.MANIFEST),
        "backup": str(backup),
        "backup_sha256": backup_sha256,
        "baseline_counts": EXPECTED_BASELINE,
        "note_hashes": {str(note_id): note_hash(record) for note_id, record in state["notes"].items()},
        "cards": {str(card_id): value for card_id, value in state["cards"].items()},
        "reviews_sha256": word_audio.canonical_hash(word_audio.all_reviews(card_ids)),
        "model": word_audio.model_snapshot(),
        "target_baseline": {str(note_id): state["notes"][note_id] for note_id in TARGETS},
        "desired_fields": {str(note_id): fields for note_id, fields in desired.items()},
        "child": child,
        "word_assignments": word_assignments,
        "example_assignment": example_assignment,
        "new_note_id": None,
    }
    word_audio.atomic_json(SNAPSHOT_PATH, snapshot)
    return snapshot


def load_snapshot(*, require_manifest_unchanged: bool = True) -> dict[str, Any]:
    snapshot = word_audio.load_json(SNAPSHOT_PATH, None)
    if not snapshot or snapshot.get("schema_version") != 1:
        raise RepairError("repair snapshot missing; run audit")
    if require_manifest_unchanged and gw.sha256_file(completion.MANIFEST) != snapshot["completion_manifest_sha256"]:
        raise RepairError("completion manifest changed after audit")
    validate_backup(Path(snapshot["backup"]), snapshot["backup_sha256"])
    return snapshot


def verify_baseline_since_audit(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    if set(state["notes"]) != set(map(int, snapshot["note_hashes"])):
        raise RepairError("note inventory changed after audit")
    expected_cards = {int(card_id): value for card_id, value in snapshot["cards"].items()}
    if state["cards"] != expected_cards:
        raise RepairError("card inventory or scheduling changed after audit")
    hashes = {note_id: note_hash(record) for note_id, record in state["notes"].items()}
    if hashes != {int(note_id): value for note_id, value in snapshot["note_hashes"].items()}:
        raise RepairError("note fields or tags changed after audit")
    if word_audio.canonical_hash(word_audio.all_reviews(sorted(state["cards"]))) != snapshot["reviews_sha256"]:
        raise RepairError("review history changed after audit")
    if word_audio.model_snapshot() != snapshot["model"]:
        raise RepairError("model/templates/styling changed after audit")


def restore_target_fields(snapshot: dict[str, Any]) -> None:
    actions = [{
        "action": "updateNoteFields",
        "params": {"note": {"id": note_id, "fields": snapshot["target_baseline"][str(note_id)]["fields"]}},
    } for note_id in sorted(TARGETS)]
    completion.anki_multi(actions)


def store_media(snapshot: dict[str, Any]) -> None:
    for assignment in snapshot["word_assignments"].values():
        word_audio.ensure_media({"assignment": assignment})
    example_audio.ensure_media(snapshot["example_assignment"])


def verify_state(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or load_snapshot(require_manifest_unchanged=False)
    new_note_id = snapshot.get("new_note_id")
    if not new_note_id:
        raise RepairError("new Neujahr note ID is missing from the checkpoint")
    require_anki()
    state = collect_state()
    if {"notes": len(state["notes"]), "cards": len(state["cards"])} != EXPECTED_FINAL:
        raise RepairError(f"post-repair count drift: {len(state['notes'])}/{len(state['cards'])}")
    baseline_ids = set(map(int, snapshot["note_hashes"]))
    if set(state["notes"]) != baseline_ids | {int(new_note_id)}:
        raise RepairError("post-repair note inventory differs from the audited +1 delta")

    for note_id in baseline_ids:
        record = state["notes"][note_id]
        if note_id in TARGETS:
            if record["fields"] != snapshot["desired_fields"][str(note_id)]:
                raise RepairError(f"wrong repaired fields: {note_id}")
            if record["tags"] != snapshot["target_baseline"][str(note_id)]["tags"]:
                raise RepairError(f"target tags changed: {note_id}")
        elif note_hash(record) != snapshot["note_hashes"][str(note_id)]:
            raise RepairError(f"unrelated note changed: {note_id}")
    child = state["notes"][int(new_note_id)]
    if child["fields"] != snapshot["child"]["fields"] or child["tags"] != snapshot["child"]["tags"]:
        raise RepairError("new Neujahr note differs from the reviewed manifest")
    if len(child["card_ids"]) != 2:
        raise RepairError("new Neujahr note must have exactly two cards")

    baseline_cards = {int(card_id): value for card_id, value in snapshot["cards"].items()}
    if any(state["cards"].get(card_id) != value for card_id, value in baseline_cards.items()):
        raise RepairError("existing card scheduling changed")
    if word_audio.canonical_hash(word_audio.all_reviews(sorted(baseline_cards))) != snapshot["reviews_sha256"]:
        raise RepairError("existing review history changed")
    if word_audio.model_snapshot() != snapshot["model"]:
        raise RepairError("model/templates/styling changed")

    for assignment in snapshot["word_assignments"].values():
        encoded = gw.anki("retrieveMediaFile", filename=assignment["media_name"])
        if not encoded or hashlib.sha256(base64.b64decode(encoded)).hexdigest() != assignment["sha256"]:
            raise RepairError(f"missing or corrupt media: {assignment['media_name']}")
    example_item = snapshot["example_assignment"]
    encoded = gw.anki("retrieveMediaFile", filename=example_item["media_name"])
    if not encoded or hashlib.sha256(base64.b64decode(encoded)).hexdigest() != example_item["sha256"]:
        raise RepairError(f"missing or corrupt media: {example_item['media_name']}")

    articleless = []
    combined = []
    for record in state["notes"].values():
        fields = record["fields"]
        if fields.get("Lemma") == "Neujahr/Silvester":
            combined.append(fields.get("SourceID"))
        if fields.get("POS") == "n." and not fields.get("Article", "").strip():
            try:
                noun_policy.validate_noun_article(
                    source_id=fields.get("SourceID", ""), lemma=fields.get("Lemma", ""),
                    pos="n.", article="", gender=fields.get("Gender", ""),
                    require_complete_mapping=False,
                )
            except noun_policy.NounPolicyError as exc:
                raise RepairError(f"unexpected articleless noun: {fields.get('Lemma')}: {exc}") from exc
            articleless.append(fields.get("SourceID"))
    if combined or set(articleless) != set(noun_policy.ARTICLELESS_NOUN_EXCEPTIONS):
        raise RepairError(f"post-repair noun invariant failed: combined={combined}, articleless={articleless}")
    return {
        "notes": len(state["notes"]), "cards": len(state["cards"]),
        "updated": len(TARGETS), "added": 1, "new_note_id": int(new_note_id),
        "articleless_exceptions": len(articleless),
    }


def command_audit(args: argparse.Namespace) -> None:
    if not args.confirm_duden_usage:
        raise RepairError("audit requires --confirm-duden-usage")
    require_anki()
    manifest = load_completion_manifest()
    desired, child = desired_from_completion(manifest)
    state = collect_state()
    validate_live_baseline(state)
    assignments = {lemma: prepare_exact_duden_audio(lemma) for lemma in EXACT_DUDEN_AUDIO}
    example_item = asyncio.run(prepare_neujahr_example_audio())
    snapshot = make_snapshot(state, desired, child, assignments, example_item)
    print(json.dumps({
        "status": "AUDITED", "backup": snapshot["backup"], "updates": len(TARGETS),
        "adds": 1, "word_audio": {lemma: item["media_name"] for lemma, item in assignments.items()},
        "example_audio": example_item["media_name"],
    }, ensure_ascii=False, indent=2))


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != CONFIRMATION:
        raise RepairError(f"confirmation must equal {CONFIRMATION}")
    snapshot = load_snapshot()
    require_anki()
    state = collect_state()
    verify_baseline_since_audit(state, snapshot)
    store_media(snapshot)
    new_note_id: int | None = None
    try:
        completion.anki_multi([{
            "action": "updateNoteFields",
            "params": {"note": {"id": note_id, "fields": snapshot["desired_fields"][str(note_id)]}},
        } for note_id in sorted(TARGETS)])
        child = snapshot["child"]
        new_note_id = gw.anki("addNote", note={
            "deckName": child["deck"], "modelName": MODEL,
            "fields": child["fields"], "tags": child["tags"],
            "options": {"allowDuplicate": True},
        })
        if not new_note_id:
            raise RepairError("Anki failed to add the reviewed Neujahr note")
        snapshot["new_note_id"] = int(new_note_id)
        word_audio.atomic_json(SNAPSHOT_PATH, snapshot)
        result = verify_state(snapshot)
    except Exception as exc:
        rollback_errors = []
        if new_note_id:
            try:
                gw.anki("deleteNotes", notes=[int(new_note_id)])
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        try:
            restore_target_fields(snapshot)
        except Exception as rollback_exc:
            rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise RepairError(f"repair failed and rollback also failed: {rollback_errors}") from exc
        raise
    print(json.dumps({"status": "APPLIED", **result}, ensure_ascii=False, indent=2))


def command_verify(_: argparse.Namespace) -> None:
    print(json.dumps({"status": "PASS", **verify_state()}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--confirm-duden-usage", action="store_true")
    audit.set_defaults(func=command_audit)
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    sub.add_parser("verify").set_defaults(func=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (
        RepairError, completion.CompletionError, example_audio.ExampleAudioError,
        word_audio.WordAudioError, gw.MigrationError, OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
