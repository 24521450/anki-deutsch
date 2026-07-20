"""Complete the live Goethe A1-B1 deck from alphabetical and Wortgruppen sources.

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
import sqlite3
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

import goethe_apkg as apkg
import goethe_werkstatt_migrate as gw
import goethe_examples
import goethe_english_audit as english_audit
import goethe_scope as scope
import goethe_source_examples
import goethe_target_highlights as target_highlights
import goethe_template_policy as production_policy
import goethe_review_policy as review_policy
import goethe_noun_policy as noun_policy

ROOT = gw.ROOT
STATE = ROOT / "tools" / ".goethe_completion"
TRANSLATIONS = ROOT / "review" / "goethe_completion_translations.json"
REDUNDANCY_POLICY = ROOT / "review" / "goethe_redundancy_policy.json"
HEADWORD_POLICY = ROOT / "review" / "goethe_headword_merges.json"
SOURCE_TEXT_OVERRIDES = ROOT / "review" / "goethe_source_text_overrides.json"
B1_ENGLISH_OVERRIDES = ROOT / "review" / "goethe_b1_english_overrides.json"
B1_DATA_OVERRIDES = ROOT / "review" / "goethe_b1_data_overrides.json"
MANIFEST = STATE / "manifest.json"
RESULT = STATE / "apply_result.json"
MODEL = gw.MODEL
MANIFEST_VERSION = 2
PREIMAGE_SCHEMA_VERSION = 1
PARENT_DECK = "Goethe Institute"
CARD_STATE_KEYS = (
    "cardId", "note", "ord", "deckName", "factor", "interval", "type",
    "queue", "due", "reps", "lapses", "left", "flags", "mod",
)
WG_FILES = {
    "A1": ROOT / "sources" / "goethe" / "Goethe_A1_Wortgruppen.md",
    "A2": ROOT / "sources" / "goethe" / "Goethe_A2_Wortgruppen.md",
    "B1": ROOT / "sources" / "goethe" / "Goethe_B1_Wortgruppen.md",
}
LEVELS = scope.LEVELS
LEVEL_RANK = scope.LEVEL_RANK
LEVEL_DECK = scope.LEVEL_DECK
LEVEL_TAG = scope.LEVEL_TAG
QUALITY_TRANSLATION = "goethe::quality::translation_review_needed"
CONFIRMATION = "COMPLETE_GOETHE_A1_A2_B1"
REVIEWED_SPLIT_FIELDS = frozenset({
    "Lemma", "POS", "Article", "Gender", "NounFormsRaw",
    "AcceptedAnswersDE", "AcceptedArticlesDE", "WordAudio", "FormOrVariantNote",
})
REVIEWED_SPLIT_CHILD_FIELDS = REVIEWED_SPLIT_FIELDS | {
    "OriginalOrder", "SourceNoteRaw", "LegacyGUID",
}
PHYSICAL_SOURCE_REF = re.compile(r"(?:A1|A2|B1)-(?:MAIN|WG)-\d{4}\Z")
SOURCE_REF_PREFIX = re.compile(r"(?:A1|A2|B1)-(?:MAIN|WG)-")


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
    left, right = source.split(".")[0], target.split(".")[0]
    if {left, right} <= {"adj", "adv"}:
        return True
    return left == right


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
        if not re.match(r"^\| (?:A1|A2|B1)-WG-", line):
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


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_card_state(card: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in CARD_STATE_KEYS if key not in card]
    if missing:
        raise CompletionError(f"card state missing keys: {missing}")
    state = {key: card[key] for key in CARD_STATE_KEYS}
    state["deckName"] = str(state["deckName"])
    for key in CARD_STATE_KEYS:
        if key != "deckName":
            try:
                state[key] = int(state[key])
            except (TypeError, ValueError) as exc:
                raise CompletionError(f"invalid card state {key}: {state[key]!r}") from exc
    return state


def canonical_live_note_state(record: dict[str, Any]) -> dict[str, Any]:
    note_id = record.get("note_id")
    if not isinstance(note_id, int):
        raise CompletionError("live note has no concrete note ID")
    fields = record.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(gw.FIELDS):
        raise CompletionError(f"live note field schema differs: {note_id}")
    cards = sorted(
        (canonical_card_state(card) for card in record.get("cards", [])),
        key=lambda card: card["cardId"],
    )
    if sorted(card["ord"] for card in cards) != [0, 1]:
        raise CompletionError(f"expected exact card ords 0/1: note {note_id}")
    if any(card["note"] != note_id for card in cards):
        raise CompletionError(f"note/card association differs: note {note_id}")
    return {
        "note_id": note_id,
        "model": str(record.get("model", "")),
        "fields": {name: str(fields[name]) for name in gw.FIELDS},
        "tags": sorted(str(tag) for tag in record.get("tags", [])),
        "cards": cards,
    }


def build_live_preimage(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    for record in records.values():
        state = canonical_live_note_state(record)
        key = str(state["note_id"])
        if key in notes:
            raise CompletionError(f"duplicate live note ID: {key}")
        notes[key] = {
            "sha256": canonical_hash(state),
            "cards": state["cards"],
        }
    return {
        "schema_version": PREIMAGE_SCHEMA_VERSION,
        "notes": {key: notes[key] for key in sorted(notes, key=int)},
    }


def load_live() -> tuple[dict[str, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    if gw.anki("version") != 6:
        raise CompletionError("unexpected AnkiConnect API version")
    raw_ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    if not isinstance(raw_ids, list):
        raise CompletionError("findNotes returned a non-list")
    ids = sorted(int(note_id) for note_id in raw_ids)
    if len(ids) != len(set(ids)):
        raise CompletionError("findNotes returned duplicate note IDs")
    id_set = set(ids)
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(ids):
        result = gw.anki("notesInfo", notes=batch)
        if not isinstance(result, list):
            raise CompletionError("notesInfo returned a non-list")
        notes.extend(result)
    returned_note_ids = [int(note["noteId"]) for note in notes]
    if len(returned_note_ids) != len(set(returned_note_ids)) or set(returned_note_ids) != id_set:
        raise CompletionError("notesInfo returned a different note ID set")
    wrong_models = [
        int(note["noteId"]) for note in notes if note.get("modelName") != MODEL
    ]
    if wrong_models:
        raise CompletionError(f"notesInfo returned another model: {wrong_models[:5]}")

    linked_card_ids: list[int] = []
    for note in notes:
        note_id = int(note["noteId"])
        if set(note.get("fields", {})) != set(gw.FIELDS):
            raise CompletionError(f"live note field schema differs: {note_id}")
        note_cards = note.get("cards")
        if not isinstance(note_cards, list):
            raise CompletionError(f"notesInfo cards missing: note {note_id}")
        linked_card_ids.extend(int(card_id) for card_id in note_cards)
    if len(linked_card_ids) != len(set(linked_card_ids)):
        raise CompletionError("notesInfo returned duplicate card IDs")

    cards: list[dict[str, Any]] = []
    for batch in gw.chunks(sorted(linked_card_ids)):
        result = gw.anki("cardsInfo", cards=batch)
        if not isinstance(result, list):
            raise CompletionError("cardsInfo returned a non-list")
        cards.extend(result)
    returned_card_ids = [int(card["cardId"]) for card in cards]
    if len(returned_card_ids) != len(set(returned_card_ids)) or set(returned_card_ids) != set(linked_card_ids):
        raise CompletionError("cardsInfo returned a different card ID set")
    by_note: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        note_id = int(card["note"])
        if note_id not in id_set:
            raise CompletionError(f"card belongs to an unexpected note: {card['cardId']}")
        by_note[note_id].append(card)
    records: dict[str, dict[str, Any]] = {}
    for note in notes:
        note_id = int(note["noteId"])
        linked = sorted(int(card_id) for card_id in note.get("cards", []))
        note_cards = sorted(by_note.get(note_id, []), key=lambda card: int(card["cardId"]))
        actual = [int(card["cardId"]) for card in note_cards]
        if linked != actual:
            raise CompletionError(f"note/card linkage drift: note {note_id}")
        ords = sorted(int(card.get("ord", -1)) for card in note_cards)
        if ords != [0, 1]:
            raise CompletionError(f"expected exact card ords 0/1: note {note_id}, ords={ords}")
        decks = {str(card.get("deckName", "")) for card in note_cards}
        level = field(note, "CEFR")
        if level not in LEVEL_DECK:
            raise CompletionError(f"live note has unsupported CEFR: {note_id}")
        if decks != {LEVEL_DECK[level]}:
            raise CompletionError(f"note cards span unexpected decks: note {note_id}")
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
            "model": note["modelName"],
            "fields": fields,
            "tags": sorted(set(note.get("tags", []))),
            "deck": note_cards[0]["deckName"],
            "cards": note_cards,
            "examples": examples,
            "source_refs": refs,
            "categories": [],
            "translated": False,
        }
    return records, dict(by_note)


def load_redundancy_policy() -> dict[str, Any]:
    if not REDUNDANCY_POLICY.exists():
        return {
            "skip_wortgruppen": [], "merge_wortgruppen": {},
            "reviewed_note_merges": [], "reviewed_note_splits": [],
            "main_source_example_overrides": {},
        }
    policy = json.loads(REDUNDANCY_POLICY.read_text(encoding="utf-8"))
    if policy.get("version") != 1:
        raise CompletionError("unsupported redundancy policy version")
    return policy


def configured_wortgruppe_key(
    ref: str,
    merge_spec: dict[str, Any],
    by_source_ref: dict[str, str],
) -> str | None:
    """Resolve an explicitly reviewed Wortgruppe route by source identity."""
    current = by_source_ref.get(ref)
    target_ref = clean(merge_spec.get("target_source_ref", ""))
    if not target_ref:
        return current
    target = by_source_ref.get(target_ref)
    if target is None:
        raise CompletionError(f"Wortgruppe source target missing: {ref} -> {target_ref}")
    if current is not None and current != target:
        raise CompletionError(f"Wortgruppe source route conflicts: {ref} -> {target_ref}")
    return target


def configured_main_source_key(
    ref: str,
    main_source_aliases: dict[str, str],
    source_targets: dict[str, str],
    records: dict[str, dict[str, Any]],
    by_source_ref: dict[str, str],
) -> str | None:
    """Resolve a reviewed main-list alias before heuristic matching.

    Alias targets use source references rather than note IDs so a fresh
    manifest can route split rows after the canonical row has been created.
    The function fails closed when a configured target is unavailable.
    """
    target_ref = main_source_aliases.get(ref)
    if target_ref:
        key = by_source_ref.get(target_ref)
        if key is None:
            raise CompletionError(f"main source alias target missing: {ref} -> {target_ref}")
        return key
    configured_target = source_targets.get(ref)
    if configured_target:
        if configured_target not in records:
            raise CompletionError(f"configured source target missing: {ref} -> {configured_target}")
        return configured_target
    return by_source_ref.get(ref)


def main_source_examples(
    ref: str,
    row: dict[str, Any],
    source_text_overrides: dict[str, list[str]],
    reviewed_overrides: dict[str, list[str]],
) -> list[str]:
    """Return reviewed per-note examples without narrowing the source whitelist."""
    values = reviewed_overrides.get(
        ref, source_text_overrides.get(ref, row["examples"]),
    )
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise CompletionError(f"invalid main-source example override: {ref}")
    return values


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


def reindex_record(index: dict[str, set[str]], key: str, record: dict[str, Any]) -> None:
    """Replace a record's index entries after its canonical fields change."""
    for variant, keys in list(index.items()):
        keys.discard(key)
        if not keys:
            del index[variant]
    index_record(index, key, record)


