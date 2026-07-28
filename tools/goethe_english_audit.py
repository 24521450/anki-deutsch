"""Build, validate, apply, and verify the canonical Goethe English audit v5.

The checked-in v5 catalog is allowed to be an honest review scaffold. Commands
that read or mutate live Anki state require the stricter evidence-backed
validation and therefore fail closed until every row has been reviewed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from urllib.parse import urlparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_examples
import goethe_apkg as apkg
import goethe_scope
import goethe_source_examples
import goethe_target_highlights
import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
SCHEMA_VERSION = 5
MANIFEST = ROOT / "review" / "goethe_english_audit_v5.jsonl"
V4_MANIFEST = ROOT / "review" / "goethe_english_audit_v4.jsonl"
V3_MANIFEST = ROOT / "review" / "goethe_english_audit_v3.jsonl"
COMPLETION_MANIFEST = ROOT / "tools" / ".goethe_completion" / "manifest.json"
B1_LEGACY_AUDIT = ROOT / "review" / "goethe_b1_english_audit.jsonl"
B1_LEGACY_OVERRIDES = ROOT / "review" / "goethe_b1_english_overrides.json"
REBASE_OVERRIDES = ROOT / "review" / "goethe_english_rebase_overrides.json"
STATE = ROOT / "tools" / ".goethe_english_audit_v5"
SNAPSHOT = STATE / "snapshot.json"
MODEL = gw.MODEL
PARENT_DECK = "Goethe Institute"
LEVELS = goethe_scope.LEVELS
EXPECTED_NOTES_BY_LEVEL = goethe_scope.EXPECTED_NOTES_BY_LEVEL
EXPECTED_CATALOG_NOTES = goethe_scope.EXPECTED_NOTES
EXPECTED_LIVE_NOTES = goethe_scope.EXPECTED_NOTES
EXPECTED_LIVE_CARDS = goethe_scope.EXPECTED_CARDS
BATCH_COUNTS = {
    **{f"V5-{index:02d}": 250 for index in range(1, 14)},
    "V5-14": 175,
}
OLD_VERIFIED_TAG = "goethe::quality::english_verified::british"
OLD_AUDITED_TAG = "goethe::quality::english_audited::british"
V3_AUDITED_TAG = "goethe::quality::english_audited::v3::british"
V4_AUDITED_TAG = "goethe::quality::english_audited::v4::british"
AUDITED_TAG = goethe_scope.ENGLISH_AUDITED_TAG

EVIDENCE_HOSTS = {
    "Cambridge": "dictionary.cambridge.org",
    "Collins": "www.collinsdictionary.com",
    "Duden": "www.duden.de",
}


def evidence_url_is_specific(provider: str, raw_url: str) -> bool:
    """Accept only direct entries (or Duden's explicit search endpoint)."""
    parsed = urlparse(raw_url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != EVIDENCE_HOSTS.get(provider, ""):
        return False
    path = parsed.path.rstrip("/")
    if provider == "Cambridge":
        return (
            path.startswith("/dictionary/german-english/")
            and bool(path.removeprefix("/dictionary/german-english/"))
        ) or (
            path.startswith("/us/dictionary/english/")
            and bool(path.removeprefix("/us/dictionary/english/"))
        )
    if provider == "Collins":
        return path.startswith("/dictionary/german-english/") and bool(path.removeprefix("/dictionary/german-english/"))
    if path.startswith("/rechtschreibung/"):
        return bool(path.removeprefix("/rechtschreibung/"))
    if path.startswith("/suchen/dudenonline/"):
        return bool(path.removeprefix("/suchen/dudenonline/"))
    if path == "/suchen/dudenonline":
        return bool(parsed.query)
    return False
REVIEW_TAG = goethe_scope.ENGLISH_REVIEW_TAG
LEGACY_ENGLISH_TAGS = {
    OLD_VERIFIED_TAG, OLD_AUDITED_TAG, V3_AUDITED_TAG, V4_AUDITED_TAG, REVIEW_TAG,
}
CONFIRMATION = "APPLY_GOETHE_ENGLISH_AUDIT_V5"
REBASE_CONFIRMATION = "REBASE_GOETHE_ENGLISH_AUDIT_V5"
PILOT_SOURCE_IDS = [
    "A2-WG-0092",
    "A1-84886454810",
    "A2-0851", "A2-MAIN-0202", "A2-0404", "A1-84886454916",
    "A2-0853", "A1-84886454763", "A1-84886454835", "A2-0654",
    "A2-0691", "A2-0074", "A1-84886455054", "A1-84886455126",
    "A1-84886454920", "A1-84886455037", "A1-84886455211",
    "A2-1152", "A2-1184", "A2-1189", "A2-WG-0173", "A2-WG-0044",
    "B1-MAIN-0002", "B1-MAIN-1252", "B1-WG-0161",
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
    return apkg.valid_apkg(path)


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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read JSONL: {path}") from exc


def manifest_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids = [str(row.get("source_id", "")) for row in rows]
    duplicates = sorted(source_id for source_id, count in Counter(source_ids).items() if count > 1)
    if duplicates:
        raise AuditError(f"duplicate source_id in audit catalog: {duplicates[:5]}")
    entries = dict(zip(source_ids, rows))
    desired_groups: dict[tuple[str, str], list[str]] = {}
    for entry in rows:
        meaning = str(entry.get("desired_meaning_en", "")).strip().casefold()
        if meaning:
            desired_groups.setdefault((str(entry.get("cefr", "")), meaning), []).append(
                str(entry.get("source_id", ""))
            )
    collisions = [
        {"cefr": key[0], "meaning_en": key[1], "source_ids": values}
        for key, values in sorted(desired_groups.items())
        if len(values) > 1
    ]
    schema_versions = {row.get("schema_version", 3) for row in rows}
    schema_version = next(iter(schema_versions)) if len(schema_versions) == 1 else None
    counts: dict[str, int] = {
        "notes": len(rows),
        "reviewed": sum(item.get("review_status") == "reviewed" for item in rows),
        "unreviewed": sum(item.get("review_status") != "reviewed" for item in rows),
        "keep": sum(item.get("decision") == "KEEP" for item in rows),
        "revise": sum(item.get("decision") == "REVISE" for item in rows),
        "pending": sum(item.get("decision") == "PENDING" for item in rows),
        "meaning_updates": sum(
            item.get("expected_meaning_en") != item.get("desired_meaning_en") for item in rows
        ),
        "example_updates": sum(
            example_pairs(item.get("expected_examples", []))
            != example_pairs(item.get("desired_examples", []))
            for item in rows
        ),
        "no_examples": sum(not item.get("desired_examples") for item in rows),
        "b1_no_examples": sum(
            item.get("cefr") == "B1" and not item.get("desired_examples") for item in rows
        ),
        "ambiguous_prompt_groups": len(collisions),
    }
    counts.update({
        level.casefold(): sum(item.get("cefr") == level for item in rows)
        for level in LEVELS
    })
    return {
        "schema_version": schema_version,
        "audit_id": (
            "goethe-english-v5-american-2026-07"
            if schema_version == SCHEMA_VERSION
            else "goethe-english-v4-2026-07"
            if schema_version == 4
            else "goethe-english-v3-2026-07"
        ),
        "standard": "American English" if schema_version == SCHEMA_VERSION else "British English",
        "primary_source": "Cambridge German-English Dictionary",
        "entries": entries,
        "ambiguous_prompt_groups": collisions,
        "counts": counts,
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        if path.suffix == ".jsonl":
            return manifest_from_rows(load_jsonl(path))
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read JSON: {path}") from exc


def example_pairs(examples: list[dict[str, Any]]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    by_german: dict[str, int] = {}
    for item in goethe_examples.merge_dialogue_replies(examples):
        pair = {"de": item["de"], "en": item["en"]}
        key = pair["de"].strip()
        previous_index = by_german.get(key)
        if previous_index is None:
            by_german[key] = len(pairs)
            pairs.append(pair)
            continue
        previous = pairs[previous_index]
        if previous == pair or (previous["en"] and not pair["en"]):
            continue
        if pair["en"] and not previous["en"]:
            pairs[previous_index] = pair
            continue
        pairs.append(pair)
    return pairs


def normalize_meaning(value: str) -> str:
    """Apply only the deck-wide delimiter convention chosen for this audit."""
    return value.strip().replace(" / ", "; ")


def stable_guid(fields: dict[str, str]) -> str:
    try:
        return goethe_scope.stable_guid(fields)
    except goethe_scope.ScopeError as exc:
        raise AuditError(str(exc)) from exc


def source_refs(fields: dict[str, str], record: dict[str, Any] | None = None) -> list[str]:
    values = list((record or {}).get("source_refs", []))
    values.extend(str(fields.get("SourceRefs", "")).split("|"))
    source_id = str(fields.get("SourceID", "")).strip()
    ordered = [source_id, *(str(value).strip() for value in values)]
    return list(dict.fromkeys(value for value in ordered if value))


def raw_source_refs(fields: dict[str, str]) -> list[str]:
    """Read stored provenance order without silently prepending ``SourceID``."""
    return [
        value.strip()
        for value in str(fields.get("SourceRefs", "")).split("|")
        if value.strip()
    ]


def scaffold_examples(record: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"de": item["de"], "en": item["en"], "origin": "goethe"}
        for item in example_pairs(record.get("examples", []))
    ]


def _select_v3_entry(
    fields: dict[str, str], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    if not candidates:
        raise AuditError(f"no v3 audit row for canonical note: {fields.get('SourceID')}")
    direct = [item for item in candidates if item["source_id"] == fields.get("SourceID")]
    if len(direct) == 1:
        return direct[0]
    guid = stable_guid(fields)
    by_guid = [item for item in candidates if item.get("legacy_guid") == guid]
    if len(by_guid) == 1:
        return by_guid[0]
    by_lemma = [item for item in candidates if item.get("lemma") == fields.get("Lemma")]
    if len(by_lemma) == 1:
        return by_lemma[0]
    if len(candidates) == 1:
        return candidates[0]
    raise AuditError(f"ambiguous v3 canonical row: {fields.get('SourceID')}")


def _union_evidence(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        for item in entry.get("evidence", []):
            marker = canonical_hash(item)
            if marker not in seen:
                seen.add(marker)
                evidence.append(item)
    return evidence


def build_v5_scaffold(
    completion: dict[str, Any],
    v3_rows: list[dict[str, Any]],
    legacy_b1_rows: list[dict[str, Any]] | None = None,
    legacy_b1_overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build canonical v5 rows without promoting B1 hints to reviewed evidence."""
    records = list(completion.get("records", {}).values())
    level_counts = Counter(record.get("fields", {}).get("CEFR") for record in records)
    cards = sum(len(record.get("cards", [])) for record in records)
    if dict(level_counts) != EXPECTED_NOTES_BY_LEVEL or cards != EXPECTED_LIVE_CARDS:
        raise AuditError(
            f"completion snapshot is not canonical: levels={dict(level_counts)}, cards={cards}"
        )

    v3_by_id = {entry["source_id"]: entry for entry in v3_rows}
    if len(v3_by_id) != len(v3_rows):
        raise AuditError("v3 audit contains duplicate source IDs")
    legacy_by_id = {
        entry["source_id"]: entry for entry in (legacy_b1_rows or [])
        if entry.get("source_id")
    }
    overrides = legacy_b1_overrides or {}
    rows: list[dict[str, Any]] = []
    covered_v3: set[str] = set()
    b1_index = 0

    for record in sorted(
        records,
        key=lambda item: (
            goethe_scope.LEVEL_RANK.get(item.get("fields", {}).get("CEFR"), 99),
            item.get("fields", {}).get("SourceID", ""),
        ),
    ):
        fields = record["fields"]
        source_id = fields.get("SourceID", "").strip()
        refs = source_refs(fields, record)
        if not source_id or not refs:
            raise AuditError("completion record has no canonical source identity")
        examples = scaffold_examples(record)
        row: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source_id": source_id,
            "source_refs": refs,
            "stable_guid": stable_guid(fields),
            "lemma": fields.get("Lemma", ""),
            "cefr": fields.get("CEFR", ""),
            "pos": fields.get("POS", ""),
            "expected_meaning_en": fields.get("MeaningEN", "").strip(),
            "desired_meaning_en": fields.get("MeaningEN", "").strip(),
            "expected_examples": examples,
            "desired_examples": copy.deepcopy(examples),
        }
        if fields.get("CEFR") in {"A1", "A2"}:
            candidates = [v3_by_id[ref] for ref in refs if ref in v3_by_id]
            primary = _select_v3_entry(fields, candidates)
            covered_v3.update(item["source_id"] for item in candidates)
            row.update({
                "decision": primary["decision"],
                "review_status": "reviewed",
                "difficult": bool(primary.get("difficult")),
                "reason": primary.get("reason", "")
                + " Migrated from the reviewed v3 canonical row; current German text is authoritative.",
                "evidence": _union_evidence([primary, *(
                    item for item in candidates if item is not primary
                )]),
                "audit_batch": "A1-A2-v3-migration",
                "review_provenance": "goethe-english-v3-2026-07",
                "collapsed_v3_source_ids": [item["source_id"] for item in candidates],
                "previous_meaning_en": primary.get("expected_meaning_en", ""),
                "previous_examples": primary.get("expected_examples", []),
            })
        else:
            b1_index += 1
            hint = legacy_by_id.get(source_id)
            override_hints = {ref: overrides[ref] for ref in refs if ref in overrides}
            legacy_hints: dict[str, Any] = {
                "classification": "hint_only_not_review_evidence",
            }
            if hint:
                legacy_hints["freedict"] = {
                    "status": hint.get("status", ""),
                    "best_dictionary_score": hint.get("best_dictionary_score", 0.0),
                    "dictionary_candidates": hint.get("dictionary_candidates", []),
                }
            if override_hints:
                legacy_hints["old_overrides"] = override_hints
            row.update({
                "decision": "PENDING",
                "review_status": "unreviewed",
                # A scaffold must carry an explicit boolean so a reviewer
                # cannot accidentally pass a null/implicit difficulty state.
                "difficult": False,
                "reason": (
                    "Pending manual American-English review against Cambridge and Duden/Collins; "
                    "current English is copied only to make this scaffold diffable."
                ),
                "evidence": [],
                "audit_batch": f"B1-{((b1_index - 1) // 250) + 1:02d}",
                "legacy_hints": legacy_hints,
            })
        rows.append(row)

    if covered_v3 != set(v3_by_id):
        missing = sorted(set(v3_by_id) - covered_v3)
        raise AuditError(f"v3 rows not collapsed into canonical notes: {missing[:5]}")
    validate_scaffold(manifest_from_rows(rows))
    return rows


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(name, path)
    except Exception:
        if os.path.exists(name):
            os.unlink(name)
        raise


def guard_scaffold_overwrite(output: Path, *, force: bool) -> None:
    if output.exists() and not force:
        reviewed_b1 = [
            row.get("source_id", "")
            for row in load_jsonl(output)
            if row.get("cefr") == "B1" and row.get("review_status") == "reviewed"
        ]
        if reviewed_b1:
            raise AuditError(
                "refusing to overwrite reviewed B1 rows; "
                "pass --force-overwrite-reviewed to discard them explicitly "
                f"({len(reviewed_b1)} rows, first={reviewed_b1[0]})"
            )


def command_scaffold(args: argparse.Namespace) -> None:
    guard_scaffold_overwrite(args.output, force=args.force_overwrite_reviewed)
    completion = load_json(args.completion_manifest)
    v3_rows = load_jsonl(args.v3_manifest)
    legacy_rows = load_jsonl(args.legacy_b1_audit) if args.legacy_b1_audit.exists() else []
    overrides = load_json(args.legacy_b1_overrides) if args.legacy_b1_overrides.exists() else {}
    rows = build_v5_scaffold(completion, v3_rows, legacy_rows, overrides)
    atomic_jsonl(args.output, rows)
    manifest = manifest_from_rows(rows)
    print(json.dumps({"catalog": str(args.output), **manifest["counts"]}, ensure_ascii=False, indent=2))


def _case_preserving_replacement(match: re.Match[str], replacement: str) -> str:
    value = match.group(0)
    if value.isupper():
        return replacement.upper()
    if value[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


AMERICAN_REPLACEMENTS = (
    (r"\bat the weekend\b", "on the weekend"),
    (r"\bcar parks\b", "parking lots"),
    (r"\bcar park\b", "parking lot"),
    (r"\bpetrol stations\b", "gas stations"),
    (r"\bpetrol station\b", "gas station"),
    (r"\brailway stations\b", "train stations"),
    (r"\brailway station\b", "train station"),
    (r"\breturn tickets\b", "round-trip tickets"),
    (r"\breturn ticket\b", "round-trip ticket"),
    (r"\bsingle tickets\b", "one-way tickets"),
    (r"\bsingle ticket\b", "one-way ticket"),
    (r"\bmobile phones\b", "cell phones"),
    (r"\bmobile phone\b", "cell phone"),
    (r"\bpostcodes\b", "ZIP codes"),
    (r"\bpostcode\b", "ZIP code"),
    (r"\bpavements\b", "sidewalks"),
    (r"\bpavement\b", "sidewalk"),
    (r"\bchemist['’]?s\b", "pharmacy"),
    (r"\bmotorways\b", "highways"),
    (r"\bmotorway\b", "highway"),
    (r"\baeroplanes\b", "airplanes"),
    (r"\baeroplane\b", "airplane"),
    (r"\blorries\b", "trucks"),
    (r"\blorry\b", "truck"),
    (r"\btyres\b", "tires"),
    (r"\btyre\b", "tire"),
    (r"\bpetrol\b", "gas"),
    (r"\brubbish\b", "trash"),
    (r"\bmaths\b", "math"),
    (r"\bper cent\b", "percent"),
    (r"\bprogrammes\b", "programs"),
    (r"\bprogramme\b", "program"),
    (r"\bneighbourhoods\b", "neighborhoods"),
    (r"\bneighbourhood\b", "neighborhood"),
    (r"\bneighbours\b", "neighbors"),
    (r"\bneighbour\b", "neighbor"),
    (r"\bfavourites\b", "favorites"),
    (r"\bfavourite\b", "favorite"),
    (r"\bcolours\b", "colors"),
    (r"\bcolour\b", "color"),
    (r"\bcentimetres\b", "centimeters"),
    (r"\bcentimetre\b", "centimeter"),
    (r"\bkilometres\b", "kilometers"),
    (r"\bkilometre\b", "kilometer"),
    (r"\bmetres\b", "meters"),
    (r"\bmetre\b", "meter"),
    (r"\blitres\b", "liters"),
    (r"\blitre\b", "liter"),
    (r"\bcentres\b", "centers"),
    (r"\bcentre\b", "center"),
    (r"\btheatres\b", "theaters"),
    (r"\btheatre\b", "theater"),
    (r"\btravelling\b", "traveling"),
    (r"\btravelled\b", "traveled"),
    (r"\btravellers\b", "travelers"),
    (r"\btraveller\b", "traveler"),
    (r"\bcancelling\b", "canceling"),
    (r"\bcancelled\b", "canceled"),
    (r"\blabelling\b", "labeling"),
    (r"\blabelled\b", "labeled"),
    (r"\borganisations\b", "organizations"),
    (r"\borganisation\b", "organization"),
    (r"\borganised\b", "organized"),
    (r"\borganising\b", "organizing"),
    (r"\brecognised\b", "recognized"),
    (r"\brecognising\b", "recognizing"),
    (r"\bapologised\b", "apologized"),
    (r"\bapologising\b", "apologizing"),
    (r"\bbehaviour\b", "behavior"),
    (r"\blabour\b", "labor"),
    (r"\bjewellery\b", "jewelry"),
    (r"\bcheques\b", "checks"),
    (r"\bcheque\b", "check"),
    (r"\blicences\b", "licenses"),
    (r"\blicence\b", "license"),
    (r"\bpractise\b", "practice"),
    (r"\bgrey\b", "gray"),
    (r"\bstoreys\b", "stories"),
    (r"\bstorey\b", "story"),
    (r"\bkerbs\b", "curbs"),
    (r"\bkerb\b", "curb"),
)


def americanize_text(value: str) -> str:
    result = str(value or "")
    for pattern, replacement in AMERICAN_REPLACEMENTS:
        result = re.sub(
            pattern,
            lambda match, replacement=replacement: _case_preserving_replacement(
                match, replacement,
            ),
            result,
            flags=re.I,
        )
    return result


def americanize_context(german: str, english: str) -> str:
    result = americanize_text(english)
    german_fold = german.casefold()
    german_ascii = unicodedata.normalize(
        "NFKD", german_fold.replace("ß", "ss"),
    ).encode("ascii", "ignore").decode("ascii")
    contextual: list[tuple[str, str]] = []
    if "urlaub" in german_ascii or "ferien" in german_ascii:
        contextual.extend([(r"\bholidays\b", "vacations"), (r"\bholiday\b", "vacation")])
    if "fussball" in german_ascii:
        contextual.append((r"\bfootball\b", "soccer"))
    if "wohnung" in german_ascii or "apartment" in german_ascii or "appartement" in german_ascii:
        contextual.extend([(r"\bflats\b", "apartments"), (r"\bflat\b", "apartment")])
    if "aufzug" in german_ascii or "fahrstuhl" in german_ascii:
        contextual.extend([(r"\blifts\b", "elevators"), (r"\blift\b", "elevator")])
    if "erdgeschoss" in german_ascii:
        contextual.append((r"\bground floor\b", "first floor"))
    if re.search(r"\b(?:ersten|1\.) stock\b", german_ascii):
        contextual.append((r"\bfirst floor\b", "second floor"))
    if re.search(r"\b(?:zweiten|2\.) stock\b", german_ascii):
        contextual.append((r"\bsecond floor\b", "third floor"))
    if "apotheke" in german_ascii:
        contextual.extend([(r"\bdrugstore\b", "pharmacy"), (r"\bchemist['’]?s\b", "pharmacy")])
    if "fuhrerschein" in german_ascii:
        contextual.extend([
            (r"\bdriving license\b", "driver's license"),
            (r"\bdriver license\b", "driver's license"),
        ])
    if re.search(r"\b(?:handy|mobiltelefon)\b", german_ascii):
        contextual.append((r"\bmobile phone\b", "cell phone"))
    if "kino" in german_ascii:
        contextual.extend([
            (r"\bcinemas\b", "movie theaters"),
            (r"\bcinema\b", "movie theater"),
        ])
    if "film" in german_ascii:
        contextual.extend([(r"\bfilms\b", "movies"), (r"\bfilm\b", "movie")])
    if "herbst" in german_ascii:
        contextual.append((r"\bautumn\b", "fall"))
    if "sussigkeit" in german_ascii:
        contextual.append((r"\bsweets\b", "candy"))
    if "pommes" in german_ascii:
        contextual.append((r"\bchips\b", "fries"))
    if "schulnote" in german_ascii or re.search(r"\bnoten?\b", german_ascii):
        contextual.append((r"\bmarks\b", "grades"))
    if "rechnung" in german_ascii or re.search(r"\bzahlen\b", german_ascii):
        contextual.append((r"\bthe bill\b", "the check"))
    for pattern, replacement in contextual:
        result = re.sub(
            pattern,
            lambda match, replacement=replacement: _case_preserving_replacement(
                match, replacement,
            ),
            result,
            flags=re.I,
        )
    return result


def rebase_rows(
    completion: dict[str, Any],
    current: dict[str, Any],
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rebase reviewed English rows onto a source-canonical completion snapshot."""
    current_entries = current.get("entries", {})
    if not isinstance(current_entries, dict):
        raise AuditError("current English audit entries are invalid")
    override_entries = overrides.get("entries", {})
    if overrides.get("version") != 1 or not isinstance(override_entries, dict):
        raise AuditError("unsupported English rebase override schema")

    by_guid = {
        str(entry.get("stable_guid", "")): entry
        for entry in current_entries.values()
        if str(entry.get("stable_guid", ""))
    }
    global_translation_candidates: dict[str, set[str]] = {}
    for entry in current_entries.values():
        for example in [
            *entry.get("expected_examples", []),
            *entry.get("desired_examples", []),
        ]:
            key = goethe_source_examples.sentence_key(example.get("de", ""))
            translation = str(example.get("en", "")).strip()
            if key and translation:
                global_translation_candidates.setdefault(key, set()).add(translation)
    global_translations = {
        key: next(iter(values))
        for key, values in global_translation_candidates.items()
        if len(values) == 1
    }
    live_translation_by_note: dict[str, dict[str, str]] = {}
    for note_id, note in completion.get("live_preimage", {}).get("notes", {}).items():
        fields = note.get("fields", {})
        translations: dict[str, str] = {}
        for index in range(1, 5):
            german_parts = str(fields.get(f"Example{index}DE", "")).split("<br>")
            english_parts = str(fields.get(f"Example{index}EN", "")).split("<br>")
            for german, english in zip(german_parts, english_parts):
                key = goethe_source_examples.sentence_key(german)
                if key and english.strip():
                    translations[key] = english.strip()
        live_translation_by_note[str(note_id)] = translations
    rows: list[dict[str, Any]] = []
    incomplete_examples: list[tuple[str, str]] = []
    for record in completion.get("records", {}).values():
        fields = record["fields"]
        source_id = str(fields.get("SourceID", "")).strip()
        refs = list(dict.fromkeys([source_id, *source_refs(fields, record)]))
        guid = stable_guid(fields)
        base = current_entries.get(source_id) or by_guid.get(guid)
        if base is None:
            candidates = [
                entry for entry in current_entries.values()
                if set(refs) & set(entry.get("source_refs", []))
            ]
            if candidates:
                base = max(
                    candidates,
                    key=lambda entry: len(set(refs) & set(entry.get("source_refs", []))),
                )
        override = override_entries.get(source_id, {})
        if base is None and not override:
            raise AuditError(f"new audit identity needs a reviewed override: {source_id}")
        if record.get("is_new") and source_id not in current_entries and not override:
            raise AuditError(f"split audit identity needs a reviewed override: {source_id}")

        base = copy.deepcopy(base or {})
        old_desired = {
            goethe_source_examples.sentence_key(item.get("de", "")): str(item.get("en", ""))
            for item in base.get("desired_examples", [])
        }
        old_expected = {
            goethe_source_examples.sentence_key(item.get("de", "")): str(item.get("en", ""))
            for item in base.get("expected_examples", [])
        }
        live_translations = live_translation_by_note.get(str(record.get("note_id", "")), {})
        translation_overrides = {
            goethe_source_examples.sentence_key(de): en
            for de, en in override.get("example_translations", {}).items()
        }
        expected_examples: list[dict[str, str]] = []
        desired_examples: list[dict[str, str]] = []
        for example in record.get("examples", []):
            de = str(example.get("de", "")).strip()
            key = goethe_source_examples.sentence_key(de)
            expected_en = (
                translation_overrides.get(key)
                or str(example.get("en", "")).strip()
                or live_translations.get(key)
                or old_expected.get(key)
                or old_desired.get(key)
                or global_translations.get(key)
            )
            desired_en = americanize_context(de, (
                translation_overrides.get(key)
                or live_translations.get(key)
                or old_desired.get(key)
                or global_translations.get(key)
                or expected_en
            ))
            if not de or not expected_en or not desired_en:
                incomplete_examples.append((source_id, de))
                continue
            origin = str(example.get("origin") or "goethe")
            expected_examples.append({"de": de, "en": expected_en, "origin": origin})
            desired_examples.append({"de": de, "en": desired_en, "origin": origin})

        authored_expected = {
            goethe_source_examples.sentence_key(item["de"])
            for item in expected_examples
            if item["origin"] == "review-authored"
        }
        authored_desired = {
            goethe_source_examples.sentence_key(item["de"])
            for item in desired_examples
            if item["origin"] == "review-authored"
        }
        expected_examples = goethe_examples.merge_dialogue_replies(expected_examples)
        desired_examples = goethe_examples.merge_dialogue_replies(desired_examples)
        for example in expected_examples:
            example["origin"] = (
                "review-authored"
                if goethe_source_examples.sentence_key(example["de"]) in authored_expected
                else "goethe"
            )
            example.pop("audio", None)
        for example in desired_examples:
            example["origin"] = (
                "review-authored"
                if goethe_source_examples.sentence_key(example["de"]) in authored_desired
                else "goethe"
            )
            example.pop("audio", None)
        expected_meaning = str(fields.get("MeaningEN", "")).strip()
        desired_meaning = americanize_context(str(fields.get("Lemma", "")), str(
            override.get("meaning_en")
            or base.get("desired_meaning_en")
            or expected_meaning
        )).strip()
        if not expected_meaning:
            expected_meaning = desired_meaning
        if not desired_meaning:
            raise AuditError(f"English rebase meaning is missing: {source_id}")

        row = base
        row.update({
            "schema_version": SCHEMA_VERSION,
            "source_id": source_id,
            "source_refs": refs,
            "stable_guid": guid,
            "lemma": fields.get("Lemma", ""),
            "cefr": fields.get("CEFR", ""),
            "pos": fields.get("POS", ""),
            "expected_meaning_en": expected_meaning,
            "desired_meaning_en": desired_meaning,
            "expected_examples": expected_examples,
            "desired_examples": desired_examples,
            "review_status": "reviewed",
            "english_variant": "American English",
            "american_review_status": "reviewed",
            "american_review_provenance": "goethe-english-v5-2026-07",
            "difficult": bool(override.get("difficult", base.get("difficult", False))),
            "reason": str(
                override.get("reason")
                or base.get("reason")
                or "Rebased onto the reviewed source identity."
            ),
            "evidence": copy.deepcopy(override.get("evidence", base.get("evidence", []))),
        })
        changed = (
            row["expected_meaning_en"] != row["desired_meaning_en"]
            or example_pairs(row["expected_examples"]) != example_pairs(row["desired_examples"])
        )
        row["decision"] = "REVISE" if changed else "KEEP"
        row["change_categories"] = ["american_english"] if changed else []
        if not row["evidence"]:
            raise AuditError(f"English rebase evidence is missing: {source_id}")
        rows.append(row)

    if incomplete_examples:
        preview = ", ".join(
            f"{source_id} {de!r}"
            for source_id, de in incomplete_examples[:10]
        )
        raise AuditError(
            "English rebase examples need reviewed translations: "
            f"{len(incomplete_examples)} ({preview})"
        )
    rows.sort(key=lambda row: (
        goethe_scope.LEVEL_RANK.get(row["cefr"], 99),
        row["source_id"],
    ))
    for index, row in enumerate(rows):
        row["audit_batch"] = f"V5-{(index // 250) + 1:02d}"
    return rows


def command_rebase(args: argparse.Namespace) -> None:
    if args.confirmation != REBASE_CONFIRMATION:
        raise AuditError(f"confirmation must equal {REBASE_CONFIRMATION}")
    completion = load_json(args.completion_manifest)
    current = load_json(args.current_manifest)
    overrides = load_json(args.overrides)
    rows = rebase_rows(completion, current, overrides)
    atomic_jsonl(args.output, rows)
    counts = manifest_from_rows(rows)["counts"]
    print(json.dumps({"catalog": str(args.output), **counts}, ensure_ascii=False, indent=2))


def command_inspect(_: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST)
    validate_scaffold(manifest)
    blockers = audit_blockers(manifest)
    print(json.dumps({
        "catalog": str(MANIFEST), **manifest["counts"],
        "full_validation": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
    }, ensure_ascii=False, indent=2))


def command_compile(_: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST)
    validate_manifest(manifest)
    print(json.dumps({"catalog": str(MANIFEST), **manifest["counts"]}, ensure_ascii=False, indent=2))


def batch_report(manifest: dict[str, Any], batch: str) -> dict[str, Any]:
    expected_rows = BATCH_COUNTS.get(batch)
    if expected_rows is None:
        raise AuditError(f"unknown v5 audit batch: {batch}")
    entries = [
        entry for entry in manifest.get("entries", {}).values()
        if entry.get("audit_batch") == batch
    ]
    if len(entries) != expected_rows:
        raise AuditError(f"{batch} must contain {expected_rows} rows, got {len(entries)}")

    source_ids = {str(entry["source_id"]) for entry in entries}
    blocker_ids: dict[str, list[str]] = {}
    blockers: Counter[str] = Counter()
    for entry in entries:
        for error in _review_entry_errors(entry):
            blockers[error] += 1
            blocker_ids.setdefault(error, []).append(str(entry["source_id"]))

    internal_collisions: list[dict[str, Any]] = []
    cross_batch_collisions: list[dict[str, Any]] = []
    for group in manifest.get("ambiguous_prompt_groups", []):
        group_ids = set(map(str, group.get("source_ids", [])))
        if not group_ids & source_ids:
            continue
        target = internal_collisions if group_ids <= source_ids else cross_batch_collisions
        target.append(group)

    verb_without_to = [
        str(entry["source_id"])
        for entry in entries
        if str(entry.get("pos", "")).casefold().startswith("v.")
        and not str(entry.get("desired_meaning_en", "")).casefold().startswith("to ")
    ]
    capitalised_gloss = [
        str(entry["source_id"])
        for entry in entries
        if str(entry.get("desired_meaning_en", ""))[:1].isupper()
    ]
    return {
        "batch": batch,
        "rows": len(entries),
        "examples": sum(len(entry.get("desired_examples", [])) for entry in entries),
        "reviewed": sum(entry.get("review_status") == "reviewed" for entry in entries),
        "keep": sum(entry.get("decision") == "KEEP" for entry in entries),
        "revise": sum(entry.get("decision") == "REVISE" for entry in entries),
        "pending": sum(entry.get("decision") == "PENDING" for entry in entries),
        "difficult": sum(entry.get("difficult") is True for entry in entries),
        "blockers": dict(sorted(blockers.items())),
        "blocker_source_ids": {key: sorted(value) for key, value in sorted(blocker_ids.items())},
        "internal_collision_groups": internal_collisions,
        "cross_batch_collision_groups": cross_batch_collisions,
        "style_warnings": {
            "verb_without_to": verb_without_to,
            "capitalised_gloss": capitalised_gloss,
        },
    }


def command_check_batch(args: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST)
    validate_scaffold(manifest)
    report = batch_report(manifest, args.batch)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    collision_count = (
        len(report["internal_collision_groups"])
        + len(report["cross_batch_collision_groups"])
    )
    if report["blockers"] or collision_count:
        detail = ", ".join(
            [*(f"{name}={count}" for name, count in report["blockers"].items()),
             f"prompt_collision_groups={collision_count}"]
        )
        raise AuditError(f"{args.batch} review is not ready: {detail}")


def find_entry(fields: dict[str, str], manifest: dict[str, Any]) -> dict[str, Any] | None:
    source_id = str(fields.get("SourceID", "")).strip()
    direct = manifest["entries"].get(source_id)
    guid = stable_guid(fields)
    matches = [
        entry for entry in manifest["entries"].values()
        if entry.get("stable_guid", entry.get("legacy_guid")) == guid
    ]
    if len(matches) > 1:
        raise AuditError(f"ambiguous stable GUID: {guid}")
    if direct is not None and matches and direct is not matches[0]:
        raise AuditError(f"source ID/stable GUID conflict: {source_id}")
    return direct or (matches[0] if matches else None)


def identity_equivalent(
    fields: dict[str, str], entry: dict[str, Any], manifest: dict[str, Any] | None = None,
) -> bool:
    """Accept only a representation-only alias of the exact audited note.

    Historical duplicate merges can leave an old survivor ``SourceID`` or a
    different provenance order in Anki.  The durable GUID and complete source
    reference set must still identify the same canonical row.  Missing, extra,
    duplicate, or cross-wired references are rejected.
    """
    expected_id = str(entry.get("source_id", "")).strip()
    expected_refs = entry.get("source_refs")
    if (
        not expected_id
        or not isinstance(expected_refs, list)
        or not expected_refs
        or expected_refs[0] != expected_id
        or len(expected_refs) != len(set(expected_refs))
    ):
        return False
    live_id = str(fields.get("SourceID", "")).strip()
    live_refs = raw_source_refs(fields)
    if (
        not live_id
        or not live_refs
        or len(live_refs) != len(set(live_refs))
        or set(live_refs) != set(expected_refs)
        or live_id not in live_refs
    ):
        return False
    if fields.get("CEFR", "") != entry.get("cefr", ""):
        return False
    if not identity_matches_reviewed_lemma(fields, str(entry.get("lemma", ""))):
        return False
    try:
        expected_guid = str(entry.get("stable_guid", entry.get("legacy_guid", "")))
        if not goethe_scope.guid_matches_expected(stable_guid(fields), expected_guid):
            return False
    except AuditError:
        return False
    if manifest is not None:
        direct = manifest.get("entries", {}).get(live_id)
        if direct is not None and direct is not entry:
            return False
    return True


def covered_source_ids(fields: dict[str, str], manifest: dict[str, Any]) -> set[str]:
    """Return the one canonical audit row carried by a current note."""
    entry = find_entry(fields, manifest)
    return {entry["source_id"]} if entry else set()


def pair_state(current: list[dict[str, str]], entry: dict[str, Any]) -> str:
    pairs = example_pairs(current)
    if pairs == example_pairs(entry["expected_examples"]):
        return "expected"
    if pairs == example_pairs(entry.get("previous_examples", [])):
        return "previous"
    desired = example_pairs(entry["desired_examples"])
    if pairs == desired:
        return "desired"
    if len(pairs) == len(desired) and all(
        current_pair["de"] == desired_pair["de"]
        and current_pair["en"] in {"", desired_pair["en"]}
        for current_pair, desired_pair in zip(pairs, desired)
    ):
        return "desired"
    return "drift"


def desired_fields(fields: dict[str, str], entry: dict[str, Any]) -> dict[str, str]:
    current_meaning = fields.get("MeaningEN", "").strip()
    allowed_meanings = {
        entry["expected_meaning_en"], entry.get("previous_meaning_en", ""), entry["desired_meaning_en"],
    }
    if current_meaning and current_meaning not in allowed_meanings and normalize_meaning(current_meaning) not in {
        normalize_meaning(value) for value in allowed_meanings
    }:
        raise AuditError(f"MeaningEN drift: {entry['source_id']} {current_meaning!r}")
    current_examples = goethe_examples.parse_fields(fields)
    if pair_state(current_examples, entry) == "drift":
        raise AuditError(f"example drift: {entry['source_id']}")
    audio_by_de = {item["de"]: item.get("audio", "") for item in current_examples}
    examples = goethe_examples.merge_dialogue_replies([
        {"de": item["de"], "en": item["en"], "audio": audio_by_de.get(item["de"], "")}
        for item in entry["desired_examples"]
    ])
    result = copy.deepcopy(fields)
    # Canonicalise representation-only merge aliases while applying the
    # reviewed row.  identity_equivalent() guards this path before it is used
    # against live records.
    result["SourceID"] = entry["source_id"]
    result["SourceRefs"] = "|".join(entry["source_refs"])
    result["MeaningEN"] = entry["desired_meaning_en"]
    goethe_examples.render_fields(result, examples)
    # Example count/order can change during a reviewed REVISE. Rebuild the
    # derived target ranges in the same transaction so standalone audit apply
    # cannot leave stale highlight JSON behind.
    result["ExampleTargetSpansJSON"] = goethe_target_highlights.build_target_spans(result)
    return result


def desired_tags(tags: list[str]) -> list[str]:
    return sorted((set(tags) - LEGACY_ENGLISH_TAGS) | {AUDITED_TAG})


def review_needed_tags(tags: list[str]) -> list[str]:
    return sorted((set(tags) - (LEGACY_ENGLISH_TAGS | {AUDITED_TAG})) | {REVIEW_TAG})


def apply_manifest_to_records(records: dict[str, dict[str, Any]], manifest: dict[str, Any], *, strict: bool) -> None:
    matched: set[str] = set()
    for record in records.values():
        entry = find_entry(record["fields"], manifest)
        if entry is None:
            record["tags"] = review_needed_tags(record["tags"])
            continue
        if entry.get("review_status") != "reviewed" or entry.get("decision") == "PENDING":
            record["tags"] = review_needed_tags(record["tags"])
            continue
        matched.update(covered_source_ids(record["fields"], manifest))
        record["fields"] = desired_fields(record["fields"], entry)
        record["examples"] = goethe_examples.parse_fields(record["fields"])
        record["tags"] = desired_tags(record["tags"])
    if strict and matched != set(manifest["entries"]):
        missing = set(manifest["entries"]) - matched
        raise AuditError(f"reviewed audit coverage missing from records: {sorted(missing)[:5]}")


def live_records() -> dict[int, dict[str, Any]]:
    if gw.anki("version") != 6:
        raise AuditError("unexpected AnkiConnect API version")
    ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(ids):
        notes.extend(gw.anki("notesInfo", notes=batch))
    notes = [
        note for note in notes
        if note.get("fields", {}).get("CEFR", {}).get("value", "") in LEVELS
    ]
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
    levels = Counter(record["fields"].get("CEFR") for record in records.values())
    if (
        len(records) != EXPECTED_LIVE_NOTES
        or cards != EXPECTED_LIVE_CARDS
        or dict(levels) != EXPECTED_NOTES_BY_LEVEL
    ):
        raise AuditError(
            f"expected {EXPECTED_NOTES_BY_LEVEL}/{EXPECTED_LIVE_CARDS}, "
            f"got {dict(levels)}/{cards}"
        )
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


def validate_scaffold(manifest: dict[str, Any]) -> None:
    """Validate canonical coverage and identity without claiming review completion."""
    entries = manifest.get("entries", {})
    if manifest.get("schema_version") != SCHEMA_VERSION or len(entries) != EXPECTED_CATALOG_NOTES:
        raise AuditError("invalid or incomplete v5 scaffold")
    counts = manifest.get("counts", {})
    actual_levels = {level: counts.get(level.casefold()) for level in LEVELS}
    if counts.get("notes") != EXPECTED_CATALOG_NOTES or actual_levels != EXPECTED_NOTES_BY_LEVEL:
        raise AuditError("v5 scaffold level counts are inconsistent")
    if counts.get("reviewed", 0) + counts.get("unreviewed", 0) != EXPECTED_CATALOG_NOTES:
        raise AuditError("v5 scaffold review counts are inconsistent")
    if (
        counts.get("keep", 0) + counts.get("revise", 0) + counts.get("pending", 0)
        != EXPECTED_CATALOG_NOTES
    ):
        raise AuditError("v5 scaffold decision counts are inconsistent")
    if counts.get("b1_no_examples") != goethe_scope.EXPECTED_EMPTY_NOTES_BY_LEVEL["B1"]:
        raise AuditError(
            "v5 scaffold must preserve exactly "
            f"{goethe_scope.EXPECTED_EMPTY_NOTES_BY_LEVEL['B1']} canonical B1 no-example notes"
        )

    guids: set[str] = set()
    for source_id, entry in entries.items():
        required = {
            "source_id", "source_refs", "stable_guid", "lemma", "cefr", "pos",
            "expected_meaning_en", "desired_meaning_en", "expected_examples",
            "desired_examples", "decision", "review_status", "evidence",
            "english_variant", "american_review_status",
            "american_review_provenance",
        }
        missing = sorted(required - set(entry))
        if missing:
            raise AuditError(f"canonical row missing fields ({', '.join(missing)}): {source_id}")
        if entry.get("source_id") != source_id or entry.get("cefr") not in LEVELS:
            raise AuditError(f"invalid canonical identity: {source_id}")
        guid = str(entry.get("stable_guid", "")).strip()
        if not guid or guid in guids:
            raise AuditError(f"missing or duplicate stable GUID: {source_id}")
        guids.add(guid)
        refs = entry.get("source_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or refs[0] != source_id
            or source_id not in refs
            or len(refs) != len(set(refs))
        ):
            raise AuditError(f"invalid source refs: {source_id}")
        expected_de = [item.get("de") for item in entry.get("expected_examples", [])]
        desired_de = [item.get("de") for item in entry.get("desired_examples", [])]
        if expected_de != desired_de:
            raise AuditError(f"English audit changed German example text: {source_id}")
        if entry.get("review_status") == "reviewed":
            if entry.get("decision") not in {"KEEP", "REVISE"}:
                raise AuditError(f"reviewed row has no final decision: {source_id}")
        elif entry.get("decision") != "PENDING":
            raise AuditError(f"unreviewed row must remain PENDING: {source_id}")


def _review_entry_errors(entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    evidence = entry.get("evidence", [])
    if not isinstance(entry.get("difficult"), bool):
        errors.append("difficult_not_explicit")
    if entry.get("review_status") != "reviewed" or entry.get("decision") == "PENDING":
        errors.append("unreviewed")
    elif not str(entry.get("reason", "")).strip():
        errors.append("invalid_reason")
    if not evidence:
        errors.append("unsupported")
    domains: set[str] = set()
    providers: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            errors.append("invalid_evidence")
            continue
        url = str(item.get("url", ""))
        provider = item.get("provider")
        if provider not in EVIDENCE_HOSTS:
            errors.append("invalid_evidence")
        else:
            providers.add(str(provider))
        if not str(item.get("supports", "")).strip():
            errors.append("invalid_evidence")
        if not evidence_url_is_specific(str(provider), url):
            errors.append("invalid_evidence")
        else:
            domains.add(urlparse(url).netloc.casefold())
    if entry.get("difficult") and len(domains) < 2:
        errors.append("difficult_needs_two_domains")
    if entry.get("cefr") == "B1" and entry.get("review_status") == "reviewed":
        if not providers & {"Cambridge", "Collins"}:
            errors.append("missing_bilingual_evidence")
        if entry.get("difficult") and "Duden" not in providers:
            errors.append("difficult_needs_duden")

    meaning = str(entry.get("desired_meaning_en", "")).strip()
    if not meaning or any(token in meaning for token in ("…", "...", "sth.", "sb.", "so.", " / ")):
        errors.append("invalid_gloss")
    if entry.get("cefr") == "B1" and entry.get("review_status") == "reviewed":
        changed = (
            meaning != str(entry.get("expected_meaning_en", "")).strip()
            or example_pairs(entry.get("desired_examples", []))
            != example_pairs(entry.get("expected_examples", []))
        )
        expected_decision = "REVISE" if changed else "KEEP"
        if entry.get("decision") != expected_decision:
            errors.append("decision_mismatch")
        if (
            str(entry.get("pos", "")).casefold().startswith("v.")
            and not meaning.casefold().startswith("to ")
        ):
            errors.append("noncanonical_verb_gloss")
        gender_markers = re.findall(r"\((?:male|female)\)", meaning.casefold())
        misplaced_gender_marker = any(
            match.start() != 0
            and meaning.casefold()[max(0, match.start() - 2):match.start()] != "; "
            for match in re.finditer(r"\((?:male|female)\)", meaning.casefold())
        )
        if (
            re.search(r"(?:^|; )(?:male|female)\s", meaning.casefold())
            or (gender_markers and misplaced_gender_marker)
        ):
            errors.append("noncanonical_gender_gloss")
    american_text = " ".join([
        meaning,
        *(str(item.get("en", "")) for item in entry.get("desired_examples", [])),
    ]).casefold()
    british_patterns = (
        r"\b(?:colour|colours|favourite|favourites|neighbour|neighbours|"
        r"neighbourhood|neighbourhoods|centre|centres|theatre|theatres|"
        r"metre|metres|kilometre|kilometres|centimetre|centimetres|"
        r"litre|litres|travelling|travelled|traveller|travellers|"
        r"cancelled|cancelling|labelled|labelling|organisation|organisations|"
        r"organised|organising|recognised|recognising|apologised|apologising|"
        r"behaviour|labour|jewellery|cheque|cheques|licence|licences|"
        r"grey|storey|storeys|kerb|kerbs|aeroplane|aeroplanes|"
        r"programme|programmes|motorway|motorways|postcode|postcodes|"
        r"pavement|pavements|lorry|lorries|petrol|rubbish|maths)\b"
    )
    british_phrases = (
        r"\b(?:car park|railway station|return ticket|single ticket|"
        r"mobile phone|driving licence|at the weekend)s?\b"
    )
    tyre_as_noun = bool(re.search(
        r"\b(?:front|rear|flat|spare|car|bike|bicycle|vehicle|winter|summer) tyres?\b"
        r"|\btyres? (?:pressure|tread|shop|dealer)\b"
        r"|\b(?:change|replace|inflate|check|repair) "
        r"(?:a |the |your |my |his |her |our |their )?tyres?\b",
        american_text,
    ))
    if re.search(british_patterns, american_text) or re.search(british_phrases, american_text) or tyre_as_noun:
        errors.append("non_american_english")
    if entry.get("english_variant") != "American English":
        errors.append("invalid_english_variant")
    if entry.get("american_review_status") != "reviewed":
        errors.append("american_review_incomplete")
    if not str(entry.get("american_review_provenance", "")).strip():
        errors.append("american_review_provenance_missing")
    for example in entry.get("desired_examples", []):
        if (
            example.get("origin") not in {"goethe", "review-authored"}
            or not str(example.get("de", "")).strip()
            or not str(example.get("en", "")).strip()
        ):
            errors.append("invalid_example")
    return sorted(set(errors))


def audit_blockers(manifest: dict[str, Any]) -> dict[str, int]:
    blockers: Counter[str] = Counter()
    for entry in manifest.get("entries", {}).values():
        blockers.update(_review_entry_errors(entry))
    collisions = len(manifest.get("ambiguous_prompt_groups", []))
    if collisions:
        blockers["prompt_collision_groups"] = collisions
    return dict(sorted(blockers.items()))


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Require the complete evidence-backed v5 audit before any live operation."""
    validate_scaffold(manifest)
    blockers = audit_blockers(manifest)
    if blockers:
        detail = ", ".join(f"{name}={count}" for name, count in blockers.items())
        raise AuditError(f"v5 audit is not ready: {detail}")


def identity_matches_reviewed_lemma(fields: dict[str, str], audited_lemma: str) -> bool:
    """Accept reviewed inflected/reflexive source labels carried by a canonical note."""
    if audited_lemma == fields.get("Lemma", ""):
        return True
    accepted = {
        item.strip() for item in str(fields.get("AcceptedAnswersDE", "")).split("|") if item.strip()
    }
    if audited_lemma in accepted:
        return True
    normalized = audited_lemma.removeprefix("(sich) ").removeprefix("sich ").strip()
    return normalized == fields.get("Lemma", "").strip()


def validate_records(records: dict[int, dict[str, Any]], manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    validate_manifest(manifest)
    resolved: dict[int, dict[str, Any]] = {}
    used: set[str] = set()
    for note_id, record in records.items():
        entry = find_entry(record["fields"], manifest)
        if entry is None:
            raise AuditError(f"live note not covered by audit: {note_id}")
        if not identity_equivalent(record["fields"], entry, manifest):
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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{datetime.now(timezone.utc).microsecond:06d}"
    backup = STATE / f"Goethe_Institute_pre_english_audit_v5_{stamp}.apkg"
    if backup.exists():
        raise AuditError(f"backup destination already exists: {backup}")
    try:
        result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.resolve().as_posix(), includeSched=True)
    except gw.MigrationError as exc:
        if "timed out" not in str(exc).casefold() and "timeout" not in str(exc).casefold():
            raise
        result = True
    if not result or not apkg.wait_for_valid_apkg(backup):
        raise AuditError("Anki APKG export failed")
    snapshot = protected_snapshot(records)
    snapshot.update({
        "created_utc": now_utc(), "manifest_sha256": hash_file(MANIFEST),
        "backup": str(backup), "backup_sha256": apkg.hash_file(backup),
    })
    atomic_json(SNAPSHOT, snapshot)
    print(json.dumps({"backup": str(backup), "sha256": snapshot["backup_sha256"], "notes": len(records), "cards": len(snapshot["cards"])}, indent=2))


def load_ready() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_json(MANIFEST)
    validate_manifest(manifest)
    snapshot = load_json(SNAPSHOT)
    if snapshot.get("manifest_sha256") != hash_file(MANIFEST):
        raise AuditError("manifest changed after snapshot")
    backup = Path(str(snapshot.get("backup", "")))
    if not apkg.valid_apkg(backup) or snapshot.get("backup_sha256") != apkg.hash_file(backup):
        raise AuditError("scheduled APKG backup is missing, corrupt, or changed")
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


def verify_applied_scope(
    records: dict[int, dict[str, Any]],
    manifest: dict[str, Any],
    snapshot: dict[str, Any],
    source_ids: set[str],
) -> list[int]:
    allowed_mixed_state(records, manifest, snapshot)
    wrong: list[int] = []
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        entry = find_entry(before["fields"], manifest)
        in_scope = entry["source_id"] in source_ids
        expected_fields = desired_fields(before["fields"], entry) if in_scope else before["fields"]
        expected_tags = desired_tags(before["tags"]) if in_scope else before["tags"]
        if (
            audit_projection(record["fields"]) != audit_projection(expected_fields)
            or record["tags"] != expected_tags
        ):
            wrong.append(note_id)
    return wrong


def command_verify(args: argparse.Namespace) -> None:
    manifest, snapshot = load_ready()
    records = live_records()
    source_ids = (
        set(PILOT_SOURCE_IDS) if args.scope == "pilot" else set(manifest["entries"])
    )
    wrong = verify_applied_scope(records, manifest, snapshot, source_ids)
    if wrong:
        raise AuditError(f"audit {args.scope} scope is not correctly applied: {wrong[:5]}")
    print(json.dumps({
        "status": "PASS", "scope": args.scope, "notes": len(records),
        "cards": len(snapshot["cards"]), **manifest["counts"],
    }, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scaffold = sub.add_parser("scaffold")
    scaffold.add_argument("--completion-manifest", type=Path, default=COMPLETION_MANIFEST)
    scaffold.add_argument("--v3-manifest", type=Path, default=V3_MANIFEST)
    scaffold.add_argument("--legacy-b1-audit", type=Path, default=B1_LEGACY_AUDIT)
    scaffold.add_argument("--legacy-b1-overrides", type=Path, default=B1_LEGACY_OVERRIDES)
    scaffold.add_argument("--output", type=Path, default=MANIFEST)
    scaffold.add_argument("--force-overwrite-reviewed", action="store_true")
    scaffold.set_defaults(func=command_scaffold)
    rebase = sub.add_parser("rebase")
    rebase.add_argument("--completion-manifest", type=Path, default=COMPLETION_MANIFEST)
    rebase.add_argument("--current-manifest", type=Path, default=V4_MANIFEST)
    rebase.add_argument("--overrides", type=Path, default=REBASE_OVERRIDES)
    rebase.add_argument("--output", type=Path, default=MANIFEST)
    rebase.add_argument("--confirmation", required=True)
    rebase.set_defaults(func=command_rebase)
    sub.add_parser("inspect").set_defaults(func=command_inspect)
    check_batch = sub.add_parser("check-batch")
    check_batch.add_argument("--batch", choices=sorted(BATCH_COUNTS), required=True)
    check_batch.set_defaults(func=command_check_batch)
    sub.add_parser("compile").set_defaults(func=command_compile)
    sub.add_parser("dry-run").set_defaults(func=command_dry_run)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    for name, func in (("pilot", command_pilot), ("apply", command_apply)):
        command = sub.add_parser(name)
        command.add_argument("--confirmation", required=True)
        command.set_defaults(func=func)
    verify = sub.add_parser("verify")
    verify.add_argument("--scope", choices=("pilot", "full"), default="full")
    verify.set_defaults(func=command_verify)
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
