"""Reviewed content corrections shared by offline and live workflows."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, MutableMapping

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "review" / "goethe_record_overrides.json"
REFLEXIVE_POLICY_PATH = ROOT / "review" / "goethe_reflexive_policy.json"

REFLEXIVE_RE = re.compile(r"^(?:\(sich\)|sich)\s+(.+)$", re.I)
REFLEXIVE_CLASSIFICATIONS = {
    "required", "optional", "construction", "no-marker", "split",
}


class ReviewPolicyError(ValueError):
    pass


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("records"), dict):
        raise ReviewPolicyError("unsupported review correction policy")
    return data


def load_reflexive_policy(path: Path = REFLEXIVE_POLICY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "records": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("records"), dict):
        raise ReviewPolicyError("unsupported reflexive policy")
    for source_id, entry in data["records"].items():
        if (
            not isinstance(source_id, str)
            or not source_id.strip()
            or not isinstance(entry, dict)
            or entry.get("classification") not in REFLEXIVE_CLASSIFICATIONS
            or not isinstance(entry.get("source_refs"), list)
            or not entry["source_refs"]
            or not all(isinstance(ref, str) and ref.strip() for ref in entry["source_refs"])
            or not isinstance(entry.get("set"), dict)
            or not isinstance(entry.get("reason"), str)
            or not entry["reason"].strip()
        ):
            raise ReviewPolicyError(f"invalid reflexive policy entry: {source_id}")
    return data


def reflexive_forms(value: str) -> list[str] | None:
    match = REFLEXIVE_RE.match(value.strip())
    if not match:
        return None
    stem = match.group(1).strip()
    if not stem:
        raise ReviewPolicyError("empty reflexive lemma")
    return [f"sich {stem}", f"s {stem}"]


def apply_reflexive_fields(
    fields: MutableMapping[str, Any],
    source_refs: list[str],
    policy: Mapping[str, Any],
) -> str | None:
    source_id = str(fields.get("SourceID", "")).strip()
    identities = {source_id, *map(str, source_refs)}
    matches = [
        key for key, entry in policy.get("records", {}).items()
        if key in identities or identities.intersection(map(str, entry.get("source_refs", [])))
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ReviewPolicyError(f"ambiguous reflexive policy match: {source_id}")
    key = matches[0]
    entry = policy["records"][key]
    expected = entry.get("expected_lemma")
    desired = entry.get("set", {}).get("Lemma")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if str(fields.get("Lemma", "")) not in {*map(str, allowed), str(desired)}:
            raise ReviewPolicyError(f"stale reflexive lemma for {key}")
    for name, value in entry.get("set", {}).items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ReviewPolicyError(f"invalid reflexive field override: {key}")
        fields[name] = value
    return key


def apply_fields(fields: MutableMapping[str, Any], policy: Mapping[str, Any] | None = None) -> bool:
    """Apply a source-id keyed correction to one record."""
    policy = policy or load_policy()
    source_id = str(fields.get("SourceID", "")).strip()
    entry = policy.get("records", {}).get(source_id)
    if not entry:
        return False
    expected = entry.get("expected", {})
    for name, value in expected.items():
        desired = entry.get("set", {}).get(name)
        if str(fields.get(name, "")) not in {str(value), str(desired)}:
            raise ReviewPolicyError(f"stale field for {source_id}: {name}")
    for name, value in entry.get("set", {}).items():
        fields[name] = value
    return True


def _resolve_reviewed_fields(
    fields: Mapping[str, Any],
    source_refs: list[str] | None = None,
    policy: Mapping[str, Any] | None = None,
    reflexive_policy: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], bool, str | None, bool]:
    """Resolve generic and reflexive review fields without mutating input."""

    if not isinstance(fields, Mapping):
        raise ReviewPolicyError("record fields must be a mapping")
    resolved = dict(fields)
    policy = policy or load_policy()
    reflexive_policy = reflexive_policy or load_reflexive_policy()
    source_id = str(resolved.get("SourceID", "")).strip()
    generic_entry = policy.get("records", {}).get(source_id, {})
    generic_full_explicit = "AcceptedFullAnswersDE" in generic_entry.get("set", {})
    explicit = apply_fields(resolved, policy)
    if source_refs is None:
        source_refs = [
            item.strip()
            for item in str(resolved.get("SourceRefs", "")).split("|")
            if item.strip()
        ]
    else:
        source_refs = [str(item).strip() for item in source_refs if str(item).strip()]
    reflexive_key = apply_reflexive_fields(
        resolved, source_refs, reflexive_policy,
    )
    reflexive_full_explicit = bool(
        reflexive_key
        and "AcceptedFullAnswersDE"
        in reflexive_policy["records"][reflexive_key].get("set", {})
    )
    if reflexive_key:
        explicit = True
    full_explicit = generic_full_explicit or reflexive_full_explicit
    lemma = str(resolved.get("Lemma", "")).strip()
    # Preserve the historical fallback for a reviewed production lemma that
    # is already explicitly written as ``sich ...``.  Marked optional forms
    # must come from reflexive_policy rather than this heuristic.
    if not explicit and lemma.startswith("sich "):
        stem = lemma[5:].strip()
        resolved["AcceptedAnswersDE"] = lemma
        resolved["AcceptedFullAnswersDE"] = f"{lemma}|s {stem}"
        explicit = True
        full_explicit = True
    return resolved, explicit, reflexive_key, full_explicit


def resolve_reviewed_fields(
    fields: Mapping[str, Any],
    source_refs: list[str] | None = None,
    policy: Mapping[str, Any] | None = None,
    reflexive_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a detached record with all reviewed field overrides applied."""

    resolved, _, _, _ = _resolve_reviewed_fields(
        fields,
        source_refs=source_refs,
        policy=policy,
        reflexive_policy=reflexive_policy,
    )
    return resolved


