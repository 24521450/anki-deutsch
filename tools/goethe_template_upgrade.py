"""Guarded rollout for the Goethe Werkstatt template/schema upgrade.

The command is deliberately separate from the historical migration scripts.  It
only updates the four additive fields, the existing model templates/styling, and
the explicitly reviewed production cards.  It never opens or edits
``collection.anki2`` directly; all collection operations go through
AnkiConnect.

Typical (live) sequence::

    python tools/goethe_template_upgrade.py audit
    python tools/goethe_template_upgrade.py backup
    python tools/goethe_template_upgrade.py apply --confirmation APPLY_GOETHE_TEMPLATE_UPGRADE
    python tools/goethe_template_upgrade.py verify

Rollback is intentionally explicit::

    python tools/goethe_template_upgrade.py rollback --confirmation ROLLBACK_GOETHE_TEMPLATE_UPGRADE

The policy and target-highlight modules are review-owned.  They are imported
lazily so this tool can still be unit-tested with fakes and so a missing review
module fails closed at the first command that needs it.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
STATE = ROOT / "tools" / ".goethe_template_upgrade"
MODEL = getattr(gw, "MODEL", "Goethe Werkstatt")
PARENT_DECK = "Goethe Institute"

# These are the only fields this rollout is allowed to add/write.  They are
# append-only on purpose: old field indexes remain stable for every existing
# migration/export tool.
NEW_FIELDS = (
    "AcceptedFullAnswersDE",
    "ProductionEnabled",
    "ProductionHint",
    "ExampleTargetSpansJSON",
)

APPLY_CONFIRMATION = "APPLY_GOETHE_TEMPLATE_UPGRADE"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_TEMPLATE_UPGRADE"

SNAPSHOT_PATH = STATE / "snapshot.json"
PLAN_PATH = STATE / "plan.json"
RESULT_PATH = STATE / "result.json"

# ``goethe_werkstatt_migrate.SCHEDULE_KEYS`` intentionally omits volatile
# timestamps.  Keep a local copy so this tool remains correct if the historical
# migration script is later re-baselined for a different collection size.
SCHEDULE_KEYS = (
    "cardId", "note", "ord", "deckName", "factor", "interval", "type",
    "queue", "due", "reps", "lapses", "left", "flags",
)


class UpgradeError(RuntimeError):
    """A fail-closed rollout/verification error."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    """Write state atomically so an interrupted command cannot fake readiness."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def chunks(values: list[Any], size: int = 100) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UpgradeError(f"state file missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UpgradeError(f"invalid JSON state: {path}") from exc


def print_json(value: Any) -> None:
    """Emit human-readable JSON even when the Windows console is cp1252."""

    payload = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        sys.stdout.write(payload + "\n")
    except UnicodeEncodeError:
        # ``ensure_ascii`` is a lossless JSON representation and avoids a
        # console encoding failure for German arrows/umlauts on legacy shells.
        sys.stdout.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def anki(action: str, **params: Any) -> Any:
    """Delegate to the repository's checked AnkiConnect wrapper."""

    try:
        return gw.anki(action, **params)
    except Exception as exc:  # MigrationError is intentionally not required here.
        raise UpgradeError(f"AnkiConnect {action} failed: {exc}") from exc


def require_version() -> None:
    if anki("version") != 6:
        raise UpgradeError("unexpected AnkiConnect API version (expected 6)")


def require_actions() -> None:
    """Fail early when an old AnkiConnect/bridge lacks a required action."""

    required = [
        "findNotes", "notesInfo", "findCards", "cardsInfo", "modelFieldNames",
        "modelTemplates", "modelStyling", "multi", "modelFieldAdd",
        "modelFieldRemove", "updateModelTemplates", "updateModelStyling",
        "updateNoteFields", "suspend", "unsuspend", "areSuspended",
        "exportPackage", "getReviewsOfCards",
    ]
    try:
        reflected = anki("apiReflect", scopes=["actions"], actions=required)
    except UpgradeError:
        # Some old AnkiConnect builds do not expose apiReflect.  The actual
        # action calls below still provide a useful error, so do not make this
        # optional probe a hard blocker.
        return
    available = set(reflected.get("actions", [])) if isinstance(reflected, dict) else set()
    missing = [name for name in required if name not in available]
    if missing:
        raise UpgradeError(f"AnkiConnect actions unavailable: {missing}")


