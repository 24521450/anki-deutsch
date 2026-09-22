"""Guarded live refresh of Goethe Werkstatt model templates and styling.

The historical template upgrade also adds model fields and manages reviewed
production suspensions.  The current collection already has that schema, so
this command updates only the model templates/style and proves that note/card
state did not change.

Typical sequence::

    python tools/goethe_template_refresh.py audit
    python tools/goethe_template_refresh.py backup
    python tools/goethe_template_refresh.py apply --confirmation APPLY_GOETHE_TEMPLATE_REFRESH
    python tools/goethe_template_refresh.py verify

Rollback is explicit::

    python tools/goethe_template_refresh.py rollback --confirmation ROLLBACK_GOETHE_TEMPLATE_REFRESH
"""
from __future__ import annotations

import argparse
import copy
import gc
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_apkg as apkg
import goethe_template_upgrade as guard


ROOT = guard.ROOT
STATE = ROOT / "tools" / ".goethe_template_refresh"
PLAN_PATH = STATE / "plan.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
RESULT_PATH = STATE / "result.json"
MODEL = guard.MODEL
PARENT_DECK = guard.PARENT_DECK
APPLY_CONFIRMATION = "APPLY_GOETHE_TEMPLATE_REFRESH"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_TEMPLATE_REFRESH"
EXPECTED_TEMPLATE_DELTA = [
    {"template": "English \u2192 German", "side": "Back"},
    {"template": "English \u2192 German", "side": "Front"},
    {"template": "German \u2192 English", "side": "Back"},
    {"template": "German \u2192 English", "side": "Front"},
]


class RefreshError(RuntimeError):
    """Raised when a live template refresh cannot be proven safe."""


def _require_live() -> None:
    guard.require_version()
    guard.require_actions()


def build_plan(inventory: dict[str, Any]) -> dict[str, Any]:
    fields = list(inventory["model"]["fields"])
    configured = list(getattr(guard.gw, "FIELDS", []))
    if fields != configured:
        raise RefreshError(
            "template refresh requires the exact current model schema: "
            f"expected {configured}, got {fields}"
        )
    templates = guard.expected_templates()
    css = guard.target_css()
    current_model = inventory["model"]
    if current_model["styling"] == css:
        raise RefreshError("dictionary-link styling change is missing from the source design")
    current_templates = current_model["templates"]
    if set(current_templates) != set(templates):
        raise RefreshError("live and target template sets differ")
    changed_templates = [
        {"template": template_name, "side": side}
        for template_name in sorted(templates)
        for side in ("Back", "Front")
        if current_templates[template_name].get(side) != templates[template_name].get(side)
    ]
    if any(item not in EXPECTED_TEMPLATE_DELTA for item in changed_templates):
        raise RefreshError(
            "template refresh contains an unexpected card-face delta: "
            f"got {changed_templates}"
        )
    return {
        "schema_version": 1,
        "model": MODEL,
        "fields": fields,
        "target_templates_hash": guard.canonical_hash(templates),
        "target_styling_hash": guard.canonical_hash(css),
        "changed_templates": changed_templates,
    }


def _load_plan() -> dict[str, Any]:
    try:
        plan = guard.load_json(PLAN_PATH)
    except guard.UpgradeError as exc:
        raise RefreshError(str(exc)) from exc
    if plan.get("schema_version") != 1 or plan.get("model") != MODEL:
        raise RefreshError("unsupported template-refresh plan")
    if not isinstance(plan.get("fields"), list) or plan["fields"] != list(plan["fields"]):
        raise RefreshError("template-refresh plan has invalid model fields")
    configured = list(getattr(guard.gw, "FIELDS", []))
    if plan["fields"] != configured:
        raise RefreshError("template-refresh plan schema differs from source schema")
    for key in ("target_templates_hash", "target_styling_hash"):
        if not isinstance(plan.get(key), str) or not plan[key]:
            raise RefreshError(f"template-refresh plan has no {key}")
    changed_templates = plan.get("changed_templates")
    if not isinstance(changed_templates, list) or any(item not in EXPECTED_TEMPLATE_DELTA for item in changed_templates):
        raise RefreshError("template-refresh plan has an unexpected template delta")
    return plan