def record_preference(key: str, record: dict[str, Any]) -> tuple[int, int, int]:
    """Prefer the lowest CEFR, then review history, then oldest note ID."""
    level = record.get("fields", {}).get("CEFR", "")
    rank = LEVEL_RANK.get(level, len(LEVELS))
    note_id = record.get("note_id")
    if not isinstance(note_id, int):
        note_id = int(key) if key.isdigit() else sys.maxsize
    return (rank, -card_reps(record.get("cards", [])), note_id)


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
    if pos and exact and not candidates:
        return None
    if not candidates:
        candidates = exact
    if gender:
        narrowed = [key for key in candidates if not records[key]["fields"].get("Gender") or records[key]["fields"]["Gender"] == gender]
        if narrowed:
            candidates = narrowed
    if examples and len(candidates) > 1:
        source_sentences = {sentence_key(value) for value in examples}
        overlap = {
            key: len(source_sentences & {
                sentence_key(item["de"]) for item in records[key]["examples"]
            })
            for key in candidates
        }
        best_overlap = max(overlap.values(), default=0)
        if best_overlap:
            candidates = [key for key in candidates if overlap[key] == best_overlap]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return min(candidates, key=lambda key: record_preference(key, records[key]))
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
    try:
        return min((left, right), key=LEVEL_RANK.__getitem__)
    except KeyError as exc:
        raise CompletionError(f"unsupported Goethe level: {exc.args[0]}") from exc


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


def apply_main_grammar(record: dict[str, Any], row: dict[str, Any]) -> None:
    """Populate grammar fields directly supported by a Goethe source entry."""
    raw = clean(row.get("note", "")).removeprefix("source: ").strip()
    if not raw:
        return
    fields = record["fields"]
    pos = clean(row.get("pos", ""))
    if pos == "n.":
        articles = list(dict.fromkeys(re.findall(r"\b(?:der|die|das)\b", raw.split("→", 1)[0])))
        if row.get("gender") == "pl.":
            articles = ["die"]
            genders = ["pl."]
        else:
            genders = [{"der": "m.", "die": "f.", "das": "n."}[item] for item in articles]
        if articles:
            fields["Article"] = "/".join(articles)
            fields["AcceptedArticlesDE"] = "|".join(articles)
            fields["Gender"] = "/".join(genders)
        forms = []
        for segment in re.split(r";\s*(?=(?:source:\s*)?(?:der|die|das)\b)", raw):
            segment = segment.removeprefix("source: ").strip()
            if "," in segment:
                value = clean(segment.split(",", 1)[1].split("→", 1)[0])
                value = re.sub(r"\s*\((?:D|A|CH)(?:\s*,\s*(?:D|A|CH))*\)\s*$", "", value)
                if value and value not in forms:
                    forms.append(value)
            elif re.search(r"\((?:nur\s+)?Pl\.(?:ural)?\)", segment, re.I):
                forms.append("(plural only)")
        if forms:
            fields["NounFormsRaw"] = " / ".join(dict.fromkeys(forms))
    elif pos == "v.":
        forms = []
        for segment in re.split(r";\s*(?=(?:source:\s*)?(?:\(?sich\)?\s+)?[a-zäöüß])", raw):
            segment = segment.removeprefix("source: ").strip()
            if "," not in segment:
                continue
            value = clean(segment.split(",", 1)[1].split("→", 1)[0])
            value = re.sub(r"\s*\((?:D|A|CH)(?:\s*,\s*(?:D|A|CH))*\)\s*$", "", value)
            if value and value not in forms:
                forms.append(value)
        if forms:
            fields["VerbFormsRaw"] = " / ".join(forms)
    if "→" in raw or re.search(r"\((?:D|A|CH)(?:\s*,\s*(?:D|A|CH))*\)", raw):
        fields["RegionalVariants"] = raw
    record["tags"] = sorted(set(record["tags"]) | {"goethe::quality::grammar_audited"})


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
        keys.sort(key=lambda key: record_preference(key, records[key]))
        survivor = records[keys[0]]
        for duplicate_key in keys[1:]:
            duplicate = records[duplicate_key]
            for example in duplicate["examples"]:
                existing = next((
                    item for item in survivor["examples"]
                    if sentence_key(item["de"]) == sentence_key(example["de"])
                ), None)
                if existing is None:
                    survivor["examples"].append(example)
                else:
                    for name in ("en", "audio"):
                        if not existing.get(name) and example.get(name):
                            existing[name] = example[name]
            for ref in duplicate["source_refs"]:
                if ref not in survivor["source_refs"]:
                    survivor["source_refs"].append(ref)
            if not survivor["fields"].get("WordAudio") and duplicate["fields"].get("WordAudio"):
                survivor["fields"]["WordAudio"] = duplicate["fields"]["WordAudio"]
            survivor["tags"] = sorted(set(survivor["tags"]) | set(duplicate["tags"]))
            survivor["fields"]["CEFR"] = lower_level(survivor["fields"]["CEFR"], duplicate["fields"]["CEFR"])
            survivor["deck"] = LEVEL_DECK[survivor["fields"]["CEFR"]]
            deletions.append({
                "note_id": duplicate["note_id"], "survivor": survivor["note_id"],
                "cards": duplicate["cards"], "fields": duplicate["fields"], "tags": duplicate["tags"],
            })
            del records[duplicate_key]
    return deletions


