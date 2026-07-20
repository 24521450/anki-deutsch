"""Export the live Goethe Werkstatt notes as deterministic agent-readable JSONL."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import goethe_werkstatt_migrate as gw
import goethe_examples
import goethe_english_audit as english_audit
import goethe_scope as scope
import goethe_target_highlights

ROOT = gw.ROOT
OUTPUT = ROOT / "data" / "build" / "anki_notes.jsonl"
EXPECTED_NOTES = scope.EXPECTED_NOTES
EXPECTED_CARDS = scope.EXPECTED_CARDS


class ExportError(RuntimeError):
    pass


def load_audit_entries() -> dict[str, dict[str, Any]]:
    """Load the same complete v4 payload that was allowed to reach Anki."""
    try:
        manifest = english_audit.load_json(english_audit.MANIFEST)
        english_audit.validate_manifest(manifest)
    except (OSError, ValueError, english_audit.AuditError) as exc:
        raise ExportError(f"English audit v4 is not ready: {exc}") from exc
    entries = manifest.get("entries")
    if not isinstance(entries, dict):
        raise ExportError("English audit v4 entries are missing")
    return entries


def split_pipe(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def field(note: dict[str, Any], name: str) -> str:
    return note.get("fields", {}).get(name, {}).get("value", "")


def stable_guid(fields: dict[str, str]) -> str:
    try:
        return scope.stable_guid(fields)
    except scope.ScopeError as exc:
        raise ExportError(str(exc)) from exc


def overflow_examples(value: str) -> list[dict[str, str]]:
    return goethe_examples.parse_overflow(value)


def target_spans(value: str, fields: dict[str, str] | None = None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExportError("ExampleTargetSpansJSON is not valid JSON") from exc
    if not isinstance(parsed, list):
        raise ExportError("ExampleTargetSpansJSON must contain a list")
    if fields is not None:
        try:
            texts = goethe_target_highlights.example_texts(fields)
            goethe_target_highlights.parse_target_spans(value, texts)
        except goethe_target_highlights.HighlightError as exc:
            raise ExportError(f"invalid ExampleTargetSpansJSON: {exc}") from exc
    return parsed


def serialize_note(note: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    fields = {name: field(note, name) for name in gw.FIELDS}
    note_id = int(note["noteId"])
    level = fields["CEFR"]
    if note.get("modelName") != gw.MODEL or level not in scope.LEVEL_DECK:
        raise ExportError(f"unexpected note type or CEFR: {note_id}")
    source_refs = split_pipe(fields["SourceRefs"])
    if (
        not fields["SourceID"]
        or not source_refs
        or len(source_refs) != len(set(source_refs))
        or fields["SourceID"] not in source_refs
    ):
        raise ExportError(f"invalid source identity: {note_id}")
    if fields["SourceID"].split("-", 1)[0] != level:
        raise ExportError(f"source identity/CEFR mismatch: {note_id}")
    if len(cards) != 2:
        raise ExportError(f"expected two cards: {note_id}")
    expected_deck = scope.LEVEL_DECK[level]
    if (
        {int(card.get("ord", -1)) for card in cards} != {0, 1}
        or any(int(card.get("note", -1)) != note_id for card in cards)
        or any(card.get("deckName") != expected_deck for card in cards)
    ):
        raise ExportError(f"invalid card identity or deck: {note_id}")
    examples = []
    for index in range(1, 5):
        german = fields[f"Example{index}DE"]
        if german:
            examples.append({
                "de": german,
                "en": fields[f"Example{index}EN"],
                "audio": fields[f"Example{index}Audio"],
            })
    examples.extend(overflow_examples(fields["MoreExamplesHTML"]))
    cards = sorted(cards, key=lambda card: int(card["ord"]))
    return {
        "guid": stable_guid(fields),
        "anki_note_id": note_id,
        "notetype": note["modelName"],
        "deck": cards[0]["deckName"],
        "lemma": fields["Lemma"],
        "meaning_en": fields["MeaningEN"],
        "cefr": fields["CEFR"],
        "pos": fields["POS"],
        "article": fields["Article"],
        "gender": fields["Gender"],
        "noun_forms_raw": fields["NounFormsRaw"],
        "verb_forms_raw": fields["VerbFormsRaw"],
        "form_or_variant_note": fields["FormOrVariantNote"],
        "usage_note_en": fields["UsageNoteEN"],
        "regional_variants": fields["RegionalVariants"],
        "accepted_answers_de": split_pipe(fields["AcceptedAnswersDE"]),
        "accepted_articles_de": split_pipe(fields["AcceptedArticlesDE"]),
        "accepted_full_answers_de": split_pipe(fields["AcceptedFullAnswersDE"]),
        "production_enabled": fields["ProductionEnabled"] == "1",
        "production_hint": fields["ProductionHint"],
        "example_target_spans": target_spans(fields["ExampleTargetSpansJSON"], fields),
        "word_audio": fields["WordAudio"],
        "examples": examples,
        "more_examples_html": fields["MoreExamplesHTML"],
        "source_id": fields["SourceID"],
        "source_refs": source_refs,
        "original_order": fields["OriginalOrder"],
        "source_note_raw": fields["SourceNoteRaw"],
        "tags": sorted(note.get("tags", [])),
        "card_ids": [int(card["cardId"]) for card in cards],
    }


def order_key(row: dict[str, Any]) -> tuple[Any, ...]:
    level = scope.LEVEL_RANK.get(row["cefr"], len(scope.LEVELS))
    raw = str(row["original_order"] or "")
    numeric = int(raw) if raw.isdigit() else 10**9
    return (level, numeric, raw.casefold(), row["lemma"].casefold(), row["anki_note_id"])


def resolve_audit_entry(row: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Resolve by canonical ID or durable GUID, with conflict guards."""
    source_id = row["source_id"]
    direct = entries.get(source_id)
    matches = [
        entry for entry in entries.values()
        if entry.get("stable_guid") == row.get("guid")
    ]
    if len(matches) > 1:
        raise ExportError(f"ambiguous stable GUID: {row['anki_note_id']}")
    if direct is not None and matches and direct is not matches[0]:
        raise ExportError(f"source ID/stable GUID conflict: {row['anki_note_id']}")
    entry = direct or (matches[0] if matches else None)
    if entry is None:
        raise ExportError(f"live note is not covered by the v4 audit: {row['anki_note_id']}")
    expected_refs = entry.get("source_refs")
    actual_refs = row.get("source_refs", [])
    if (
        not isinstance(expected_refs, list)
        or not expected_refs
        or expected_refs[0] != entry.get("source_id")
        or len(expected_refs) != len(set(expected_refs))
        or len(actual_refs) != len(set(actual_refs))
        or set(actual_refs) != set(expected_refs)
        or source_id not in actual_refs
        or row.get("guid") != entry.get("stable_guid")
        or row.get("cefr") != entry.get("cefr")
    ):
        raise ExportError(f"stable identity drift from v4 audit: {row['anki_note_id']}")
    return entry


