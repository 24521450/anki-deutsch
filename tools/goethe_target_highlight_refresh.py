"""Guarded refresh of reviewed Goethe target-highlight spans and templates.

The workflow is deliberately narrow: it may write only the reviewed
``ExampleTargetSpansJSON`` values and the existing Goethe Werkstatt model
templates.  Collection access is exclusively through AnkiConnect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import goethe_target_highlights as highlights
import goethe_apkg as apkg
import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
STATE = ROOT / "tools" / ".goethe_target_highlight_refresh"
MANIFEST_PATH = ROOT / "review" / "goethe_target_highlight_repairs.json"
MODEL = getattr(gw, "MODEL", "Goethe Werkstatt")
PARENT_DECK = "Goethe Institute"
TARGET_FIELD = "ExampleTargetSpansJSON"

EXPECTED_CHANGED_NOTES = 40
EXPECTED_CHANGED_EXAMPLES = 44
APPLY_CONFIRMATION = "APPLY_GOETHE_TARGET_HIGHLIGHT_REFRESH"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_TARGET_HIGHLIGHT_REFRESH"

SCHEDULE_KEYS = (
    "cardId", "note", "ord", "deckName", "factor", "interval", "type",
    "queue", "due", "reps", "lapses", "left", "flags",
)


class RefreshError(RuntimeError):
    """A fail-closed refresh error."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def chunks(values: list[Any], size: int = 250) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def anki(action: str, **params: Any) -> Any:
    try:
        return gw.anki(action, **params)
    except Exception as exc:
        raise RefreshError(f"AnkiConnect {action} failed: {exc}") from exc


def require_version() -> None:
    if anki("version") != 6:
        raise RefreshError("unexpected AnkiConnect API version (expected 6)")


def field_value(note: dict[str, Any], name: str) -> str:
    raw = note.get("fields", {}).get(name, {})
    if isinstance(raw, dict):
        return str(raw.get("value", "") or "")
    return str(raw or "")


def note_fields(note: dict[str, Any]) -> dict[str, str]:
    raw_fields = note.get("fields")
    if not isinstance(raw_fields, dict):
        raise RefreshError(f"notesInfo fields missing: note {note.get('noteId')}")
    return {name: field_value(note, name) for name in raw_fields}


def fetch_notes() -> list[dict[str, Any]]:
    raw_ids = anki("findNotes", query=f'note:"{MODEL}"')
    if not isinstance(raw_ids, list):
        raise RefreshError("findNotes returned a non-list")
    ids = sorted(int(value) for value in raw_ids)
    if len(ids) != len(set(ids)):
        raise RefreshError("findNotes returned duplicate note IDs")
    notes: list[dict[str, Any]] = []
    for batch in chunks(ids):
        result = anki("notesInfo", notes=batch)
        if not isinstance(result, list):
            raise RefreshError("notesInfo returned a non-list")
        notes.extend(result)
    returned = [int(note["noteId"]) for note in notes]
    if len(returned) != len(set(returned)) or set(returned) != set(ids):
        raise RefreshError("notesInfo returned a different note ID set")
    wrong_model = [
        int(note["noteId"]) for note in notes if note.get("modelName") != MODEL
    ]
    if wrong_model:
        raise RefreshError(f"notesInfo returned another model: {wrong_model[:5]}")
    notes.sort(key=lambda note: int(note["noteId"]))
    return notes


