"""Safely prune Goethe examples to the reviewed source whitelist for each CEFR level."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_examples
import goethe_apkg as apkg
import goethe_scope as scope
import goethe_source_examples
import goethe_werkstatt_migrate as gw
import goethe_word_audio as word_audio


ROOT = gw.ROOT
STATE = ROOT / "tools" / ".goethe_example_cleanup"
MANIFEST_PATH = STATE / "manifest.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
MANIFEST_SCHEMA_VERSION = 2
EXPECTED_NOTES = scope.EXPECTED_NOTES
EXPECTED_CARDS = scope.EXPECTED_CARDS
EXPECTED_AFFECTED = 0
EXPECTED_REMOVED = 0
EXPECTED_REMAINING = scope.EXPECTED_EXAMPLE_OCCURRENCES
EXPECTED_EMPTY = scope.EXPECTED_EMPTY_NOTES
EXPECTED_BY_LEVEL = dict(scope.EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL)
EXPECTED_EMPTY_BY_LEVEL = dict(scope.EXPECTED_EMPTY_NOTES_BY_LEVEL)
APPLY_CONFIRMATION = "PRUNE_GOETHE_EXAMPLES_TO_LEVEL_SOURCES"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_EXAMPLE_CLEANUP"
REBASELINE_MODEL_CONFIRMATION = "REBASELINE_GOETHE_MODEL_AFTER_EXTERNAL_CHANGE"
EXAMPLE_FIELDS = tuple(
    f"Example{index}{suffix}"
    for index in range(1, 5)
    for suffix in ("DE", "EN", "Audio")
) + ("MoreExamplesHTML",)


class CleanupError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hashes() -> dict[str, str]:
    paths = {
        "A1": goethe_source_examples.SOURCE_PATHS["A1"],
        "A2": goethe_source_examples.SOURCE_PATHS["A2"],
        "B1": goethe_source_examples.SOURCE_PATHS["B1"],
        "overrides": goethe_source_examples.OVERRIDES_PATH,
    }
    audit_path = english_audit_path()
    if audit_path:
        paths["english_audit"] = audit_path
    return {name: hash_file(path) for name, path in paths.items()}


def english_audit_path() -> Path | None:
    try:
        audit = importlib.import_module("goethe_english_audit")
    except ImportError:
        return None
    # v3 is immutable migration history, never a live cleanup authority.
    candidates = [getattr(audit, "MANIFEST", None)]
    return next((Path(raw) for raw in candidates if raw and Path(raw).exists()), None)


def english_audit_entries(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict) and isinstance(data.get("entries"), dict):
        return [entry for entry in data["entries"].values() if isinstance(entry, dict)]
    if isinstance(data, dict):
        return [data]
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def live_records() -> dict[int, dict[str, Any]]:
    try:
        records = word_audio.live_records()
    except word_audio.WordAudioError as exc:
        raise CleanupError(str(exc)) from exc
    cards = sum(len(record["cards"]) for record in records.values())
    if (len(records), cards) != (EXPECTED_NOTES, EXPECTED_CARDS):
        raise CleanupError(f"expected {EXPECTED_NOTES}/{EXPECTED_CARDS}, got {len(records)}/{cards}")
    return records


def example_fields(fields: dict[str, str]) -> dict[str, str]:
    return {name: fields.get(name, "") for name in EXAMPLE_FIELDS}


def desired_example_fields(
    fields: dict[str, str], allowed: dict[str, dict[str, str]],
) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]]]:
    before = goethe_examples.parse_fields(fields)
    kept = goethe_source_examples.filter_examples(fields.get("CEFR", ""), before, allowed)
    rendered = dict(fields)
    goethe_examples.render_fields(rendered, kept)
    return example_fields(rendered), kept, [item for item in before if item not in kept]


def reviewed_allowed_examples() -> dict[str, dict[str, str]]:
    allowed = goethe_source_examples.allowed_examples_by_level()
    audit_path = english_audit_path()
    if audit_path is None:
        return allowed
    try:
        audit = importlib.import_module("goethe_english_audit")
        manifest = audit.load_json(audit_path)
        audit.validate_scaffold(manifest)
    except audit.AuditError:
        return allowed
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CleanupError(f"invalid English audit artifact: {exc}") from exc
    for entry in manifest.get("entries", {}).values():
        # A partial scaffold may contribute only rows that already passed the
        # complete evidence gate. In particular, never promote pending B1
        # desired examples into the cleanup whitelist.
        if entry.get("review_status") != "reviewed" or audit._review_entry_errors(entry):
            continue
        if entry.get("cefr") not in allowed:
            continue
        for example in entry.get("desired_examples", []):
            key = goethe_source_examples.sentence_key(example["de"])
            allowed[entry["cefr"]].setdefault(key, example["de"])
    return allowed


def record_fingerprint(record: dict[str, Any]) -> str:
    return canonical_hash({
        "model": record["model"], "fields": record["fields"], "tags": record["tags"],
        "cards": [word_audio.schedule_projection(card) for card in record["cards"]],
    })


def compile_manifest(records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    allowed = reviewed_allowed_examples()
    updates: dict[str, dict[str, str]] = {}
    removals: list[dict[str, Any]] = []
    remaining_by_level = {level: 0 for level in scope.LEVELS}
    empty_by_level = {level: 0 for level in scope.LEVELS}
    empty = 0
    for note_id, record in sorted(records.items()):
        fields = record["fields"]
        desired, kept, removed = desired_example_fields(fields, allowed)
        remaining_by_level[fields["CEFR"]] += len(kept)
        empty += not kept
        empty_by_level[fields["CEFR"]] += not kept
        if removed:
            updates[str(note_id)] = desired
            removals.append({
                "note_id": note_id, "level": fields["CEFR"], "lemma": fields["Lemma"],
                "removed": removed, "before_count": len(kept) + len(removed), "after_count": len(kept),
            })
    summary = {
        "notes": len(records), "cards": sum(len(record["cards"]) for record in records.values()),
        "affected_notes": len(updates), "removed_occurrences": sum(len(item["removed"]) for item in removals),
        "remaining_occurrences": sum(remaining_by_level.values()), "empty_notes": empty,
        "remaining_by_level": remaining_by_level, "empty_by_level": empty_by_level,
    }
    expected = {
        "notes": EXPECTED_NOTES, "cards": EXPECTED_CARDS, "affected_notes": EXPECTED_AFFECTED,
        "removed_occurrences": EXPECTED_REMOVED, "remaining_occurrences": EXPECTED_REMAINING,
        "empty_notes": EXPECTED_EMPTY, "remaining_by_level": EXPECTED_BY_LEVEL,
        "empty_by_level": EXPECTED_EMPTY_BY_LEVEL,
    }
    if summary != expected:
        raise CleanupError(f"cleanup projection changed: {summary} != {expected}")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION, "created_utc": now_utc(),
        "levels": list(scope.LEVELS), "source_hashes": source_hashes(),
        "expected_fingerprints": {str(note_id): record_fingerprint(record) for note_id, record in records.items()},
        "updates": updates, "removals": removals, "pilot_ids": [], "summary": summary,
    }


def command_compile(_: argparse.Namespace) -> None:
    manifest = compile_manifest(live_records())
    word_audio.atomic_json(MANIFEST_PATH, manifest)
    print(json.dumps({"manifest": str(MANIFEST_PATH), **manifest["summary"]}, ensure_ascii=False, indent=2))


def load_manifest() -> dict[str, Any]:
    manifest = word_audio.load_json(MANIFEST_PATH, None)
    if not manifest or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CleanupError("cleanup manifest missing or incompatible; run compile")
    if manifest.get("levels") != list(scope.LEVELS):
        raise CleanupError("cleanup manifest level set is stale; run compile")
    if manifest.get("source_hashes") != source_hashes():
        raise CleanupError("Goethe source or reviewed overrides changed after compile")
    return manifest


def verify_compiled_baseline(records: dict[int, dict[str, Any]], manifest: dict[str, Any]) -> None:
    if set(records) != set(map(int, manifest["expected_fingerprints"])):
        raise CleanupError("live note ID set changed after compile")
    bad = [
        note_id for note_id, record in records.items()
        if manifest["expected_fingerprints"][str(note_id)] != record_fingerprint(record)
    ]
    if bad:
        raise CleanupError(f"live deck changed after compile: {bad[:5]}")


def command_audit(_: argparse.Namespace) -> None:
    manifest = load_manifest()
    verify_compiled_baseline(live_records(), manifest)
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))


def command_snapshot(_: argparse.Namespace) -> None:
    manifest = load_manifest()
    records = live_records()
    verify_compiled_baseline(records, manifest)
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{time.time_ns() % 1_000_000_000:09d}"
    backup = STATE / f"Goethe_Institute_pre_example_cleanup_{stamp}.apkg"
    if backup.exists():
        raise CleanupError(f"backup destination already exists: {backup}")
    try:
        result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.as_posix(), includeSched=True)
    except gw.MigrationError as exc:
        if "timed out" not in str(exc).casefold() and "timeout" not in str(exc).casefold():
            raise
        result = True
    if not result or not apkg.wait_for_valid_apkg(backup):
        raise CleanupError("Anki APKG export failed")
    cards = [card for record in records.values() for card in record["cards"]]
    reviews = word_audio.all_reviews([int(card["cardId"]) for card in cards])
    snapshot = {
        "schema_version": 1, "created_utc": now_utc(), "manifest_sha256": hash_file(MANIFEST_PATH),
        "backup": str(backup), "backup_sha256": hash_file(backup),
        "notes": {str(note_id): {"model": record["model"], "fields": record["fields"], "tags": record["tags"]}
                  for note_id, record in records.items()},
        "cards": {str(card["cardId"]): word_audio.schedule_projection(card) for card in cards},
        "reviews": reviews, "reviews_sha256": canonical_hash(reviews), "model": word_audio.model_snapshot(),
    }
    word_audio.atomic_json(SNAPSHOT_PATH, snapshot)
    print(json.dumps({"backup": str(backup), "sha256": snapshot["backup_sha256"],
                      "notes": len(records), "cards": len(cards)}, indent=2))


def load_ready() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_manifest()
    snapshot = word_audio.load_json(SNAPSHOT_PATH, None)
    if not snapshot or snapshot.get("manifest_sha256") != hash_file(MANIFEST_PATH):
        raise CleanupError("matching snapshot missing; run snapshot after compile")
    backup = Path(str(snapshot.get("backup", "")))
    if not apkg.valid_apkg(backup) or snapshot.get("backup_sha256") != apkg.hash_file(backup):
        raise CleanupError("scheduled APKG backup is missing, corrupt, or changed")
    return manifest, snapshot


def desired_full_fields(note_id: int, manifest: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, str]:
    fields = copy.deepcopy(snapshot["notes"][str(note_id)]["fields"])
    fields.update(manifest["updates"].get(str(note_id), {}))
    return fields


def verify_allowed_mixed_state(
    records: dict[int, dict[str, Any]], manifest: dict[str, Any], snapshot: dict[str, Any],
) -> None:
    if set(records) != set(map(int, snapshot["notes"])):
        raise CleanupError("live note ID set changed after snapshot")
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        if record["model"] != before["model"] or record["tags"] != before["tags"]:
            raise CleanupError(f"model or tags changed: {note_id}")
        before_examples = example_fields(before["fields"])
        desired_examples = example_fields(desired_full_fields(note_id, manifest, snapshot))
        actual_examples = example_fields(record["fields"])
        if actual_examples not in (before_examples, desired_examples):
            raise CleanupError(f"partial or unexpected example-field drift: note={note_id}")
        for name, value in before["fields"].items():
            actual = record["fields"].get(name, "")
            if name in EXAMPLE_FIELDS:
                continue
            if actual != value:
                raise CleanupError(f"unexpected field drift: note={note_id} field={name}")


def update_notes(values: dict[int, dict[str, str]]) -> None:
    actions = [
        {"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": fields}}}
        for note_id, fields in values.items()
    ]
    for batch in gw.chunks(actions, 40):
        results = gw.anki("multi", actions=batch)
        errors = [item.get("error") for item in results if isinstance(item, dict) and item.get("error")]
        if errors:
            raise CleanupError(f"Anki field update failed: {errors[:3]}")


def selected_ids(manifest: dict[str, Any], scope: str) -> list[int]:
    return manifest["pilot_ids"] if scope == "pilot" else sorted(map(int, manifest["updates"]))


def command_apply(args: argparse.Namespace) -> None:
    if not args.dry_run and args.confirmation != APPLY_CONFIRMATION:
        raise CleanupError(f"confirmation must equal {APPLY_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    verify_allowed_mixed_state(records, manifest, snapshot)
    ids = selected_ids(manifest, args.scope)
    values = {
        note_id: manifest["updates"][str(note_id)] for note_id in ids
        if example_fields(records[note_id]["fields"]) != manifest["updates"][str(note_id)]
    }
    print(json.dumps({"scope": args.scope, "selected_notes": len(ids),
                      "changed_notes": len(values), "dry_run": args.dry_run}, indent=2))
    if args.dry_run:
        return
    try:
        update_notes(values)
    except Exception:
        update_notes({note_id: example_fields(snapshot["notes"][str(note_id)]["fields"]) for note_id in values})
        raise


def verify_unchanged_collection(
    records: dict[int, dict[str, Any]], snapshot: dict[str, Any], *, include_model: bool = True,
) -> None:
    cards = [card for record in records.values() for card in record["cards"]]
    if {str(card["cardId"]): word_audio.schedule_projection(card) for card in cards} != snapshot["cards"]:
        raise CleanupError("card IDs, decks, or scheduling changed")
    reviews = word_audio.all_reviews([int(card["cardId"]) for card in cards])
    if canonical_hash(reviews) != snapshot["reviews_sha256"]:
        raise CleanupError("review history changed")
    if include_model and word_audio.model_snapshot() != snapshot["model"]:
        raise CleanupError("model fields, templates, or styling changed")


def verify_scope_fields(
    records: dict[int, dict[str, Any]], manifest: dict[str, Any], snapshot: dict[str, Any], scope: str,
) -> None:
    selected = set(selected_ids(manifest, scope))
    for note_id, record in records.items():
        expected = (desired_full_fields(note_id, manifest, snapshot)
                    if note_id in selected else snapshot["notes"][str(note_id)]["fields"])
        if record["fields"] != expected:
            raise CleanupError(f"example cleanup verification failed: {note_id}")


def command_rebaseline_model(args: argparse.Namespace) -> None:
    if args.confirmation != REBASELINE_MODEL_CONFIRMATION:
        raise CleanupError(f"confirmation must equal {REBASELINE_MODEL_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    verify_allowed_mixed_state(records, manifest, snapshot)
    verify_scope_fields(records, manifest, snapshot, args.scope)
    verify_unchanged_collection(records, snapshot, include_model=False)
    current_model = word_audio.model_snapshot()
    if current_model == snapshot["model"]:
        raise CleanupError("model has not changed; rebaseline is unnecessary")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = STATE / f"snapshot_before_model_rebaseline_{stamp}.json"
    word_audio.atomic_json(archive, snapshot)
    history = list(snapshot.get("model_rebaseline_history", []))
    history.append({
        "accepted_utc": now_utc(), "scope_verified": args.scope,
        "previous_model_sha256": canonical_hash(snapshot["model"]),
        "current_model_sha256": canonical_hash(current_model),
        "archived_snapshot": str(archive),
    })
    snapshot["model"] = current_model
    snapshot["model_rebaseline_history"] = history
    word_audio.atomic_json(SNAPSHOT_PATH, snapshot)
    print(json.dumps(history[-1], ensure_ascii=False, indent=2))


def command_verify(args: argparse.Namespace) -> None:
    manifest, snapshot = load_ready()
    records = live_records()
    verify_allowed_mixed_state(records, manifest, snapshot)
    verify_scope_fields(records, manifest, snapshot, args.scope)
    verify_unchanged_collection(records, snapshot)
    print(json.dumps({"scope": args.scope, "notes": len(records), "cards": EXPECTED_CARDS,
                      "verified_notes": len(selected_ids(manifest, args.scope))}, indent=2))


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise CleanupError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    verify_allowed_mixed_state(records, manifest, snapshot)
    values = {
        note_id: example_fields(snapshot["notes"][str(note_id)]["fields"])
        for note_id, record in records.items()
        if example_fields(record["fields"]) != example_fields(snapshot["notes"][str(note_id)]["fields"])
    }
    update_notes(values)
    print(json.dumps({"restored_notes": len(values)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compile").set_defaults(func=command_compile)
    sub.add_parser("audit").set_defaults(func=command_audit)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    apply = sub.add_parser("apply")
    apply.add_argument("--scope", choices=("pilot", "full"), default="full")
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--confirmation")
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify")
    verify.add_argument("--scope", choices=("pilot", "full"), default="full")
    verify.set_defaults(func=command_verify)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    rebaseline = sub.add_parser("rebaseline-model")
    rebaseline.add_argument("--scope", choices=("pilot", "full"), required=True)
    rebaseline.add_argument("--confirmation", required=True)
    rebaseline.set_defaults(func=command_rebaseline_model)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (CleanupError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
