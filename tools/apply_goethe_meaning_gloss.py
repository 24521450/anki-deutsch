"""Guarded live application of the seven approved meaning-gloss revisions.

The audit itself is report-only.  This command is the explicit apply gate:
it validates the exact live source IDs and old glosses, exports a scheduled
APKG backup, updates only MeaningEN through AnkiConnect, verifies the notes,
then promotes the approved values into the canonical v5 JSONL catalog.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_apkg as apkg
import goethe_english_audit as english_audit
import goethe_meaning_gloss_audit as meaning_audit
import goethe_werkstatt_migrate as gw


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "review" / "goethe_english_audit_v5.jsonl"
STATE_DIR = ROOT / "tools" / ".goethe_meaning_gloss_apply"
STATE = STATE_DIR / "apply_state.json"
DECK = "Goethe Institute"
CONFIRMATION = "APPLY_GOETHE_MEANING_GLOSS_V1"

TARGET_SOURCE_IDS = frozenset({
    "A1-84886454532",
    "A2-0316",
    "A2-0335",
    "A2-0758",
    "A2-0935",
    "B1-MAIN-0178",
    "B1-MAIN-2492",
})


def note_fields(note: dict[str, Any]) -> dict[str, str]:
    return {
        name: str(value.get("value", ""))
        for name, value in note.get("fields", {}).items()
    }


def notes_info(note_ids: list[int]) -> dict[int, dict[str, Any]]:
    notes = gw.anki("notesInfo", notes=note_ids)
    return {int(note["noteId"]): note for note in notes}


def build_plan() -> dict[str, Any]:
    manifest = meaning_audit.load_manifest()
    live_rows = meaning_audit.load_live_rows()
    report = meaning_audit.build_report(live_rows, manifest, "AnkiConnect live collection")
    targets = [row for row in report if row["source_id"] in TARGET_SOURCE_IDS]
    if len(targets) != len(TARGET_SOURCE_IDS):
        raise RuntimeError(f"target coverage changed: expected {len(TARGET_SOURCE_IDS)}, got {len(targets)}")
    if {row["source_id"] for row in targets} != TARGET_SOURCE_IDS:
        raise RuntimeError("target source identity mismatch")
    if any(row["decision"] != "PROPOSE_REVISE" for row in targets):
        raise RuntimeError("approved target set is no longer exactly the seven proposal rows")
    if any(row["current_meaning_en"] != row["manifest_meaning_en"] for row in targets):
        raise RuntimeError("live deck already differs from the canonical pre-apply meanings")
    if any(row["current_meaning_en"] == row["recommended_meaning_en"] for row in targets):
        raise RuntimeError("one or more target meanings already equal the recommendation")

    ids = [int(row["anki_note_id"]) for row in targets]
    live_notes = notes_info(ids)
    records: list[dict[str, Any]] = []
    for row in sorted(targets, key=lambda item: item["source_id"]):
        note_id = int(row["anki_note_id"])
        note = live_notes.get(note_id)
        if note is None:
            raise RuntimeError(f"target note disappeared: {note_id}")
        fields = note_fields(note)
        if fields.get("SourceID") != row["source_id"]:
            raise RuntimeError(f"SourceID mismatch for note {note_id}")
        if fields.get("Lemma") != row["lemma"]:
            raise RuntimeError(f"lemma mismatch for {row['source_id']}")
        if fields.get("MeaningEN") != row["current_meaning_en"]:
            raise RuntimeError(f"MeaningEN changed during planning for {row['source_id']}")
        records.append({
            "source_id": row["source_id"],
            "note_id": note_id,
            "card_ids": sorted(int(card_id) for card_id in note.get("cards", [])),
            "lemma": row["lemma"],
            "before_meaning_en": row["current_meaning_en"],
            "after_meaning_en": row["recommended_meaning_en"],
            "reason": row["reason"],
            "secondary_evidence": row["secondary_evidence"],
            "before_fields": fields,
        })
    return {"records": records, "manifest": manifest}


def export_backup() -> tuple[Path, str]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_%f")
    path = STATE_DIR / f"Goethe_Institute_pre_meaning_gloss_v1_{stamp}.apkg"
    try:
        result = gw.anki(
            "exportPackage",
            request_timeout=60,
            deck=DECK,
            path=path.resolve().as_posix(),
            includeSched=True,
        )
    except gw.MigrationError as exc:
        # AnkiConnect can finish writing a large APKG after the HTTP request
        # times out.  Accept only a CRC/readable package in that case.
        if "timed out" not in str(exc).casefold() and "timeout" not in str(exc).casefold():
            raise
        result = True
    if not result or not apkg.wait_for_valid_apkg(path):
        raise RuntimeError(f"APKG backup failed: {path}")
    return path, apkg.hash_file(path)


def apply_note_fields(records: list[dict[str, Any]]) -> None:
    actions = [
        {
            "action": "updateNoteFields",
            "params": {
                "note": {
                    "id": int(item["note_id"]),
                    "fields": {"MeaningEN": item["after_meaning_en"]},
                }
            },
        }
        for item in records
    ]
    results = gw.anki("multi", actions=actions)
    errors = [item.get("error") for item in results if isinstance(item, dict) and item.get("error")]
    if errors:
        raise RuntimeError(f"Anki update failed: {errors[:3]}")


def verify_notes(records: list[dict[str, Any]]) -> None:
    live = notes_info([int(item["note_id"]) for item in records])
    for item in records:
        note_id = int(item["note_id"])
        note = live.get(note_id)
        if note is None:
            raise RuntimeError(f"updated note missing: {note_id}")
        fields = note_fields(note)
        if fields.get("SourceID") != item["source_id"]:
            raise RuntimeError(f"post-apply SourceID mismatch: {note_id}")
        if fields.get("MeaningEN") != item["after_meaning_en"]:
            raise RuntimeError(f"post-apply MeaningEN mismatch: {item['source_id']}")
        before_fields = item["before_fields"]
        for name, before_value in before_fields.items():
            if name != "MeaningEN" and fields.get(name, "") != before_value:
                raise RuntimeError(f"unexpected field change: {item['source_id']} {name}")
        card_ids = sorted(int(card_id) for card_id in note.get("cards", []))
        if card_ids != item["card_ids"]:
            raise RuntimeError(f"card identity changed: {item['source_id']}")


def promote_manifest(records: list[dict[str, Any]], applied_utc: str) -> None:
    rows = english_audit.load_jsonl(MANIFEST)
    by_id = {item["source_id"]: item for item in records}
    seen: set[str] = set()
    updated: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row.get("source_id", ""))
        item = by_id.get(source_id)
        if item is None:
            updated.append(row)
            continue
        if row.get("desired_meaning_en") != item["before_meaning_en"]:
            raise RuntimeError(f"manifest pre-apply meaning mismatch: {source_id}")
        replacement = copy.deepcopy(row)
        replacement["desired_meaning_en"] = item["after_meaning_en"]
        replacement["previous_meaning_en"] = item["before_meaning_en"]
        replacement["decision"] = "REVISE"
        replacement["change_categories"] = sorted(set(row.get("change_categories", [])) | {"meaning_gloss"})
        primary_evidence = [
            evidence for evidence in row.get("evidence", [])
            if evidence.get("provider") not in {"Duden", "Collins"}
        ]
        replacement["evidence"] = primary_evidence + item["secondary_evidence"]
        replacement["meaning_gloss_audit"] = {
            "artifact": "review/goethe_meaning_gloss_audit_v1.jsonl",
            "decision": "PROPOSE_REVISE",
            "previous_meaning_en": item["before_meaning_en"],
            "applied_meaning_en": item["after_meaning_en"],
            "reason": item["reason"],
            "secondary_evidence": item["secondary_evidence"],
            "applied_utc": applied_utc,
        }
        updated.append(replacement)
        seen.add(source_id)
    if seen != set(by_id):
        raise RuntimeError(f"manifest promotion coverage mismatch: {sorted(set(by_id) - seen)}")
    english_audit.atomic_jsonl(MANIFEST, updated)
    promoted = english_audit.load_json(MANIFEST)
    english_audit.validate_manifest(promoted)


def apply() -> None:
    gw.anki("version")
    plan = build_plan()
    records = plan["records"]
    backup, backup_sha256 = export_backup()
    applied_utc = datetime.now(timezone.utc).isoformat()
    state = {
        "status": "backup_created",
        "created_utc": applied_utc,
        "backup": str(backup),
        "backup_sha256": backup_sha256,
        "records": records,
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    apply_note_fields(records)
    verify_notes(records)
    promote_manifest(records, applied_utc)
    state["status"] = "PASS"
    state["verified_utc"] = datetime.now(timezone.utc).isoformat()
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "notes": len(records),
        "cards": sum(len(item["card_ids"]) for item in records),
        "backup": str(backup),
        "backup_sha256": backup_sha256,
        "source_ids": [item["source_id"] for item in records],
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation", required=True)
    args = parser.parse_args()
    if args.confirmation != CONFIRMATION:
        print(f"ERROR: confirmation must equal {CONFIRMATION}")
        return 1
    try:
        apply()
    except (OSError, RuntimeError, ValueError, english_audit.AuditError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