def apply_reviewed_note_merges(
    records: dict[str, dict[str, Any]],
    deletions: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Schedule guarded note-ID merges before source rows are routed.

    Missing duplicates are accepted so rebuilding after an apply is
    idempotent. A duplicate that is still present must match both reviewed
    note ID and source identity, and its survivor must do the same.
    """
    scheduled = {int(item["note_id"]): int(item["survivor"]) for item in deletions}
    for group in groups:
        survivor_id = int(group["survivor"])
        duplicate_id = int(group["duplicate"])
        survivor_key, duplicate_key = str(survivor_id), str(duplicate_id)
        survivor = records.get(survivor_key)
        if survivor is None:
            raise CompletionError(
                f"reviewed merge survivor missing: {duplicate_id} -> {survivor_id}"
            )
        survivor_ref = str(group["survivor_source_ref"])
        if survivor.get("note_id") != survivor_id or survivor_ref not in survivor["source_refs"]:
            raise CompletionError(
                f"reviewed merge survivor identity mismatch: {survivor_id} {survivor_ref}"
            )
        if duplicate_id in scheduled and scheduled[duplicate_id] != survivor_id:
            raise CompletionError(
                f"reviewed merge deletion conflicts: {duplicate_id} -> {scheduled[duplicate_id]}"
            )
        duplicate = records.get(duplicate_key)
        if duplicate is None:
            continue
        duplicate_ref = str(group["duplicate_source_ref"])
        if duplicate.get("note_id") != duplicate_id or duplicate_ref not in duplicate["source_refs"]:
            raise CompletionError(
                f"reviewed merge duplicate identity mismatch: {duplicate_id} {duplicate_ref}"
            )
        if record_preference(duplicate_key, duplicate) < record_preference(survivor_key, survivor):
            raise CompletionError(
                f"reviewed merge violates survivor priority: {duplicate_id} -> {survivor_id}"
            )
        if duplicate_id not in scheduled:
            deletions.append({
                "note_id": duplicate_id,
                "survivor": survivor_id,
                "cards": duplicate["cards"],
                "fields": duplicate["fields"],
                "tags": duplicate["tags"],
            })
            scheduled[duplicate_id] = survivor_id
        del records[duplicate_key]
    return deletions


def apply_reviewed_note_splits(
    records: dict[str, dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, str]:
    """Apply guarded one-survivor/many-child splits after source routing.

    Existing policies use a single ``child`` object.  A ``children`` list is
    also accepted for one combined live note that accidentally contains two or
    more distinct physical source rows.  Each child may use its physical
    source ID directly (or a durable suffixed split ID for the historic
    holiday migration).
    """
    coverage_aliases: dict[str, str] = {}
    for group in groups:
        try:
            source_ref = group["source_ref"]
            survivor_id = int(group["survivor_note_id"])
            expected_lemma = group["expected_lemma"]
            expected_refs = group["expected_source_refs"]
            survivor_spec = group["survivor"]
            raw_children = group.get("children")
            if raw_children is None:
                raw_children = [group["child"]]
            if not isinstance(raw_children, list) or not raw_children:
                raise ValueError("children must be a non-empty list")
            child_specs = raw_children
            survivor_source_id = survivor_spec["source_id"]
            survivor_refs = survivor_spec["source_refs"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CompletionError(f"invalid reviewed split policy: {exc}") from exc

        scalar_values = (source_ref, expected_lemma, survivor_source_id)
        if not all(
            isinstance(value, str) and value == clean(value) and value
            for value in scalar_values
        ):
            raise CompletionError("invalid reviewed split policy: blank or unnormalised identity")
        if survivor_id <= 0:
            raise CompletionError("invalid reviewed split policy: note ID")

        ref_lists = [expected_refs, survivor_refs]
        parsed_children: list[tuple[dict[str, Any], str, str, list[str], str, dict[str, str]]] = []
        for child_spec in child_specs:
            try:
                child_source_id = child_spec["source_id"]
                child_coverage_ref = child_spec["coverage_ref"]
                child_refs = child_spec["source_refs"]
                child_cefr = child_spec["cefr"]
                child_overrides = child_spec["field_overrides"]
            except (KeyError, TypeError) as exc:
                raise CompletionError(f"invalid reviewed split child: {exc}") from exc
            scalar_values = (child_source_id, child_coverage_ref, child_cefr)
            if not all(
                isinstance(value, str) and value == clean(value) and value
                for value in scalar_values
            ):
                raise CompletionError("invalid reviewed split child: blank or unnormalised identity")
            if child_cefr not in LEVEL_DECK:
                raise CompletionError("invalid reviewed split child: CEFR")
            ref_lists.append(child_refs)
            if (
                not isinstance(child_refs, list)
                or not child_refs
                or len(child_refs) != len(set(child_refs))
                or any(not isinstance(ref, str) or ref != clean(ref) or not ref for ref in child_refs)
                or child_coverage_ref not in expected_refs
                or child_source_id not in child_refs
                or not PHYSICAL_SOURCE_REF.fullmatch(child_coverage_ref)
                or not (
                    child_source_id == child_coverage_ref
                    or child_source_id.startswith(f"{child_coverage_ref}-")
                )
            ):
                raise CompletionError("invalid reviewed split child: source identity")
            if child_source_id in coverage_aliases:
                raise CompletionError(f"duplicate reviewed split child: {child_source_id}")
            if not isinstance(child_overrides, dict) or not child_overrides.get("Lemma"):
                raise CompletionError("invalid reviewed split child field overrides")
            unsupported = set(child_overrides) - REVIEWED_SPLIT_CHILD_FIELDS
            if unsupported or any(not isinstance(value, str) for value in child_overrides.values()):
                raise CompletionError(
                    f"invalid reviewed split child field overrides: {sorted(unsupported)!r}"
                )
            if (
                child_overrides.get("LegacyGUID") != f"goethe:{child_source_id}"
                or child_overrides.get("OriginalOrder") != child_coverage_ref
                or not child_overrides.get("SourceNoteRaw", "").startswith(child_coverage_ref)
            ):
                raise CompletionError("invalid reviewed split child identity fields")
            parsed_children.append((
                child_spec, child_source_id, child_coverage_ref, child_refs,
                child_cefr, child_overrides,
            ))
            coverage_aliases[child_source_id] = child_coverage_ref

        for refs in ref_lists:
            if (
                not isinstance(refs, list)
                or not refs
                or len(refs) != len(set(refs))
                or any(not isinstance(ref, str) or ref != clean(ref) or not ref for ref in refs)
            ):
                raise CompletionError("invalid reviewed split policy: source refs")
        if source_ref not in expected_refs or survivor_source_id not in survivor_refs:
            raise CompletionError("invalid reviewed split policy: source identity")
        survivor_overrides = survivor_spec.get("field_overrides")
        if not isinstance(survivor_overrides, dict) or not survivor_overrides.get("Lemma"):
            raise CompletionError("invalid reviewed split survivor field overrides")
        unsupported = set(survivor_overrides) - REVIEWED_SPLIT_FIELDS
        if unsupported or any(not isinstance(value, str) for value in survivor_overrides.values()):
            raise CompletionError(
                f"invalid reviewed split survivor field overrides: {sorted(unsupported)!r}"
            )

        survivor = records.get(str(survivor_id))
        if survivor is None or survivor.get("note_id") != survivor_id:
            raise CompletionError(f"reviewed split survivor missing: {survivor_id}")

        matches_by_child: list[tuple[str, dict[str, Any]] | None] = []
        for _, child_source_id, _, _, _, _ in parsed_children:
            matches = [
                (key, record) for key, record in records.items()
                if record["fields"].get("SourceID") == child_source_id
            ]
            if len(matches) > 1:
                raise CompletionError(f"reviewed split child identity is ambiguous: {child_source_id}")
            matches_by_child.append(matches[0] if matches else None)

        any_missing = any(match is None for match in matches_by_child)
        if any_missing:
            if (
                survivor["fields"].get("SourceID") != source_ref
                or survivor["fields"].get("Lemma") != expected_lemma
                or set(survivor["source_refs"]) != set(expected_refs)
                or len(survivor["source_refs"]) != len(expected_refs)
            ):
                raise CompletionError(
                    f"reviewed split combined identity mismatch: {survivor_id} {source_ref}"
                )
        elif (
            survivor["fields"].get("SourceID") not in {source_ref, survivor_source_id}
            or set(survivor["source_refs"]) != set(survivor_refs)
            or len(survivor["source_refs"]) != len(survivor_refs)
        ):
            raise CompletionError(f"reviewed split survivor identity mismatch: {survivor_id}")

        for (child_spec, child_source_id, _, child_refs, child_cefr, child_overrides), match in zip(
            parsed_children, matches_by_child,
        ):
            if match is None:
                child_key = f"new:{child_source_id}"
                if child_key in records:
                    raise CompletionError(f"reviewed split child key conflicts: {child_key}")
                child = new_record(
                    child_source_id,
                    child_overrides["Lemma"],
                    child_cefr,
                    child_overrides.get("POS", ""),
                    child_overrides.get("Gender", ""),
                )
                child["source_refs"] = list(child_refs)
                child["fields"]["SourceID"] = child_source_id
                child["fields"]["SourceRefs"] = "|".join(child_refs)
                child["fields"].update(child_overrides)
                child["categories"] = list(survivor.get("categories", []))
                child["tags"] = sorted(
                    (set(child["tags"]) | (set(survivor.get("tags", [])) - set(LEVEL_TAG.values())))
                    - set(LEVEL_TAG.values())
                    | {LEVEL_TAG[child_cefr], "goethe::migration::completed"}
                )
                records[child_key] = child
            else:
                _, child = match
                if (
                    child.get("note_id") == survivor_id
                    or set(child["source_refs"]) != set(child_refs)
                    or len(child["source_refs"]) != len(child_refs)
                ):
                    raise CompletionError(f"reviewed split child identity mismatch: {child_source_id}")
                child["source_refs"] = list(child_refs)
                child["fields"]["SourceRefs"] = "|".join(child_refs)
                child["fields"]["CEFR"] = child_cefr
                child["deck"] = LEVEL_DECK[child_cefr]
                child["fields"].update(child_overrides)

        survivor["source_refs"] = list(survivor_refs)
        survivor["fields"]["SourceID"] = survivor_source_id
        survivor["fields"]["SourceRefs"] = "|".join(survivor_refs)
        survivor["fields"].update(survivor_overrides)
    return coverage_aliases


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
            if record_preference(duplicate_key, duplicate) < record_preference(survivor_key, survivor):
                raise CompletionError(
                    f"headword merge violates survivor priority: {duplicate_id} -> {entry['survivor']}"
                )
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


def english_audit_for_build() -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load a complete v4 audit without making an incomplete scaffold authoritative."""
    state: dict[str, Any] = {
        "path": str(english_audit.MANIFEST),
        "schema_version": None,
        "entries": 0,
        "uncovered": scope.EXPECTED_NOTES,
        "ready": False,
        "error": "English audit v4 artifact is missing",
    }
    if not english_audit.MANIFEST.exists():
        return None, state
    entries: Any = {}
    try:
        manifest = english_audit.load_json(english_audit.MANIFEST)
        entries = manifest.get("entries", {})
        state.update({
            "schema_version": manifest.get("schema_version"),
            "entries": len(entries) if isinstance(entries, dict) else 0,
        })
        if manifest.get("schema_version") != 4:
            raise english_audit.AuditError("English audit artifact is not schema v4")
        english_audit.validate_manifest(manifest)
    except (english_audit.AuditError, KeyError, TypeError, ValueError) as exc:
        missing = max(scope.EXPECTED_NOTES - state["entries"], 0)
        unreviewed = sum(
            entry.get("review_status") != "reviewed"
            for entry in entries.values()
            if isinstance(entry, dict)
        ) if isinstance(entries, dict) else 0
        state["uncovered"] = missing + unreviewed
        state["error"] = str(exc)
        return None, state
    state.update({"uncovered": 0, "error": ""})
    return manifest, state


def apply_final_english_audit(
    records: dict[str, dict[str, Any]],
    manifest: dict[str, Any] | None,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Apply v4 last and atomically so a coverage failure leaves no partial audit."""
    if manifest is None:
        return state
    reviewed = copy.deepcopy(records)
    try:
        english_audit.apply_manifest_to_records(reviewed, manifest, strict=True)
    except english_audit.AuditError as exc:
        state["error"] = str(exc)
        return state
    records.clear()
    records.update(reviewed)
    state["ready"] = True
    return state


def build_manifest() -> dict[str, Any]:
    records, _ = load_live()
    live_preimage = build_live_preimage(records)
    redundancy_policy = load_redundancy_policy()
    source_text_overrides = load_source_text_overrides()
    skipped_source_refs = set(redundancy_policy.get("skip_wortgruppen", []))
    merge_wortgruppen = redundancy_policy.get("merge_wortgruppen", {})
    reviewed_note_merges = redundancy_policy.get("reviewed_note_merges", [])
    reviewed_note_splits = redundancy_policy.get("reviewed_note_splits", [])
    preserve_note_ids = set(map(int, redundancy_policy.get("preserve_note_ids", [])))
    source_targets = {str(ref): str(note_id) for ref, note_id in redundancy_policy.get("source_targets", {}).items()}
    main_source_aliases = {
        str(ref): str(target) for ref, target in redundancy_policy.get("main_source_aliases", {}).items()
    }
    main_source_example_overrides = redundancy_policy.get("main_source_example_overrides", {})
    deletions = merge_exact_duplicates(records, preserve_note_ids)
    deletions = apply_reviewed_note_merges(records, deletions, reviewed_note_merges)
    # Stored merge-policy snapshots are an A1/A2 baseline. Apply them before
    # adding B1 provenance so they cannot erase freshly attached B1 refs.
    deletions = apply_headword_policy(records, deletions)
    index = variant_index(records)
    by_source_ref = {
        ref: key for key, record in records.items() for ref in record["source_refs"]
    }
    ambiguous: list[dict[str, Any]] = []
    source_counts = {f"{level}_{kind}": 0 for level in LEVELS for kind in ("MAIN", "WG")}
    main_files = {"A1": gw.SOURCE_A1, "A2": gw.SOURCE_A2, "B1": gw.SOURCE_B1}
    for level, path in main_files.items():
        for row in gw.parse_markdown(path):
            source_counts[f"{level}_MAIN"] += 1
            ref = f"{level}-MAIN-{row['row']:04d}"
            key = configured_main_source_key(
                ref, main_source_aliases, source_targets, records, by_source_ref,
            ) or find_record(
                records, index, row["word"], row["pos"], row["gender"], row["examples"],
            )
            if key is None:
                key = f"new:{ref}"
                records[key] = new_record(ref, row["word"], level, row["pos"], row["gender"])
                index_record(index, key, records[key])
            record = records[key]
            prior_level = record["fields"].get("CEFR") or level
            add_ref(record, ref, level)
            by_source_ref[ref] = key
            if ref in main_source_aliases:
                accepted = split_answers(record["fields"].get("AcceptedAnswersDE", ""))
                for variant in source_variants(row["word"]):
                    if variant not in accepted:
                        accepted.append(variant)
                record["fields"]["AcceptedAnswersDE"] = "|".join(accepted)
                index_record(index, key, record)
            if level == "B1" and LEVEL_RANK[prior_level] < LEVEL_RANK[level]:
                # Lower-level ownership wins. Overlaps gain provenance only;
                # B1 examples and metadata must not expand the A1/A2 note.
                continue
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
            if level == "B1":
                apply_main_grammar(record, row)
            examples = main_source_examples(
                ref, row, source_text_overrides["examples"], main_source_example_overrides,
            )
            for example in examples:
                add_example(record, example)

    for level, path in WG_FILES.items():
        for row in parse_wortgruppen(path):
            source_counts[f"{level}_WG"] += 1
            if row["id"] in skipped_source_refs:
                continue
            lemma = wg_lemma(row)
            merge_spec = merge_wortgruppen.get(row["id"], {})
            key = configured_wortgruppe_key(row["id"], merge_spec, by_source_ref) or find_record(
                records, index, merge_spec.get("target") or row["match"] or lemma,
            )
            if key is None:
                if merge_spec:
                    raise CompletionError(f"redundancy merge target missing: {row['id']} -> {merge_spec.get('target')}")
                key = f"new:{row['id']}"
                records[key] = new_record(row["id"], lemma, level)
                index_record(index, key, records[key])
            record = records[key]
            prior_level = record["fields"].get("CEFR") or level
            add_ref(record, row["id"], level)
            by_source_ref[row["id"]] = key
            if level == "B1" and LEVEL_RANK[prior_level] < LEVEL_RANK[level]:
                continue
            if row["canonical"]:
                fields = record["fields"]
                fields["Lemma"] = row["canonical"]
                fields["POS"] = row["pos"]
                fields["Article"] = row["article"]
                fields["Gender"] = row["gender"]
                fields["NounFormsRaw"] = row["noun_forms"]
                if row["pos"] == "v." and row.get("grammar_note", "").startswith("Conjugation: "):
                    fields["VerbFormsRaw"] = row["grammar_note"].removeprefix("Conjugation: ")
                fields["AcceptedAnswersDE"] = "|".join(wg_answers(row))
                fields["AcceptedArticlesDE"] = "|".join(row["article"].split("/"))
                record["tags"] = sorted(set(record["tags"]) | {"goethe::quality::grammar_audited"})
                reindex_record(index, key, record)
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

    source_coverage_aliases = apply_reviewed_note_splits(records, reviewed_note_splits)
    deletions.extend(merge_exact_duplicates(records, preserve_note_ids))
    allowed_examples = goethe_source_examples.allowed_examples_by_level()
    audit_manifest, audit_state = english_audit_for_build()
    if audit_manifest is not None:
        for entry in audit_manifest["entries"].values():
            for example in entry["desired_examples"]:
                key = goethe_source_examples.sentence_key(example["de"])
                allowed_examples[entry["cefr"]].setdefault(key, example["de"])
    for record in records.values():
        refs = list(dict.fromkeys(record["source_refs"]))
        refs.sort(key=lambda ref: (LEVEL_RANK.get(ref.split("-", 1)[0], 99), ref))
        record["source_refs"] = refs
        record["fields"]["SourceRefs"] = "|".join(refs)
        if refs:
            record["fields"]["SourceID"] = refs[0]
        level = record["fields"]["CEFR"]
        record["examples"] = goethe_source_examples.filter_examples(
            level, record["examples"], allowed_examples,
        )
        goethe_examples.render_fields(record["fields"], record["examples"])
        record["deck"] = LEVEL_DECK[level]
        record["tags"] = sorted(
            (set(record["tags"]) - set(LEVEL_TAG.values()))
            | {LEVEL_TAG[level], "goethe::migration::completed"}
            | {f"goethe::wortgruppe::{category}" for category in record["categories"]}
    )
    apply_translation_cache(records)
    apply_b1_data_overrides(records)
    review_policy.apply_all(records)
    audit_state = apply_final_english_audit(records, audit_manifest, audit_state)
    finalize_template_fields(records)
    manifest = {
        "version": MANIFEST_VERSION, "records": records, "deletions": deletions,
        "live_preimage": live_preimage,
        "source_counts": source_counts, "skipped_source_refs": sorted(skipped_source_refs),
        "source_coverage_aliases": source_coverage_aliases,
        "english_audit": audit_state,
        "ambiguous": ambiguous,
    }
    return manifest


def save_manifest(manifest: dict[str, Any]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_payload_hash(manifest: dict[str, Any]) -> str:
    return canonical_hash(manifest)


def load_manifest_artifact() -> tuple[dict[str, Any], str, str]:
    try:
        raw = MANIFEST.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompletionError(f"cannot load manifest artifact: {exc}") from exc
    if not isinstance(manifest, dict):
        raise CompletionError("manifest root must be an object")
    return manifest, hashlib.sha256(raw).hexdigest(), manifest_payload_hash(manifest)


def require_manifest_file_hash(expected: str) -> None:
    if not MANIFEST.exists() or hash_file(MANIFEST) != expected:
        raise CompletionError("manifest artifact changed during guarded apply")


def valid_apkg(path: Path) -> bool:
    return apkg.valid_apkg(path)


def planned_apply_inventory(manifest: dict[str, Any]) -> tuple[set[int], set[int]]:
    note_ids = {
        int(record["note_id"])
        for record in manifest.get("records", {}).values()
        if not record.get("is_new") and record.get("note_id") is not None
    }
    card_ids = {
        int(card["cardId"])
        for record in manifest.get("records", {}).values()
        if not record.get("is_new")
        for card in record.get("cards", [])
    }
    for deletion in manifest.get("deletions", []):
        if deletion.get("note_id") is None:
            raise CompletionError("deletion has no concrete Anki note ID")
        note_ids.add(int(deletion["note_id"]))
        card_ids.update(int(card["cardId"]) for card in deletion.get("cards", []))
    return note_ids, card_ids


def validated_live_preimage(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("version") != MANIFEST_VERSION:
        raise CompletionError(
            f"completion manifest v{MANIFEST_VERSION} required; rebuild before apply"
        )
    preimage = manifest.get("live_preimage")
    if not isinstance(preimage, dict) or preimage.get("schema_version") != PREIMAGE_SCHEMA_VERSION:
        raise CompletionError("completion manifest live preimage is missing or unsupported")
    notes = preimage.get("notes")
    if not isinstance(notes, dict):
        raise CompletionError("completion manifest live preimage notes must be an object")

    card_ids: set[int] = set()
    normalized_notes: dict[str, dict[str, Any]] = {}
    for key, entry in notes.items():
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise CompletionError(f"invalid live preimage signature: {key}")
        try:
            note_id = int(key)
        except (TypeError, ValueError) as exc:
            raise CompletionError(f"invalid live preimage note ID: {key!r}") from exc
        cards = entry.get("cards")
        if not isinstance(cards, list):
            raise CompletionError(f"invalid live preimage cards: note {note_id}")
        projected = sorted(
            (canonical_card_state(card) for card in cards), key=lambda card: card["cardId"],
        )
        if sorted(card["ord"] for card in projected) != [0, 1]:
            raise CompletionError(f"invalid live preimage card ords: note {note_id}")
        if any(card["note"] != note_id for card in projected):
            raise CompletionError(f"invalid live preimage card association: note {note_id}")
        current_ids = {card["cardId"] for card in projected}
        if card_ids & current_ids:
            raise CompletionError(f"duplicate live preimage card IDs: note {note_id}")
        card_ids.update(current_ids)
        normalized_notes[str(note_id)] = {
            "sha256": str(entry["sha256"]),
            "cards": projected,
        }

    planned_notes, planned_cards = planned_apply_inventory(manifest)
    preimage_notes = {int(note_id) for note_id in normalized_notes}
    if preimage_notes != planned_notes or card_ids != planned_cards:
        raise CompletionError("live preimage inventory differs from the planned apply inventory")

    planned_card_states: dict[int, dict[str, Any]] = {}
    planned_records = [
        record for record in manifest.get("records", {}).values() if not record.get("is_new")
    ]
    planned_records.extend(manifest.get("deletions", []))
    for record in planned_records:
        for card in record.get("cards", []):
            state = canonical_card_state(card)
            if state["cardId"] in planned_card_states:
                raise CompletionError(f"planned card appears more than once: {state['cardId']}")
            planned_card_states[state["cardId"]] = state
    preimage_card_states = {
        card["cardId"]: card for entry in normalized_notes.values() for card in entry["cards"]
    }
    if preimage_card_states != planned_card_states:
        raise CompletionError("live preimage card state differs from manifest records")
    return {"schema_version": PREIMAGE_SCHEMA_VERSION, "notes": normalized_notes}


def expected_apply_inventory(manifest: dict[str, Any]) -> tuple[set[int], set[int]]:
    """Return exact pre-apply IDs from the immutable build-time preimage."""
    if manifest.get("version") != MANIFEST_VERSION:
        # Keep this small diagnostic helper useful for legacy unit fixtures;
        # guarded apply/export paths always call validated_live_preimage first.
        return planned_apply_inventory(manifest)
    notes = validated_live_preimage(manifest)["notes"]
    return (
        {int(note_id) for note_id in notes},
        {card["cardId"] for entry in notes.values() for card in entry["cards"]},
    )


def verify_apply_inventory(
    manifest: dict[str, Any],
    live: dict[str, dict[str, Any]],
    cards: dict[int, list[dict[str, Any]]] | None = None,
) -> None:
    """Fail closed unless live notes exactly match the build-time preimage."""
    expected = validated_live_preimage(manifest)["notes"]
    actual = build_live_preimage(live)["notes"]
    if set(actual) != set(expected):
        raise CompletionError(
            f"live note inventory changed since build: expected {len(expected)}, got {len(actual)}"
        )
    changed = [note_id for note_id in expected if actual[note_id] != expected[note_id]]
    if changed:
        raise CompletionError(f"live note/card preimage changed since build: {changed[:5]}")
    if cards is not None:
        mapped = {
            int(card["cardId"]): int(note_id)
            for note_id, note_cards in cards.items()
            for card in note_cards
        }
        expected_mapping = {
            card["cardId"]: int(note_id)
            for note_id, entry in expected.items()
            for card in entry["cards"]
        }
        if mapped != expected_mapping:
            raise CompletionError("load_live card map differs from the build-time preimage")


def inspect_apkg_preimage(path: Path, manifest: dict[str, Any]) -> None:
    """Read the exported SQLite collection and verify inventory/scheduling."""
    preimage = validated_live_preimage(manifest)["notes"]
    expected_note_ids = {int(note_id) for note_id in preimage}
    expected_cards = {
        card["cardId"]: card for entry in preimage.values() for card in entry["cards"]
    }
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            member, payload = apkg.read_collection(path)
            extracted = Path(temp_dir) / member
            extracted.write_bytes(payload)
            database = sqlite3.connect(
                f"file:{extracted.as_posix()}?mode=ro&immutable=1", uri=True,
            )
            try:
                database.execute("PRAGMA query_only = ON")
                if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise CompletionError("APKG collection SQLite integrity check failed")
                note_ids = {int(row[0]) for row in database.execute("SELECT id FROM notes")}
                if note_ids != expected_note_ids:
                    raise CompletionError(
                        f"APKG note inventory differs: expected {len(expected_note_ids)}, got {len(note_ids)}"
                    )
                deck_row = database.execute("SELECT decks FROM col").fetchone()
                if deck_row is None:
                    raise CompletionError("APKG collection has no deck registry")
                decks = json.loads(deck_row[0])
                rows = database.execute(
                    "SELECT id, nid, did, ord, factor, ivl, type, queue, due, "
                    "reps, lapses, left, flags, mod FROM cards"
                ).fetchall()
                actual_cards: dict[int, dict[str, Any]] = {}
                for row in rows:
                    deck = decks.get(str(row[2]))
                    if not isinstance(deck, dict) or not deck.get("name"):
                        raise CompletionError(f"APKG card has an unknown deck ID: {row[0]}")
                    card = canonical_card_state({
                        "cardId": row[0], "note": row[1], "deckName": deck["name"],
                        "ord": row[3], "factor": row[4], "interval": row[5],
                        "type": row[6], "queue": row[7], "due": row[8],
                        "reps": row[9], "lapses": row[10], "left": row[11],
                        "flags": row[12], "mod": row[13],
                    })
                    if card["cardId"] in actual_cards:
                        raise CompletionError(f"APKG contains a duplicate card ID: {card['cardId']}")
                    actual_cards[card["cardId"]] = card
                if set(actual_cards) != set(expected_cards):
                    raise CompletionError(
                        f"APKG card inventory differs: expected {len(expected_cards)}, got {len(actual_cards)}"
                    )
                changed = [
                    card_id for card_id in expected_cards
                    if actual_cards[card_id] != expected_cards[card_id]
                ]
                if changed:
                    raise CompletionError(f"APKG card scheduling differs from live preimage: {changed[:5]}")
            finally:
                database.close()
    except CompletionError:
        raise
    except (OSError, BadZipFile, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise CompletionError(f"cannot inspect APKG collection: {exc}") from exc


def export_apply_backup(
    manifest: dict[str, Any], *,
    manifest_file_sha256: str | None = None,
    manifest_payload_sha256: str | None = None,
) -> dict[str, Any]:
    """Create and validate a scheduled APKG immediately before destructive apply."""
    if manifest_file_sha256 is None:
        manifest_file_sha256 = hash_file(MANIFEST)
    if manifest_payload_sha256 is None:
        manifest_payload_sha256 = manifest_payload_hash(manifest)
    if manifest_payload_hash(manifest) != manifest_payload_sha256:
        raise CompletionError("manifest payload changed before backup")
    require_manifest_file_hash(manifest_file_sha256)
    live, cards = load_live()
    verify_apply_inventory(manifest, live, cards)
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"_{time.time_ns() % 1_000_000_000:09d}"
    backup = STATE / f"Goethe_Institute_pre_completion_{stamp}.apkg"
    if backup.exists():
        raise CompletionError(f"backup destination already exists: {backup}")
    export_error: gw.MigrationError | None = None
    try:
        exported = gw.anki(
            "exportPackage", deck=PARENT_DECK, path=backup.resolve().as_posix(), includeSched=True,
        )
    except gw.MigrationError as exc:
        if "timed out" not in str(exc).casefold() and "timeout" not in str(exc).casefold():
            raise
        export_error = exc
        exported = False
    # AnkiConnect can return before the package has been flushed to disk.
    if exported or export_error is not None:
        exported = apkg.wait_for_valid_apkg(backup)
    if not exported or not valid_apkg(backup):
        raise CompletionError("scheduled APKG backup failed validation")
    inspect_apkg_preimage(backup, manifest)
    require_manifest_file_hash(manifest_file_sha256)
    if manifest_payload_hash(manifest) != manifest_payload_sha256:
        raise CompletionError("manifest payload changed during backup")
    after_backup, after_cards = load_live()
    verify_apply_inventory(manifest, after_backup, after_cards)
    expected_notes, expected_cards = expected_apply_inventory(manifest)
    snapshot = {
        "schema_version": 1,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backup": str(backup.resolve()),
        "backup_sha256": hash_file(backup),
        "note_ids": sorted(expected_notes),
        "card_ids": sorted(expected_cards),
        "manifest_file_sha256": manifest_file_sha256,
        "manifest_payload_sha256": manifest_payload_sha256,
    }
    snapshot_path = STATE / f"snapshot_{backup.stem}.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    snapshot["snapshot"] = str(snapshot_path.resolve())
    return snapshot


def apply_translation_cache(records: dict[str, dict[str, Any]]) -> None:
    if not TRANSLATIONS.exists():
        return
    cache = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
    for record in records.values():
        changed = False
        fields = record["fields"]
        if not fields.get("MeaningEN") and fields.get("Lemma") in cache:
            fields["MeaningEN"] = cache[fields["Lemma"]]
            changed = True
        for example in record["examples"]:
            if not example["en"] and example["de"] in cache:
                example["en"] = cache[example["de"]]
                changed = True
        if changed:
            record["translated"] = True
            record["tags"] = sorted(set(record["tags"]) | {QUALITY_TRANSLATION, "goethe::quality::review_needed"})


def apply_b1_english_overrides(records: dict[str, dict[str, Any]]) -> None:
    """Retained for forensic compatibility; never part of the build path.

    B1 legacy overrides are triage hints only. The v4 audit is the sole
    English authority, and ``build_manifest`` deliberately does not call this
    helper.
    """
    if not B1_ENGLISH_OVERRIDES.exists():
        return
    overrides = json.loads(B1_ENGLISH_OVERRIDES.read_text(encoding="utf-8"))
    by_ref = {ref: record for record in records.values() for ref in record["source_refs"]}
    for source_id, override in overrides.items():
        if source_id not in by_ref:
            raise CompletionError(f"B1 English override source missing: {source_id}")
        record = by_ref[source_id]
        if record["fields"].get("CEFR") != "B1":
            continue
        if "meaning_en" in override:
            record["fields"]["MeaningEN"] = clean(override["meaning_en"])
        examples = override.get("examples", {})
        available = {item["de"]: item for item in record["examples"]}
        unknown = set(examples) - set(available)
        if unknown:
            raise CompletionError(f"B1 English override examples missing for {source_id}: {sorted(unknown)!r}")
        for german, english in examples.items():
            available[german]["en"] = clean(english)
        record["tags"] = sorted(
            (set(record["tags"]) - {QUALITY_TRANSLATION}) | {"goethe::quality::english_audited"}
        )


def apply_b1_data_overrides(records: dict[str, dict[str, Any]]) -> None:
    if not B1_DATA_OVERRIDES.exists():
        return
    overrides = json.loads(B1_DATA_OVERRIDES.read_text(encoding="utf-8"))
    by_ref = {ref: record for record in records.values() for ref in record["source_refs"]}
    allowed = {"POS", "Article", "Gender", "NounFormsRaw", "VerbFormsRaw", "RegionalVariants"}
    for source_id, fields in overrides.items():
        if source_id not in by_ref:
            raise CompletionError(f"B1 data override source missing: {source_id}")
        unknown = set(fields) - allowed
        if unknown:
            raise CompletionError(f"unsupported B1 data override fields for {source_id}: {sorted(unknown)!r}")
        record = by_ref[source_id]
        if record["fields"].get("CEFR") != "B1":
            continue
        record["fields"].update({name: clean(value) for name, value in fields.items()})
        if "Article" in fields:
            record["fields"]["AcceptedArticlesDE"] = "|".join(fields["Article"].split("/"))
        record["tags"] = sorted(set(record["tags"]) | {"goethe::quality::grammar_audited"})


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
        "english_audit": manifest["english_audit"],
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
    finalize_template_fields(manifest["records"])
    save_manifest(manifest)
    print(f"translations={len(cache)} manifest updated")


def render_examples(record: dict[str, Any]) -> None:
    goethe_examples.render_fields(record["fields"], record["examples"])
    record["fields"]["ExampleTargetSpansJSON"] = target_highlights.build_target_spans(record["fields"])


def finalize_template_fields(records: dict[str, dict[str, Any]]) -> None:
    """Derive reviewed production fields and deterministic example spans.

    This is the final build step, after source and translation overrides have
    settled the examples and meanings.  The policy module is restricted to the
    four appended template fields; spans are rebuilt after it so their offsets
    always match the final rendered examples.
    """
    for record in records.values():
        render_examples(record)
    try:
        production_policy.apply_policy(records, strict=True)
    except production_policy.PolicyError as exc:
        raise CompletionError(f"production template policy failed: {exc}") from exc
    for record in records.values():
        record["fields"]["ExampleTargetSpansJSON"] = target_highlights.build_target_spans(record["fields"])


def validate_manifest(
    manifest: dict[str, Any], *, strict_corpus: bool = True,
) -> dict[str, int]:
    if strict_corpus:
        validated_live_preimage(manifest)
    records = list(manifest["records"].values())
    audit_state = manifest.get("english_audit", {})
    audit_ready = isinstance(audit_state, dict) and audit_state.get("ready") is True
    for record in records:
        rendered = copy.deepcopy(record)
        render_examples(rendered)
        if strict_corpus and audit_ready and rendered["fields"] != record["fields"]:
            raise CompletionError(
                f"manifest rendered fields are stale: {record.get('note_id')}"
            )
        fields = record["fields"] if strict_corpus else rendered["fields"]
        if not fields.get("Lemma") or not fields.get("MeaningEN") or not fields.get("SourceRefs"):
            raise CompletionError(f"required fields missing: {record.get('note_id')} {fields.get('Lemma')}")
        validate_noun_fields(fields)
        if fields["CEFR"] not in LEVEL_DECK or record["deck"] != LEVEL_DECK[fields["CEFR"]]:
            raise CompletionError(f"level/deck mismatch: {fields['Lemma']}")
        if any(not item["en"] for item in record["examples"]):
            raise CompletionError(f"untranslated example: {fields['Lemma']}")
        if fields.get("ProductionEnabled", "") not in {"", "1"}:
            raise CompletionError(f"invalid production flag: {fields['Lemma']}")
        if fields.get("ProductionEnabled") == "1" and not fields.get("AcceptedFullAnswersDE", "").strip():
            raise CompletionError(f"enabled production answer missing: {fields['Lemma']}")
        try:
            target_highlights.parse_target_spans(
                fields.get("ExampleTargetSpansJSON", ""),
                target_highlights.example_texts(fields),
            )
        except target_highlights.HighlightError as exc:
            raise CompletionError(f"invalid target spans: {fields['Lemma']}: {exc}") from exc
    refs = {ref for record in records for ref in record["source_refs"]}
    coverage_aliases = manifest.get("source_coverage_aliases", {})
    if not isinstance(coverage_aliases, dict):
        raise CompletionError("invalid source coverage aliases")
    for derived_ref, physical_ref in coverage_aliases.items():
        if (
            not isinstance(derived_ref, str)
            or derived_ref not in refs
            or not isinstance(physical_ref, str)
            or not PHYSICAL_SOURCE_REF.fullmatch(physical_ref)
        ):
            raise CompletionError(f"invalid source coverage alias: {derived_ref!r}")
    skipped_source_refs = set(manifest.get("skipped_source_refs", []))
    expected = sum(manifest["source_counts"].values()) - len(skipped_source_refs)
    source_refs = set()
    for ref in refs:
        if not SOURCE_REF_PREFIX.match(ref):
            continue
        physical_ref = coverage_aliases.get(ref, ref)
        if not PHYSICAL_SOURCE_REF.fullmatch(physical_ref):
            raise CompletionError(f"unmapped derived source ref: {ref}")
        source_refs.add(physical_ref)
    if source_refs & skipped_source_refs:
        raise CompletionError("skipped source ref was retained in the manifest")
    if len(source_refs) != expected:
        raise CompletionError(f"source coverage mismatch: {len(source_refs)} != {expected}")
    deleted = {item["note_id"] for item in manifest["deletions"]}
    survivors = {record["note_id"] for record in records if not record["is_new"]}
    if deleted & survivors:
        raise CompletionError("deleted note also survives")
    summary = {
        "records": len(records), "new": sum(record["is_new"] for record in records),
        "delete": len(deleted), "source_refs": len(source_refs),
        "a1": sum(record["fields"]["CEFR"] == "A1" for record in records),
        "a2": sum(record["fields"]["CEFR"] == "A2" for record in records),
        "b1": sum(record["fields"]["CEFR"] == "B1" for record in records),
    }
    if strict_corpus:
        actual_by_level = {level: summary[level.casefold()] for level in LEVELS}
        if len(records) != scope.EXPECTED_NOTES or actual_by_level != scope.EXPECTED_NOTES_BY_LEVEL:
            raise CompletionError(
                f"canonical corpus mismatch: {actual_by_level} != {scope.EXPECTED_NOTES_BY_LEVEL}"
            )
        bad_card_shape = [
            record.get("note_id") for record in records
            if not record["is_new"] and len(record.get("cards", [])) != 2
        ]
        if bad_card_shape:
            raise CompletionError(f"expected two cards for existing notes: {bad_card_shape[:5]}")
        if manifest.get("ambiguous"):
            raise CompletionError("ambiguous source routing remains")
        audit_state = manifest.get("english_audit", {})
        if not (
            isinstance(audit_state, dict)
            and audit_state.get("schema_version") == 4
            and audit_state.get("ready") is True
            and audit_state.get("entries") == scope.EXPECTED_NOTES
            and audit_state.get("uncovered") == 0
        ):
            reason = audit_state.get("error", "missing audit status") if isinstance(audit_state, dict) else "missing audit status"
            raise CompletionError(f"English audit v4 is not ready: {reason}")
    return summary


def validate_noun_fields(fields: dict[str, str]) -> None:
    """Enforce the learner-facing article invariant for every noun record."""
    if fields.get("POS", "").strip() != "n.":
        return
    try:
        is_exception = noun_policy.validate_noun_article(
            source_id=fields.get("SourceID", ""),
            lemma=fields.get("Lemma", ""),
            pos=fields.get("POS", ""),
            article=fields.get("Article", ""),
            gender=fields.get("Gender", ""),
            require_complete_mapping=False,
        )
    except noun_policy.NounPolicyError as exc:
        raise CompletionError(f"noun article policy failed for {fields.get('Lemma')!r}: {exc}") from exc

    accepted_articles = split_answers(fields.get("AcceptedArticlesDE", ""))
    full_answers = split_answers(fields.get("AcceptedFullAnswersDE", ""))
    if is_exception:
        if accepted_articles or any(re.match(r"^(?:der|die|das)\s+", value, flags=re.I) for value in full_answers):
            raise CompletionError(
                f"articleless exception has article-bearing answer metadata: {fields.get('Lemma')}"
            )
        return
    if not accepted_articles:
        raise CompletionError(f"noun AcceptedArticlesDE missing: {fields.get('Lemma')}")
    if fields.get("ProductionEnabled") == "1" and (not full_answers or any(
        not re.match(r"^(?:der|die|das)\s+\S", value, flags=re.I) for value in full_answers
    )):
        raise CompletionError(f"noun AcceptedFullAnswersDE missing article: {fields.get('Lemma')}")


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


def manifest_existing_records(manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(record["note_id"]): record
        for record in manifest["records"].values()
        if not record.get("is_new")
    }


def expected_post_cards(
    record: dict[str, Any], preimage_entry: dict[str, Any], *, ignore_mod: bool = False,
) -> list[dict[str, Any]]:
    cards = copy.deepcopy(preimage_entry["cards"])
    for card in cards:
        card["deckName"] = record["deck"]
        if ignore_mod:
            card.pop("mod", None)
    return sorted(cards, key=lambda card: card["cardId"])


def verify_desired_record(
    record: dict[str, Any], live_record: dict[str, Any], preimage_entry: dict[str, Any] | None,
) -> None:
    note_id = int(record["note_id"])
    if live_record.get("model") != MODEL:
        raise CompletionError(f"post-apply model mismatch: {note_id}")
    if live_record.get("fields") != record["fields"]:
        raise CompletionError(f"post-apply fields mismatch: {note_id}")
    if sorted(live_record.get("tags", [])) != sorted(record.get("tags", [])):
        raise CompletionError(f"post-apply tags mismatch: {note_id}")
    actual_cards = sorted(
        (canonical_card_state(card) for card in live_record.get("cards", [])),
        key=lambda card: card["cardId"],
    )
    if preimage_entry is None:
        if sorted(card["ord"] for card in actual_cards) != [0, 1] or any(
            card["deckName"] != record["deck"] or card["note"] != note_id
            for card in actual_cards
        ):
            raise CompletionError(f"post-apply new card state mismatch: {note_id}")
        return
    expected_cards = expected_post_cards(record, preimage_entry, ignore_mod=True)
    actual_without_mod = [
        {key: value for key, value in card.items() if key != "mod"}
        for card in actual_cards
    ]
    expected_without_mod = [
        {key: value for key, value in card.items() if key != "mod"}
        for card in expected_cards
    ]
    if actual_without_mod != expected_without_mod:
        raise CompletionError(f"post-apply card state mismatch: {note_id}")


def verify_pre_delete_state(
    manifest: dict[str, Any], live: dict[str, dict[str, Any]],
    new_ids: dict[str, int], *, skip_new: bool,
) -> None:
    preimage = validated_live_preimage(manifest)["notes"]
    existing = manifest_existing_records(manifest)
    deletion_ids = {int(item["note_id"]) for item in manifest["deletions"]}
    expected_ids = {int(note_id) for note_id in preimage} | set(new_ids.values())
    if {int(note_id) for note_id in live} != expected_ids:
        raise CompletionError("live inventory changed before destructive deletion")
    actual_preimage = build_live_preimage(live)["notes"]
    for note_id in deletion_ids:
        key = str(note_id)
        if actual_preimage.get(key) != preimage.get(key):
            raise CompletionError(f"deletion target changed before delete: {note_id}")
    for note_id, record in existing.items():
        verify_desired_record(record, live[str(note_id)], preimage[str(note_id)])
    for key, note_id in new_ids.items():
        record = manifest["records"][key]
        record = copy.deepcopy(record)
        record["note_id"] = note_id
        verify_desired_record(record, live[str(note_id)], None)
    if skip_new and new_ids:
        raise CompletionError("internal error: skipped new records have assigned IDs")


def verify_post_apply_state(
    manifest: dict[str, Any], live: dict[str, dict[str, Any]],
    new_ids: dict[str, int], *, skip_new: bool,
) -> dict[str, Any]:
    preimage = validated_live_preimage(manifest)["notes"]
    existing = manifest_existing_records(manifest)
    deletion_ids = {int(item["note_id"]) for item in manifest["deletions"]}
    expected_ids = set(existing) | set(new_ids.values())
    actual_ids = {int(note_id) for note_id in live}
    if deletion_ids & actual_ids:
        raise CompletionError(f"deleted notes still present: {sorted(deletion_ids & actual_ids)[:5]}")
    if actual_ids != expected_ids:
        raise CompletionError(
            f"post-apply note inventory differs: expected {len(expected_ids)}, got {len(actual_ids)}"
        )
    for note_id, record in existing.items():
        verify_desired_record(record, live[str(note_id)], preimage[str(note_id)])
    for key, note_id in new_ids.items():
        record = copy.deepcopy(manifest["records"][key])
        record["note_id"] = note_id
        verify_desired_record(record, live[str(note_id)], None)
    if not skip_new:
        levels = {
            level: sum(
                live_record["fields"].get("CEFR") == level for live_record in live.values()
            )
            for level in LEVELS
        }
        if len(live) != scope.EXPECTED_NOTES or levels != scope.EXPECTED_NOTES_BY_LEVEL:
            raise CompletionError(f"post-apply canonical corpus mismatch: {levels}")
    return {
        "notes": len(live),
        "cards": sum(len(record.get("cards", [])) for record in live.values()),
        "deleted_absent": True,
        "skipped_new": skip_new,
    }


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != CONFIRMATION:
        raise CompletionError(f"confirmation must equal {CONFIRMATION}")
    manifest, manifest_file_sha256, manifest_payload_sha256 = load_manifest_artifact()
    summary = validate_manifest(manifest)
    if gw.anki("version") != 6:
        raise CompletionError("unexpected AnkiConnect API version")
    actual_fields = gw.anki("modelFieldNames", modelName=MODEL)
    if actual_fields != gw.FIELDS:
        raise CompletionError("target model schema not upgraded")
    backup = export_apply_backup(
        manifest,
        manifest_file_sha256=manifest_file_sha256,
        manifest_payload_sha256=manifest_payload_sha256,
    )
    require_manifest_file_hash(manifest_file_sha256)
    before_update, before_update_cards = load_live()
    verify_apply_inventory(manifest, before_update, before_update_cards)
    existing_records = [record for record in manifest["records"].values() if not record["is_new"]]
    actions: list[dict[str, Any]] = []
    for record in existing_records:
        note_id = int(record["note_id"])
        actions.append({"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": record["fields"]}}})
        current_tags = set(before_update[str(note_id)].get("tags", []))
        desired_tags = set(record.get("tags", []))
        remove_tags = sorted(current_tags - desired_tags)
        add_tags = sorted(desired_tags - current_tags)
        if remove_tags:
            actions.append({"action": "removeTags", "params": {"notes": [note_id], "tags": " ".join(remove_tags)}})
        if add_tags:
            actions.append({"action": "addTags", "params": {"notes": [note_id], "tags": " ".join(add_tags)}})
    if actions:
        anki_multi(actions)
    for record in existing_records:
        card_ids = [card["cardId"] for card in record["cards"]]
        if card_ids and any(card["deckName"] != record["deck"] for card in before_update[str(record["note_id"])]["cards"]):
            gw.anki("changeDeck", cards=card_ids, deck=record["deck"])
    new_ids: dict[str, int] = {}
    for key, record in manifest["records"].items():
        if not record["is_new"] or args.skip_new:
            continue
        note_id = gw.anki("addNote", note={
            "deckName": record["deck"], "modelName": MODEL,
            "fields": record["fields"], "tags": record["tags"],
            "options": {"allowDuplicate": True},
        })
        if not note_id:
            raise CompletionError(f"failed to add note: {record['fields']['Lemma']}")
        new_ids[key] = int(note_id)
    require_manifest_file_hash(manifest_file_sha256)
    before_delete, _ = load_live()
    verify_pre_delete_state(manifest, before_delete, new_ids, skip_new=bool(args.skip_new))
    delete_ids = [item["note_id"] for item in manifest["deletions"]]
    if delete_ids:
        result = gw.anki("deleteNotes", notes=delete_ids)
        if result is not None:
            raise CompletionError(f"unexpected deleteNotes response: {result!r}")
        deleted_count = len(delete_ids)
    else:
        deleted_count = 0
    after, _ = load_live()
    post = verify_post_apply_state(manifest, after, new_ids, skip_new=bool(args.skip_new))
    post_preimage = build_live_preimage(after)
    result_artifact = {
        "schema_version": 1,
        "manifest_file_sha256": manifest_file_sha256,
        "manifest_payload_sha256": manifest_payload_sha256,
        "backup": backup["backup"],
        "backup_sha256": backup["backup_sha256"],
        "new_ids": new_ids,
        "deleted_ids": delete_ids,
        "post": post,
        "post_preimage": post_preimage,
    }
    STATE.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result_artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        **summary,
        "new_ids": len(new_ids),
        "deleted_ids": deleted_count,
        "backup": backup["backup"],
        "backup_sha256": backup["backup_sha256"],
        "result": str(RESULT),
        "post": post,
        "skipped_new": bool(args.skip_new),
    }, indent=2))


def command_verify(_: argparse.Namespace) -> None:
    manifest, manifest_file_sha256, manifest_payload_sha256 = load_manifest_artifact()
    validate_manifest(manifest)
    if not RESULT.exists():
        raise CompletionError("apply result missing; run guarded apply first")
    try:
        result = json.loads(RESULT.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompletionError(f"cannot load apply result: {exc}") from exc
    if result.get("manifest_file_sha256") != manifest_file_sha256:
        raise CompletionError("manifest file hash differs from the applied result")
    if result.get("manifest_payload_sha256") != manifest_payload_sha256:
        raise CompletionError("manifest payload hash differs from the applied result")
    new_ids = {str(key): int(value) for key, value in result.get("new_ids", {}).items()}
    skip_new = bool(result.get("post", {}).get("skipped_new"))
    live, _ = load_live()
    post = verify_post_apply_state(manifest, live, new_ids, skip_new=skip_new)
    expected_post = result.get("post_preimage", {}).get("notes")
    actual_post = build_live_preimage(live)["notes"]
    if expected_post != actual_post:
        raise CompletionError("live post-apply preimage differs from the recorded result")
    print(json.dumps({"verified": True, "post": post, "result": str(RESULT)}, indent=2))


def command_apply_b1(_: argparse.Namespace) -> None:
    raise CompletionError("apply-b1 is deprecated and non-mutating; use the guarded apply command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build").set_defaults(func=command_build)
    sub.add_parser("translate").set_defaults(func=command_translate)
    sub.add_parser("dry-run").set_defaults(func=command_dry_run)
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.add_argument("--skip-new", action="store_true")
    apply.set_defaults(func=command_apply)
    sub.add_parser("verify").set_defaults(func=command_verify)
    apply_b1 = sub.add_parser("apply-b1")
    apply_b1.set_defaults(func=command_apply_b1)
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
