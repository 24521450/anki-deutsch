"""Reviewed content corrections shared by offline and live workflows."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, MutableMapping

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "review" / "goethe_record_overrides.json"

REFLEXIVE_RE = re.compile(r"^(?:\(sich\)|sich)\s+(.+)$", re.I)


class ReviewPolicyError(ValueError):
    pass


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("records"), dict):
        raise ReviewPolicyError("unsupported review correction policy")
    return data


def reflexive_forms(value: str) -> list[str] | None:
    match = REFLEXIVE_RE.match(value.strip())
    if not match:
        return None
    stem = match.group(1).strip()
    if not stem:
        raise ReviewPolicyError("empty reflexive lemma")
    return [f"sich {stem}", f"s {stem}"]


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


def apply_all(records: Any, policy: Mapping[str, Any] | None = None) -> int:
    policy = policy or load_policy()
    changed = 0
    values = records.values() if isinstance(records, Mapping) else records
    for record in values:
        fields = record.get("fields", record) if isinstance(record, Mapping) else record
        if isinstance(fields, MutableMapping):
            explicit = apply_fields(fields, policy)
            lemma = str(fields.get("Lemma", "")).strip()
            # Every reviewed reflexive entry requires the marker in production,
            # including the twenty records that already use ``sich``.
            if not explicit and lemma.startswith("sich "):
                stem = lemma[5:].strip()
                fields["AcceptedAnswersDE"] = lemma
                fields["AcceptedFullAnswersDE"] = f"{lemma}|s {stem}"
                explicit = True
            changed += int(explicit)
    return changed
