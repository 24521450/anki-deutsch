"""Complete the live Goethe A1/A2 deck from alphabetical and Wortgruppen sources.

Build and translation commands only write ignored state. ``apply`` is guarded by
an explicit confirmation and operates through AnkiConnect, never collection.anki2.
"""
from __future__ import annotations

import argparse
import copy
import concurrent.futures
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

import goethe_werkstatt_migrate as gw
import goethe_examples
import goethe_english_audit as english_audit
import goethe_source_examples

ROOT = gw.ROOT
STATE = ROOT / "tools" / ".goethe_completion"
TRANSLATIONS = ROOT / "review" / "goethe_completion_translations.json"
REDUNDANCY_POLICY = ROOT / "review" / "goethe_redundancy_policy.json"
HEADWORD_POLICY = ROOT / "review" / "goethe_headword_merges.json"
SOURCE_TEXT_OVERRIDES = ROOT / "review" / "goethe_source_text_overrides.json"
MANIFEST = STATE / "manifest.json"
MODEL = gw.MODEL
WG_FILES = {
    "A1": ROOT / "sources" / "goethe" / "Goethe_A1_Wortgruppen.md",
    "A2": ROOT / "sources" / "goethe" / "Goethe_A2_Wortgruppen.md",
}
LEVEL_DECK = {"A1": gw.A1_DECK, "A2": gw.A2_DECK}
LEVEL_TAG = {"A1": "goethe::level::a1", "A2": "goethe::level::a2"}
QUALITY_TRANSLATION = "goethe::quality::translation_review_needed"
CONFIRMATION = "COMPLETE_GOETHE_A1_A2"


class CompletionError(RuntimeError):
    pass


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or "")).strip())


def sentence_key(value: str) -> str:
    return goethe_source_examples.sentence_key(value)


def lemma_key(value: str, *, fold: bool = False) -> str:
    value = unicodedata.normalize("NFC", clean(value))
    value = re.sub(r"^\(sich\)\s*|^sich\s+", "", value, flags=re.I)
    value = re.sub(r"^(der|die|das)\s+", "", value, flags=re.I)
    value = value.replace("(Kredit)-", "Kredit").replace("(e)", "")
    return value.casefold() if fold else value


def compatible_pos(source: str, target: str) -> bool:
    source, target = clean(source).casefold(), clean(target).casefold()
    if not source or not target:
        return True
    return source.split(".")[0] == target.split(".")[0]


def split_answers(value: str) -> list[str]:
    return [clean(part) for part in value.split("|") if clean(part)]


def split_refs(value: str) -> list[str]:
    return [clean(part) for part in value.split("|") if clean(part)]


def source_variants(value: str) -> list[str]:
    value = clean(value)
    variants = [value]
    variants.extend(part for part in re.split(r"\s+/\s+", value) if part)
    if "/" in value:
        variants.extend(part.strip() for part in value.split("/") if part.strip())
        match = re.match(r"^(.*\s)?([^\s/]+)/([^\s/]+)(\s.*)?$", value)
        if match:
            prefix, left, right, suffix = match.group(1) or "", match.group(2), match.group(3), match.group(4) or ""
            variants.extend((prefix + left + suffix, prefix + right + suffix))
    variants.append(re.sub(r"\(([^)]*)\)", r"\1", value))
    variants.append(re.sub(r"\(([^)]*)\)", "", value))
    variants.append(re.sub(r",\s*(?:[-=¨].*)$", "", value))
    return list(dict.fromkeys(lemma_key(item) for item in variants if lemma_key(item)))


def field(note: dict[str, Any], name: str) -> str:
    return note.get("fields", {}).get(name, {}).get("value", "")