def valid_apkg(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            # Anki 2.1 may use either SQLite filename depending on collection
            # format.  ``testzip`` catches a truncated asynchronous export.
            return bool({"collection.anki2", "collection.anki21"} & names) and archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def export_backup() -> Path:
    """Export a scheduling-preserving APKG and wait for the file to settle."""

    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = STATE / f"Goethe_Institute_pre_template_upgrade_{stamp}.apkg"
    # A stale file must never satisfy a later export attempt.
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise UpgradeError(f"cannot clear pending backup path: {path}") from exc
    # AnkiConnect's implementation in this repository accepts only
    # ``includeSched`` (not the newer includeMedia parameter).
    export_error: UpgradeError | None = None
    try:
        result = anki(
            "exportPackage", deck=PARENT_DECK,
            path=path.resolve().as_posix(), includeSched=True,
        )
    except UpgradeError as exc:
        export_error = exc
        result = False
    # A large collection with media can outlive the HTTP client's 30-second
    # request timeout even though Anki continues writing the requested path.
    # Treat that specific timeout as an asynchronous export and validate the
    # archive instead of discarding a usable backup; fail immediately for all
    # other action errors.
    if export_error is not None and "timed out" not in str(export_error).casefold():
        raise export_error
    # Export can return before Windows has flushed the archive.  Poll briefly
    # (longer after a client timeout), then independently validate the result
    # rather than trusting the boolean response.
    attempts = 720 if export_error is not None else 60
    for _ in range(attempts):
        if path.exists() and valid_apkg(path):
            return path
        time.sleep(0.25)
    if not result or not path.exists() or not valid_apkg(path):
        raise UpgradeError(f"scheduling-preserving APKG export failed: {path}")
    return path


def model_fields() -> list[str]:
    return list(anki("modelFieldNames", modelName=MODEL))


def configured_field_sets() -> tuple[list[str], list[str]]:
    """Return (old, target) field order from the source contract.

    The source contract may already have been updated by the schema work.  We
    remove only the four known additions and require that they are appended,
    never silently accepting an unrelated field or a reordered legacy field.
    """

    configured = list(getattr(gw, "FIELDS", []))
    if not configured:
        raise UpgradeError("goethe_werkstatt_migrate.FIELDS is empty")
    if len(configured) != len(set(configured)):
        raise UpgradeError("source FIELDS contains duplicate names")
    unknown = [name for name in configured if name in NEW_FIELDS]
    old = [name for name in configured if name not in NEW_FIELDS]
    target = old + list(NEW_FIELDS)
    if configured not in (old, target):
        raise UpgradeError(
            "source FIELDS contains an inserted/reordered upgrade field; "
            f"expected old or append-only target, got {configured}"
        )
    # ``unknown`` is intentionally computed above to make the intent explicit;
    # a duplicate addition is still rejected by the target comparison.
    if len(unknown) != len(set(unknown)):
        raise UpgradeError("duplicate upgrade field in source FIELDS")
    return old, target


def source_templates() -> dict[str, Any]:
    try:
        templates = gw.templates()
    except Exception as exc:
        raise UpgradeError(f"cannot build target templates: {exc}") from exc
    if not isinstance(templates, dict) or not templates:
        raise UpgradeError("target templates are empty")
    return templates


def target_css() -> str:
    design = getattr(gw, "DESIGN", ROOT / "design" / "GoetheWerkstatt")
    path = Path(design) / "styling.css"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UpgradeError(f"cannot read target styling: {path}") from exc


def model_snapshot() -> dict[str, Any]:
    styling = anki("modelStyling", modelName=MODEL)
    if isinstance(styling, dict):
        styling = styling.get("css")
    if not isinstance(styling, str):
        raise UpgradeError("modelStyling returned no CSS text")
    return {
        "fields": model_fields(),
        "templates": anki("modelTemplates", modelName=MODEL),
        "styling": styling,
    }


def field_value(note: dict[str, Any], name: str) -> str:
    item = note.get("fields", {}).get(name, {})
    if isinstance(item, dict):
        return str(item.get("value", "") or "")
    return str(item or "")


def note_fields(note: dict[str, Any], names: Iterable[str]) -> dict[str, str]:
    return {name: field_value(note, name) for name in names}


def note_tags(note: dict[str, Any]) -> list[str]:
    # Preserve Anki's serialized order in the snapshot.  Tags are usually
    # treated as a set by Anki, but a rollout must still detect any concurrent
    # note mutation rather than normalising it away.
    return [str(tag) for tag in note.get("tags", [])]


def fetch_notes() -> list[dict[str, Any]]:
    ids = sorted(int(value) for value in anki("findNotes", query=f'note:"{MODEL}"'))
    if len(ids) != len(set(ids)):
        raise UpgradeError("findNotes returned duplicate note IDs")
    notes: list[dict[str, Any]] = []
    for batch in chunks(ids, 250):
        notes.extend(anki("notesInfo", notes=batch))
    notes.sort(key=lambda item: int(item["noteId"]))
    if {int(item["noteId"]) for item in notes} != set(ids):
        raise UpgradeError("notesInfo returned a different note ID set")
    wrong_model = [int(item["noteId"]) for item in notes if item.get("modelName") != MODEL]
    if wrong_model:
        raise UpgradeError(f"notesInfo returned notes from another model: {wrong_model[:5]}")
    return notes


def fetch_cards(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    card_ids = sorted({int(card_id) for note in notes for card_id in note.get("cards", [])})
    cards: list[dict[str, Any]] = []
    for batch in chunks(card_ids, 250):
        cards.extend(anki("cardsInfo", cards=batch))
    cards.sort(key=lambda item: int(item["cardId"]))
    if {int(item["cardId"]) for item in cards} != set(card_ids):
        raise UpgradeError("cardsInfo returned a different card ID set")
    by_note: dict[int, list[dict[str, Any]]] = {}
    for card in cards:
        by_note.setdefault(int(card["note"]), []).append(card)
    for note in notes:
        note_id = int(note["noteId"])
        linked = sorted(int(value) for value in note.get("cards", []))
        actual = sorted(int(card["cardId"]) for card in by_note.get(note_id, []))
        if linked != actual:
            raise UpgradeError(f"note/card linkage drift: note {note_id}")
        ords = sorted(int(card.get("ord", -1)) for card in by_note.get(note_id, []))
        if ords != [0, 1]:
            raise UpgradeError(f"expected exactly ord 0/1 cards: note {note_id}, ords={ords}")
    return cards


def deck_counts() -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for deck in (
        getattr(gw, "A1_DECK", "Goethe Institute::A1 Wordlist"),
        getattr(gw, "A2_DECK", "Goethe Institute::A2 Wordlist"),
        getattr(gw, "B1_DECK", "Goethe Institute::B1 Wordlist"),
    ):
        notes = anki("findNotes", query=f'deck:"{deck}" note:"{MODEL}"')
        cards = anki("findCards", query=f'deck:"{deck}" note:"{MODEL}"')
        result[deck] = {"notes": len(notes), "cards": len(cards)}
    return result


def schedule_projection(card: dict[str, Any]) -> dict[str, Any]:
    return {key: card.get(key) for key in SCHEDULE_KEYS}


def suspension_map(cards: list[dict[str, Any]]) -> dict[str, bool]:
    ids = [int(card["cardId"]) for card in cards]
    values: list[Any] = []
    for batch in chunks(ids, 250):
        batch_values = anki("areSuspended", cards=batch)
        if len(batch_values) != len(batch):
            raise UpgradeError("areSuspended returned an unexpected length")
        values.extend(batch_values)
    if any(value is None for value in values):
        raise UpgradeError("areSuspended returned a missing card")
    return {str(card_id): bool(value) for card_id, value in zip(ids, values)}


def reviews_map(cards: list[dict[str, Any]]) -> dict[str, Any]:
    ids = sorted(int(card["cardId"]) for card in cards)
    result: dict[str, Any] = {}
    for batch in chunks(ids, 250):
        values = anki("getReviewsOfCards", cards=batch)
        if not isinstance(values, dict):
            raise UpgradeError("getReviewsOfCards returned a non-object")
        result.update({str(key): value for key, value in values.items()})
    if set(result) != {str(card_id) for card_id in ids}:
        missing = sorted({str(card_id) for card_id in ids} - set(result), key=int)
        extra = sorted(set(result) - {str(card_id) for card_id in ids}, key=int)
        raise UpgradeError(f"getReviewsOfCards ID mismatch; missing={missing[:5]} extra={extra[:5]}")
    return {key: result[key] for key in sorted(result, key=int)}


def static_card_text(value: Any) -> str:
    """Extract enough visible text to catch empty/unrendered cards."""

    text = str(value or "")
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"\{\{[^}]*\}\}", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def validate_rendered_cards(cards: list[dict[str, Any]]) -> None:
    bad: list[int] = []
    for card in cards:
        question = static_card_text(card.get("question"))
        answer = static_card_text(card.get("answer"))
        raw_question = str(card.get("question", ""))
        raw_answer = str(card.get("answer", ""))
        if not question or not answer:
            bad.append(int(card["cardId"]))
            continue
        if "{{" in raw_question or "{{" in raw_answer:
            bad.append(int(card["cardId"]))
            continue
        if any(token in question.casefold() or token in answer.casefold() for token in ("undefined", "[object object]")):
            bad.append(int(card["cardId"]))
    if bad:
        raise UpgradeError(f"blank/unresolved rendered cards: {bad[:10]}")


def live_inventory() -> dict[str, Any]:
    """Capture the complete mutable surface used by apply/verify."""

    fields = model_fields()
    notes = fetch_notes()
    cards = fetch_cards(notes)
    suspensions = suspension_map(cards)
    reviews = reviews_map(cards)
    note_state: dict[str, Any] = {}
    for note in notes:
        note_id = int(note["noteId"])
        note_state[str(note_id)] = {
            "model": str(note.get("modelName", "")),
            "fields": note_fields(note, fields),
            "tags": note_tags(note),
            "cards": sorted(int(card_id) for card_id in note.get("cards", [])),
            "source_id": field_value(note, "SourceID"),
        }
    card_state: dict[str, Any] = {}
    for card in cards:
        card_id = int(card["cardId"])
        card_state[str(card_id)] = {
            "schedule": schedule_projection(card),
            "suspended": suspensions[str(card_id)],
            "note": int(card["note"]),
            "ord": int(card["ord"]),
            "deckName": str(card.get("deckName", "")),
        }
    return {
        "created_utc": now_utc(),
        "model": model_snapshot(),
        "notes": note_state,
        "cards": card_state,
        "reviews": reviews,
        "reviews_sha256": canonical_hash(reviews),
        "deck_counts": deck_counts(),
    }


def _load_policy_module() -> Any:
    try:
        import goethe_template_policy as module
    except ImportError as exc:
        raise UpgradeError(
            "goethe_template_policy is missing; compile the reviewed production policy first"
        ) from exc
    if not callable(getattr(module, "apply_policy", None)):
        raise UpgradeError("goethe_template_policy.apply_policy is missing")
    return module


def _load_highlight_module() -> Any:
    try:
        import goethe_target_highlights as module
    except ImportError as exc:
        raise UpgradeError(
            "goethe_target_highlights is missing; build reviewed target spans first"
        ) from exc
    # ``build_spans`` is the reviewed public contract.  The first review
    # implementation shipped the more descriptive ``build_target_spans``
    # name; accepting it as a compatibility alias keeps an already-reviewed
    # checkout usable while the API rename lands.  No heuristic fallback is
    # allowed: one of these deterministic builders must be callable.
    if not callable(getattr(module, "build_spans", None)) and not callable(
        getattr(module, "build_target_spans", None)
    ):
        raise UpgradeError(
            "goethe_target_highlights.build_spans is missing "
            "(build_target_spans compatibility alias also absent)"
        )
    return module


def _build_spans(module: Any, fields: dict[str, str]) -> str:
    builder = getattr(module, "build_spans", None)
    if not callable(builder):
        builder = getattr(module, "build_target_spans", None)
    if not callable(builder):  # defensive; _load_highlight_module checks this
        raise UpgradeError("target-span builder is not callable")
    value = builder(fields)
    if not isinstance(value, str):
        raise UpgradeError("target spans must be a JSON string")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise UpgradeError("target spans are not valid JSON") from exc
    if not isinstance(decoded, list):
        raise UpgradeError("target spans JSON must contain an array")
    return value


def build_plan(inventory: dict[str, Any]) -> dict[str, Any]:
    """Compile reviewed values into a note-ID guarded, immutable plan."""

    old_fields, target_fields = configured_field_sets()
    live_model_fields = inventory["model"]["fields"]
    if live_model_fields not in (old_fields, target_fields):
        raise UpgradeError(
            "live model field order is neither the exact old nor target schema: "
            f"{live_model_fields}"
        )
    policy = _load_policy_module()
    highlights = _load_highlight_module()

    # Policy accepts either {note_id: {"fields": ...}} or bare field maps.  We
    # use the richer shape so it can inspect source IDs/levels without another
    # AnkiConnect round trip; all values are deep-copied before policy mutation.
    records = {
        note_id: {
            "note_id": int(note_id),
            "fields": copy.deepcopy(item["fields"]),
            "tags": list(item["tags"]),
            "model": item["model"],
        }
        for note_id, item in inventory["notes"].items()
    }
    before_policy_fields = {
        note_id: copy.deepcopy(item["fields"])
        for note_id, item in records.items()
    }
    try:
        policy_audit = policy.apply_policy(records)
    except Exception as exc:
        raise UpgradeError(f"review policy rejected live notes: {exc}") from exc

    # A policy may return a note-field mapping in addition to mutating the
    # records (useful for small standalone policy runners).  Accept that shape
    # only when every returned key is a live note ID; an ordinary audit object
    # such as {"enabled": 42} is retained as provenance and ignored here.
    if isinstance(policy_audit, dict) and policy_audit:
        keys = {str(key) for key in policy_audit}
        live_keys = set(records)
        if keys <= live_keys:
            for note_id, value in policy_audit.items():
                if isinstance(value, dict) and isinstance(value.get("fields"), dict):
                    records[str(note_id)]["fields"] = copy.deepcopy(value["fields"])
                elif isinstance(value, dict):
                    records[str(note_id)]["fields"] = copy.deepcopy(value)
                else:
                    raise UpgradeError(f"policy returned invalid fields for note {note_id}")

    # The policy is only authorized to derive the four appended fields.  A
    # stray edit to a legacy field would otherwise be silently omitted from
    # the update payload and make the audit misleading.
    for note_id, item in records.items():
        if not isinstance(item.get("fields"), dict):
            raise UpgradeError(f"policy returned invalid fields for note {note_id}")
        for name in old_fields:
            if item["fields"].get(name, "") != before_policy_fields[note_id].get(name, ""):
                raise UpgradeError(f"policy changed legacy field {name}: note {note_id}")

    updates: dict[str, dict[str, str]] = {}
    disabled_note_ids: list[int] = []
    for note_id in sorted(records, key=int):
        item = records[note_id]
        values = item.get("fields", item) if isinstance(item, dict) else item
        if not isinstance(values, dict):
            raise UpgradeError(f"policy returned invalid fields for note {note_id}")
        # Target spans are computed after policy values (especially accepted
        # answers) are final.  The API returns a JSON string by contract.
        try:
            spans = _build_spans(highlights, values)
        except Exception as exc:
            raise UpgradeError(f"target-span build failed for note {note_id}: {exc}") from exc
        values[NEW_FIELDS[3]] = spans
        update = {name: str(values.get(name, "") or "") for name in NEW_FIELDS}
        if update["ProductionEnabled"] not in ("", "1"):
            raise UpgradeError(
                f"ProductionEnabled must be '1' or empty, note {note_id}: "
                f"{update['ProductionEnabled']!r}"
            )
        if not update["ProductionEnabled"]:
            disabled_note_ids.append(int(note_id))
        elif not update["AcceptedFullAnswersDE"].strip():
            raise UpgradeError(
                f"enabled production note has no full accepted answer: {note_id}"
            )
        # Empty pipe components are almost always a malformed review override.
        for name in ("AcceptedFullAnswersDE",):
            if update[name] and any(not part.strip() for part in update[name].split("|")):
                raise UpgradeError(f"empty accepted-answer component: {note_id}")
        updates[note_id] = update

    # Resolve disabled note IDs to exact reverse-card IDs.  Never infer an ord
    # from list order; a malformed note is a hard failure.
    cards_by_note: dict[int, list[tuple[int, int]]] = {}
    for card_id, item in inventory["cards"].items():
        cards_by_note.setdefault(int(item["note"]), []).append((int(item["ord"]), int(card_id)))
    disabled_card_ids: list[int] = []
    for note_id in disabled_note_ids:
        candidates = [card_id for ord_value, card_id in cards_by_note.get(note_id, []) if ord_value == 1]
        if len(candidates) != 1:
            raise UpgradeError(f"disabled note has no unique reverse card: {note_id}")
        disabled_card_ids.append(candidates[0])

    # If the review module provides an audit, retain it for provenance but do
    # not trust counts over the live-derived ID sets.
    audit = {
        "policy": policy_audit,
        "notes": len(updates),
        "disabled_notes": len(disabled_note_ids),
        "disabled_cards": len(disabled_card_ids),
        "disabled_note_ids": disabled_note_ids,
        "disabled_card_ids": sorted(disabled_card_ids),
    }
    return {
        "schema_version": 1,
        "created_utc": now_utc(),
        "model": MODEL,
        "old_fields": old_fields,
        "target_fields": target_fields,
        "new_fields": list(NEW_FIELDS),
        "updates": updates,
        "source_ids": {note_id: inventory["notes"][note_id]["source_id"] for note_id in updates},
        "disabled_note_ids": disabled_note_ids,
        "disabled_card_ids": sorted(disabled_card_ids),
        "audit": audit,
        "target_templates_hash": canonical_hash(source_templates()),
        "target_styling_hash": canonical_hash(target_css()),
    }


def write_plan(plan: dict[str, Any]) -> None:
    atomic_json(PLAN_PATH, plan)


def load_plan() -> dict[str, Any]:
    plan = load_json(PLAN_PATH)
    if plan.get("schema_version") != 1 or plan.get("new_fields") != list(NEW_FIELDS):
        raise UpgradeError("unsupported or stale template-upgrade plan")
    old_fields, target_fields = configured_field_sets()
    if plan.get("old_fields") != old_fields or plan.get("target_fields") != target_fields:
        raise UpgradeError("template-upgrade plan schema does not match source contract")
    if not isinstance(plan.get("updates"), dict):
        raise UpgradeError("template-upgrade plan has no note updates")
    if not isinstance(plan.get("source_ids"), dict) or set(plan["source_ids"]) != set(plan["updates"]):
        raise UpgradeError("template-upgrade plan SourceID map does not match note updates")
    for note_id, values in plan["updates"].items():
        try:
            int(note_id)
        except (TypeError, ValueError) as exc:
            raise UpgradeError(f"template-upgrade plan has invalid note ID: {note_id!r}") from exc
        if not isinstance(values, dict) or set(values) != set(NEW_FIELDS):
            raise UpgradeError(f"template-upgrade plan has invalid new fields: note {note_id}")
    try:
        disabled_ids = [int(value) for value in plan.get("disabled_card_ids", [])]
    except (TypeError, ValueError) as exc:
        raise UpgradeError("template-upgrade plan has invalid disabled card IDs") from exc
    if len(disabled_ids) != len(set(disabled_ids)):
        raise UpgradeError("template-upgrade plan has duplicate disabled card IDs")
    try:
        disabled_note_ids = [int(value) for value in plan.get("disabled_note_ids", [])]
    except (TypeError, ValueError) as exc:
        raise UpgradeError("template-upgrade plan has invalid disabled note IDs") from exc
    if len(disabled_note_ids) != len(set(disabled_note_ids)):
        raise UpgradeError("template-upgrade plan has duplicate disabled note IDs")
    return plan


def write_snapshot(inventory: dict[str, Any], backup: Path, plan: dict[str, Any]) -> None:
    snapshot = copy.deepcopy(inventory)
    snapshot.update({
        "schema_version": 1,
        "backup": str(backup.resolve()),
        "backup_sha256": hash_file(backup),
        "plan_sha256": canonical_hash(plan),
        "old_fields": plan["old_fields"],
        "target_fields": plan["target_fields"],
        "new_fields": list(NEW_FIELDS),
    })
    atomic_json(SNAPSHOT_PATH, snapshot)


def load_snapshot() -> dict[str, Any]:
    snapshot = load_json(SNAPSHOT_PATH)
    if snapshot.get("schema_version") != 1:
        raise UpgradeError("unsupported template-upgrade snapshot")
    backup = Path(snapshot.get("backup", ""))
    if not backup.exists() or not valid_apkg(backup):
        raise UpgradeError(f"snapshot APKG missing or invalid: {backup}")
    if hash_file(backup) != snapshot.get("backup_sha256"):
        raise UpgradeError("snapshot APKG SHA-256 changed")
    return snapshot


def validate_snapshot_contract(snapshot: dict[str, Any], plan: dict[str, Any]) -> None:
    """Validate JSON state independently of the APKG checksum."""

    if snapshot.get("schema_version") != 1:
        raise UpgradeError("unsupported template-upgrade snapshot")
    if snapshot.get("old_fields") != plan.get("old_fields") or snapshot.get("target_fields") != plan.get("target_fields"):
        raise UpgradeError("snapshot schema does not match the immutable plan")
    if snapshot.get("new_fields") != list(NEW_FIELDS):
        raise UpgradeError("snapshot additive fields are not the approved four")
    model = snapshot.get("model")
    if (
        not isinstance(model, dict)
        or model.get("fields") != plan.get("old_fields")
        or not isinstance(model.get("templates"), dict)
        or not isinstance(model.get("styling"), str)
    ):
        raise UpgradeError("snapshot was not captured from the exact old model schema")
    notes = snapshot.get("notes")
    cards = snapshot.get("cards")
    if not isinstance(notes, dict) or not isinstance(cards, dict) or not notes or not cards:
        raise UpgradeError("snapshot note/card inventory is missing")
    if not isinstance(snapshot.get("reviews"), dict):
        raise UpgradeError("snapshot review inventory is missing")
    if snapshot.get("reviews_sha256") != canonical_hash(snapshot["reviews"]):
        raise UpgradeError("snapshot review hash is inconsistent")
    source_ids = plan.get("source_ids", {})
    if set(source_ids) != set(notes):
        raise UpgradeError("plan SourceID map does not cover snapshot notes")
    for note_id, item in notes.items():
        if source_ids[note_id] != item.get("source_id"):
            raise UpgradeError(f"snapshot SourceID differs from immutable plan: {note_id}")


def _state_note_ids(snapshot: dict[str, Any]) -> set[str]:
    return set(snapshot.get("notes", {}))


def _state_card_ids(snapshot: dict[str, Any]) -> set[str]:
    return set(snapshot.get("cards", {}))


def compare_baseline(
    snapshot: dict[str, Any],
    inventory: dict[str, Any],
    *,
    allow_target_model: bool = False,
    allow_suspension_changes: bool = False,
) -> None:
    """Refuse to operate if a user/reviewer changed the collection meanwhile."""

    if set(inventory["notes"]) != _state_note_ids(snapshot):
        raise UpgradeError("live note ID set changed since snapshot")
    if set(inventory["cards"]) != _state_card_ids(snapshot):
        raise UpgradeError("live card ID set changed since snapshot")
    old_fields = snapshot["old_fields"]
    for note_id, before in snapshot["notes"].items():
        current = inventory["notes"][note_id]
        if current["model"] != before["model"]:
            raise UpgradeError(f"note model changed since snapshot: {note_id}")
        if current["source_id"] != before["source_id"]:
            raise UpgradeError(f"SourceID changed since snapshot: {note_id}")
        if current["tags"] != before["tags"]:
            raise UpgradeError(f"note tags changed since snapshot: {note_id}")
        # During post-field apply the four new fields are allowed to differ;
        # every legacy field and card linkage must remain byte-for-byte stable.
        for name in old_fields:
            if current["fields"].get(name, "") != before["fields"].get(name, ""):
                raise UpgradeError(f"untouched field changed: note={note_id} field={name}")
        if current["cards"] != before["cards"]:
            raise UpgradeError(f"note/card linkage changed: {note_id}")
    for card_id, before in snapshot["cards"].items():
        current = inventory["cards"][card_id]
        if current["note"] != before["note"] or current["ord"] != before["ord"] or current["deckName"] != before["deckName"]:
            raise UpgradeError(f"card identity/deck changed: {card_id}")
        if current["schedule"] != before["schedule"]:
            if not allow_suspension_changes:
                raise UpgradeError(f"card scheduling changed since snapshot: {card_id}")
            # The only schedule mutation this rollout is allowed to leave
            # pending is Anki's queue=-1 marker for a newly suspended card.
            # Due/interval/repetition history must never be waved through just
            # because rollback is being attempted.
            if current["suspended"] == before["suspended"] or any(
                current["schedule"].get(key) != before["schedule"].get(key)
                for key in SCHEDULE_KEYS
                if key != "queue"
            ):
                raise UpgradeError(f"unexpected card scheduling drift: {card_id}")
        if not allow_suspension_changes and current["suspended"] != before["suspended"]:
            raise UpgradeError(f"card suspension changed since snapshot: {card_id}")
    if (
        "reviews" in inventory
        and "reviews" in snapshot
        and inventory["reviews"] != snapshot["reviews"]
    ) or inventory["reviews_sha256"] != snapshot["reviews_sha256"]:
        raise UpgradeError("review history changed since snapshot")
    if inventory["deck_counts"] != snapshot["deck_counts"]:
        raise UpgradeError("deck note/card counts changed since snapshot")


def compare_reviews_and_identity(snapshot: dict[str, Any], inventory: dict[str, Any]) -> None:
    if set(inventory["cards"]) != _state_card_ids(snapshot):
        raise UpgradeError("card ID set changed")
    if (
        "reviews" in inventory
        and "reviews" in snapshot
        and inventory["reviews"] != snapshot["reviews"]
    ) or inventory["reviews_sha256"] != snapshot["reviews_sha256"]:
        raise UpgradeError("review history hash changed")
    if inventory["deck_counts"] != snapshot["deck_counts"]:
        raise UpgradeError("deck counts changed")
    for card_id, before in snapshot["cards"].items():
        current = inventory["cards"][card_id]
        if current["note"] != before["note"] or current["ord"] != before["ord"] or current["deckName"] != before["deckName"]:
            raise UpgradeError(f"card identity changed: {card_id}")


def expected_templates() -> dict[str, Any]:
    templates = source_templates()
    if list(templates) != ["German → English", "English → German"]:
        raise UpgradeError(f"unexpected target template names/order: {list(templates)}")
    return templates


def assert_target_design(plan: dict[str, Any]) -> None:
    """Ensure review-owned template/CSS sources did not change after backup."""

    templates = expected_templates()
    css = target_css()
    if canonical_hash(templates) != plan.get("target_templates_hash"):
        raise UpgradeError("target template source changed after backup")
    if canonical_hash(css) != plan.get("target_styling_hash"):
        raise UpgradeError("target styling source changed after backup")


def expected_disabled_cards(plan: dict[str, Any]) -> set[str]:
    try:
        return {str(int(value)) for value in plan.get("disabled_card_ids", [])}
    except (TypeError, ValueError) as exc:
        raise UpgradeError("plan contains an invalid disabled card ID") from exc


def upgrade_schema_suffix(plan: dict[str, Any], actual: list[str] | None = None) -> list[str]:
    """Return the already-installed additive suffix, rejecting any other shape."""

    fields = list(actual if actual is not None else model_fields())
    old = list(plan["old_fields"])
    suffix = list(plan["new_fields"])
    if fields == old:
        return []
    if fields[: len(old)] != old:
        raise UpgradeError(f"model schema is not an old-field prefix: {fields}")
    installed = fields[len(old):]
    if len(installed) > len(suffix) or installed != suffix[: len(installed)]:
        raise UpgradeError(f"model schema has an unexpected upgrade suffix: {fields}")
    return installed


def ensure_model_fields(plan: dict[str, Any]) -> None:
    actual = model_fields()
    if actual == plan["target_fields"]:
        return
    if actual != plan["old_fields"]:
        raise UpgradeError(f"cannot add fields from unexpected model schema: {actual}")
    for index, name in enumerate(NEW_FIELDS, start=len(plan["old_fields"])):
        anki("modelFieldAdd", modelName=MODEL, fieldName=name, index=index)
    if model_fields() != plan["target_fields"]:
        raise UpgradeError("model field add did not produce exact target order")


def update_new_fields(plan: dict[str, Any], inventory: dict[str, Any]) -> int:
    actions: list[dict[str, Any]] = []
    changed = 0
    for note_id in sorted(plan["updates"], key=int):
        desired = plan["updates"][note_id]
        if note_id not in inventory["notes"]:
            raise UpgradeError(f"planned note missing before update: {note_id}")
        current = inventory["notes"][note_id]["fields"]
        values = {name: desired[name] for name in NEW_FIELDS if current.get(name, "") != desired[name]}
        if not values:
            continue
        changed += 1
        actions.append({
            "action": "updateNoteFields",
            "params": {"note": {"id": int(note_id), "fields": values}},
        })
    for batch in chunks(actions, 40):
        response = anki("multi", actions=batch)
        if not isinstance(response, list) or len(response) != len(batch):
            raise UpgradeError("Anki multi returned an unexpected update result")
        errors = [item.get("error") for item in response if isinstance(item, dict) and item.get("error")]
        if errors:
            raise UpgradeError(f"new-field update errors: {errors[:3]}")
    return changed


def verify_new_fields(plan: dict[str, Any], inventory: dict[str, Any]) -> None:
    for note_id, desired in plan["updates"].items():
        current = inventory["notes"].get(note_id)
        if current is None:
            raise UpgradeError(f"planned note disappeared: {note_id}")
        for name in NEW_FIELDS:
            if current["fields"].get(name, "") != desired[name]:
                raise UpgradeError(f"new field mismatch: note={note_id} field={name}")


def apply_templates_and_style() -> None:
    templates = expected_templates()
    anki("updateModelTemplates", model={"name": MODEL, "templates": templates})
    anki("updateModelStyling", model={"name": MODEL, "css": target_css()})
    current = model_snapshot()
    if current["templates"] != templates:
        raise UpgradeError("target templates were not installed exactly")
    if current["styling"] != target_css():
        raise UpgradeError("target styling was not installed exactly")


def suspend_disabled(plan: dict[str, Any], snapshot: dict[str, Any]) -> list[int]:
    target = expected_disabled_cards(plan)
    before = snapshot["cards"]
    newly = [
        int(card_id) for card_id in sorted(target, key=int)
        if not bool(before[card_id]["suspended"])
    ]
    if newly:
        result = anki("suspend", cards=newly)
        # ``suspend`` returns false when every card was already suspended.  We
        # pass only newly-unsuspended cards, so false indicates an unexpected
        # race/error, not a harmless no-op.
        if result is False:
            raise UpgradeError("suspend returned false for newly-suspended cards")
    return newly


def verify_suspension(plan: dict[str, Any], snapshot: dict[str, Any], inventory: dict[str, Any], newly: Iterable[int] | None = None) -> None:
    target = expected_disabled_cards(plan)
    newly_set = {str(int(value)) for value in (newly or [])}
    for card_id, before in snapshot["cards"].items():
        current = inventory["cards"][card_id]
        expected = bool(before["suspended"])
        if card_id in target:
            expected = True
        if current["suspended"] != expected:
            raise UpgradeError(f"disabled-card suspension mismatch: {card_id}")
        old_schedule = before["schedule"]
        now_schedule = current["schedule"]
        if card_id in newly_set:
            for key, value in old_schedule.items():
                if key == "queue":
                    continue
                if now_schedule.get(key) != value:
                    raise UpgradeError(f"suspension changed schedule field {key}: {card_id}")
            if now_schedule.get("queue") != -1:
                raise UpgradeError(f"newly suspended card queue is not -1: {card_id}")
        elif now_schedule != old_schedule:
            raise UpgradeError(f"non-target card scheduling changed: {card_id}")


def disabled_placeholder_check(plan: dict[str, Any], cards: list[dict[str, Any]]) -> None:
    """Ensure disabled production cards remain non-empty and contain no type box."""

    target = expected_disabled_cards(plan)
    if not target:
        return
    by_id = {str(int(card["cardId"])): card for card in cards}
    for card_id in target:
        card = by_id.get(card_id)
        if card is None:
            raise UpgradeError(f"disabled card missing after template update: {card_id}")
        raw = str(card.get("question", ""))
        visible = static_card_text(raw).casefold()
        if not visible:
            raise UpgradeError(f"disabled production card rendered blank: {card_id}")
        # A disabled card must not silently become a production prompt if a
        # user manually unsuspends it.  The placeholder may be class-only, so
        # inspect raw HTML as well as visible copy.
        marker = "gw-production-disabled" in raw.casefold()
        wording = "production" in visible or "disabled" in visible or "unavailable" in visible
        if not (marker or wording):
            raise UpgradeError(f"disabled production placeholder missing: {card_id}")
        if "[anki:type:" in raw.casefold() or re.search(
            r"\bid\s*=\s*(?:['\"]?typeans\b)", raw, flags=re.I
        ):
            raise UpgradeError(f"disabled production card still exposes type box: {card_id}")


def write_result(result: dict[str, Any]) -> None:
    atomic_json(RESULT_PATH, result)


def load_result() -> dict[str, Any] | None:
    if not RESULT_PATH.exists():
        return None
    return load_json(RESULT_PATH)


def clear_result() -> None:
    try:
        RESULT_PATH.unlink(missing_ok=True)
    except OSError as exc:
        raise UpgradeError(f"cannot clear stale rollout result: {RESULT_PATH}") from exc


def command_audit(_: argparse.Namespace) -> None:
    require_version()
    require_actions()
    inventory = live_inventory()
    plan = build_plan(inventory)
    write_plan(plan)
    policy_report = plan["audit"].get("policy")
    if isinstance(policy_report, dict):
        policy_summary = {
            key: value
            for key, value in policy_report.items()
            if key not in {"groups", "collisions", "collision_groups"}
        }
        policy_summary["groups"] = len(policy_report.get("groups", []))
        policy_summary["collision_groups"] = len(policy_report.get("collision_groups", []))
    else:
        policy_summary = policy_report
    print_json({
        "status": "AUDIT_PASS",
        "notes": len(inventory["notes"]),
        "cards": len(inventory["cards"]),
        "disabled_notes": len(plan["disabled_note_ids"]),
        "disabled_cards": len(plan["disabled_card_ids"]),
        "old_fields": plan["old_fields"],
        "target_fields": plan["target_fields"],
        "policy_audit": policy_summary,
    })


def command_backup(_: argparse.Namespace) -> None:
    require_version()
    require_actions()
    inventory = live_inventory()
    plan = build_plan(inventory)
    if inventory["model"]["fields"] != plan["old_fields"]:
        raise UpgradeError("backup requires the exact old model schema")
    backup = export_backup()
    write_plan(plan)
    write_snapshot(inventory, backup, plan)
    clear_result()
    print_json({
        "status": "BACKUP_PASS", "backup": str(backup.resolve()),
        "backup_sha256": hash_file(backup), "notes": len(inventory["notes"]),
        "cards": len(inventory["cards"]), "plan": str(PLAN_PATH),
        "snapshot": str(SNAPSHOT_PATH),
    })


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise UpgradeError(f"confirmation must equal {APPLY_CONFIRMATION}")
    require_version()
    require_actions()
    snapshot = load_snapshot()
    plan = load_plan()
    if snapshot.get("plan_sha256") != canonical_hash(plan):
        raise UpgradeError("plan changed after backup snapshot")
    validate_snapshot_contract(snapshot, plan)
    assert_target_design(plan)
    inventory = live_inventory()
    compare_baseline(snapshot, inventory)
    if inventory["model"]["fields"] != plan["old_fields"]:
        raise UpgradeError("apply requires the exact old model schema")
    clear_result()

    changed_notes = 0
    newly_suspended: list[int] = []
    try:
        ensure_model_fields(plan)
        # Re-read after modelFieldAdd so updateNoteFields sees all four names,
        # while templates still use the old safe rendering.
        inventory = live_inventory()
        compare_baseline(snapshot, inventory, allow_target_model=True)
        changed_notes = update_new_fields(plan, inventory)
        inventory = live_inventory()
        compare_baseline(snapshot, inventory, allow_target_model=True)
        verify_new_fields(plan, inventory)
        apply_templates_and_style()
        inventory = live_inventory()
        compare_reviews_and_identity(snapshot, inventory)
        verify_new_fields(plan, inventory)
        validate_rendered_cards([
            # cardsInfo is needed for rendered question/answer; inventory keeps
            # compact schedule state only, so fetch the linked cards again.
            card for batch in chunks(
                [int(card_id) for card_id in inventory["cards"]], 250
            ) for card in anki("cardsInfo", cards=batch)
        ])
        # Re-fetch cards once for the disabled placeholder check.
        notes = fetch_notes()
        cards = fetch_cards(notes)
        disabled_placeholder_check(plan, cards)
        newly_suspended = suspend_disabled(plan, snapshot)
        inventory = live_inventory()
        compare_reviews_and_identity(snapshot, inventory)
        verify_new_fields(plan, inventory)
        verify_suspension(plan, snapshot, inventory, newly_suspended)
        if inventory["model"]["fields"] != plan["target_fields"]:
            raise UpgradeError("target model fields missing after apply")
        if inventory["model"]["templates"] != expected_templates() or inventory["model"]["styling"] != target_css():
            raise UpgradeError("target model templates/styling drifted after apply")
    except Exception as exc:
        # Do not hide the original failure.  A partial apply is left auditable;
        # the explicit rollback command is the only operation that reverses it.
        raise UpgradeError(f"apply failed (use rollback after inspection): {exc}") from exc

    result = {
        "schema_version": 1,
        "status": "applied",
        "applied_utc": now_utc(),
        "changed_notes": changed_notes,
        "newly_suspended": newly_suspended,
        "disabled_cards": plan["disabled_card_ids"],
        "notes": len(inventory["notes"]),
        "cards": len(inventory["cards"]),
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
        "plan_sha256": snapshot["plan_sha256"],
    }
    write_result(result)
    print_json(result)


def restore_note_fields(snapshot: dict[str, Any], plan: dict[str, Any], inventory: dict[str, Any]) -> int:
    actions: list[dict[str, Any]] = []
    present_fields = set(inventory["model"]["fields"])
    writable = [name for name in plan["new_fields"] if name in present_fields]
    if not writable:
        return 0
    for note_id in sorted(snapshot["notes"], key=int):
        current = inventory["notes"][note_id]
        before = snapshot["notes"][note_id]["fields"]
        values = {
            name: before.get(name, "") for name in writable
            if current["fields"].get(name, "") != before.get(name, "")
        }
        if values:
            actions.append({
                "action": "updateNoteFields",
                "params": {"note": {"id": int(note_id), "fields": values}},
            })
    for batch in chunks(actions, 40):
        response = anki("multi", actions=batch)
        if not isinstance(response, list) or len(response) != len(batch):
            raise UpgradeError("rollback field update returned an unexpected result")
        errors = [item.get("error") for item in response if isinstance(item, dict) and item.get("error")]
        if errors:
            raise UpgradeError(f"rollback field update errors: {errors[:3]}")
    return len(actions)


def restore_suspensions(snapshot: dict[str, Any], inventory: dict[str, Any], plan: dict[str, Any], result: dict[str, Any] | None) -> list[int]:
    # Prefer the recorded set, but derive it if a process crashed before result
    # was written.  Never unsuspend a card that was already suspended in the
    # pre-upgrade snapshot.
    candidates = rollback_suspension_candidates(snapshot, inventory, plan, result)
    # Refuse to override a concurrent/manual suspension change.
    for card_id in candidates:
        if not inventory["cards"].get(card_id, {}).get("suspended", False):
            raise UpgradeError(f"rollback expected newly suspended card, but it is active: {card_id}")
    values = [int(card_id) for card_id in sorted(candidates, key=int)]
    if values:
        returned = anki("unsuspend", cards=values)
        if returned is False:
            raise UpgradeError("unsuspend returned false for recorded rollout cards")
    return values


def rollback_suspension_candidates(
    snapshot: dict[str, Any], inventory: dict[str, Any], plan: dict[str, Any], result: dict[str, Any] | None
) -> set[str]:
    """Identify and validate the only suspension delta rollback may reverse."""

    if result is None:
        recorded: set[str] = set()
    else:
        raw_recorded = result.get("newly_suspended")
        if not isinstance(raw_recorded, list):
            raise UpgradeError("result has no explicit newly_suspended list")
        recorded = {str(int(value)) for value in raw_recorded}
    target = expected_disabled_cards(plan)
    if recorded - target:
        raise UpgradeError("result contains a non-target newly suspended card")
    missing_target = target - set(snapshot.get("cards", {}))
    if missing_target:
        raise UpgradeError(f"plan references cards absent from snapshot: {sorted(missing_target, key=int)[:5]}")
    if any(bool(snapshot["cards"][card_id]["suspended"]) for card_id in recorded):
        raise UpgradeError("result attempts to unsuspend a pre-suspended card")
    candidates = recorded
    for card_id, before in snapshot["cards"].items():
        now = inventory["cards"][card_id]
        if now["suspended"] == before["suspended"]:
            continue
        if result is None:
            raise UpgradeError(
                "suspension changed but applied result is missing; use the APKG backup"
            )
        if card_id not in candidates or before["suspended"] or not now["suspended"]:
            raise UpgradeError(f"unexpected suspension drift before rollback: {card_id}")
    return candidates


def remove_upgrade_fields(plan: dict[str, Any]) -> None:
    actual = model_fields()
    installed = upgrade_schema_suffix(plan, actual)
    if not installed:
        return
    for name in reversed(installed):
        anki("modelFieldRemove", modelName=MODEL, fieldName=name)
    if model_fields() != plan["old_fields"]:
        raise UpgradeError("rollback did not restore exact old field order")


def command_verify(args: argparse.Namespace) -> None:
    require_version()
    snapshot = load_snapshot()
    plan = load_plan()
    if snapshot.get("plan_sha256") != canonical_hash(plan):
        raise UpgradeError("plan changed after snapshot")
    validate_snapshot_contract(snapshot, plan)
    inventory = live_inventory()
    result = load_result()
    if args.baseline or not result:
        compare_baseline(snapshot, inventory)
        print_json({
            "status": "BASELINE_PASS", "notes": len(inventory["notes"]),
            "cards": len(inventory["cards"]), "model_fields": inventory["model"]["fields"],
        })
        return
    if result.get("status") != "applied":
        raise UpgradeError("result state is not an applied rollout")
    assert_target_design(plan)
    compare_reviews_and_identity(snapshot, inventory)
    verify_new_fields(plan, inventory)
    verify_suspension(plan, snapshot, inventory, result.get("newly_suspended", []))
    if inventory["model"]["fields"] != plan["target_fields"]:
        raise UpgradeError("target fields are not installed")
    if inventory["model"]["templates"] != expected_templates() or inventory["model"]["styling"] != target_css():
        raise UpgradeError("target templates/styling are not installed")
    validate_rendered_cards([
        card for batch in chunks([int(card_id) for card_id in inventory["cards"]], 250)
        for card in anki("cardsInfo", cards=batch)
    ])
    notes = fetch_notes()
    cards = fetch_cards(notes)
    disabled_placeholder_check(plan, cards)
    print_json({
        "status": "VERIFY_PASS", "notes": len(inventory["notes"]),
        "cards": len(inventory["cards"]), "disabled_cards": len(plan["disabled_card_ids"]),
        "newly_suspended": result.get("newly_suspended", []),
        "reviews_sha256": inventory["reviews_sha256"],
    })


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise UpgradeError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    require_version()
    require_actions()
    snapshot = load_snapshot()
    plan = load_plan()
    if snapshot.get("plan_sha256") != canonical_hash(plan):
        raise UpgradeError("plan changed after snapshot")
    validate_snapshot_contract(snapshot, plan)
    inventory = live_inventory()
    compare_reviews_and_identity(snapshot, inventory)
    # Legacy fields, tags, note IDs, and card linkage must still match.  New
    # fields and target model/templates are the only expected differences.
    compare_baseline(
        snapshot,
        inventory,
        allow_target_model=True,
        allow_suspension_changes=True,
    )
    current_model = inventory["model"]["fields"]
    upgrade_schema_suffix(plan, current_model)
    result = load_result()
    rollback_suspension_candidates(snapshot, inventory, plan, result)
    restore_note_fields(snapshot, plan, inventory)
    # Restore the old template/style before deleting fields so no template
    # references a field that is about to disappear.
    old_model = snapshot["model"]
    if inventory["model"]["templates"] != old_model["templates"]:
        anki("updateModelTemplates", model={"name": MODEL, "templates": old_model["templates"]})
    if inventory["model"]["styling"] != old_model["styling"]:
        anki("updateModelStyling", model={"name": MODEL, "css": old_model["styling"]})
    remove_upgrade_fields(plan)
    inventory = live_inventory()
    restored_suspensions = restore_suspensions(snapshot, inventory, plan, result)
    inventory = live_inventory()
    # Full baseline equality is the rollback gate.  If Anki's scheduler cannot
    # restore a card's exact due state after unsuspend, stop and use APKG GUI
    # restore rather than silently claiming success.
    compare_baseline(snapshot, inventory)
    if inventory["model"] != snapshot["model"]:
        raise UpgradeError("rollback model snapshot mismatch")
    write_result({
        "schema_version": 1,
        "status": "rolled_back",
        "rolled_back_utc": now_utc(),
        "restored_suspensions": restored_suspensions,
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
    })
    print_json({
        "status": "ROLLBACK_PASS", "notes": len(inventory["notes"]),
        "cards": len(inventory["cards"]), "restored_suspensions": restored_suspensions,
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="compile and validate the reviewed live plan").set_defaults(func=command_audit)
    sub.add_parser("backup", help="export APKG and capture the immutable baseline").set_defaults(func=command_backup)
    apply = sub.add_parser("apply", help="apply fields/templates and suspend reviewed cards")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify", help="verify applied state (or --baseline before apply)")
    verify.add_argument("--baseline", action="store_true")
    verify.set_defaults(func=command_verify)
    rollback = sub.add_parser("rollback", help="restore fields/templates and pre-rollout suspension")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except UpgradeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # fail closed for malformed external state
        print(f"ERROR: unexpected rollout failure: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
