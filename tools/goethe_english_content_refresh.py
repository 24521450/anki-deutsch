"""Review B1 American English and safely apply the reviewed field changes.

The ``prepare`` command normalizes the B1 English audit from the current live
text, verifies every changed field against Anki, and writes a checked override
manifest. The remaining commands update only those reviewed fields and marked
record overrides through AnkiConnect. They preserve and verify card schedules,
review history, note identities, and deck counts.

    python tools/goethe_english_content_refresh.py prepare
    python tools/goethe_english_content_refresh.py audit
    python tools/goethe_english_content_refresh.py backup
    python tools/goethe_english_content_refresh.py apply --confirmation APPLY_GOETHE_ENGLISH_CONTENT_REFRESH
    python tools/goethe_english_content_refresh.py verify
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import goethe_apkg as apkg
import goethe_english_audit as english_audit
import goethe_template_upgrade as guard


ROOT = guard.ROOT
STATE = ROOT / "tools" / ".goethe_english_content_refresh"
PLAN_PATH = STATE / "plan.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
RESULT_PATH = STATE / "result.json"
AUDIT_PATH = ROOT / "review" / "goethe_english_audit_v5.jsonl"
OVERRIDES_PATH = ROOT / "review" / "goethe_b1_american_english_overrides.json"
RECORD_OVERRIDES_PATH = ROOT / "review" / "goethe_record_overrides.json"
LIVE_REFRESH_BATCH = "american_english_and_abbreviation_review_2026_09"
APPLY_CONFIRMATION = "APPLY_GOETHE_ENGLISH_CONTENT_REFRESH"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_ENGLISH_CONTENT_REFRESH"


class RefreshError(RuntimeError):
    """Raised when the content refresh cannot be proven safe."""


def _case_like(replacement: str, source: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _sub(text: str, pattern: str, replacement: str | Callable[[re.Match[str]], str]) -> str:
    if callable(replacement):
        return re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(
        pattern,
        lambda match: _case_like(replacement, match.group(0)),
        text,
        flags=re.I,
    )


def _plural_word(match: re.Match[str], singular: str, plural: str) -> str:
    replacement = plural if match.group(0).casefold().endswith("s") else singular
    return _case_like(replacement, match.group(0))


def _us_spelling(text: str) -> str:
    for pattern, replacement in (
        (r"\banalysing\b", "analyzing"),
        (r"\banalysed\b", "analyzed"),
        (r"\banalyses\b", "analyzes"),
        (r"\banalyse\b", "analyze"),
        (r"\brecognising\b", "recognizing"),
        (r"\brecognised\b", "recognized"),
        (r"\brecognises\b", "recognizes"),
        (r"\brecognise\b", "recognize"),
        (r"\brealising\b", "realizing"),
        (r"\brealised\b", "realized"),
        (r"\brealises\b", "realizes"),
        (r"\brealise\b", "realize"),
    ):
        text = _sub(text, pattern, replacement)
    text = _sub(text, r"\bfulfils\b", "fulfills")
    text = _sub(text, r"\bfulfil\b", "fulfill")
    text = _sub(text, r"\borganis(e|ed|ing|ation|ations)\b", lambda m: _case_like(
        "organiz" + {
            "e": "e", "ed": "ed", "ing": "ing",
            "ation": "ation", "ations": "ations",
        }[m.group(1).casefold()],
        m.group(0),
    ))
    for pattern, replacement in (
        (r"\bhumour\b", "humor"),
        (r"\bcolour\b", "color"),
        (r"\bcolours\b", "colors"),
        (r"\bfavour\b", "favor"),
        (r"\bneighbourhoods\b", "neighborhoods"),
        (r"\bneighbourhood\b", "neighborhood"),
        (r"\bneighbours\b", "neighbors"),
        (r"\bneighbour\b", "neighbor"),
        (r"\bcatalogue\b", "catalog"),
        (r"\bdialogue\b", "dialog"),
        (r"\bcosy\b", "cozy"),
        (r"\bjudgement\b", "judgment"),
        (r"\bspelt\b", "spelled"),
        (r"\bburnt\b", "burned"),
        (r"\btravellers\b", "travelers"),
        (r"\btraveller\b", "traveler"),
        (r"\btravelled\b", "traveled"),
        (r"\btravelling\b", "traveling"),
        (r"\bcancelled\b", "canceled"),
        (r"\bcancelling\b", "canceling"),
        (r"\blicence\b", "license"),
        (r"\bcentres\b", "centers"),
        (r"\bcentre\b", "center"),
        (r"\btheatres\b", "theaters"),
        (r"\btheatre\b", "theater"),
        (r"\bprogrammes\b", "programs"),
        (r"\bprogramme\b", "program"),
        (r"\bkilometres\b", "kilometers"),
        (r"\bkilometre\b", "kilometer"),
        (r"\bmetres\b", "meters"),
        (r"\bmetre\b", "meter"),
        (r"\bgrey\b", "gray"),
        (r"\bafterwards\b", "afterward"),
        (r"\bdownwards\b", "downward"),
        (r"\bupwards\b", "upward"),
        (r"\bbackwards\b", "backward"),
        (r"\btowards\b", "toward"),
    ):
        text = _sub(text, pattern, replacement)
    return text


def _rewrite_american_english(source_id: str, field: str, original: str) -> str:
    text = original
    # U.S. punctuation and date/time order.
    text = _sub(text, r"\b(Mr|Mrs|Ms|Dr|Prof)\s+(?=[A-Z])", lambda m: m.group(1) + ". ")
    month_pattern = (
        r"\b(\d{1,2}) (January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\b"
    )
    text = re.sub(
        month_pattern,
        lambda m: f"{m.group(2)} {int(m.group(1))}",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(\d{1,2})\.(\d{2})(?=\s*(?:a\.m\.|p\.m\.))",
        r"\1:\2",
        text,
        flags=re.I,
    )

    text = _us_spelling(text)
    substitutions: tuple[tuple[str, str | Callable[[re.Match[str]], str]], ...] = (
        (r"\bregistered post\b", "registered mail"),
        (r"\bcash machines?\b", lambda m: _case_like("ATMs" if m.group(0).casefold().endswith("s") else "ATM", m.group(0))),
        (r"\bpostbox\b|\bletterbox\b", "mailbox"),
        (r"\bnumber plates?\b", lambda m: _case_like("license plates" if m.group(0).casefold().endswith("s") else "license plate", m.group(0))),
        (r"\bparcels\b", "packages"),
        (r"\bparcel\b", "package"),
        (r"\bjumpers\b", "sweaters"),
        (r"\bjumper\b", "sweater"),
        (r"\btrousers\b", "pants"),
        (r"\bto queue\b", "to line up"),
        (r"\bqueue\b", "line"),
        (r"\bcloakroom\b", "coatroom"),
        (r"\bwheelie bin\b", "trash can"),
        (r"\bshared flat\b", "shared apartment"),
        (r"\bflatshare\b", "shared apartment"),
        (r"\bat weekends\b", "on weekends"),
        (r"\bline(s)?\s+(is|are)\s+(always|constantly)\s+engaged\b", lambda m: _case_like(
            f"line{'s' if m.group(1) else ''} {m.group(2)} {m.group(3)} busy",
            m.group(0),
        )),
        (r"\ball the lines are engaged\b", "all the lines are busy"),
        (r"\bto queue\b", "to line up"),
        (r"\ba apartment\b", lambda m: _case_like("an apartment", m.group(0))),
    )
    for pattern, replacement in substitutions:
        text = _sub(text, pattern, replacement)

    # Contextual vocabulary whose American equivalent depends on the card.
    if source_id in {"B1-MAIN-0460", "B1-MAIN-0554"}:
        text = _sub(text, r"\btins?\b", lambda m: _plural_word(m, "can", "cans"))
    if source_id == "B1-MAIN-0494":
        text = _sub(text, r"\btins\b", "cans")
    if source_id == "B1-MAIN-0802":
        text = _sub(text, r"\bminced meat\b", "ground meat")
    if source_id == "B1-MAIN-0167":
        text = _sub(text, r"\bto post\b", "to mail")
        text = _sub(text, r"\bposted\b", "mailed")
    if source_id in {"B1-MAIN-0252", "B1-MAIN-0986"}:
        text = _sub(text, r"\bcash machine\b", "ATM")
    if source_id == "B1-MAIN-0171":
        text = _sub(text, r"battery is flat", "battery is dead")
    if source_id == "B1-MAIN-0349":
        text = _sub(text, r"to be engaged", "to be busy")
    if source_id == "B1-MAIN-0440" and field == "MeaningEN":
        text = "mailbox"
    if source_id == "B1-MAIN-0460" and field == "MeaningEN":
        text = "can (Germany/Switzerland)"
    if source_id == "B1-MAIN-0525":
        text = "draftproof; tightly sealed" if field == "MeaningEN" else "Our windows aren't sealed well. There's always a draft."
    if source_id == "B1-MAIN-0554" and field == "MeaningEN":
        text = "can"
    if source_id == "B1-MAIN-0640":
        text = _sub(text, r"registered post", "registered mail")
    if source_id == "B1-MAIN-0781":
        text = _sub(text, r"carriageway", "roadway")
    if source_id == "B1-MAIN-0877":
        text = _sub(text, r"cake tin", "cake pan")
    if source_id == "B1-MAIN-0984":
        # "Sidewalk" is already the American term; keep the source's regional label.
        pass
    if source_id == "B1-MAIN-1041":
        text = _sub(text, r"the post", "the mail")
    if source_id == "B1-MAIN-1135":
        text = _sub(text, r"parcel", "package")
    if source_id == "B1-MAIN-1258":
        text = _sub(text, r"take leave", "take time off")
    if source_id == "B1-MAIN-1524":
        text = _sub(text, r"\blift\b", "elevator")
    if source_id == "B1-MAIN-1638":
        text = _sub(text, r"aircraft", "plane")
    if source_id == "B1-MAIN-1641":
        text = "mobile; able to get around easily" if field == "MeaningEN" else "I can get around the city easily by bike."
    if source_id == "B1-MAIN-1792":
        if field == "MeaningEN":
            text = "doctor's office; office hours (Austrian German)"
        elif field == "Example1EN":
            text = "The doctor's office will be closed until January 7."
        elif field == "Example2EN":
            text = "Dr. Berger's office hours are from 8 a.m. to noon."
    if source_id == "B1-MAIN-1988":
        text = _sub(text, r"advertising leaflets", "advertising flyers")
    if source_id == "B1-MAIN-2049" and field == "Example1EN":
        text = "Traveling during school breaks is always more expensive. That's peak season."
    if source_id == "B1-MAIN-2097":
        text = _sub(text, r"queue", "line")
    if source_id == "B1-MAIN-2180":
        text = _sub(text, r"our post", "our mail")
    if source_id == "B1-MAIN-2284":
        text = _sub(text, r"on Ward 3", "in Ward 3")
    if source_id == "B1-MAIN-2311":
        text = _sub(text, r"the toilet", "the restroom")
    if source_id == "B1-MAIN-2492":
        text = _sub(text, r"underground", "subway")
    if source_id == "B1-MAIN-2564":
        text = _sub(text, r"by post", "by mail")
    if source_id == "B1-MAIN-2656":
        text = _sub(text, r"directory enquiries", "directory assistance")
    if source_id in {"B1-MAIN-2691", "B1-MAIN-2692"}:
        if field == "MeaningEN":
            text = _sub(text, r"locum", "substitute doctor")
        elif field == "Example1EN":
            text = "When Dr. Müller was away, I saw the doctor covering for him."
    if source_id == "B1-MAIN-1251" and field == "Example1EN":
        text = text.replace("M?ller", "Müller")
    if source_id == "B1-MAIN-2806":
        text = _sub(text, r"advertising through my mailbox", "advertising in my mailbox")
    if source_id == "B1-MAIN-2904":
        text = _sub(text, r"draught", "draft")
    if source_id == "B1-MAIN-0443":
        text = _sub(text, r"postwoman", "mail carrier")
        text = _sub(text, r"Has the mail carrier been yet\?", "Has the mail carrier been here yet?")
    if source_id == "B1-MAIN-0975":
        text = _sub(text, r"carriageway", "roadway")
    if source_id == "B1-MAIN-1093":
        if field == "MeaningEN":
            text = "ground meat"
        else:
            text = _sub(text, r"\bmince\b", "ground meat")
    if source_id == "B1-WG-0008" and field == "MeaningEN":
        text = "first floor"
    if source_id == "B1-WG-0020" and field == "MeaningEN":
        text = "shared apartment"
    if source_id == "B1-MAIN-1404" and field == "MeaningEN":
        text = "sick person"
    if source_id == "A1-84886454922" and field == "MeaningEN":
        text = "truck"
    return text


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshError(f"cannot read JSONL review source: {path}") from exc


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def _notes_by_source(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for note_id, item in inventory["notes"].items():
        fields = item["fields"]
        identities = {str(item.get("source_id", ""))}
        identities.update(
            ref.strip() for ref in str(fields.get("SourceRefs", "")).split("|") if ref.strip()
        )
        for identity in identities - {""}:
            if identity in result and result[identity]["note_id"] != note_id:
                ambiguous.add(identity)
            else:
                result[identity] = {**item, "note_id": note_id}
    if ambiguous:
        raise RefreshError(f"ambiguous live SourceRefs: {sorted(ambiguous)[:8]}")
    return result


def command_prepare(_: argparse.Namespace) -> None:
    guard.require_version()
    inventory = guard.live_inventory()
    by_source = _notes_by_source(inventory)
    rows = _read_jsonl(AUDIT_PATH)
    prior_records = (
        copy.deepcopy(_load_review_overrides()["records"])
        if OVERRIDES_PATH.exists()
        else {}
    )
    changed_entries: dict[str, dict[str, Any]] = prior_records
    changed_row_count = 0
    example_beyond_card_fields: list[str] = []

    for row in rows:
        source_id = str(row.get("source_id", ""))
        if row.get("cefr") != "B1" and source_id != "A1-84886454922":
            continue
        note = by_source.get(source_id)
        if note is None:
            # Only rows that actually need an update require a live note. The
            # transformation is computed first so stale/deleted audit rows do
            # not block unrelated rebuilds.
            note = None
        before_manifest: dict[str, str] = {}
        after_manifest: dict[str, str] = {}
        prior_meaning = str(row.get("desired_meaning_en", ""))
        target_meaning = _rewrite_american_english(source_id, "MeaningEN", prior_meaning)
        if target_meaning != prior_meaning:
            before_manifest["MeaningEN"] = prior_meaning
            after_manifest["MeaningEN"] = target_meaning
        for index, example in enumerate(row.get("desired_examples", []), start=1):
            prior = str(example.get("en", ""))
            field = f"Example{index}EN"
            target = _rewrite_american_english(source_id, field, prior)
            if target == prior:
                continue
            if index > 4:
                example_beyond_card_fields.append(source_id)
                continue
            before_manifest[field] = prior
            after_manifest[field] = target

        if not after_manifest:
            continue
        if example_beyond_card_fields:
            continue
        if note is None:
            raise RefreshError(f"American English target has no live note: {source_id}")
        current_fields = note["fields"]
        if source_id != note["source_id"] and row.get("lemma") != current_fields.get("Lemma"):
            raise RefreshError(f"SourceID alias has a different live lemma: {source_id}")
        prior_record = changed_entries.get(str(note["source_id"]), {})
        for name, expected in before_manifest.items():
            current = str(current_fields.get(name, ""))
            allowed = {expected, after_manifest[name]}
            if name in prior_record.get("expected", {}):
                allowed.add(prior_record["expected"][name])
            if name in prior_record.get("set", {}):
                allowed.add(prior_record["set"][name])
            if current not in allowed:
                raise RefreshError(
                    f"live English differs from v5 before-image for {source_id} {name}: "
                    f"expected one of {sorted(allowed)!r}, got {current!r}"
                )
        # This is an editorial correction: expected_* continues to describe
        # the original source text, while desired_* becomes the approved AmE.
        if "MeaningEN" in after_manifest:
            row["desired_meaning_en"] = after_manifest["MeaningEN"]
        for index, example in enumerate(row.get("desired_examples", []), start=1):
            field = f"Example{index}EN"
            if field in after_manifest:
                example["en"] = after_manifest[field]
        row["review_status"] = "reviewed"
        row["english_variant"] = "American English"
        row["american_review_status"] = "reviewed"
        row["american_review_provenance"] = "goethe-english-v5-american-normalization-2026-09"
        categories = list(row.get("change_categories", []))
        if "american_english_normalization" not in categories:
            categories.append("american_english_normalization")
        row["change_categories"] = categories
        row["decision"] = (
            "REVISE"
            if row.get("expected_meaning_en") != row.get("desired_meaning_en")
            or english_audit.example_pairs(row.get("expected_examples", []))
            != english_audit.example_pairs(row.get("desired_examples", []))
            else "KEEP"
        )
        reason = str(row.get("reason", "")).strip()
        suffix = "Reviewed for American English spelling, vocabulary, dates, and punctuation."
        if suffix not in reason:
            row["reason"] = f"{reason} {suffix}".strip()

        live_source_id = str(note["source_id"])
        record = changed_entries.setdefault(live_source_id, {
            "source_id": live_source_id,
            "source_refs": [
                ref.strip() for ref in str(current_fields.get("SourceRefs", "")).split("|") if ref.strip()
            ],
            "lemma": str(current_fields.get("Lemma", "")),
            "cefr": str(current_fields.get("CEFR", "")),
            "expected": {},
            "set": {},
            "audit_source_ids": [],
            "reason": "American English normalization; exact prior values are retained as guards.",
        })
        record["audit_source_ids"].append(source_id)
        for name, before in before_manifest.items():
            after = after_manifest[name]
            prior_expected = record["expected"].get(name)
            prior_after = record["set"].get(name)
            if prior_expected is not None and prior_expected != before and prior_after != before:
                raise RefreshError(f"conflicting expected value for {live_source_id} {name}")
            if prior_after is not None and prior_after not in {before, after}:
                raise RefreshError(f"conflicting American English target for {live_source_id} {name}")
            if prior_expected is None:
                record["expected"][name] = before
            record["set"][name] = after
        changed_row_count += 1

    if example_beyond_card_fields:
        raise RefreshError(
            "AmE changes affect overflow examples outside the four card fields: "
            f"{sorted(set(example_beyond_card_fields))[:8]}"
        )
    if not changed_entries:
        raise RefreshError("no American English corrections were found")
    for record in changed_entries.values():
        record["audit_source_ids"] = sorted(set(record["audit_source_ids"]))
    overrides = {
        "schema_version": 1,
        "batch": LIVE_REFRESH_BATCH,
        "records": {key: changed_entries[key] for key in sorted(changed_entries)},
    }
    _write_jsonl(AUDIT_PATH, rows)
    OVERRIDES_PATH.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    guard.print_json({
        "status": "PREPARE_PASS",
        "changed_audit_rows": changed_row_count,
        "changed_live_notes": len(changed_entries),
        "changed_fields": sum(len(item["set"]) for item in changed_entries.values()),
        "audit": str(AUDIT_PATH),
        "overrides": str(OVERRIDES_PATH),
    })


def _load_review_overrides() -> dict[str, Any]:
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshError(f"cannot read reviewed American English overrides: {OVERRIDES_PATH}") from exc
    if data.get("schema_version") != 1 or data.get("batch") != LIVE_REFRESH_BATCH:
        raise RefreshError("unsupported American English override manifest")
    if not isinstance(data.get("records"), dict) or not data["records"]:
        raise RefreshError("American English override manifest has no records")
    return data


def _extra_record_overrides() -> dict[str, Any]:
    try:
        data = json.loads(RECORD_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshError(f"cannot read record overrides: {RECORD_OVERRIDES_PATH}") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("records"), dict):
        raise RefreshError("unsupported Goethe record override policy")
    return data


def _plan_records(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_source = _notes_by_source(inventory)
    desired_by_note: dict[str, dict[str, Any]] = {}

    def add_fields(source_id: str, expected: dict[str, Any], desired: dict[str, Any], label: str) -> None:
        note = by_source.get(source_id)
        if note is None:
            raise RefreshError(f"reviewed SourceID is absent from Anki: {source_id}")
        note_id = str(note["note_id"])
        current = note["fields"]
        record = desired_by_note.setdefault(note_id, {
            "note_id": int(note_id),
            "source_id": str(note["source_id"]),
            "cards": list(note["cards"]),
            "before": {},
            "after": {},
            "review_sources": [],
        })
        for name, after in desired.items():
            if not isinstance(name, str) or not isinstance(after, str):
                raise RefreshError(f"invalid reviewed field in {source_id}: {name!r}")
            before = expected.get(name)
            if not isinstance(before, str):
                raise RefreshError(f"no exact before-image for {source_id} {name}")
            now = str(current.get(name, ""))
            if now not in {before, after}:
                raise RefreshError(
                    f"stale field for {source_id} {name}: expected {before!r} or {after!r}, got {now!r}"
                )
            if name in record["after"] and record["after"][name] != after:
                raise RefreshError(f"multiple targets conflict for {source_id} {name}")
            if name in record["before"] and record["before"][name] != before:
                raise RefreshError(f"multiple before-images conflict for {source_id} {name}")
            record["before"][name] = before
            record["after"][name] = after
            record["review_sources"].append(label)

    overrides = _load_review_overrides()
    for source_id, item in overrides["records"].items():
        if not isinstance(item, dict) or not isinstance(item.get("expected"), dict) or not isinstance(item.get("set"), dict):
            raise RefreshError(f"invalid American English record: {source_id}")
        if set(item["expected"]) != set(item["set"]):
            raise RefreshError(f"American English before/after fields differ: {source_id}")
        add_fields(source_id, item["expected"], item["set"], "american_english")

    record_overrides = _extra_record_overrides()
    for source_id, item in record_overrides["records"].items():
        if item.get("refresh_batch") != LIVE_REFRESH_BATCH:
            continue
        names = item.get("refresh_fields")
        if not isinstance(names, list) or not names:
            raise RefreshError(f"marked record override has no refresh_fields: {source_id}")
        expected = item.get("expected", {})
        desired = item.get("set", {})
        if not isinstance(expected, dict) or not isinstance(desired, dict):
            raise RefreshError(f"invalid marked record override: {source_id}")
        add_fields(
            source_id,
            {name: expected[name] for name in names if name in expected},
            {name: desired[name] for name in names if name in desired},
            "record_override",
        )

    planned: dict[str, dict[str, Any]] = {}
    for note_id, item in desired_by_note.items():
        current = inventory["notes"][note_id]["fields"]
        changes = {
            name for name, after in item["after"].items()
            if current.get(name, "") != after
        }
        if not changes:
            continue
        item["before"] = {name: item["before"][name] for name in sorted(changes)}
        item["after"] = {name: item["after"][name] for name in sorted(changes)}
        item["review_sources"] = sorted(set(item["review_sources"]))
        planned[note_id] = item
    return {key: planned[key] for key in sorted(planned, key=int)}


def _source_hashes() -> dict[str, str]:
    return {
        "english_audit_v5": guard.hash_file(AUDIT_PATH),
        "english_overrides": guard.hash_file(OVERRIDES_PATH),
        "record_overrides": guard.hash_file(RECORD_OVERRIDES_PATH),
    }


def build_plan(inventory: dict[str, Any]) -> dict[str, Any]:
    fields = list(inventory["model"]["fields"])
    configured = list(getattr(guard.gw, "FIELDS", []))
    if fields != configured:
        raise RefreshError(f"live model schema differs from source: expected {configured}, got {fields}")
    records = _plan_records(inventory)
    return {
        "schema_version": 1,
        "model": guard.MODEL,
        "fields": fields,
        "source_hashes": _source_hashes(),
        "records": records,
    }


def _load_plan() -> dict[str, Any]:
    try:
        plan = guard.load_json(PLAN_PATH)
    except guard.UpgradeError as exc:
        raise RefreshError(str(exc)) from exc
    if plan.get("schema_version") != 1 or plan.get("model") != guard.MODEL:
        raise RefreshError("unsupported English-content refresh plan")
    if plan.get("fields") != list(getattr(guard.gw, "FIELDS", [])):
        raise RefreshError("English-content plan has an invalid model schema")
    if plan.get("source_hashes") != _source_hashes():
        raise RefreshError("reviewed English or record sources changed after audit")
    if not isinstance(plan.get("records"), dict):
        raise RefreshError("English-content plan is missing target records")
    return plan


def _load_snapshot() -> dict[str, Any]:
    try:
        snapshot = guard.load_json(SNAPSHOT_PATH)
    except guard.UpgradeError as exc:
        raise RefreshError(str(exc)) from exc
    if snapshot.get("schema_version") != 1:
        raise RefreshError("unsupported English-content snapshot")
    backup = Path(str(snapshot.get("backup", "")))
    if not backup.exists() or not apkg.valid_apkg(backup):
        raise RefreshError(f"English-content APKG backup is missing or invalid: {backup}")
    if apkg.hash_file(backup) != snapshot.get("backup_sha256"):
        raise RefreshError("English-content APKG backup hash changed")
    return snapshot


def _export_backup() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = STATE / f"Goethe_Institute_pre_english_content_refresh_{stamp}.apkg"
    path.unlink(missing_ok=True)
    error: guard.UpgradeError | None = None
    try:
        result = guard.anki(
            "exportPackage",
            deck=guard.PARENT_DECK,
            path=path.resolve().as_posix(),
            includeSched=True,
        )
    except guard.UpgradeError as exc:
        error = exc
        if "timed out" not in str(exc).casefold():
            raise RefreshError(str(exc)) from exc
        result = False
    attempts = 720 if error is not None else 60
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
    plan: dict[str, Any],
    *,
    after: bool,
) -> None:
    if current["model"] != snapshot["model"]:
        raise RefreshError("model templates, styling, or fields changed during content refresh")
    if set(current["notes"]) != set(snapshot["notes"]):
        raise RefreshError("live note ID set changed during content refresh")
    if set(current["cards"]) != set(snapshot["cards"]):
        raise RefreshError("live card ID set changed during content refresh")
    expected_key = "after" if after else "before"
    for note_id, before_note in snapshot["notes"].items():
        now = current["notes"][note_id]
        for key in ("model", "source_id", "tags", "cards"):
            if now[key] != before_note[key]:
                raise RefreshError(f"note identity changed: {note_id} ({key})")
        target = plan["records"].get(note_id, {})
        updates = target.get(expected_key, {})
        for name, value in before_note["fields"].items():
            expected = updates.get(name, value)
            if now["fields"].get(name, "") != expected:
                raise RefreshError(f"unexpected note field state: {note_id} {name}")
    for card_id, before_card in snapshot["cards"].items():
        now = current["cards"][card_id]
        if now != before_card:
            raise RefreshError(f"card schedule/suspension changed: {card_id}")
    if current["reviews"] != snapshot["reviews"]:
        raise RefreshError("review history changed during content refresh")
    if current["reviews_sha256"] != snapshot["reviews_sha256"]:
        raise RefreshError("review history hash changed during content refresh")
    if current["deck_counts"] != snapshot["deck_counts"]:
        raise RefreshError("deck counts changed during content refresh")


def _update_fields(plan: dict[str, Any], inventory: dict[str, Any], *, after: bool) -> int:
    key = "after" if after else "before"
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
            raise RefreshError("AnkiConnect multi returned an unexpected field-update result")
        errors = [
            item.get("error") for item in response
            if isinstance(item, dict) and item.get("error")
        ]
        if errors:
            raise RefreshError(f"AnkiConnect field update errors: {errors[:3]}")
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
        "target_fields": sum(len(item["after"]) for item in plan["records"].values()),
        "plan": str(PLAN_PATH),
    })


def command_backup(_: argparse.Namespace) -> None:
    guard.require_version()
    plan = _load_plan()
    inventory = guard.live_inventory()
    if guard.canonical_hash(plan) != guard.canonical_hash(build_plan(inventory)):
        raise RefreshError("live content targets changed since audit")
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
    guard.require_version()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("English-content plan changed after backup")
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, plan, after=False)
    if guard.canonical_hash(build_plan(current)) != guard.canonical_hash(plan):
        raise RefreshError("live English-content target set changed after backup")
    changed = _update_fields(plan, current, after=True)
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, plan, after=True)
    guard.atomic_json(RESULT_PATH, {
        "schema_version": 1,
        "status": "applied",
        "applied_utc": guard.now_utc(),
        "plan_sha256": guard.canonical_hash(plan),
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
        "updated_notes": changed,
    })
    guard.print_json({"status": "APPLY_PASS", "updated_notes": changed, "target_notes": len(plan["records"])})


def command_verify(_: argparse.Namespace) -> None:
    guard.require_version()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("English-content plan changed after backup")
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, plan, after=True)
    guard.print_json({
        "status": "VERIFY_PASS",
        "notes": len(current["notes"]),
        "cards": len(current["cards"]),
        "updated_notes": len(plan["records"]),
    })


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise RefreshError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    guard.require_version()
    plan = _load_plan()
    snapshot = _load_snapshot()
    if snapshot.get("plan_sha256") != guard.canonical_hash(plan):
        raise RefreshError("English-content plan changed after backup")
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, plan, after=True)
    restored = _update_fields(plan, current, after=False)
    current = guard.live_inventory()
    _compare_inventory(snapshot, current, plan, after=False)
    guard.atomic_json(RESULT_PATH, {
        "schema_version": 1,
        "status": "rolled_back",
        "rolled_back_utc": guard.now_utc(),
        "plan_sha256": guard.canonical_hash(plan),
        "backup": snapshot["backup"],
        "backup_sha256": snapshot["backup_sha256"],
        "restored_notes": restored,
    })
    guard.print_json({"status": "ROLLBACK_PASS", "restored_notes": restored})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare").set_defaults(func=command_prepare)
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
    except Exception as exc:
        print(f"ERROR: unexpected English-content refresh failure: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