def parse_wortgruppen(path: Path) -> list[dict[str, str]]:
    category = ""
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            category = line[3:].strip()
        if not line.startswith("| A"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 15:
            raise CompletionError(f"bad Wortgruppen row: {line}")
        row = dict(zip((
            "id", "entry", "detail", "cefr", "page", "match", "note",
            "canonical", "pos", "article", "gender", "noun_forms",
            "variants", "grammar_note", "dictionary_sources",
        ), cells))
        row["category"] = category
        rows.append(row)
    return rows


def category_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")


def wg_lemma(row: dict[str, str]) -> str:
    if row.get("canonical"):
        return row["canonical"]
    if row["match"]:
        return row["match"]
    entry = clean(row["entry"])
    detail = clean(row["detail"])
    if re.fullmatch(r"[\d\s.,:/%+-]+", entry) and detail:
        return clean(re.split(r";", detail, maxsplit=1)[0]).replace("...", "").strip()
    return re.sub(r",\s*(?:[-=¨].*)$", "", entry).strip()


def wg_answers(row: dict[str, str]) -> list[str]:
    if row.get("canonical"):
        variants = [
            item.strip() for item in re.split(r"<br\s*/?>", row.get("variants", ""), flags=re.I)
            if item.strip()
        ]
        return list(dict.fromkeys([row["canonical"], *variants]))
    entry = clean(row["entry"])
    if re.fullmatch(r"[\d\s.,:/%+-]+", entry):
        return [wg_lemma(row)]
    answers = []
    for part in re.split(r"\s+/\s+", entry):
        value = re.sub(r",\s*(?:[-=¨].*)$", "", part).strip()
        value = re.sub(r"^(der|die|das)\s+", "", value, flags=re.I)
        if value:
            answers.append(value)
    return list(dict.fromkeys(answers or [wg_lemma(row)]))


def card_reps(cards: list[dict[str, Any]]) -> int:
    return sum(int(card.get("reps", 0)) for card in cards)


def load_live() -> tuple[dict[str, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(ids):
        notes.extend(gw.anki("notesInfo", notes=batch))
    cards = gw.all_card_info()
    by_note: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        by_note[int(card["note"])].append(card)
    records: dict[str, dict[str, Any]] = {}
    for note in notes:
        note_id = int(note["noteId"])
        fields = {name: field(note, name) for name in gw.FIELDS}
        examples = []
        for index in range(1, 5):
            de = fields[f"Example{index}DE"]
            if de:
                examples.append({"de": de, "en": fields[f"Example{index}EN"], "audio": fields[f"Example{index}Audio"]})
        examples.extend(goethe_examples.parse_overflow(fields["MoreExamplesHTML"]))
        refs = split_answers(fields.get("SourceRefs", "")) or ([fields["SourceID"]] if fields["SourceID"] else [])
        records[str(note_id)] = {
            "note_id": note_id,
            "is_new": False,
            "fields": fields,
            "tags": sorted(set(note.get("tags", []))),
            "deck": by_note[note_id][0]["deckName"],
            "cards": by_note[note_id],
            "examples": examples,
            "source_refs": refs,
            "categories": [],
            "translated": False,
        }
    return records, by_note


def load_redundancy_policy() -> dict[str, Any]:
    if not REDUNDANCY_POLICY.exists():
        return {"skip_wortgruppen": [], "merge_wortgruppen": {}}
    policy = json.loads(REDUNDANCY_POLICY.read_text(encoding="utf-8"))
    if policy.get("version") != 1:
        raise CompletionError("unsupported redundancy policy version")
    return policy


def load_source_text_overrides() -> dict[str, Any]:
    if not SOURCE_TEXT_OVERRIDES.exists():
        return {"examples": {}}
    overrides = json.loads(SOURCE_TEXT_OVERRIDES.read_text(encoding="utf-8"))
    if overrides.get("version") != 1 or not isinstance(overrides.get("examples"), dict):
        raise CompletionError("unsupported source-text override schema")
    return overrides


def record_variants(record: dict[str, Any]) -> set[str]:
    values = [record["fields"]["Lemma"]] + split_answers(record["fields"].get("AcceptedAnswersDE", ""))
    return {variant for value in values for variant in source_variants(value)}


def variant_index(records: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for key, record in records.items():
        for variant in record_variants(record):
            index[variant].add(key)
            index["~" + variant.casefold()].add(key)
    return index


def index_record(index: dict[str, set[str]], key: str, record: dict[str, Any]) -> None:
    for variant in record_variants(record):
        index[variant].add(key)
        index["~" + variant.casefold()].add(key)


def find_record(
    records: dict[str, dict[str, Any]], index: dict[str, set[str]], word: str,
    pos: str = "", gender: str = "", examples: list[str] | None = None,
) -> str | None:
    variants = set(source_variants(word))
    exact = sorted({key for variant in variants for key in index.get(variant, set())})
    if not exact and pos:
        exact = sorted({key for variant in variants for key in index.get("~" + variant.casefold(), set())})
    if not exact and clean(word).endswith("-"):
        prefix = lemma_key(word)[:-1]
        exact = sorted({key for variant, keys in index.items() if not variant.startswith("~") and variant.startswith(prefix) for key in keys})
    candidates = [key for key in exact if compatible_pos(pos, records[key]["fields"].get("POS", ""))]
    if not candidates:
        candidates = exact
    if gender:
        narrowed = [key for key in candidates if not records[key]["fields"].get("Gender") or records[key]["fields"]["Gender"] == gender]
        if narrowed:
            candidates = narrowed
    if examples and len(candidates) > 1:
        source_sentences = {sentence_key(value) for value in examples}
        scored = []
        for key in candidates:
            overlap = len(source_sentences & {sentence_key(item["de"]) for item in records[key]["examples"]})
            scored.append((overlap, card_reps(records[key]["cards"]), key))
        scored.sort(reverse=True)
        if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][2]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return max(candidates, key=lambda key: (card_reps(records[key]["cards"]), -int(key) if key.isdigit() else 0))
    return None


def new_record(ref: str, lemma: str, level: str, pos: str = "", gender: str = "") -> dict[str, Any]:
    fields = {name: "" for name in gw.FIELDS}
    fields.update({
        "Lemma": lemma, "CEFR": level, "POS": pos, "Gender": gender,
        "AcceptedAnswersDE": lemma, "SourceID": ref, "SourceRefs": ref,
        "OriginalOrder": ref, "SourceNoteRaw": ref,
    })
    return {
        "note_id": None, "is_new": True, "fields": fields,
        "tags": [LEVEL_TAG[level], "goethe::migration::completed"],
        "deck": LEVEL_DECK[level], "cards": [], "examples": [],
        "source_refs": [ref], "categories": [], "translated": True,
    }


def lower_level(left: str, right: str) -> str:
    return "A1" if "A1" in {left, right} else "A2"


def add_example(record: dict[str, Any], german: str) -> None:
    key = sentence_key(german)
    if not key or any(sentence_key(item["de"]) == key for item in record["examples"]):
        return
    record["examples"].append({"de": clean(german), "en": "", "audio": ""})
    record["translated"] = True


def add_ref(record: dict[str, Any], ref: str, level: str) -> None:
    if ref not in record["source_refs"]:
        record["source_refs"].append(ref)
    current = record["fields"].get("CEFR") or level
    target = lower_level(current, level)
    record["fields"]["CEFR"] = target
    record["deck"] = LEVEL_DECK[target]


def merge_exact_duplicates(records: dict[str, dict[str, Any]], preserve_note_ids: set[int] | None = None) -> list[dict[str, Any]]:
    preserve_note_ids = preserve_note_ids or set()
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for key, record in records.items():
        if record["is_new"]:
            continue
        fields = record["fields"]
        identity = (lemma_key(fields["Lemma"]), clean(fields["MeaningEN"]).casefold(), clean(fields["POS"]).casefold())
        groups[identity].append(key)
    deletions = []
    for identity, keys in groups.items():
        if not identity[1] or len(keys) < 2:
            continue
        if any(records[key]["note_id"] in preserve_note_ids for key in keys):
            continue
        keys.sort(key=lambda key: (card_reps(records[key]["cards"]), -int(key)), reverse=True)
        survivor = records[keys[0]]
        for duplicate_key in keys[1:]:
            duplicate = records[duplicate_key]
            for example in duplicate["examples"]:
                if not any(sentence_key(item["de"]) == sentence_key(example["de"]) for item in survivor["examples"]):
                    survivor["examples"].append(example)
            for ref in duplicate["source_refs"]:
                if ref not in survivor["source_refs"]:
                    survivor["source_refs"].append(ref)
            survivor["fields"]["CEFR"] = lower_level(survivor["fields"]["CEFR"], duplicate["fields"]["CEFR"])
            survivor["deck"] = LEVEL_DECK[survivor["fields"]["CEFR"]]
            deletions.append({
                "note_id": duplicate["note_id"], "survivor": survivor["note_id"],
                "cards": duplicate["cards"], "fields": duplicate["fields"], "tags": duplicate["tags"],
            })
            del records[duplicate_key]
    return deletions


def apply_headword_policy(records: dict[str, dict[str, Any]], deletions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not HEADWORD_POLICY.exists():
        return deletions
    policy = json.loads(HEADWORD_POLICY.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise CompletionError("unsupported headword merge policy")
    existing_deletions = {item["note_id"] for item in deletions}
    updates = {str(note_id): value for note_id, value in policy.get("updates", {}).items()}
    for entry in policy.get("groups", []):
        survivor_key = str(entry["survivor"])
        if survivor_key not in records:
            continue
        survivor = records[survivor_key]
        if survivor_key in updates:
            survivor["fields"] = copy.deepcopy(updates[survivor_key])
            survivor["examples"] = goethe_examples.parse_fields(survivor["fields"])
            survivor["source_refs"] = split_refs(survivor["fields"].get("SourceRefs", ""))
        for duplicate_id in entry.get("delete", []):
            duplicate_key = str(duplicate_id)
            if duplicate_key not in records:
                continue
            duplicate = records[duplicate_key]
            if duplicate_id not in existing_deletions:
                deletions.append({
                    "note_id": duplicate_id, "survivor": entry["survivor"],
                    "cards": duplicate["cards"], "fields": duplicate["fields"], "tags": duplicate["tags"],
                })
                existing_deletions.add(duplicate_id)
            del records[duplicate_key]
    for note_id, value in updates.items():
        if note_id in records and not any(note_id == str(entry["survivor"]) for entry in policy.get("groups", [])):
            records[note_id]["fields"] = copy.deepcopy(value)
            records[note_id]["examples"] = goethe_examples.parse_fields(records[note_id]["fields"])
            records[note_id]["source_refs"] = split_refs(records[note_id]["fields"].get("SourceRefs", ""))
    return deletions


def build_manifest() -> dict[str, Any]:
    records, _ = load_live()
    redundancy_policy = load_redundancy_policy()
    source_text_overrides = load_source_text_overrides()
    skipped_source_refs = set(redundancy_policy.get("skip_wortgruppen", []))
    merge_wortgruppen = redundancy_policy.get("merge_wortgruppen", {})
    preserve_note_ids = set(map(int, redundancy_policy.get("preserve_note_ids", [])))
    source_targets = {str(ref): str(note_id) for ref, note_id in redundancy_policy.get("source_targets", {}).items()}
    deletions = merge_exact_duplicates(records, preserve_note_ids)
    index = variant_index(records)
    ambiguous: list[dict[str, Any]] = []
    source_counts = {"A1_MAIN": 0, "A2_MAIN": 0, "A1_WG": 0, "A2_WG": 0}
    for level, path in (("A1", gw.SOURCE_A1), ("A2", gw.SOURCE_A2)):
        for row in gw.parse_markdown(path):
            source_counts[f"{level}_MAIN"] += 1
            ref = f"{level}-MAIN-{row['row']:04d}"
            configured_target = source_targets.get(ref)
            if configured_target and configured_target not in records:
                raise CompletionError(f"configured source target missing: {ref} -> {configured_target}")
            key = configured_target or find_record(records, index, row["word"], row["pos"], row["gender"], row["examples"])
            if key is None:
                key = f"new:{ref}"
                records[key] = new_record(ref, row["word"], level, row["pos"], row["gender"])
                index_record(index, key, records[key])
            record = records[key]
            add_ref(record, ref, level)
            if not record["fields"].get("POS"):
                record["fields"]["POS"] = row["pos"]
            if not record["fields"].get("Gender"):
                record["fields"]["Gender"] = row["gender"]
            if not record["fields"].get("Article") and row["gender"] in {"m.", "f.", "n.", "pl."}:
                article = {"m.": "der", "f.": "die", "n.": "das", "pl.": "die"}[row["gender"]]
                record["fields"]["Article"] = article
                record["fields"]["AcceptedArticlesDE"] = article
            if row["note"] and row["note"] not in record["fields"].get("SourceNoteRaw", ""):
                record["fields"]["SourceNoteRaw"] = clean(record["fields"].get("SourceNoteRaw", "") + " | " + row["note"])
            examples = source_text_overrides["examples"].get(ref, row["examples"])
            for example in examples:
                add_example(record, example)

    for level, path in WG_FILES.items():
        by_source_ref = {
            ref: key for key, record in records.items() for ref in record["source_refs"]
        }
        for row in parse_wortgruppen(path):
            source_counts[f"{level}_WG"] += 1
            if row["id"] in skipped_source_refs:
                continue
            lemma = wg_lemma(row)
            merge_spec = merge_wortgruppen.get(row["id"], {})
            key = by_source_ref.get(row["id"]) or find_record(
                records, index, merge_spec.get("target") or row["match"] or lemma,
            )
            if key is None:
                if merge_spec:
                    raise CompletionError(f"redundancy merge target missing: {row['id']} -> {merge_spec.get('target')}")
                key = f"new:{row['id']}"
                records[key] = new_record(row["id"], lemma, level)
                index_record(index, key, records[key])
            record = records[key]
            add_ref(record, row["id"], level)
            if row["canonical"]:
                fields = record["fields"]
                fields["Lemma"] = row["canonical"]
                fields["POS"] = row["pos"]
                fields["Article"] = row["article"]
                fields["Gender"] = row["gender"]
                fields["NounFormsRaw"] = row["noun_forms"]
                fields["AcceptedAnswersDE"] = "|".join(wg_answers(row))
                fields["AcceptedArticlesDE"] = row["article"]
                record["tags"] = sorted(set(record["tags"]) | {"goethe::quality::grammar_audited"})
            if merge_spec.get("as_example"):
                add_example(record, lemma)
            accepted = split_answers(record["fields"].get("AcceptedAnswersDE", ""))
            record["fields"]["AcceptedAnswersDE"] = "|".join(dict.fromkeys(accepted + wg_answers(row)))
            category = category_slug(row["category"])
            if category and category not in record["categories"]:
                record["categories"].append(category)
            detail = clean("; ".join(value for value in (row["entry"], row["detail"], row["note"]) if value))
            if detail and detail not in record["fields"].get("FormOrVariantNote", ""):
                record["fields"]["FormOrVariantNote"] = clean(record["fields"].get("FormOrVariantNote", "") + " | " + detail)
            grammar_note = row.get("grammar_note", "")
            if grammar_note and grammar_note not in record["fields"].get("FormOrVariantNote", ""):
                record["fields"]["FormOrVariantNote"] = clean(
                    record["fields"].get("FormOrVariantNote", "") + " | " + grammar_note
                )

    deletions.extend(merge_exact_duplicates(records, preserve_note_ids))
    deletions = apply_headword_policy(records, deletions)
    allowed_examples = goethe_source_examples.allowed_examples_by_level()
    audit_manifest = None
    if english_audit.MANIFEST.exists():
        try:
            audit_manifest = english_audit.load_json(english_audit.MANIFEST)
            english_audit.validate_manifest(audit_manifest)
            for entry in audit_manifest["entries"].values():
                for example in entry["desired_examples"]:
                    key = goethe_source_examples.sentence_key(example["de"])
                    allowed_examples[entry["cefr"]].setdefault(key, example["de"])
        except english_audit.AuditError as exc:
            raise CompletionError(f"English audit policy failed: {exc}") from exc
    for record in records.values():
        refs = list(dict.fromkeys(record["source_refs"]))
        refs.sort(key=lambda ref: (0 if ref.startswith("A1") else 1, ref))
        record["source_refs"] = refs
        record["fields"]["SourceRefs"] = "|".join(refs)
        if refs:
            record["fields"]["SourceID"] = refs[0]
        level = record["fields"]["CEFR"]
        record["examples"] = goethe_source_examples.filter_examples(
            level, record["examples"], allowed_examples,
        )
        record["deck"] = LEVEL_DECK[level]
        record["tags"] = sorted(
            (set(record["tags"]) - set(LEVEL_TAG.values()))
            | {LEVEL_TAG[level], "goethe::migration::completed"}
            | {f"goethe::wortgruppe::{category}" for category in record["categories"]}
        )
    if audit_manifest is not None:
        try:
            english_audit.apply_manifest_to_records(records, audit_manifest, strict=True)
        except english_audit.AuditError as exc:
            raise CompletionError(f"English audit policy failed: {exc}") from exc
    manifest = {
        "version": 1, "records": records, "deletions": deletions,
        "source_counts": source_counts, "skipped_source_refs": sorted(skipped_source_refs),
        "ambiguous": ambiguous,
    }
    return manifest


def save_manifest(manifest: dict[str, Any]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def command_build(_: argparse.Namespace) -> None:
    manifest = build_manifest()
    save_manifest(manifest)
    records = list(manifest["records"].values())
    print(json.dumps({
        "manifest": str(MANIFEST), "records": len(records),
        "new": sum(record["is_new"] for record in records),
        "delete": len(manifest["deletions"]),
        "untranslated_notes": sum(not record["fields"].get("MeaningEN") for record in records),
        "untranslated_examples": sum(not example["en"] for record in records for example in record["examples"]),
        "source_counts": manifest["source_counts"], "ambiguous": len(manifest["ambiguous"]),
    }, ensure_ascii=False, indent=2))


def translate_one(text: str) -> str:
    query = urllib.parse.urlencode({"client": "gtx", "sl": "de", "tl": "en", "dt": "t", "q": text})
    request = urllib.request.Request("https://translate.googleapis.com/translate_a/single?" + query, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    translated = clean("".join(part[0] for part in data[0] if part and part[0]))
    if not translated:
        raise CompletionError(f"empty translation: {text}")
    return translated


def command_translate(_: argparse.Namespace) -> None:
    if not MANIFEST.exists():
        raise CompletionError("manifest missing; run build")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache = json.loads(TRANSLATIONS.read_text(encoding="utf-8")) if TRANSLATIONS.exists() else {}
    wanted = set()
    for record in manifest["records"].values():
        if not record["fields"].get("MeaningEN"):
            wanted.add(record["fields"]["Lemma"])
        for example in record["examples"]:
            if not example["en"]:
                wanted.add(example["de"])
    pending = sorted(wanted - set(cache))
    print(f"translate pending={len(pending)} cached={len(cache)}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(translate_one, text): text for text in pending}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            text = futures[future]
            cache[text] = future.result()
            if index % 25 == 0:
                STATE.mkdir(parents=True, exist_ok=True)
                TRANSLATIONS.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"translated={index}/{len(pending)}")
    TRANSLATIONS.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    for record in manifest["records"].values():
        changed = False
        if not record["fields"].get("MeaningEN"):
            record["fields"]["MeaningEN"] = cache[record["fields"]["Lemma"]]
            changed = True
        for example in record["examples"]:
            if not example["en"]:
                example["en"] = cache[example["de"]]
                changed = True
        if changed:
            record["translated"] = True
            record["tags"] = sorted(set(record["tags"]) | {QUALITY_TRANSLATION, "goethe::quality::review_needed"})
    save_manifest(manifest)
    print(f"translations={len(cache)} manifest updated")


def render_examples(record: dict[str, Any]) -> None:
    goethe_examples.render_fields(record["fields"], record["examples"])


def validate_manifest(manifest: dict[str, Any]) -> dict[str, int]:
    records = list(manifest["records"].values())
    for record in records:
        render_examples(record)
        fields = record["fields"]
        if not fields.get("Lemma") or not fields.get("MeaningEN") or not fields.get("SourceRefs"):
            raise CompletionError(f"required fields missing: {record.get('note_id')} {fields.get('Lemma')}")
        if fields["CEFR"] not in LEVEL_DECK or record["deck"] != LEVEL_DECK[fields["CEFR"]]:
            raise CompletionError(f"level/deck mismatch: {fields['Lemma']}")
        if any(not item["en"] for item in record["examples"]):
            raise CompletionError(f"untranslated example: {fields['Lemma']}")
    refs = {ref for record in records for ref in record["source_refs"]}
    skipped_source_refs = set(manifest.get("skipped_source_refs", []))
    expected = sum(manifest["source_counts"].values()) - len(skipped_source_refs)
    source_refs = {ref for ref in refs if re.match(r"A[12]-(?:MAIN|WG)-", ref)}
    if source_refs & skipped_source_refs:
        raise CompletionError("skipped source ref was retained in the manifest")
    if len(source_refs) != expected:
        raise CompletionError(f"source coverage mismatch: {len(source_refs)} != {expected}")
    deleted = {item["note_id"] for item in manifest["deletions"]}
    survivors = {record["note_id"] for record in records if not record["is_new"]}
    if deleted & survivors:
        raise CompletionError("deleted note also survives")
    return {
        "records": len(records), "new": sum(record["is_new"] for record in records),
        "delete": len(deleted), "source_refs": len(source_refs),
        "a1": sum(record["fields"]["CEFR"] == "A1" for record in records),
        "a2": sum(record["fields"]["CEFR"] == "A2" for record in records),
    }


def command_dry_run(_: argparse.Namespace) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(json.dumps(validate_manifest(manifest), indent=2))


def anki_multi(actions: list[dict[str, Any]], size: int = 60) -> list[Any]:
    results = []
    for batch in gw.chunks(actions, size):
        response = gw.anki("multi", actions=batch)
        errors = [item.get("error") for item in response if isinstance(item, dict) and item.get("error")]
        if errors:
            raise CompletionError(f"Anki multi errors: {errors[:3]}")
        results.extend(response)
    return results


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != CONFIRMATION:
        raise CompletionError(f"confirmation must equal {CONFIRMATION}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    summary = validate_manifest(manifest)
    actual_fields = gw.anki("modelFieldNames", modelName=MODEL)
    if actual_fields != gw.FIELDS:
        raise CompletionError("target model schema not upgraded")
    actions = []
    existing_records = [record for record in manifest["records"].values() if not record["is_new"]]
    for record in existing_records:
        actions.append({"action": "updateNoteFields", "params": {"note": {"id": record["note_id"], "fields": record["fields"]}}})
        actions.append({"action": "removeTags", "params": {"notes": [record["note_id"]], "tags": " ".join(LEVEL_TAG.values())}})
        actions.append({"action": "addTags", "params": {"notes": [record["note_id"]], "tags": " ".join(record["tags"])}})
    anki_multi(actions)
    for record in existing_records:
        card_ids = [card["cardId"] for card in record["cards"]]
        if card_ids and any(card["deckName"] != record["deck"] for card in record["cards"]):
            gw.anki("changeDeck", cards=card_ids, deck=record["deck"])
    new_ids = []
    for record in manifest["records"].values():
        if not record["is_new"] or args.skip_new:
            continue
        note_id = gw.anki("addNote", note={
            "deckName": record["deck"], "modelName": MODEL,
            "fields": record["fields"], "tags": record["tags"],
            "options": {"allowDuplicate": True},
        })
        if not note_id:
            raise CompletionError(f"failed to add note: {record['fields']['Lemma']}")
        new_ids.append(note_id)
    delete_ids = [item["note_id"] for item in manifest["deletions"]]
    if args.keep_duplicates:
        by_note_id = {record["note_id"]: record for record in manifest["records"].values() if not record["is_new"]}
        for duplicate in manifest["deletions"]:
            survivor = by_note_id[duplicate["survivor"]]
            level = survivor["fields"]["CEFR"]
            refs = survivor["fields"]["SourceRefs"]
            gw.anki("updateNoteFields", note={"id": duplicate["note_id"], "fields": {"CEFR": level, "SourceRefs": refs}})
            gw.anki("removeTags", notes=[duplicate["note_id"]], tags=" ".join(LEVEL_TAG.values()))
            duplicate_tags = (set(duplicate["tags"]) - set(LEVEL_TAG.values())) | {LEVEL_TAG[level], "goethe::migration::completed"}
            gw.anki("addTags", notes=[duplicate["note_id"]], tags=" ".join(sorted(duplicate_tags)))
            card_ids = [card["cardId"] for card in duplicate["cards"]]
            if card_ids and any(card["deckName"] != LEVEL_DECK[level] for card in duplicate["cards"]):
                gw.anki("changeDeck", cards=card_ids, deck=LEVEL_DECK[level])
        deleted_count = 0
    elif delete_ids:
        gw.anki("deleteNotes", notes=delete_ids)
        deleted_count = len(delete_ids)
    else:
        deleted_count = 0
    print(json.dumps({**summary, "new_ids": len(new_ids), "deleted_ids": deleted_count, "kept_duplicates": len(delete_ids) if args.keep_duplicates else 0, "skipped_new": bool(args.skip_new)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build").set_defaults(func=command_build)
    sub.add_parser("translate").set_defaults(func=command_translate)
    sub.add_parser("dry-run").set_defaults(func=command_dry_run)
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.add_argument("--keep-duplicates", action="store_true")
    apply.add_argument("--skip-new", action="store_true")
    apply.set_defaults(func=command_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (CompletionError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
