"""Merge Goethe notes that were split by English gloss in the legacy deck."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_examples
import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
MODEL = gw.MODEL
STATE = ROOT / "tools" / ".goethe_headword_cleanup"
POLICY = ROOT / "review" / "goethe_headword_merges.json"
PARENT_DECK = "Goethe Institute"
EXPECTED_BEFORE = (1596, 3192)
EXPECTED_GROUPS = 57
EXPECTED_DELETIONS = 66
APPLY_CONFIRMATION = "MERGE_GOETHE_HEADWORDS"

# `der, die, das` is one Goethe source row; the legacy export split it into
# two `das` notes plus the existing `der` note.
DER_DAS_IDS = (1584886454605, 1584886454606, 1584886454607)
METADATA_OVERRIDES = {
    1584887177227: {"POS": "n.", "Article": "der", "Gender": "m.", "AcceptedArticlesDE": "der", "MeaningEN": "morning"},
    1783863835220: {"POS": "adj.", "Article": "", "Gender": "", "AcceptedArticlesDE": [], "MeaningEN": "orange (colour)"},
    1497484861847: {"POS": "v.", "Article": "", "Gender": "", "AcceptedArticlesDE": [], "FormOrVariantNote": "weg/weg-"},
}
DROP_GLOSSES = {"machen": {"to come to"}}
DAR_VARIANT_IDS = (1497484860928, 1497484860926)  # darauf, darüber


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or "")).strip()).casefold()


def fields(note: dict[str, Any]) -> dict[str, str]:
    return {name: value["value"] for name, value in note["fields"].items()}


def cards_for(notes: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    card_ids = [card_id for note in notes for card_id in note.get("cards", [])]
    cards: list[dict[str, Any]] = []
    for batch in gw.chunks(card_ids, 250):
        cards.extend(gw.anki("cardsInfo", cards=batch))
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        result[int(card["note"])].append(card)
    return result


def live_notes() -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(ids, 250):
        notes.extend(gw.anki("notesInfo", notes=batch))
    return notes, cards_for(notes)


def reps(cards: dict[int, list[dict[str, Any]]], note_id: int) -> int:
    return sum(int(card.get("reps", 0)) for card in cards.get(note_id, []))


def source_order(note: dict[str, Any]) -> tuple[int, int]:
    value = fields(note).get("OriginalOrder", "")
    return (int(value) if value.isdigit() else 10**9, int(note["noteId"]))


def group_notes(notes: list[dict[str, Any]], *, include_der_das: bool = True) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        value = fields(note)
        groups[(value.get("CEFR", ""), value.get("Lemma", "").strip(), value.get("POS", ""))].append(note)
    result = [items for items in groups.values() if len(items) > 1]
    if not include_der_das:
        return result
    by_ids = {int(note["noteId"]): note for note in notes}
    if not all(note_id in by_ids for note_id in DER_DAS_IDS):
        raise RuntimeError("der/das canonical notes are missing")
    result = [items for items in result if {int(note["noteId"]) for note in items} != set(DER_DAS_IDS[:2])]
    result.append([by_ids[note_id] for note_id in DER_DAS_IDS])
    result.sort(key=lambda items: (fields(items[0]).get("CEFR", ""), fields(items[0]).get("Lemma", "").casefold()))
    return result


def choose_survivor(group: list[dict[str, Any]], cards: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    def key(note: dict[str, Any]) -> tuple[int, int, int]:
        value = fields(note)
        has_main = int("-MAIN-" in value.get("SourceRefs", ""))
        return (reps(cards, int(note["noteId"])), has_main, -int(note["noteId"]))
    return max(group, key=key)


def split_gloss(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def merged_gloss(group: list[dict[str, Any]]) -> str:
    lemma = normalize(fields(group[0]).get("Lemma", ""))
    values: list[str] = []
    seen: set[str] = set()
    for note in sorted(group, key=lambda item: source_order(item)):
        for part in split_gloss(fields(note).get("MeaningEN", "")):
            if normalize(part) in DROP_GLOSSES.get(lemma, set()):
                continue
            key = normalize(part)
            if key not in seen:
                seen.add(key)
                values.append(part)
    return "; ".join(values)


def merged_examples(group: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for note in sorted(group, key=source_order):
        for example in goethe_examples.parse_fields(fields(note)):
            key = normalize(example.get("de", ""))
            if key and key not in seen:
                seen.add(key)
                result.append(dict(example))
    return result


def refs(group: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for note in sorted(group, key=source_order):
        for ref in fields(note).get("SourceRefs", "").split("|"):
            ref = ref.strip()
            if ref and ref not in result:
                result.append(ref)
    return result


def desired_fields(group: list[dict[str, Any]], survivor: dict[str, Any]) -> dict[str, str]:
    base = next((note for note in sorted(group, key=source_order) if "-MAIN-" in fields(note).get("SourceRefs", "")), group[0])
    result = fields(survivor)
    base_fields = fields(base)
    for name in ("Lemma", "CEFR", "POS", "Article", "Gender", "NounFormsRaw", "VerbFormsRaw", "AcceptedAnswersDE", "AcceptedArticlesDE", "WordAudio", "SourceNoteRaw", "OriginalOrder"):
        result[name] = base_fields.get(name, "")
    result["MeaningEN"] = merged_gloss(group)
    result["SourceRefs"] = "|".join(refs(group))
    goethe_examples.render_fields(result, merged_examples(group))
    if set(int(note["noteId"]) for note in group) == set(DER_DAS_IDS):
        result["Lemma"] = "der"
        result["AcceptedAnswersDE"] = "der|die|das"
        result["AcceptedArticlesDE"] = ""
    apply_metadata_overrides(result, int(survivor["noteId"]))
    return result


def apply_metadata_overrides(value: dict[str, str], note_id: int) -> None:
    for name, override in METADATA_OVERRIDES.get(note_id, {}).items():
        value[name] = "|".join(override) if isinstance(override, list) else override


def provenance_updates(notes: list[dict[str, Any]], group_updates: dict[int, dict[str, str]]) -> dict[int, dict[str, str]]:
    by_id = {int(note["noteId"]): note for note in notes}
    updates: dict[int, dict[str, str]] = {}
    da_survivor = next((value for note_id, value in group_updates.items() if value.get("Lemma") == "da"), None)
    if da_survivor is not None:
        da_survivor["SourceRefs"] = "|".join(ref for ref in da_survivor["SourceRefs"].split("|") if ref != "A2-MAIN-0201")
    for note_id in DAR_VARIANT_IDS:
        value = fields(by_id[note_id])
        refs_value = value.get("SourceRefs", "").split("|")
        if "A2-MAIN-0201" not in refs_value:
            refs_value.append("A2-MAIN-0201")
        value["SourceRefs"] = "|".join(ref for ref in refs_value if ref)
        updates[note_id] = value
    return updates


def compile_policy(notes: list[dict[str, Any]], cards: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    if (len(notes), sum(len(value) for value in cards.values())) != EXPECTED_BEFORE:
        raise RuntimeError("live inventory changed before headword cleanup")
    groups = group_notes(notes)
    if len(groups) != EXPECTED_GROUPS:
        raise RuntimeError(f"expected {EXPECTED_GROUPS} merge groups, got {len(groups)}")
    entries = []
    for group in groups:
        survivor = choose_survivor(group, cards)
        member_ids = [int(note["noteId"]) for note in group]
        entries.append({
            "key": "|".join((fields(survivor).get("CEFR", ""), fields(survivor).get("Lemma", ""), fields(survivor).get("POS", ""))),
            "survivor": int(survivor["noteId"]),
            "members": member_ids,
            "delete": [note_id for note_id in member_ids if note_id != int(survivor["noteId"])],
            "meaning_en": merged_gloss(group),
            "fields": desired_fields(group, survivor),
            "reps": {str(note_id): reps(cards, note_id) for note_id in member_ids},
        })
    deletions = sum(len(entry["delete"]) for entry in entries)
    if deletions != EXPECTED_DELETIONS:
        raise RuntimeError(f"expected {EXPECTED_DELETIONS} deletions, got {deletions}")
    updates = {int(entry["survivor"]): entry["fields"] for entry in entries}
    by_id = {int(note["noteId"]): note for note in notes}
    for note_id in METADATA_OVERRIDES:
        if note_id not in updates:
            value = fields(by_id[note_id])
            apply_metadata_overrides(value, note_id)
            updates[note_id] = value
    updates.update(provenance_updates(notes, updates))
    return {"schema_version": 1, "created_utc": now_utc(), "before": {"notes": len(notes), "cards": sum(len(value) for value in cards.values())}, "groups": entries, "updates": {str(note_id): value for note_id, value in sorted(updates.items())}, "delete_ids": sorted(note_id for entry in entries for note_id in entry["delete"])}


def protected_snapshot(notes: list[dict[str, Any]], cards: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    card_values = [card for values in cards.values() for card in values]
    card_ids = sorted(int(card["cardId"]) for card in card_values)
    reviews: dict[str, Any] = {}
    for batch in gw.chunks(card_ids, 50):
        reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    return {
        "notes": {str(note["noteId"]): {"model": note["modelName"], "tags": sorted(note["tags"]), "fields": fields(note)} for note in notes},
        "cards": {str(card["cardId"]): {key: card.get(key) for key in gw.SCHEDULE_KEYS} for card in card_values},
        "reviews_hash": canonical_hash(reviews),
        "reviews": reviews,
    }


def valid_backup(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return "collection.anki2" in archive.namelist() and archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def command_compile(_: argparse.Namespace) -> None:
    if gw.anki("version") != 6:
        raise RuntimeError("unexpected AnkiConnect version")
    notes, cards = live_notes()
    policy = compile_policy(notes, cards)
    POLICY.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"policy": str(POLICY), "groups": len(policy["groups"]), "delete": len(policy["delete_ids"])}, ensure_ascii=False, indent=2))


def load_policy() -> dict[str, Any]:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1 or len(policy.get("groups", [])) != EXPECTED_GROUPS or len(policy.get("delete_ids", [])) != EXPECTED_DELETIONS:
        raise RuntimeError("invalid headword merge policy")
    return policy


def command_dry_run(_: argparse.Namespace) -> None:
    policy = load_policy()
    print(json.dumps({"groups": len(policy["groups"]), "delete_ids": len(policy["delete_ids"]), "target_notes": EXPECTED_BEFORE[0] - len(policy["delete_ids"]), "target_cards": EXPECTED_BEFORE[1] - 2 * len(policy["delete_ids"])}, indent=2))


def command_verify(_: argparse.Namespace) -> None:
    if gw.anki("version") != 6:
        raise RuntimeError("unexpected AnkiConnect version")
    policy = load_policy()
    notes, cards = live_notes()
    if (len(notes), sum(len(value) for value in cards.values())) != (EXPECTED_BEFORE[0] - EXPECTED_DELETIONS, EXPECTED_BEFORE[1] - 2 * EXPECTED_DELETIONS):
        raise RuntimeError("live inventory does not match completed headword cleanup")
    current_ids = {int(note["noteId"]) for note in notes}
    if current_ids & set(policy["delete_ids"]):
        raise RuntimeError("deleted note IDs are still present")
    by_id = {int(note["noteId"]): fields(note) for note in notes}
    mismatches = [note_id for note_id, value in policy["updates"].items() if int(note_id) in by_id and by_id[int(note_id)] != value]
    if mismatches:
        raise RuntimeError(f"post-cleanup field mismatch: {mismatches[:5]}")
    if group_notes(notes, include_der_das=False):
        raise RuntimeError("exact duplicate headword groups remain")
    print(json.dumps({"status": "PASS", "notes": len(notes), "cards": sum(len(value) for value in cards.values()), "groups": len(policy["groups"]), "deleted": len(policy["delete_ids"])}, indent=2))


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise RuntimeError(f"confirmation must equal {APPLY_CONFIRMATION}")
    if gw.anki("version") != 6:
        raise RuntimeError("unexpected AnkiConnect version")
    policy = load_policy()
    notes, cards = live_notes()
    by_id = {int(note["noteId"]): note for note in notes}
    if set(policy["delete_ids"]) - set(by_id):
        raise RuntimeError("policy delete IDs do not match live deck")
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_pre_headword_cleanup_{stamp}.apkg"
    try:
        exported = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.resolve().as_posix(), includeSched=True)
    except gw.MigrationError:
        exported = False
        for _ in range(30):
            if backup.exists() and valid_backup(backup):
                exported = True
                break
            time.sleep(2)
    if not exported or not backup.exists() or not valid_backup(backup):
        raise RuntimeError("APKG backup failed")
    before_state = protected_snapshot(notes, cards)
    updates = {int(note_id): value for note_id, value in policy["updates"].items()}
    original = {note_id: fields(note) for note_id, note in by_id.items() if note_id in updates}
    try:
        actions = [{"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": value}}} for note_id, value in updates.items()]
        for batch in gw.chunks(actions, 40):
            response = gw.anki("multi", actions=batch)
            if any(item.get("error") for item in response if isinstance(item, dict)):
                raise RuntimeError("survivor field update failed")
        verify = {int(note["noteId"]): fields(note) for note in gw.anki("notesInfo", notes=list(updates))}
        if verify != updates:
            raise RuntimeError("survivor field verification failed")
        gw.anki("deleteNotes", notes=policy["delete_ids"])
    except Exception:
        for note_id, value in original.items():
            gw.anki("updateNoteFields", note={"id": note_id, "fields": value})
        raise
    remaining_notes, remaining_cards = live_notes()
    remaining_ids = {int(note["noteId"]) for note in remaining_notes}
    expected_ids = set(int(note_id) for note_id in before_state["notes"]) - set(policy["delete_ids"])
    if remaining_ids != expected_ids:
        raise RuntimeError("post-delete note inventory differs from policy")
    for note in remaining_notes:
        note_id = int(note["noteId"])
        before_note = before_state["notes"][str(note_id)]
        if note_id in updates:
            if fields(note) != updates[note_id]:
                raise RuntimeError(f"post-delete survivor fields differ: {note_id}")
        elif {"model": note["modelName"], "tags": sorted(note["tags"]), "fields": fields(note)} != before_note:
            raise RuntimeError(f"unrelated note changed: {note_id}")
    after_cards = {str(int(card["cardId"])): {key: card.get(key) for key in gw.SCHEDULE_KEYS} for values in remaining_cards.values() for card in values}
    expected_cards = {card_id: value for card_id, value in before_state["cards"].items() if card_id in after_cards}
    if after_cards != expected_cards:
        raise RuntimeError("post-delete scheduling changed")
    after_reviews: dict[str, Any] = {}
    for batch in gw.chunks(sorted(int(card_id) for card_id in after_cards), 50):
        after_reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    expected_reviews = {card_id: value for card_id, value in before_state["reviews"].items() if card_id in after_cards}
    if canonical_hash(after_reviews) != canonical_hash(expected_reviews):
        raise RuntimeError("post-delete review history changed")
    print(json.dumps({"backup": str(backup.resolve()), "deleted_notes": len(policy["delete_ids"]), "remaining_notes": EXPECTED_BEFORE[0] - len(policy["delete_ids"]), "remaining_cards": EXPECTED_BEFORE[1] - 2 * len(policy["delete_ids"])}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compile").set_defaults(func=command_compile)
    sub.add_parser("dry-run").set_defaults(func=command_dry_run)
    sub.add_parser("verify").set_defaults(func=command_verify)
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        args.func(args)
    except (RuntimeError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
