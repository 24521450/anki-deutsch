"""Guarded live refresh of the reviewed Goethe answer contract.

This command updates only answer-contract fields through AnkiConnect.  It is
kept separate from the historical schema upgrade because the live model
already contains the additive fields that upgrade introduced.

Typical sequence::

    python tools/goethe_answer_contract_refresh.py audit
    python tools/goethe_answer_contract_refresh.py backup
    python tools/goethe_answer_contract_refresh.py apply --confirmation APPLY_GOETHE_ANSWER_CONTRACT_REFRESH
    python tools/goethe_answer_contract_refresh.py verify

Rollback is explicit::

    python tools/goethe_answer_contract_refresh.py rollback --confirmation ROLLBACK_GOETHE_ANSWER_CONTRACT_REFRESH
"""
from __future__ import annotations

import argparse
import copy
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_apkg as apkg
import goethe_review_policy as review_policy
import goethe_target_highlights as highlights
import goethe_template_policy as production_policy
import goethe_template_upgrade as guard


ROOT = guard.ROOT
STATE = ROOT / "tools" / ".goethe_answer_contract_refresh"
PLAN_PATH = STATE / "plan.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
RESULT_PATH = STATE / "result.json"
MODEL = guard.MODEL
PARENT_DECK = guard.PARENT_DECK

ANSWER_FIELDS = (
    "AcceptedAnswersDE",
    "AcceptedFullAnswersDE",
    "ExampleTargetSpansJSON",
)
APPLY_CONFIRMATION = "APPLY_GOETHE_ANSWER_CONTRACT_REFRESH"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_ANSWER_CONTRACT_REFRESH"


class RefreshError(RuntimeError):
    """Raised when a live answer refresh cannot be proven safe."""


def _source_refs(fields: dict[str, str]) -> list[str]:
    return [
        item.strip()
        for item in str(fields.get("SourceRefs", "")).split("|")
        if item.strip()
    ]


def _highlight_builder() -> Any:
    builder = getattr(highlights, "build_spans", None)
    if not callable(builder):
        builder = getattr(highlights, "build_target_spans", None)
    if not callable(builder):
        raise RefreshError("target-span builder is unavailable")
    return builder


