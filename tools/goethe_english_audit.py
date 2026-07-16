"""Validate, apply, and verify the evidence-backed Goethe English audit v3."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

import goethe_examples
import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
MANIFEST = ROOT / "review" / "goethe_english_audit_v3.jsonl"
STATE = ROOT / "tools" / ".goethe_english_audit_v3"
SNAPSHOT = STATE / "snapshot.json"
MODEL = gw.MODEL
PARENT_DECK = "Goethe Institute"
EXPECTED_NOTES = 1530
EXPECTED_CARDS = 3060
OLD_VERIFIED_TAG = "goethe::quality::english_verified::british"
OLD_AUDITED_TAG = "goethe::quality::english_audited::british"
AUDITED_TAG = "goethe::quality::english_audited::v3::british"
REVIEW_TAG = "goethe::quality::translation_review_needed"
CONFIRMATION = "APPLY_GOETHE_ENGLISH_AUDIT_V3"
PILOT_SOURCE_IDS = [
    "A1-84886454810",
    "A2-0851", "A2-MAIN-0202", "A2-0404", "A1-84886454916",
    "A2-0853", "A1-84886454763", "A1-84886454835", "A2-0654",
    "A2-0691", "A2-0074", "A1-84886455054", "A1-84886455126",
    "A1-84886454920", "A1-84886455037", "A1-84886455211",
    "A2-1152", "A2-1184", "A2-1189", "A2-WG-0173", "A2-WG-0044",
]


class AuditError(RuntimeError):
    pass


def audit_projection(fields: dict[str, str]) -> dict[str, Any]:
    ignored = {"WordAudio", "MoreExamplesHTML"} | {
        f"Example{index}{suffix}" for index in range(1, 5) for suffix in ("DE", "EN", "Audio")
    }
    return {
        "fields": {name: value for name, value in fields.items() if name not in ignored},
        "examples": example_pairs(goethe_examples.parse_fields(fields)),
    }


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_apkg(path: Path) -> bool:
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            return bool({"collection.anki2", "collection.anki21"} & names) and archive.testzip() is None
    except (OSError, BadZipFile):
        return False


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    except Exception:
        if os.path.exists(name):
            os.unlink(name)
        raise


def load_json(path: Path) -> dict[str, Any]:
    try:
        if path.suffix == ".jsonl":
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            entries = {row["source_id"]: row for row in rows}
            desired_groups: dict[tuple[str, str], list[str]] = {}
            for entry in rows:
                key = (entry["cefr"], entry["desired_meaning_en"].casefold())
                desired_groups.setdefault(key, []).append(entry["source_id"])
            collisions = [
                {"cefr": key[0], "meaning_en": key[1], "source_ids": values}
                for key, values in desired_groups.items() if len(values) > 1
            ]
            return {
                "schema_version": 3,
                "audit_id": "goethe-english-v3-2026-07",
                "standard": "British English",
                "primary_source": "Cambridge German-English Dictionary",
                "entries": entries,
                "ambiguous_prompt_groups": collisions,
                "counts": {
                    "notes": len(rows),
                    "keep": sum(item["decision"] == "KEEP" for item in rows),
                    "revise": sum(item["decision"] == "REVISE" for item in rows),
                    "meaning_updates": sum(item["expected_meaning_en"] != item["desired_meaning_en"] for item in rows),
                    "example_updates": sum(example_pairs(item["expected_examples"]) != example_pairs(item["desired_examples"]) for item in rows),
                    "a1": sum(item["cefr"] == "A1" for item in rows),
                    "a2": sum(item["cefr"] == "A2" for item in rows),
                    "ambiguous_prompt_groups": len(collisions),
                },
            }
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read JSON: {path}") from exc


def example_pairs(examples: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"de": str(item.get("de") or ""), "en": str(item.get("en") or "")} for item in examples]


def normalize_meaning(value: str) -> str:
    """Apply only the deck-wide delimiter convention chosen for this audit."""
    return value.strip().replace(" / ", "; ")


def command_compile(_: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST)
    validate_manifest(manifest)
    print(json.dumps({"catalog": str(MANIFEST), **manifest["counts"]}, ensure_ascii=False, indent=2))


def find_entry(fields: dict[str, str], manifest: dict[str, Any]) -> dict[str, Any] | None:
    source_id = fields.get("SourceID", "")
    if source_id in manifest["entries"]:
        return manifest["entries"][source_id]
    guid = fields.get("LegacyGUID", "")
    matches = [entry for entry in manifest["entries"].values() if entry["legacy_guid"] == guid]
    if len(matches) > 1:
        raise AuditError(f"ambiguous LegacyGUID: {guid}")
    return matches[0] if matches else None


def covered_source_ids(fields: dict[str, str], manifest: dict[str, Any]) -> set[str]:
    """Return every audited source row carried by a merged note."""
    refs = {item.strip() for item in str(fields.get("SourceRefs", "")).split("|") if item.strip()}
    refs.add(str(fields.get("SourceID", "")).strip())
    return refs & set(manifest["entries"])


def pair_state(current: list[dict[str, str]], entry: dict[str, Any]) -> str:
    pairs = example_pairs(current)
    if pairs == example_pairs(entry["expected_examples"]):
        return "expected"
    if pairs == example_pairs(entry.get("previous_examples", [])):
        return "previous"
    if pairs == example_pairs(entry["desired_examples"]):
        return "desired"
    return "drift"


def desired_fields(fields: dict[str, str], entry: dict[str, Any]) -> dict[str, str]:
    current_meaning = fields.get("MeaningEN", "").strip()
    allowed_meanings = {
        entry["expected_meaning_en"], entry.get("previous_meaning_en", ""), entry["desired_meaning_en"],
    }
    if current_meaning not in allowed_meanings and normalize_meaning(current_meaning) not in {
        normalize_meaning(value) for value in allowed_meanings
    }:
        raise AuditError(f"MeaningEN drift: {entry['source_id']} {current_meaning!r}")
    current_examples = goethe_examples.parse_fields(fields)
    if pair_state(current_examples, entry) == "drift":
        raise AuditError(f"example drift: {entry['source_id']}")
    audio_by_de = {item["de"]: item.get("audio", "") for item in current_examples}
    examples = [
        {"de": item["de"], "en": item["en"], "audio": audio_by_de.get(item["de"], "")}
        for item in entry["desired_examples"]
    ]
    result = copy.deepcopy(fields)
    result["MeaningEN"] = entry["desired_meaning_en"]
    goethe_examples.render_fields(result, examples)
    return result


def desired_tags(tags: list[str]) -> list[str]:
    return sorted((set(tags) - {OLD_VERIFIED_TAG, OLD_AUDITED_TAG, REVIEW_TAG}) | {AUDITED_TAG})


def apply_manifest_to_records(records: dict[str, dict[str, Any]], manifest: dict[str, Any], *, strict: bool) -> None:
    matched: set[str] = set()
    for record in records.values():
        entry = find_entry(record["fields"], manifest)
        if entry is None:
            record["tags"] = sorted((set(record["tags"]) - {AUDITED_TAG}) | {REVIEW_TAG})
            continue
        matched.update(covered_source_ids(record["fields"], manifest))
        record["fields"] = desired_fields(record["fields"], entry)
        record["examples"] = goethe_examples.parse_fields(record["fields"])
        record["tags"] = desired_tags(record["tags"])
    if strict and matched != set(manifest["entries"]):
        missing = set(manifest["entries"]) - matched
        raise AuditError(f"audit coverage missing from records: {sorted(missing)[:5]}")


def live_records() -> dict[int, dict[str, Any]]:
    if gw.anki("version") != 6:
        raise AuditError("unexpected AnkiConnect API version")
    ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(ids):
        notes.extend(gw.anki("notesInfo", notes=batch))
    card_ids = [int(card_id) for note in notes for card_id in note.get("cards", [])]
    cards: list[dict[str, Any]] = []
    for batch in gw.chunks(card_ids, 20):
        cards.extend(gw.anki("cardsInfo", cards=batch))
    by_note: dict[int, list[dict[str, Any]]] = {}
    for card in cards:
        by_note.setdefault(int(card["note"]), []).append(card)
    records = {}
    for note in notes:
        note_id = int(note["noteId"])
        fields = {name: note.get("fields", {}).get(name, {}).get("value", "") for name in gw.FIELDS}
        records[note_id] = {
            "note_id": note_id, "model": note["modelName"], "fields": fields,
            "tags": sorted(note.get("tags", [])),
            "cards": sorted(by_note.get(note_id, []), key=lambda card: int(card["cardId"])),
        }
    cards = sum(len(record["cards"]) for record in records.values())
    if len(records) != EXPECTED_NOTES or cards != EXPECTED_CARDS:
        raise AuditError(f"expected {EXPECTED_NOTES}/{EXPECTED_CARDS}, got {len(records)}/{cards}")
    return records


def all_reviews(card_ids: list[int]) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for batch in gw.chunks(sorted(card_ids), 250):
        reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    return reviews


def schedule_projection(card: dict[str, Any]) -> dict[str, Any]:
    return {key: card.get(key) for key in gw.SCHEDULE_KEYS}


def model_snapshot() -> dict[str, Any]:
    return {
        "fields": gw.anki("modelFieldNames", modelName=MODEL),
        "templates": gw.anki("modelTemplates", modelName=MODEL),
        "styling": gw.anki("modelStyling", modelName=MODEL),
    }


def anki_multi(actions: list[dict[str, Any]], size: int = 60) -> None:
    for batch in gw.chunks(actions, size):
        results = gw.anki("multi", actions=batch)
        errors = [item.get("error") for item in results if isinstance(item, dict) and item.get("error")]
        if errors:
            raise AuditError(f"Anki multi errors: {errors[:3]}")


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 3 or len(manifest.get("entries", {})) != EXPECTED_NOTES:
        raise AuditError("invalid or incomplete manifest")
    counts = manifest["counts"]
    if counts["notes"] != EXPECTED_NOTES or counts["a1"] + counts["a2"] != EXPECTED_NOTES:
        raise AuditError("manifest counts are inconsistent")
    if counts["keep"] + counts["revise"] != EXPECTED_NOTES:
        raise AuditError("not every note has an audit decision")
    if counts["ambiguous_prompt_groups"]:
        raise AuditError("ambiguous English prompts remain")
    allowed_origins = {"goethe", "review-authored"}
    allowed_providers = {"Cambridge", "Collins", "Duden"}
    for entry in manifest["entries"].values():
        evidence = entry.get("evidence", [])
        if entry.get("review_status") != "reviewed" or not evidence:
            raise AuditError(f"unreviewed or unsupported entry: {entry.get('source_id')}")
        domains = set()
        for item in evidence:
            url = item.get("url", "") if isinstance(item, dict) else ""
            if item.get("provider") not in allowed_providers or not item.get("supports"):
                raise AuditError(f"invalid evidence metadata: {entry['source_id']}")
            if not url.startswith("https://") or url.rstrip("/").endswith("german-english"):
                raise AuditError(f"invalid evidence URL: {entry['source_id']}")
            domains.add(url.split("/", 3)[2].casefold())
        if entry.get("difficult") and len(domains) < 2:
            raise AuditError(f"difficult entry needs two source domains: {entry['source_id']}")
        meaning = entry.get("desired_meaning_en", "")
        if not meaning or any(token in meaning for token in ("…", "...", "sth.", "sb.", "so.", " / ")):
            raise AuditError(f"invalid learner gloss: {entry['source_id']}")
        for example in entry.get("desired_examples", []):
            if example.get("origin") not in allowed_origins or not example.get("de") or not example.get("en"):
                raise AuditError(f"invalid audited example: {entry['source_id']}")


def validate_records(records: dict[int, dict[str, Any]], manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    validate_manifest(manifest)
    resolved: dict[int, dict[str, Any]] = {}
    used: set[str] = set()
    for note_id, record in records.items():
        entry = find_entry(record["fields"], manifest)
        if entry is None:
            raise AuditError(f"live note not covered by audit: {note_id}")
        if entry["note_id_guard"] != note_id:
            raise AuditError(f"note ID guard changed: {entry['source_id']} {note_id}")
        if entry["lemma"] != record["fields"]["Lemma"] or entry["cefr"] != record["fields"]["CEFR"]:
            raise AuditError(f"identity drift: {entry['source_id']}")
        desired_fields(record["fields"], entry)
        resolved[note_id] = entry
        used.update(covered_source_ids(record["fields"], manifest))
    if used != set(manifest["entries"]):
        raise AuditError("live audit coverage mismatch")
    return resolved


def command_dry_run(_: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST)
    records = live_records()
    validate_records(records, manifest)
    changed = sum(
        desired_fields(record["fields"], find_entry(record["fields"], manifest)) != record["fields"]
        or desired_tags(record["tags"]) != record["tags"]
        for record in records.values()
    )
    print(json.dumps({**manifest["counts"], "live_notes": len(records), "live_changes": changed}, indent=2))


def protected_snapshot(records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    cards = [card for record in records.values() for card in record["cards"]]
    card_ids = [int(card["cardId"]) for card in cards]
    reviews = all_reviews(card_ids)
    return {
        "notes": {
            str(note_id): {"fields": record["fields"], "tags": record["tags"], "model": record["model"]}
            for note_id, record in records.items()
        },
        "cards": {str(card["cardId"]): schedule_projection(card) for card in cards},
        "reviews": reviews,
        "reviews_sha256": canonical_hash(reviews),
        "model": model_snapshot(),
    }


def command_snapshot(_: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST)
    records = live_records()
    validate_records(records, manifest)
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_pre_english_audit_{stamp}.apkg"
    try:
        result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.resolve().as_posix(), includeSched=True)
    except gw.MigrationError:
        result = valid_apkg(backup)
    if not result or not valid_apkg(backup):
        raise AuditError("Anki APKG export failed")
    snapshot = protected_snapshot(records)
    snapshot.update({
        "created_utc": now_utc(), "manifest_sha256": hash_file(MANIFEST),
        "backup": str(backup), "backup_sha256": hash_file(backup),
    })
    atomic_json(SNAPSHOT, snapshot)
    print(json.dumps({"backup": str(backup), "sha256": snapshot["backup_sha256"], "notes": len(records), "cards": len(snapshot["cards"])}, indent=2))


def load_ready() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_json(MANIFEST)
    snapshot = load_json(SNAPSHOT)
    if snapshot.get("manifest_sha256") != hash_file(MANIFEST):
        raise AuditError("manifest changed after snapshot")
    return manifest, snapshot


def verify_protected_collection(records: dict[int, dict[str, Any]], snapshot: dict[str, Any]) -> None:
    cards = [card for record in records.values() for card in record["cards"]]
    schedules = {str(card["cardId"]): schedule_projection(card) for card in cards}
    if schedules != snapshot["cards"]:
        raise AuditError("card scheduling changed")
    reviews = all_reviews([int(card["cardId"]) for card in cards])
    if canonical_hash(reviews) != snapshot["reviews_sha256"]:
        raise AuditError("review history changed")
    if model_snapshot() != snapshot["model"]:
        raise AuditError("note type changed")


def allowed_mixed_state(records: dict[int, dict[str, Any]], manifest: dict[str, Any], snapshot: dict[str, Any]) -> None:
    if set(records) != set(map(int, snapshot["notes"])):
        raise AuditError("live note IDs changed after snapshot")
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        entry = find_entry(before["fields"], manifest)
        target_fields = desired_fields(before["fields"], entry)
        target_tags = desired_tags(before["tags"])
        if audit_projection(record["fields"]) not in (
            audit_projection(before["fields"]), audit_projection(target_fields),
        ):
            raise AuditError(f"unexpected mixed field state: {note_id}")
        if record["tags"] not in (before["tags"], target_tags):
            raise AuditError(f"unexpected mixed tag state: {note_id}")
    verify_protected_collection(records, snapshot)


def update_notes(records: dict[int, dict[str, Any]], manifest: dict[str, Any], snapshot: dict[str, Any], source_ids: set[str]) -> int:
    actions: list[dict[str, Any]] = []
    changed = 0
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        entry = find_entry(before["fields"], manifest)
        if entry["source_id"] not in source_ids:
            continue
        fields = desired_fields(record["fields"], entry)
        tags = desired_tags(before["tags"])
        if audit_projection(record["fields"]) != audit_projection(fields):
            actions.append({"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": fields}}})
        if record["tags"] != tags:
            remove = sorted(set(record["tags"]) - set(tags))
            add = sorted(set(tags) - set(record["tags"]))
            if remove:
                actions.append({"action": "removeTags", "params": {"notes": [note_id], "tags": " ".join(remove)}})
            if add:
                actions.append({"action": "addTags", "params": {"notes": [note_id], "tags": " ".join(add)}})
        if audit_projection(record["fields"]) != audit_projection(fields) or record["tags"] != tags:
            changed += 1
    anki_multi(actions)
    return changed


def command_pilot(args: argparse.Namespace) -> None:
    if args.confirmation != CONFIRMATION:
        raise AuditError(f"confirmation must equal {CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    allowed_mixed_state(records, manifest, snapshot)
    changed = update_notes(records, manifest, snapshot, set(PILOT_SOURCE_IDS))
    records = live_records()
    allowed_mixed_state(records, manifest, snapshot)
    print(json.dumps({"pilot": len(PILOT_SOURCE_IDS), "changed": changed}, indent=2))


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != CONFIRMATION:
        raise AuditError(f"confirmation must equal {CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    allowed_mixed_state(records, manifest, snapshot)
    changed = update_notes(records, manifest, snapshot, set(manifest["entries"]))
    records = live_records()
    allowed_mixed_state(records, manifest, snapshot)
    print(json.dumps({"notes": len(records), "changed": changed}, indent=2))


def command_verify(_: argparse.Namespace) -> None:
    manifest, snapshot = load_ready()
    records = live_records()
    allowed_mixed_state(records, manifest, snapshot)
    wrong = []
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        entry = find_entry(before["fields"], manifest)
        target = desired_fields(record["fields"], entry)
        if audit_projection(record["fields"]) != audit_projection(target) or record["tags"] != desired_tags(before["tags"]):
            wrong.append(note_id)
    if wrong:
        raise AuditError(f"audit not fully applied: {wrong[:5]}")
    print(json.dumps({"status": "PASS", "notes": len(records), "cards": len(snapshot["cards"]), **manifest["counts"]}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compile").set_defaults(func=command_compile)
    sub.add_parser("dry-run").set_defaults(func=command_dry_run)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    for name, func in (("pilot", command_pilot), ("apply", command_apply)):
        command = sub.add_parser(name)
        command.add_argument("--confirmation", required=True)
        command.set_defaults(func=func)
    sub.add_parser("verify").set_defaults(func=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (AuditError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
