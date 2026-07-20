"""Guarded live application of reviewed Goethe content corrections."""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone

import goethe_review_policy as policy
import goethe_target_highlights as highlights
import goethe_werkstatt_migrate as gw

STATE = gw.ROOT / "tools" / ".goethe_review_corrections"
SNAPSHOT = STATE / "snapshot.json"
APPLY_CONFIRMATION = "APPLY_GOETHE_REVIEW_CORRECTIONS"


def live_notes() -> list[dict]:
    if gw.anki("version") != 6:
        raise RuntimeError("unexpected AnkiConnect version")
    ids = gw.anki("findNotes", query='note:"Goethe Werkstatt"')
    result = []
    for batch in gw.chunks(ids):
        result.extend(gw.anki("notesInfo", notes=batch))
    return result


def fields(note: dict) -> dict[str, str]:
    return {key: value.get("value", "") for key, value in note.get("fields", {}).items()}


def plan() -> dict[str, dict]:
    notes = live_notes()
    records = {}
    reviewed = policy.load_policy()
    for note in notes:
        current = fields(note)
        source_id = current.get("SourceID", "")
        desired = copy.deepcopy(current)
        changed = policy.apply_fields(desired, reviewed)
        lemma = desired.get("Lemma", "").strip()
        if lemma.startswith("sich "):
            stem = lemma[5:].strip()
            desired["AcceptedAnswersDE"] = lemma
            desired["AcceptedFullAnswersDE"] = f"{lemma}|s {stem}"
            changed = True
        if not changed:
            continue
        desired["ExampleTargetSpansJSON"] = highlights.build_target_spans(desired)
        records[str(note["noteId"])] = {
            "note_id": int(note["noteId"]),
            "cards": sorted(int(card) for card in note.get("cards", [])),
            "source_id": source_id,
            "before": current,
            "after": desired,
        }
    if len(records) != 32:
        raise RuntimeError(f"review correction target set changed: expected 32, got {len(records)}")
    return records


def command_audit(_: argparse.Namespace) -> None:
    result = plan()
    STATE.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"notes": len(result), "source_ids": sorted(item["source_id"] for item in result.values())}, ensure_ascii=False, indent=2))


def command_backup(_: argparse.Namespace) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = STATE / f"Goethe_Institute_pre_review_{stamp}.apkg"
    if not gw.anki("exportPackage", deck="Goethe Institute", path=path.resolve().as_posix(), includeSched=True):
        raise RuntimeError("APKG backup failed")
    print(path)


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise RuntimeError(f"confirmation must equal {APPLY_CONFIRMATION}")
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    current = plan()
    if set(saved) != set(current):
        raise RuntimeError("live correction target inventory changed since audit")
    actions = []
    allowed = {
        "Lemma", "POS", "AcceptedAnswersDE", "AcceptedFullAnswersDE",
        "FormOrVariantNote", "ExampleTargetSpansJSON",
    }
    for note_id, item in current.items():
        payload = {name: item["after"].get(name, "") for name in allowed if item["after"].get(name, "") != item["before"].get(name, "")}
        if payload:
            actions.append({"action": "updateNoteFields", "params": {"note": {"id": int(note_id), "fields": payload}}})
    for batch in gw.chunks(actions, 25):
        response = gw.anki("multi", actions=batch)
        if any(item.get("error") for item in response if isinstance(item, dict)):
            raise RuntimeError("review correction update failed")
    command_verify(argparse.Namespace())


def command_verify(_: argparse.Namespace) -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    by_id = {str(note["noteId"]): fields(note) for note in live_notes()}
    for note_id, item in expected.items():
        for name in (
            "Lemma", "POS", "AcceptedAnswersDE", "AcceptedFullAnswersDE",
            "FormOrVariantNote", "ExampleTargetSpansJSON",
        ):
            if by_id.get(note_id, {}).get(name, "") != item["after"].get(name, ""):
                raise RuntimeError(f"post-correction field mismatch: {note_id} {name}")
    print(json.dumps({"status": "PASS", "notes": len(expected)}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=command_audit)
    sub.add_parser("backup").set_defaults(func=command_backup)
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    sub.add_parser("verify").set_defaults(func=command_verify)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