def _canonical_records(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    generic_policy = review_policy.load_policy()
    reflexive_policy = review_policy.load_reflexive_policy()
    records: dict[str, dict[str, Any]] = {}
    for note_id, item in inventory["notes"].items():
        current = item["fields"]
        # Disabled production notes intentionally remain outside this refresh;
        # their full answer field must stay empty.
        if current.get("ProductionEnabled") != "1":
            continue
        desired = review_policy.resolve_reviewed_fields(
            current,
            source_refs=_source_refs(current),
            policy=generic_policy,
            reflexive_policy=reflexive_policy,
        )
        records[note_id] = {
            "fields": desired,
            "source_id": item["source_id"],
            "cards": list(item["cards"]),
        }
    return records


def build_plan(inventory: dict[str, Any]) -> dict[str, Any]:
    """Compile exact note-ID-guarded answer changes from live state."""

    fields = list(inventory["model"]["fields"])
    configured = list(getattr(guard.gw, "FIELDS", []))
    if fields != configured:
        raise RefreshError(
            "answer refresh requires the exact current model schema: "
            f"expected {configured}, got {fields}"
        )
    missing = [name for name in ANSWER_FIELDS if name not in fields]
    if missing:
        raise RefreshError(f"live model is missing answer fields: {missing}")

    # Start with the complete live record set so the production policy can
    # validate global cue collisions and canonical full-answer derivation.
    all_records: dict[str, dict[str, Any]] = {}
    for note_id, item in inventory["notes"].items():
        all_records[note_id] = {
            "fields": copy.deepcopy(item["fields"]),
            "source_refs": _source_refs(item["fields"]),
        }
    canonical = _canonical_records(inventory)
    for note_id, item in canonical.items():
        all_records[note_id]["fields"] = item["fields"]
    try:
        production_policy.apply_policy(
            all_records,
            strict=True,
            # Semantic EN->DE collisions require ProductionHint edits, which
            # are intentionally outside this answer-only live refresh.  Keep
            # the visible-cue/policy-coverage checks strict and leave the
            # separate ambiguity catalog workflow to resolve those groups.
            semantic_strict=False,
        )
    except production_policy.PolicyError as exc:
        raise RefreshError(f"production answer policy failed: {exc}") from exc

    builder = _highlight_builder()
    planned: dict[str, dict[str, Any]] = {}
    for note_id, item in inventory["notes"].items():
        current = item["fields"]
        if current.get("ProductionEnabled") != "1":
            continue
        desired = all_records[note_id]["fields"]
        desired["ExampleTargetSpansJSON"] = builder(desired)
        after = {
            name: str(desired.get(name, "") or "")
            for name in ANSWER_FIELDS
        }
        changed = {
            name: value for name, value in after.items()
            if value != str(current.get(name, "") or "")
        }
        # This tool must never smuggle a content or scheduling correction into
        # an answer refresh.  Any such drift is a hard stop for review.
        outside = {
            name for name, value in desired.items()
            if value != current.get(name, "") and name not in ANSWER_FIELDS
        }
        if outside:
            raise RefreshError(
                f"unexpected non-answer policy drift for note {note_id}: {sorted(outside)}"
            )
        if not changed:
            continue
        planned[note_id] = {
            "note_id": int(note_id),
            "source_id": item["source_id"],
            "cards": list(item["cards"]),
            "before": {name: current.get(name, "") for name in ANSWER_FIELDS},
            "after": after,
        }

    return {
        "schema_version": 1,
        "model": MODEL,
        "fields": fields,
        "answer_fields": list(ANSWER_FIELDS),
        "records": {key: planned[key] for key in sorted(planned, key=int)},
        "source_ids": {
            key: planned[key]["source_id"] for key in sorted(planned, key=int)
        },
    }


def _load_plan() -> dict[str, Any]:
    try:
        plan = guard.load_json(PLAN_PATH)
    except guard.UpgradeError as exc:
        raise RefreshError(str(exc)) from exc
    if plan.get("schema_version") != 1 or plan.get("model") != MODEL:
        raise RefreshError("unsupported answer-refresh plan")
    configured = list(getattr(guard.gw, "FIELDS", []))
    if not isinstance(plan.get("fields"), list) or plan["fields"] != configured:
        raise RefreshError("answer-refresh plan has invalid model fields")
    if plan.get("answer_fields") != list(ANSWER_FIELDS):
        raise RefreshError("answer-refresh plan fields are not the approved contract")
    if not isinstance(plan.get("records"), dict) or not isinstance(plan.get("source_ids"), dict):
        raise RefreshError("answer-refresh plan is missing records/source IDs")
    if set(plan["records"]) != set(plan["source_ids"]):
        raise RefreshError("answer-refresh plan SourceID map does not match records")
    for note_id, item in plan["records"].items():
        try:
            int(note_id)
        except (TypeError, ValueError) as exc:
            raise RefreshError(f"invalid note ID in answer-refresh plan: {note_id!r}") from exc
        if not isinstance(item, dict) or set(item.get("before", {})) != set(ANSWER_FIELDS):
            raise RefreshError(f"invalid before fields for note {note_id}")
        if not isinstance(item.get("after"), dict) or set(item["after"]) != set(ANSWER_FIELDS):
            raise RefreshError(f"invalid after fields for note {note_id}")
    return plan


def _load_snapshot() -> dict[str, Any]:
    try:
        snapshot = guard.load_json(SNAPSHOT_PATH)
    except guard.UpgradeError as exc:
        raise RefreshError(str(exc)) from exc
    if snapshot.get("schema_version") != 1:
        raise RefreshError("unsupported answer-refresh snapshot")
    backup = Path(str(snapshot.get("backup", "")))
    if not backup.exists() or not apkg.valid_apkg(backup):
        raise RefreshError(f"answer-refresh APKG backup missing or invalid: {backup}")
    if apkg.hash_file(backup) != snapshot.get("backup_sha256"):
        raise RefreshError("answer-refresh APKG SHA-256 changed")
    return snapshot


def _export_backup() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = STATE / f"Goethe_Institute_pre_answer_contract_{stamp}.apkg"
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
    allow_answer_fields: bool,
    allow_target_design: bool = False,
) -> None:
    allowed_models = [snapshot["model"]]
    if allow_target_design:
        allowed_models.append({
            "fields": snapshot["model"]["fields"],
            "templates": guard.expected_templates(),
            "styling": guard.target_css(),
        })
    if current["model"] not in allowed_models:
        raise RefreshError("model fields/templates/styling changed unexpectedly")
    if set(current["notes"]) != set(snapshot["notes"]):
        raise RefreshError("live note ID set changed since answer-refresh snapshot")
    if set(current["cards"]) != set(snapshot["cards"]):
        raise RefreshError("live card ID set changed since answer-refresh snapshot")
    for note_id, before in snapshot["notes"].items():
        now = current["notes"][note_id]
        for key in ("model", "source_id", "tags", "cards"):
            if now[key] != before[key]:
                raise RefreshError(f"note identity changed: {note_id} ({key})")
        for name, value in before["fields"].items():
            if allow_answer_fields and name in ANSWER_FIELDS:
                continue
            if now["fields"].get(name, "") != value:
                raise RefreshError(f"unexpected note field drift: {note_id} {name}")
    for card_id, before in snapshot["cards"].items():
        now = current["cards"][card_id]
        for key in ("schedule", "suspended", "note", "ord", "deckName"):
            if now[key] != before[key]:
                raise RefreshError(f"card state changed: {card_id} ({key})")
    if current["reviews"] != snapshot["reviews"]:
        raise RefreshError("review history changed since answer-refresh snapshot")
    if current["reviews_sha256"] != snapshot["reviews_sha256"]:
        raise RefreshError("review history hash changed since answer-refresh snapshot")
    if current["deck_counts"] != snapshot["deck_counts"]:
        raise RefreshError("deck counts changed since answer-refresh snapshot")