def _load_snapshot() -> dict[str, Any]:
    try:
        snapshot = guard.load_json(SNAPSHOT_PATH)
    except guard.UpgradeError as exc:
        raise RefreshError(str(exc)) from exc
    if snapshot.get("schema_version") != 1:
        raise RefreshError("unsupported template-refresh snapshot")
    backup = Path(str(snapshot.get("backup", "")))
    if not backup.exists() or not apkg.valid_apkg(backup):
        raise RefreshError(f"template-refresh APKG backup missing or invalid: {backup}")
    if apkg.hash_file(backup) != snapshot.get("backup_sha256"):
        raise RefreshError("template-refresh APKG SHA-256 changed")
    return snapshot


def _target_design(plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    templates = guard.expected_templates()
    css = guard.target_css()
    if guard.canonical_hash(templates) != plan["target_templates_hash"]:
        raise RefreshError("target template source changed after audit/backup")
    if guard.canonical_hash(css) != plan["target_styling_hash"]:
        raise RefreshError("target styling source changed after audit/backup")
    return templates, css


def _export_backup() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = STATE / f"Goethe_Institute_pre_template_refresh_{stamp}.apkg"
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise RefreshError(f"cannot clear pending backup path: {path}") from exc

    export_error: guard.UpgradeError | None = None
    try:
        result = guard.anki(
            "exportPackage",
            deck=PARENT_DECK,
            path=path.resolve().as_posix(),
            includeSched=True,
        )
    except guard.UpgradeError as exc:
        export_error = exc
        # Anki may keep exporting after the HTTP client times out.  Accept
        # only that specific asynchronous case; all other action failures
        # remain a hard stop.
        if "timed out" not in str(exc).casefold():
            raise RefreshError(str(exc)) from exc
        result = False

    attempts = 720 if export_error is not None else 60
    for _ in range(attempts):
        if path.exists() and apkg.valid_apkg(path):
            return path
        time.sleep(0.25)
    if not result or not path.exists() or not apkg.valid_apkg(path):
        raise RefreshError(f"scheduled APKG backup failed validation: {path}")
    return path


def _compare_inventory(
    snapshot: dict[str, Any],
    current: dict[str, Any],
    *,
    expected_model: dict[str, Any] | None = None,
) -> None:
    expected = expected_model or snapshot["model"]
    if current["model"] != expected:
        raise RefreshError("model fields/templates/styling differ from expected state")
    if set(current["notes"]) != set(snapshot["notes"]):
        raise RefreshError("live note ID set changed since template-refresh snapshot")
    if set(current["cards"]) != set(snapshot["cards"]):
        raise RefreshError("live card ID set changed since template-refresh snapshot")
    for note_id, before in snapshot["notes"].items():
        now = current["notes"][note_id]
        for key in ("model", "fields", "tags", "cards", "source_id"):
            if now[key] != before[key]:
                raise RefreshError(f"note state changed: {note_id} ({key})")
    for card_id, before in snapshot["cards"].items():
        now = current["cards"][card_id]
        if now != before:
            raise RefreshError(f"card state changed: {card_id}")
    if current["reviews"] != snapshot["reviews"]:
        raise RefreshError("review history changed since template-refresh snapshot")
    if current["reviews_sha256"] != snapshot["reviews_sha256"]:
        raise RefreshError("review history hash changed since template-refresh snapshot")
    if current["deck_counts"] != snapshot["deck_counts"]:
        raise RefreshError("deck counts changed since template-refresh snapshot")


def _live_inventory() -> dict[str, Any]:
    """Capture template-refresh state without retaining rendered card HTML.

    AnkiConnect's cardsInfo includes both rendered sides. Once the dictionary
    controller is present, retaining all 6,842 responses can use several GB of
    memory. Keep each response batch bounded and store only scheduling data.
    """
    fields = guard.model_fields()
    notes = guard.fetch_notes()
    linked_ids = [int(card_id) for note in notes for card_id in note.get("cards", [])]
    if len(linked_ids) != len(set(linked_ids)):
        raise RefreshError("notesInfo returned duplicate card IDs")
    card_ids = sorted(linked_ids)
    card_rows: list[dict[str, Any]] = []
    card_state: dict[str, dict[str, Any]] = {}
    by_note: dict[int, list[dict[str, Any]]] = {}
    for batch in guard.chunks(card_ids, 100):
        details = guard.anki("cardsInfo", cards=batch)
        if not isinstance(details, list) or len(details) != len(batch):
            raise RefreshError("cardsInfo returned an unexpected card set")
        batch_ids = [int(card["cardId"]) for card in details]
        if len(batch_ids) != len(set(batch_ids)) or set(batch_ids) != set(batch):
            raise RefreshError("cardsInfo returned a different card ID set")
        for card in details:
            card_id = int(card["cardId"])
            note_id = int(card["note"])
            summary = {
                "cardId": card_id,
                "note": note_id,
                "ord": int(card.get("ord", -1)),
                "deckName": str(card.get("deckName", "")),
            }
            card_rows.append(summary)
            by_note.setdefault(note_id, []).append(summary)
            card_state[str(card_id)] = {
                "schedule": guard.schedule_projection(card),
                "suspended": False,
                "note": note_id,
                "ord": summary["ord"],
                "deckName": summary["deckName"],
            }
        del details
        gc.collect()

    if set(card_state) != {str(card_id) for card_id in card_ids}:
        raise RefreshError("cardsInfo returned a different card ID set")
    for note in notes:
        note_id = int(note["noteId"])
        linked = sorted(int(card_id) for card_id in note.get("cards", []))
        actual_cards = by_note.get(note_id, [])
        actual = sorted(int(card["cardId"]) for card in actual_cards)
        ords = sorted(int(card["ord"]) for card in actual_cards)
        if linked != actual or ords != [0, 1]:
            raise RefreshError(f"note/card linkage drift: note {note_id}")

    suspensions = guard.suspension_map(card_rows)
    for card_id, suspended in suspensions.items():
        card_state[card_id]["suspended"] = suspended
    reviews = guard.reviews_map(card_rows)
    note_state = {
        str(int(note["noteId"])): {
            "model": str(note.get("modelName", "")),
            "fields": guard.note_fields(note, fields),
            "tags": guard.note_tags(note),
            "cards": sorted(int(card_id) for card_id in note.get("cards", [])),
            "source_id": guard.field_value(note, "SourceID"),
        }
        for note in notes
    }
    return {
        "created_utc": guard.now_utc(),
        "model": guard.model_snapshot(),
        "notes": note_state,
        "cards": card_state,
        "reviews": reviews,
        "reviews_sha256": guard.canonical_hash(reviews),
        "deck_counts": guard.deck_counts(),
    }


def _card_detail_batches(inventory: dict[str, Any]):
    card_ids = [int(card_id) for card_id in inventory["cards"]]
    expected = set(card_ids)
    seen: set[int] = set()
    for batch in guard.chunks(card_ids, 100):
        response = guard.anki("cardsInfo", cards=batch)
        if not isinstance(response, list) or len(response) != len(batch):
            raise RefreshError("cardsInfo returned an unexpected card set")
        batch_ids = [int(card["cardId"]) for card in response]
        if len(batch_ids) != len(set(batch_ids)) or set(batch_ids) != set(batch):
            raise RefreshError("cardsInfo returned a different card ID set")
        seen.update(batch_ids)
        guard.validate_rendered_cards(response)
        yield response
        del response
        gc.collect()
    if seen != expected:
        raise RefreshError("cardsInfo returned a different card ID set")


def _validate_rendered_contract(
    inventory: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    templates, _ = _target_design(plan)
    english_front = templates["English \u2192 German"]["Front"]
    if "{{type:Lemma}}" not in english_front:
        raise RefreshError("English production front has no native type comparison")
    if re.search(r'<input\s+id=["\']typeans\b', english_front, flags=re.I):
        raise RefreshError("English production front must use Anki's native type input")

    note_enabled = {
        note_id: item["fields"].get("ProductionEnabled") == "1"
        for note_id, item in inventory["notes"].items()
    }
    for batch in _card_detail_batches(inventory):
        for card in batch:
            if int(card.get("ord", -1)) != 1:
                continue
            note_id = str(int(card["note"]))
            enabled = note_enabled.get(note_id)
            if enabled is None:
                raise RefreshError(f"rendered card refers to unknown note: {card['cardId']}")
            question = str(card.get("question", ""))
            folded_question = question.casefold()
            has_native_type = any(
                marker in folded_question
                for marker in ("[anki:type:", "[[type:", "{{type:")
            )
            has_type_input = bool(re.search(r"<input\s+id=[\"']typeans\b", question, flags=re.I))
            if enabled and not (has_native_type or has_type_input):
                raise RefreshError(f"active production card has invalid answer input: {card['cardId']}")
            if not enabled and (has_native_type or has_type_input):
                raise RefreshError(f"disabled production card exposes answer input: {card['cardId']}")


def command_audit(_: argparse.Namespace) -> None:
    _require_live()
    inventory = _live_inventory()
    plan = build_plan(inventory)
    guard.atomic_json(PLAN_PATH, plan)
    RESULT_PATH.unlink(missing_ok=True)
    guard.print_json({
        "status": "AUDIT_PASS",
        "notes": len(inventory["notes"]),
        "cards": len(inventory["cards"]),
        "model_fields": plan["fields"],
        "plan": str(PLAN_PATH),
    })


def command_backup(_: argparse.Namespace) -> None:
    _require_live()
    plan = _load_plan()
    inventory = _live_inventory()
    current = build_plan(inventory)
    if guard.canonical_hash(plan) != guard.canonical_hash(current):
        raise RefreshError("live model/source target changed since template audit")
    backup = _export_backup()
    snapshot = copy.deepcopy(inventory)
    snapshot.update({
        "schema_version": 1,
        "plan_sha256": guard.canonical_hash(plan),
        "backup": str(backup.resolve()),
        "backup_sha256": apkg.hash_file(backup),
    })
    guard.atomic_json(SNAPSHOT_PATH, snapshot)
    RESULT_PATH.unlink(missing_ok=True)
    guard.print_json({
        "status": "BACKUP_PASS",
        "backup": str(backup.resolve()),
        "backup_sha256": snapshot["backup_sha256"],
        "snapshot": str(SNAPSHOT_PATH),
    })


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise RefreshError(f"confirmation must equal {APPLY_CONFIRMATION}")
    _require_live()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("template-refresh plan changed after backup")
    templates, css = _target_design(plan)
    current = _live_inventory()
    _compare_inventory(snapshot, current)
    guard.anki("updateModelTemplates", model={"name": MODEL, "templates": templates})
    guard.anki("updateModelStyling", model={"name": MODEL, "css": css})
    current = _live_inventory()
    expected_model = {"fields": snapshot["model"]["fields"], "templates": templates, "styling": css}
    _compare_inventory(snapshot, current, expected_model=expected_model)
    _validate_rendered_contract(current, plan)
    guard.atomic_json(RESULT_PATH, {
        "schema_version": 1,
        "status": "applied",
        "applied_utc": guard.now_utc(),
        "plan_sha256": guard.canonical_hash(plan),
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
    })
    guard.print_json({"status": "APPLY_PASS", "notes": len(current["notes"]), "cards": len(current["cards"])})


def command_verify(_: argparse.Namespace) -> None:
    _require_live()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("template-refresh plan changed after backup")
    templates, css = _target_design(plan)
    current = _live_inventory()
    expected_model = {"fields": snapshot["model"]["fields"], "templates": templates, "styling": css}
    _compare_inventory(snapshot, current, expected_model=expected_model)
    _validate_rendered_contract(current, plan)
    guard.print_json({"status": "VERIFY_PASS", "notes": len(current["notes"]), "cards": len(current["cards"])})


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise RefreshError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    _require_live()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("template-refresh plan changed after backup")
    current = _live_inventory()
    _compare_inventory(snapshot, current, expected_model={
        "fields": snapshot["model"]["fields"],
        "templates": guard.expected_templates(),
        "styling": guard.target_css(),
    })
    old_model = snapshot["model"]
    guard.anki("updateModelTemplates", model={"name": MODEL, "templates": old_model["templates"]})
    guard.anki("updateModelStyling", model={"name": MODEL, "css": old_model["styling"]})
    current = _live_inventory()
    _compare_inventory(snapshot, current)
    guard.atomic_json(RESULT_PATH, {
        "schema_version": 1,
        "status": "rolled_back",
        "rolled_back_utc": guard.now_utc(),
        "plan_sha256": guard.canonical_hash(plan),
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
    })
    guard.print_json({"status": "ROLLBACK_PASS", "notes": len(current["notes"]), "cards": len(current["cards"])})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=command_audit)
    sub.add_parser("backup").set_defaults(func=command_backup)
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    sub.add_parser("verify").set_defaults(func=command_verify)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RefreshError as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception as exc:  # fail closed for malformed external state
        print(f"ERROR: unexpected template-refresh failure: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