def validate_audited_content(rows: list[dict[str, Any]], entries: dict[str, dict[str, Any]]) -> None:
    matched: set[str] = set()
    for row in rows:
        entry = resolve_audit_entry(row, entries)
        matched.add(str(entry["source_id"]))
        if row["lemma"] != entry.get("lemma") or row["meaning_en"] != entry.get("desired_meaning_en"):
            raise ExportError(f"English meaning drift from v4 audit: {row['anki_note_id']}")
        expected_examples = [
            {"de": item.get("de", ""), "en": item.get("en", "")}
            for item in entry.get("desired_examples", [])
        ]
        actual_examples = [{"de": item["de"], "en": item["en"]} for item in row["examples"]]
        if actual_examples != expected_examples:
            raise ExportError(f"English examples drift from v4 audit: {row['anki_note_id']}")
        # Emit the canonical v4 identity even if a pre-unification live note
        # still carries a historical merged SourceID or ref order.
        row["source_id"] = entry["source_id"]
        row["source_refs"] = list(entry["source_refs"])
    if matched != set(entries):
        missing = sorted(set(entries) - matched)
        raise ExportError(f"live canonical source IDs do not match the v4 audit: {missing[:5]}")


def load_live_rows() -> list[dict[str, Any]]:
    if gw.anki("version") != 6:
        raise ExportError("unexpected AnkiConnect API version")
    note_ids = gw.anki("findNotes", query=f'note:"{gw.MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(note_ids):
        notes.extend(gw.anki("notesInfo", notes=batch))
    card_ids = [int(card_id) for note in notes for card_id in note.get("cards", [])]
    cards: list[dict[str, Any]] = []
    for batch in gw.chunks(card_ids):
        cards.extend(gw.anki("cardsInfo", cards=batch))
    by_note: dict[int, list[dict[str, Any]]] = {}
    for card in cards:
        by_note.setdefault(int(card["note"]), []).append(card)
    rows = [serialize_note(note, by_note.get(int(note["noteId"]), [])) for note in notes]
    rows.sort(key=order_key)
    validate_audited_content(rows, load_audit_entries())
    validate_rows(rows)
    return rows


def validate_rows(rows: list[dict[str, Any]]) -> None:
    if len(rows) != EXPECTED_NOTES:
        raise ExportError(f"expected {EXPECTED_NOTES} notes, got {len(rows)}")
    if sum(len(row["card_ids"]) for row in rows) != EXPECTED_CARDS:
        raise ExportError("unexpected card count")
    if len({row["anki_note_id"] for row in rows}) != len(rows):
        raise ExportError("duplicate Anki note ID")
    if len({row["guid"] for row in rows}) != len(rows):
        raise ExportError("duplicate stable GUID")
    if len({row["source_id"] for row in rows}) != len(rows):
        raise ExportError("duplicate canonical source ID")
    if len({card_id for row in rows for card_id in row["card_ids"]}) != EXPECTED_CARDS:
        raise ExportError("duplicate card ID")
    actual_by_level = {
        level: sum(row["cefr"] == level for row in rows) for level in scope.LEVELS
    }
    if actual_by_level != scope.EXPECTED_NOTES_BY_LEVEL:
        raise ExportError(
            f"unexpected per-level note counts: {actual_by_level}"
        )
    if rows != sorted(rows, key=order_key):
        raise ExportError("rows are not in canonical A1-A2-B1 order")
    example_counts = {
        level: sum(len(row["examples"]) for row in rows if row["cefr"] == level)
        for level in scope.LEVELS
    }
    if example_counts != scope.EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL:
        raise ExportError(f"unexpected per-level example counts: {example_counts}")
    empty_counts = {
        level: sum(not row["examples"] for row in rows if row["cefr"] == level)
        for level in scope.LEVELS
    }
    if empty_counts != scope.EXPECTED_EMPTY_NOTES_BY_LEVEL:
        raise ExportError(f"unexpected per-level no-example counts: {empty_counts}")
    for row in rows:
        level = row["cefr"]
        if row["notetype"] != gw.MODEL or level not in scope.LEVEL_DECK:
            raise ExportError(f"unexpected note type or CEFR: {row['anki_note_id']}")
        if row["deck"] != scope.LEVEL_DECK[level]:
            raise ExportError(f"level/deck mismatch: {row['anki_note_id']}")
        if not row["lemma"] or not row["meaning_en"] or not row["source_refs"]:
            raise ExportError(f"required field missing: {row['anki_note_id']}")
        if row["source_id"] != row["source_refs"][0] or not row["guid"]:
            raise ExportError(f"invalid stable identity: {row['anki_note_id']}")
        if len(row["card_ids"]) != 2:
            raise ExportError(f"expected two cards: {row['anki_note_id']}")
        tags = set(row["tags"])
        if scope.ENGLISH_AUDITED_TAG not in tags or scope.ENGLISH_REVIEW_TAG in tags:
            raise ExportError(f"English audit v4 is not applied: {row['anki_note_id']}")
        if not row["word_audio"]:
            raise ExportError(f"word audio missing: {row['anki_note_id']}")
        if any(not example["en"] or not example["audio"] for example in row["examples"]):
            raise ExportError(f"example translation or audio missing: {row['anki_note_id']}")
        if level == "B1" and not row["examples"] and not row["source_id"].startswith("B1-WG-"):
            raise ExportError(f"unexpected B1 no-example note: {row['anki_note_id']}")


def write_jsonl(rows: list[dict[str, Any]], path: Path = OUTPUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    try:
        rows = load_live_rows()
        write_jsonl(rows)
    except (ExportError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json.dumps({"output": str(OUTPUT), "notes": len(rows), "cards": sum(len(row["card_ids"]) for row in rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