def _verify_target_fields(
    plan: dict[str, Any], inventory: dict[str, Any], *, before: bool,
) -> None:
    key = "before" if before else "after"
    for note_id, item in plan["records"].items():
        current = inventory["notes"].get(note_id)
        if current is None:
            raise RefreshError(f"planned note disappeared: {note_id}")
        expected = item[key]
        for name, value in expected.items():
            if current["fields"].get(name, "") != value:
                raise RefreshError(f"answer field mismatch: {note_id} {name}")


def _update_target_fields(plan: dict[str, Any], inventory: dict[str, Any], *, before: bool) -> int:
    key = "before" if before else "after"
    actions: list[dict[str, Any]] = []
    for note_id, item in plan["records"].items():
        current = inventory["notes"][note_id]["fields"]
        payload = {
            name: value for name, value in item[key].items()
            if current.get(name, "") != value
        }
        if payload:
            actions.append({
                "action": "updateNoteFields",
                "params": {"note": {"id": int(note_id), "fields": payload}},
            })
    for batch in guard.chunks(actions, 40):
        response = guard.anki("multi", actions=batch)
        if not isinstance(response, list) or len(response) != len(batch):
            raise RefreshError("Anki multi returned an unexpected answer-update result")
        errors = [
            item.get("error") for item in response
            if isinstance(item, dict) and item.get("error")
        ]
        if errors:
            raise RefreshError(f"answer field update errors: {errors[:3]}")
    return len(actions)


def command_audit(_: argparse.Namespace) -> None:
    guard.require_version()
    inventory = guard.live_inventory()
    plan = build_plan(inventory)
    guard.atomic_json(PLAN_PATH, plan)
    RESULT_PATH.unlink(missing_ok=True)
    guard.print_json({
        "status": "AUDIT_PASS",
        "notes": len(inventory["notes"]),
        "cards": len(inventory["cards"]),
        "target_notes": len(plan["records"]),
        "source_ids": list(plan["source_ids"].values()),
        "plan": str(PLAN_PATH),
    })


def command_backup(_: argparse.Namespace) -> None:
    guard.require_version()
    saved = _load_plan()
    inventory = guard.live_inventory()
    current = build_plan(inventory)
    if guard.canonical_hash(saved) != guard.canonical_hash(current):
        raise RefreshError("live answer target set changed since audit")
    backup = _export_backup()
    snapshot = copy.deepcopy(inventory)
    snapshot.update({
        "schema_version": 1,
        "answer_fields": list(ANSWER_FIELDS),
        "plan_sha256": guard.canonical_hash(saved),
        "backup": str(backup.resolve()),
        "backup_sha256": apkg.hash_file(backup),
    })
    guard.atomic_json(SNAPSHOT_PATH, snapshot)
    RESULT_PATH.unlink(missing_ok=True)
    guard.print_json({
        "status": "BACKUP_PASS",
        "backup": str(backup.resolve()),
        "backup_sha256": snapshot["backup_sha256"],
        "target_notes": len(saved["records"]),
        "snapshot": str(SNAPSHOT_PATH),
    })


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise RefreshError(f"confirmation must equal {APPLY_CONFIRMATION}")
    guard.require_version()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("answer-refresh plan changed after backup")
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, allow_answer_fields=False)
    _verify_target_fields(plan, current, before=True)
    changed = _update_target_fields(plan, current, before=False)
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, allow_answer_fields=True)
    _verify_target_fields(plan, current, before=False)
    guard.atomic_json(RESULT_PATH, {
        "schema_version": 1,
        "status": "applied",
        "applied_utc": guard.now_utc(),
        "changed_notes": changed,
        "plan_sha256": guard.canonical_hash(plan),
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
    })
    guard.print_json({"status": "APPLY_PASS", "changed_notes": changed})


def command_verify(_: argparse.Namespace) -> None:
    guard.require_version()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("answer-refresh plan changed after backup")
    current = guard.live_inventory()
    _compare_inventory(
        snapshot,
        current,
        allow_answer_fields=True,
        allow_target_design=True,
    )
    _verify_target_fields(plan, current, before=False)
    guard.print_json({
        "status": "VERIFY_PASS",
        "notes": len(current["notes"]),
        "cards": len(current["cards"]),
        "target_notes": len(plan["records"]),
    })


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise RefreshError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    guard.require_version()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("answer-refresh plan changed after backup")
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, allow_answer_fields=True)
    _verify_target_fields(plan, current, before=False)
    restored = _update_target_fields(plan, current, before=True)
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, allow_answer_fields=False)
    _verify_target_fields(plan, current, before=True)
    guard.atomic_json(RESULT_PATH, {
        "schema_version": 1,
        "status": "rolled_back",
        "rolled_back_utc": guard.now_utc(),
        "restored_notes": restored,
        "plan_sha256": guard.canonical_hash(plan),
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
    })
    guard.print_json({"status": "ROLLBACK_PASS", "restored_notes": restored})


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
        print(f"ERROR: unexpected answer-refresh failure: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