def reviewed_full_answer_override(
    fields: Mapping[str, Any],
    source_refs: list[str] | None = None,
    policy: Mapping[str, Any] | None = None,
    reflexive_policy: Mapping[str, Any] | None = None,
) -> str | None:
    """Return only an explicitly reviewed full-answer override, if present."""

    resolved, _, _, full_explicit = _resolve_reviewed_fields(
        fields,
        source_refs=source_refs,
        policy=policy,
        reflexive_policy=reflexive_policy,
    )
    if not full_explicit or "AcceptedFullAnswersDE" not in resolved:
        return None
    value = resolved["AcceptedFullAnswersDE"]
    if not isinstance(value, str):
        raise ReviewPolicyError("AcceptedFullAnswersDE must be text")
    return value


def apply_all(
    records: Any,
    policy: Mapping[str, Any] | None = None,
    reflexive_policy: Mapping[str, Any] | None = None,
    *,
    strict_reflexive: bool = False,
) -> int:
    policy = policy or load_policy()
    reflexive_policy = reflexive_policy or load_reflexive_policy()
    changed = 0
    matched_reflexive: set[str] = set()
    values = records.values() if isinstance(records, Mapping) else records
    for record in values:
        fields = record.get("fields", record) if isinstance(record, Mapping) else record
        if isinstance(fields, MutableMapping):
            source_refs = (
                list(record.get("source_refs", []))
                if isinstance(record, Mapping)
                else str(fields.get("SourceRefs", "")).split("|")
            )
            resolved, explicit, reflexive_key, _ = _resolve_reviewed_fields(
                fields,
                source_refs=source_refs,
                policy=policy,
                reflexive_policy=reflexive_policy,
            )
            if reflexive_key:
                matched_reflexive.add(reflexive_key)
            if explicit:
                for name, value in resolved.items():
                    fields[name] = value
            changed += int(explicit)
    if strict_reflexive:
        expected = set(reflexive_policy.get("records", {}))
        if matched_reflexive != expected:
            missing = sorted(expected - matched_reflexive)
            raise ReviewPolicyError(
                f"reflexive policy coverage missing: {missing[:5]}"
            )
    return changed