def source_templates() -> dict[str, Any]:
    try:
        templates = gw.templates()
    except Exception as exc:
        raise RefreshError(f"cannot build repository templates: {exc}") from exc
    if not isinstance(templates, dict) or not templates:
        raise RefreshError("repository templates are empty")
    return templates


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RefreshError(f"{label} missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RefreshError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RefreshError(f"{label} must be a JSON object")
    return value


def compact_spans(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_spans(value: Any, fields: dict[str, str], label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RefreshError(f"{label} must be a decoded span array")
    texts = highlights.example_texts(fields)
    encoded = compact_spans(value)
    try:
        highlights.parse_target_spans(encoded, texts)
    except highlights.HighlightError as exc:
        raise RefreshError(f"{label} is invalid: {exc}") from exc
    return value


def load_manifest() -> dict[str, Any]:
    manifest = read_json(MANIFEST_PATH, "review manifest")
    if manifest.get("schema_version") != 1:
        raise RefreshError("unsupported review manifest schema")
    if manifest.get("expected_changed_notes") != EXPECTED_CHANGED_NOTES:
        raise RefreshError(
            f"review manifest must declare {EXPECTED_CHANGED_NOTES} changed notes"
        )
    if manifest.get("expected_changed_examples") != EXPECTED_CHANGED_EXAMPLES:
        raise RefreshError(
            f"review manifest must declare {EXPECTED_CHANGED_EXAMPLES} changed examples"
        )
    repairs = manifest.get("repairs")
    if not isinstance(repairs, list) or len(repairs) != EXPECTED_CHANGED_NOTES:
        raise RefreshError("review manifest repair count differs from its declaration")
    source_ids = [item.get("source_id") for item in repairs if isinstance(item, dict)]
    if len(source_ids) != len(repairs) or any(not isinstance(value, str) or not value for value in source_ids):
        raise RefreshError("review manifest has an invalid SourceID")
    if len(source_ids) != len(set(source_ids)):
        raise RefreshError("review manifest has duplicate SourceIDs")
    if source_ids != sorted(source_ids):
        raise RefreshError("review manifest repairs must be sorted by source_id")
    note_ids: list[int] = []
    card_ids: list[int] = []
    changed_examples = 0
    for item in repairs:
        required = {"source_id", "note_id", "card_ids", "lemma", "before", "after"}
        if set(item) != required:
            raise RefreshError(
                f"review repair keys differ for {item.get('source_id', '<unknown>')}"
            )
        note_id = item["note_id"]
        cards = item["card_ids"]
        if not isinstance(note_id, int) or isinstance(note_id, bool):
            raise RefreshError(f"review repair note_id is invalid: {item['source_id']}")
        if (
            not isinstance(cards, list) or len(cards) != 2
            or any(not isinstance(value, int) or isinstance(value, bool) for value in cards)
            or cards != sorted(cards) or len(set(cards)) != 2
        ):
            raise RefreshError(f"review repair card_ids are invalid: {item['source_id']}")
        if not isinstance(item["lemma"], str) or not item["lemma"]:
            raise RefreshError(f"review repair lemma is invalid: {item['source_id']}")
        before, after = item["before"], item["after"]
        if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):
            raise RefreshError(f"review repair spans are invalid: {item['source_id']}")
        delta = sum(left != right for left, right in zip(before, after))
        if not delta:
            raise RefreshError(f"review repair contains no change: {item['source_id']}")
        changed_examples += delta
        note_ids.append(note_id)
        card_ids.extend(cards)
    if len(note_ids) != len(set(note_ids)):
        raise RefreshError("review manifest has duplicate note IDs")
    if len(card_ids) != len(set(card_ids)):
        raise RefreshError("review manifest has duplicate card IDs")
    if changed_examples != EXPECTED_CHANGED_EXAMPLES:
        raise RefreshError(
            "review manifest changed-example count differs from its declaration"
        )
    return manifest


def build_audit_plan(notes: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    by_id = {int(note["noteId"]): note for note in notes}
    live_sources: dict[str, int] = {}
    live_card_ids: list[int] = []
    changed: dict[int, tuple[list[Any], list[Any]]] = {}
    note_card_contract: dict[str, Any] = {}
    for note in notes:
        note_id = int(note["noteId"])
        fields = note_fields(note)
        source_id = fields.get("SourceID", "")
        if not source_id:
            raise RefreshError(f"live Goethe note has no SourceID: {note_id}")
        if source_id in live_sources:
            raise RefreshError(f"live Goethe notes have duplicate SourceID: {source_id}")
        live_sources[source_id] = note_id
        cards = sorted(int(value) for value in note.get("cards", []))
        if len(cards) != 2 or len(set(cards)) != 2:
            raise RefreshError(f"expected exactly two linked cards: note {note_id}")
        live_card_ids.extend(cards)
        note_card_contract[str(note_id)] = {"source_id": source_id, "card_ids": cards}
        try:
            current = json.loads(fields.get(TARGET_FIELD, ""))
        except json.JSONDecodeError as exc:
            raise RefreshError(f"live target spans are invalid JSON: note {note_id}") from exc
        current = validate_spans(current, fields, f"live target spans for note {note_id}")
        try:
            desired = json.loads(highlights.build_spans(fields))
        except (json.JSONDecodeError, highlights.HighlightError) as exc:
            raise RefreshError(f"cannot recompute target spans: note {note_id}: {exc}") from exc
        desired = validate_spans(desired, fields, f"recomputed target spans for note {note_id}")
        if current != desired:
            changed[note_id] = (current, desired)
    if len(live_card_ids) != len(set(live_card_ids)):
        raise RefreshError("live Goethe notes contain duplicate linked card IDs")

    repairs = manifest["repairs"]
    reviewed_ids = {int(item["note_id"]) for item in repairs}
    if set(changed) != reviewed_ids:
        missing = sorted(reviewed_ids - set(changed))
        extra = sorted(set(changed) - reviewed_ids)
        raise RefreshError(
            f"live changed-note set differs from review; missing={missing[:5]} extra={extra[:5]}"
        )
    for item in repairs:
        note_id = int(item["note_id"])
        note = by_id.get(note_id)
        if note is None:
            raise RefreshError(f"reviewed note is absent from live collection: {note_id}")
        fields = note_fields(note)
        source_id = item["source_id"]
        if live_sources.get(source_id) != note_id:
            raise RefreshError(f"reviewed SourceID/note_id mismatch: {source_id}")
        cards = sorted(int(value) for value in note.get("cards", []))
        if cards != item["card_ids"]:
            raise RefreshError(f"reviewed card IDs differ: {source_id}")
        if fields.get("Lemma", "") != item["lemma"]:
            raise RefreshError(f"reviewed lemma differs: {source_id}")
        before, after = changed[note_id]
        validate_spans(item["before"], fields, f"reviewed before spans for {source_id}")
        validate_spans(item["after"], fields, f"reviewed after spans for {source_id}")
        if before != item["before"] or after != item["after"]:
            raise RefreshError(f"reviewed span delta differs: {source_id}")

    return {
        "schema_version": 1,
        "created_utc": now_utc(),
        "review_manifest_sha256": hash_file(MANIFEST_PATH),
        "target_templates_sha256": canonical_hash(source_templates()),
        "changed_notes": len(repairs),
        "changed_examples": sum(
            left != right
            for item in repairs
            for left, right in zip(item["before"], item["after"])
        ),
        "live_note_ids": sorted(by_id),
        "live_card_ids": sorted(live_card_ids),
        "note_card_contract_sha256": canonical_hash(note_card_contract),
        "repairs": repairs,
    }


def write_plan(plan: dict[str, Any]) -> None:
    atomic_json(STATE / "plan.json", {
        "schema_version": 1,
        "plan": plan,
        "plan_sha256": canonical_hash(plan),
    })


def load_plan(*, require_sources: bool = True) -> tuple[dict[str, Any], str]:
    envelope = read_json(STATE / "plan.json", "audit plan")
    if envelope.get("schema_version") != 1 or not isinstance(envelope.get("plan"), dict):
        raise RefreshError("unsupported audit plan schema")
    plan = envelope["plan"]
    plan_hash = envelope.get("plan_sha256")
    if not isinstance(plan_hash, str) or plan_hash != canonical_hash(plan):
        raise RefreshError("audit plan hash is invalid")
    if plan.get("schema_version") != 1:
        raise RefreshError("unsupported audit plan payload")
    if plan.get("changed_notes") != EXPECTED_CHANGED_NOTES or plan.get("changed_examples") != EXPECTED_CHANGED_EXAMPLES:
        raise RefreshError("audit plan changed-count contract is invalid")
    repairs = plan.get("repairs")
    if not isinstance(repairs, list) or len(repairs) != EXPECTED_CHANGED_NOTES:
        raise RefreshError("audit plan repair inventory is invalid")
    if require_sources:
        if plan.get("review_manifest_sha256") != hash_file(MANIFEST_PATH):
            raise RefreshError("review manifest changed after audit")
        manifest = load_manifest()
        if repairs != manifest["repairs"]:
            raise RefreshError("audit plan repairs differ from the review manifest")
        if plan.get("target_templates_sha256") != canonical_hash(source_templates()):
            raise RefreshError("repository templates changed after audit")
    return plan, plan_hash


def model_snapshot() -> dict[str, Any]:
    fields = anki("modelFieldNames", modelName=MODEL)
    if not isinstance(fields, list) or TARGET_FIELD not in fields:
        raise RefreshError(f"model schema does not contain {TARGET_FIELD}")
    templates = anki("modelTemplates", modelName=MODEL)
    if not isinstance(templates, dict) or not templates:
        raise RefreshError("modelTemplates returned an empty/non-object value")
    styling = anki("modelStyling", modelName=MODEL)
    if isinstance(styling, dict):
        styling = styling.get("css")
    if not isinstance(styling, str):
        raise RefreshError("modelStyling returned no CSS text")
    return {"fields": list(fields), "templates": templates, "styling": styling}


def fetch_cards(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linked = sorted(int(card_id) for note in notes for card_id in note.get("cards", []))
    if len(linked) != len(set(linked)):
        raise RefreshError("notesInfo returned duplicate linked card IDs")
    cards: list[dict[str, Any]] = []
    for batch in chunks(linked):
        result = anki("cardsInfo", cards=batch)
        if not isinstance(result, list):
            raise RefreshError("cardsInfo returned a non-list")
        cards.extend(result)
    returned = [int(card["cardId"]) for card in cards]
    if len(returned) != len(set(returned)) or set(returned) != set(linked):
        raise RefreshError("cardsInfo returned a different card ID set")
    outside_backup = [
        int(card["cardId"]) for card in cards
        if str(card.get("deckName", "")) != PARENT_DECK
        and not str(card.get("deckName", "")).startswith(PARENT_DECK + "::")
    ]
    if outside_backup:
        raise RefreshError(
            f"model cards fall outside the APKG deck tree: {outside_backup[:5]}"
        )
    by_note: dict[int, list[dict[str, Any]]] = {}
    for card in cards:
        by_note.setdefault(int(card["note"]), []).append(card)
    for note in notes:
        note_id = int(note["noteId"])
        expected = sorted(int(value) for value in note.get("cards", []))
        actual_cards = by_note.get(note_id, [])
        actual = sorted(int(card["cardId"]) for card in actual_cards)
        if actual != expected:
            raise RefreshError(f"note/card linkage drift: note {note_id}")
        if sorted(int(card.get("ord", -1)) for card in actual_cards) != [0, 1]:
            raise RefreshError(f"expected exact card ords 0/1: note {note_id}")
    cards.sort(key=lambda card: int(card["cardId"]))
    return cards


def suspensions(cards: list[dict[str, Any]]) -> dict[str, bool]:
    ids = [int(card["cardId"]) for card in cards]
    result: dict[str, bool] = {}
    for batch in chunks(ids):
        values = anki("areSuspended", cards=batch)
        if not isinstance(values, list) or len(values) != len(batch) or any(value is None for value in values):
            raise RefreshError("areSuspended returned an invalid result")
        result.update({str(card_id): bool(value) for card_id, value in zip(batch, values)})
    return result


def reviews(cards: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [int(card["cardId"]) for card in cards]
    result: dict[str, Any] = {}
    for batch in chunks(ids):
        values = anki("getReviewsOfCards", cards=batch)
        if not isinstance(values, dict):
            raise RefreshError("getReviewsOfCards returned a non-object")
        result.update({str(key): value for key, value in values.items()})
    expected = {str(card_id) for card_id in ids}
    if set(result) != expected:
        raise RefreshError("getReviewsOfCards returned a different card ID set")
    return {key: result[key] for key in sorted(result, key=int)}


def live_inventory(plan: dict[str, Any]) -> dict[str, Any]:
    notes = fetch_notes()
    model = model_snapshot()
    field_names = model["fields"]
    note_by_id = {int(note["noteId"]): note for note in notes}
    target_ids = {int(item["note_id"]) for item in plan["repairs"]}
    if not target_ids <= set(note_by_id):
        raise RefreshError("a reviewed target note disappeared")
    note_contract: dict[str, Any] = {}
    for note in notes:
        note_id = int(note["noteId"])
        raw_fields = note.get("fields")
        if not isinstance(raw_fields, dict) or set(raw_fields) != set(field_names):
            raise RefreshError(f"live note field schema differs: {note_id}")
        cards = sorted(int(value) for value in note.get("cards", []))
        note_contract[str(note_id)] = {
            "source_id": field_value(note, "SourceID"), "card_ids": cards,
        }
    target_notes: dict[str, Any] = {}
    for note_id in sorted(target_ids):
        note = note_by_id[note_id]
        target_notes[str(note_id)] = {
            "model": str(note.get("modelName", "")),
            "fields": {name: field_value(note, name) for name in field_names},
            "tags": [str(tag) for tag in note.get("tags", [])],
            "cards": sorted(int(value) for value in note.get("cards", [])),
        }
    cards = fetch_cards(notes)
    suspended = suspensions(cards)
    review_state = reviews(cards)
    card_state = {
        str(int(card["cardId"])): {
            "schedule": {name: card.get(name) for name in SCHEDULE_KEYS},
            "suspended": suspended[str(int(card["cardId"]))],
        }
        for card in cards
    }
    incomplete = [
        int(card["cardId"]) for card in cards
        if any(name not in card for name in SCHEDULE_KEYS)
    ]
    if incomplete:
        raise RefreshError(f"cardsInfo omitted scheduling fields: {incomplete[:5]}")
    return {
        "note_ids": sorted(note_by_id),
        "card_ids": [int(card["cardId"]) for card in cards],
        "note_card_contract_sha256": canonical_hash(note_contract),
        "target_notes": target_notes,
        "cards": card_state,
        "reviews": review_state,
        "reviews_sha256": canonical_hash(review_state),
        "model": model,
    }


def split_highlighter(template: str, label: str) -> tuple[str, str, str]:
    start = "</main>\n<script>\n"
    end = "\n</script>\n<script>\n"
    if template.count(start) != 1:
        raise RefreshError(f"{label} has no unique target-highlighter start marker")
    prefix, remainder = template.split(start, 1)
    if end not in remainder:
        raise RefreshError(f"{label} has no target-highlighter end marker")
    highlighter, suffix = remainder.split(end, 1)
    return prefix + start, highlighter, end + suffix


def validate_template_preimage(live_templates: Any) -> None:
    target = source_templates()
    if not isinstance(live_templates, dict) or set(live_templates) != set(target):
        raise RefreshError("live template inventory differs from repository")
    for name, expected in target.items():
        current = live_templates.get(name)
        if (
            not isinstance(current, dict) or set(current) != set(expected)
            or current.get("Front") != expected.get("Front")
        ):
            raise RefreshError(f"unrelated live template drift: {name}")
        current_prefix, _, current_suffix = split_highlighter(
            str(current.get("Back", "")), f"live template {name}",
        )
        target_prefix, _, target_suffix = split_highlighter(
            str(expected.get("Back", "")), f"repository template {name}",
        )
        if current_prefix != target_prefix or current_suffix != target_suffix:
            raise RefreshError(f"unrelated live template drift: {name}")


def validate_plan_preimage(plan: dict[str, Any], inventory: dict[str, Any]) -> None:
    if inventory["note_ids"] != plan.get("live_note_ids"):
        raise RefreshError("live note inventory changed after audit")
    if inventory["card_ids"] != plan.get("live_card_ids"):
        raise RefreshError("live card inventory changed after audit")
    if inventory["note_card_contract_sha256"] != plan.get("note_card_contract_sha256"):
        raise RefreshError("live SourceID/card linkage changed after audit")
    validate_template_preimage(inventory["model"].get("templates"))
    for item in plan["repairs"]:
        note_id = str(int(item["note_id"]))
        current = inventory["target_notes"].get(note_id)
        if current is None:
            raise RefreshError(f"reviewed note missing from inventory: {note_id}")
        if current["model"] != MODEL:
            raise RefreshError(f"reviewed note model changed: {note_id}")
        fields = current["fields"]
        if fields.get("SourceID") != item["source_id"] or fields.get("Lemma") != item["lemma"]:
            raise RefreshError(f"reviewed note identity changed: {note_id}")
        if current["cards"] != item["card_ids"]:
            raise RefreshError(f"reviewed card linkage changed: {note_id}")
        try:
            spans = json.loads(fields.get(TARGET_FIELD, ""))
        except json.JSONDecodeError as exc:
            raise RefreshError(f"reviewed live spans are invalid JSON: {note_id}") from exc
        if spans != item["before"]:
            raise RefreshError(f"reviewed live span preimage changed: {note_id}")


def export_backup() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = STATE / f"Goethe_Institute_pre_target_highlight_refresh_{stamp}.apkg"
    if path.exists():
        raise RefreshError(f"backup destination already exists: {path}")
    timed_out = False
    try:
        result = anki(
            "exportPackage", deck=PARENT_DECK, path=path.resolve().as_posix(),
            includeSched=True,
        )
    except RefreshError as exc:
        message = str(exc).casefold()
        if "timed out" not in message and "timeout" not in message:
            raise
        # Anki can keep flushing a large export after the HTTP transport times
        # out.  The archive validator, not the transport result, is the gate.
        timed_out = True
        result = False
    if not result and not timed_out:
        raise RefreshError(f"scheduling-preserving APKG export failed: {path}")
    wait_timeout = 180.0 if timed_out else 60.0
    if not apkg.wait_for_valid_apkg(path, timeout=wait_timeout):
        raise RefreshError(f"scheduling-preserving APKG export failed: {path}")
    return path


def command_backup(_: argparse.Namespace) -> None:
    require_version()
    plan, plan_hash = load_plan()
    inventory = live_inventory(plan)
    validate_plan_preimage(plan, inventory)
    backup = export_backup()
    if live_inventory(plan) != inventory:
        raise RefreshError("live collection changed during backup")
    snapshot = {
        "schema_version": 1,
        "created_utc": now_utc(),
        "plan_sha256": plan_hash,
        "backup": str(backup.resolve()),
        "backup_sha256": hash_file(backup),
        "inventory_sha256": canonical_hash(inventory),
        "inventory": inventory,
    }
    atomic_json(STATE / "snapshot.json", snapshot)
    try:
        (STATE / "result.json").unlink(missing_ok=True)
    except OSError as exc:
        raise RefreshError("cannot clear stale refresh result") from exc
    print_json({
        "status": "BACKUP_PASS", "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
        "target_notes": len(inventory["target_notes"]),
        "cards": len(inventory["cards"]),
    })


def load_snapshot(plan: dict[str, Any], plan_hash: str) -> dict[str, Any]:
    snapshot = read_json(STATE / "snapshot.json", "backup snapshot")
    if snapshot.get("schema_version") != 1:
        raise RefreshError("unsupported backup snapshot schema")
    if snapshot.get("plan_sha256") != plan_hash:
        raise RefreshError("backup snapshot belongs to another audit plan")
    backup_value = snapshot.get("backup")
    if not isinstance(backup_value, str) or not backup_value:
        raise RefreshError("backup snapshot has no APKG path")
    backup = Path(backup_value)
    if not apkg.valid_apkg(backup):
        raise RefreshError(f"snapshot APKG is missing or invalid: {backup}")
    if snapshot.get("backup_sha256") != hash_file(backup):
        raise RefreshError("snapshot APKG SHA-256 changed")
    inventory = snapshot.get("inventory")
    if not isinstance(inventory, dict):
        raise RefreshError("backup snapshot has no live inventory")
    if snapshot.get("inventory_sha256") != canonical_hash(inventory):
        raise RefreshError("backup snapshot inventory hash is invalid")
    if inventory.get("reviews_sha256") != canonical_hash(inventory.get("reviews")):
        raise RefreshError("backup snapshot review hash is inconsistent")
    if not isinstance(inventory.get("target_notes"), dict) or len(inventory["target_notes"]) != EXPECTED_CHANGED_NOTES:
        raise RefreshError("backup snapshot target-note inventory is invalid")
    validate_plan_preimage(plan, inventory)
    return snapshot


def require_baseline(snapshot: dict[str, Any], inventory: dict[str, Any]) -> None:
    if inventory != snapshot["inventory"]:
        raise RefreshError("live preimage changed after backup")


def update_spans(plan: dict[str, Any]) -> int:
    actions = [
        {
            "action": "updateNoteFields",
            "version": 6,
            "params": {"note": {
                "id": int(item["note_id"]),
                "fields": {TARGET_FIELD: compact_spans(item["after"])},
            }},
        }
        for item in plan["repairs"]
    ]
    for batch in chunks(actions, 40):
        result = anki("multi", actions=batch)
        if not isinstance(result, list) or len(result) != len(batch):
            raise RefreshError("Anki multi returned an unexpected span-update result")
        errors = [
            item.get("error") for item in result
            if isinstance(item, dict) and item.get("error")
        ]
        if errors or any(not isinstance(item, dict) for item in result):
            raise RefreshError(f"target-span update failed: {errors[:3]}")
    return len(actions)


def verify_applied(
    plan: dict[str, Any], snapshot: dict[str, Any], inventory: dict[str, Any],
) -> None:
    baseline = snapshot["inventory"]
    for name in (
        "note_ids", "card_ids", "note_card_contract_sha256", "cards",
        "reviews", "reviews_sha256",
    ):
        if inventory.get(name) != baseline.get(name):
            raise RefreshError(f"post-refresh {name} invariant changed")
    if inventory["model"]["fields"] != baseline["model"]["fields"]:
        raise RefreshError("post-refresh model schema changed")
    if inventory["model"]["styling"] != baseline["model"]["styling"]:
        raise RefreshError("post-refresh model styling changed")
    templates = source_templates()
    if canonical_hash(templates) != plan["target_templates_sha256"]:
        raise RefreshError("repository templates changed during refresh")
    if inventory["model"]["templates"] != templates:
        raise RefreshError("post-refresh model templates differ from repository")
    if set(inventory["target_notes"]) != set(baseline["target_notes"]):
        raise RefreshError("post-refresh target-note inventory changed")
    repairs = {str(int(item["note_id"])): item for item in plan["repairs"]}
    for note_id, before in baseline["target_notes"].items():
        current = inventory["target_notes"][note_id]
        for name in ("model", "tags", "cards"):
            if current[name] != before[name]:
                raise RefreshError(f"post-refresh note {name} changed: {note_id}")
        if set(current["fields"]) != set(before["fields"]):
            raise RefreshError(f"post-refresh note field schema changed: {note_id}")
        for field_name, value in before["fields"].items():
            expected = value
            if field_name == TARGET_FIELD:
                expected = compact_spans(repairs[note_id]["after"])
            if current["fields"].get(field_name) != expected:
                kind = "target spans" if field_name == TARGET_FIELD else "untouched field"
                raise RefreshError(
                    f"post-refresh {kind} changed unexpectedly: note={note_id} field={field_name}"
                )


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise RefreshError(f"confirmation must equal {APPLY_CONFIRMATION}")
    require_version()
    plan, plan_hash = load_plan()
    snapshot = load_snapshot(plan, plan_hash)
    inventory = live_inventory(plan)
    require_baseline(snapshot, inventory)
    try:
        changed_notes = update_spans(plan)
        templates = source_templates()
        if canonical_hash(templates) != plan["target_templates_sha256"]:
            raise RefreshError("repository templates changed during apply")
        anki(
            "updateModelTemplates",
            model={"name": MODEL, "templates": templates},
        )
        inventory = live_inventory(plan)
        verify_applied(plan, snapshot, inventory)
    except Exception as exc:
        raise RefreshError(
            f"apply failed (inspect state, then use guarded rollback): {exc}"
        ) from exc
    result = {
        "schema_version": 1,
        "status": "applied",
        "applied_utc": now_utc(),
        "plan_sha256": plan_hash,
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
        "changed_notes": changed_notes,
        "changed_examples": plan["changed_examples"],
    }
    atomic_json(STATE / "result.json", result)
    print_json({
        "status": "APPLY_PASS", "changed_notes": changed_notes,
        "changed_examples": plan["changed_examples"],
    })


def load_result(plan_hash: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    result = read_json(STATE / "result.json", "refresh result")
    if result.get("schema_version") != 1 or result.get("status") != "applied":
        raise RefreshError("refresh result is not in applied state")
    if result.get("plan_sha256") != plan_hash:
        raise RefreshError("refresh result belongs to another audit plan")
    if (
        result.get("backup") != snapshot.get("backup")
        or result.get("backup_sha256") != snapshot.get("backup_sha256")
    ):
        raise RefreshError("refresh result belongs to another backup")
    if result.get("changed_notes") != EXPECTED_CHANGED_NOTES or result.get("changed_examples") != EXPECTED_CHANGED_EXAMPLES:
        raise RefreshError("refresh result changed-count contract is invalid")
    return result


def command_verify(_: argparse.Namespace) -> None:
    require_version()
    plan, plan_hash = load_plan()
    snapshot = load_snapshot(plan, plan_hash)
    load_result(plan_hash, snapshot)
    inventory = live_inventory(plan)
    verify_applied(plan, snapshot, inventory)
    print_json({
        "status": "VERIFY_PASS",
        "changed_notes": plan["changed_notes"],
        "changed_examples": plan["changed_examples"],
        "cards": len(inventory["cards"]),
        "reviews_sha256": inventory["reviews_sha256"],
    })


def validate_rollback_preimage(
    plan: dict[str, Any], snapshot: dict[str, Any], inventory: dict[str, Any],
) -> None:
    baseline = snapshot["inventory"]
    for name in (
        "note_ids", "card_ids", "note_card_contract_sha256", "cards",
        "reviews", "reviews_sha256",
    ):
        if inventory.get(name) != baseline.get(name):
            raise RefreshError(f"rollback {name} invariant changed")
    if inventory["model"]["fields"] != baseline["model"]["fields"]:
        raise RefreshError("rollback model schema changed")
    if inventory["model"]["styling"] != baseline["model"]["styling"]:
        raise RefreshError("rollback model styling changed")
    current_templates = inventory["model"]["templates"]
    old_templates = baseline["model"]["templates"]
    if (
        current_templates != old_templates
        and canonical_hash(current_templates) != plan["target_templates_sha256"]
    ):
        raise RefreshError("rollback found an unrecognized model-template state")
    repairs = {str(int(item["note_id"])): item for item in plan["repairs"]}
    if set(inventory["target_notes"]) != set(baseline["target_notes"]):
        raise RefreshError("rollback target-note inventory changed")
    for note_id, before in baseline["target_notes"].items():
        current = inventory["target_notes"][note_id]
        for name in ("model", "tags", "cards"):
            if current[name] != before[name]:
                raise RefreshError(f"rollback note {name} changed: {note_id}")
        if set(current["fields"]) != set(before["fields"]):
            raise RefreshError(f"rollback note field schema changed: {note_id}")
        for field_name, value in before["fields"].items():
            actual = current["fields"].get(field_name)
            if field_name == TARGET_FIELD:
                allowed = {value, compact_spans(repairs[note_id]["after"])}
                if actual not in allowed:
                    raise RefreshError(f"rollback found unrecognized target spans: {note_id}")
            elif actual != value:
                raise RefreshError(
                    f"rollback found an untouched-field change: note={note_id} field={field_name}"
                )


def restore_snapshot_values(
    plan: dict[str, Any], snapshot: dict[str, Any], inventory: dict[str, Any],
) -> int:
    baseline = snapshot["inventory"]
    actions: list[dict[str, Any]] = []
    for item in plan["repairs"]:
        note_id = str(int(item["note_id"]))
        desired = baseline["target_notes"][note_id]["fields"][TARGET_FIELD]
        current = inventory["target_notes"][note_id]["fields"][TARGET_FIELD]
        if current != desired:
            actions.append({
                "action": "updateNoteFields",
                "version": 6,
                "params": {"note": {
                    "id": int(note_id), "fields": {TARGET_FIELD: desired},
                }},
            })
    for batch in chunks(actions, 40):
        result = anki("multi", actions=batch)
        if not isinstance(result, list) or len(result) != len(batch):
            raise RefreshError("Anki multi returned an unexpected rollback result")
        errors = [
            item.get("error") for item in result
            if isinstance(item, dict) and item.get("error")
        ]
        if errors or any(not isinstance(item, dict) for item in result):
            raise RefreshError(f"target-span rollback failed: {errors[:3]}")
    old_templates = baseline["model"]["templates"]
    if inventory["model"]["templates"] != old_templates:
        anki(
            "updateModelTemplates",
            model={"name": MODEL, "templates": old_templates},
        )
    return len(actions)


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise RefreshError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    require_version()
    plan, plan_hash = load_plan(require_sources=False)
    snapshot = load_snapshot(plan, plan_hash)
    inventory = live_inventory(plan)
    validate_rollback_preimage(plan, snapshot, inventory)
    try:
        restored_notes = restore_snapshot_values(plan, snapshot, inventory)
        inventory = live_inventory(plan)
        if inventory != snapshot["inventory"]:
            raise RefreshError("rollback did not restore the exact backup snapshot")
    except Exception as exc:
        raise RefreshError(f"rollback failed: {exc}") from exc
    atomic_json(STATE / "result.json", {
        "schema_version": 1,
        "status": "rolled_back",
        "rolled_back_utc": now_utc(),
        "plan_sha256": plan_hash,
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
        "restored_notes": restored_notes,
    })
    print_json({
        "status": "ROLLBACK_PASS", "restored_notes": restored_notes,
        "cards": len(inventory["cards"]),
    })


def print_json(value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        sys.stdout.write(payload + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def command_audit(_: argparse.Namespace) -> None:
    require_version()
    manifest = load_manifest()
    plan = build_audit_plan(fetch_notes(), manifest)
    write_plan(plan)
    print_json({
        "status": "AUDIT_PASS",
        "changed_notes": plan["changed_notes"],
        "changed_examples": plan["changed_examples"],
        "plan": str((STATE / "plan.json").resolve()),
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("audit").set_defaults(func=command_audit)
    subparsers.add_parser("backup").set_defaults(func=command_backup)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    subparsers.add_parser("verify").set_defaults(func=command_verify)
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RefreshError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: unexpected refresh failure: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
