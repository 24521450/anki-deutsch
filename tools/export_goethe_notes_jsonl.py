"""Export the live Goethe Werkstatt notes as deterministic agent-readable JSONL."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import goethe_werkstatt_migrate as gw
import goethe_examples

ROOT = gw.ROOT
OUTPUT = ROOT / "data" / "build" / "anki_notes.jsonl"
EXPECTED_NOTES = 1596
EXPECTED_CARDS = 3192


class ExportError(RuntimeError):
    pass


def split_pipe(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def field(note: dict[str, Any], name: str) -> str:
    return note.get("fields", {}).get(name, {}).get("value", "")


def stable_guid(fields: dict[str, str]) -> str:
    if fields.get("LegacyGUID"):
        return fields["LegacyGUID"]
    if fields.get("SourceID"):
        return "goethe:" + fields["SourceID"]
    raise ExportError("note has neither LegacyGUID nor SourceID")


def overflow_examples(value: str) -> list[dict[str, str]]:
    return goethe_examples.parse_overflow(value)


def serialize_note(note: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    fields = {name: field(note, name) for name in gw.FIELDS}
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
        "anki_note_id": int(note["noteId"]),
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
        "word_audio": fields["WordAudio"],
        "examples": examples,
        "more_examples_html": fields["MoreExamplesHTML"],
        "source_id": fields["SourceID"],
        "source_refs": split_pipe(fields["SourceRefs"]),
        "original_order": fields["OriginalOrder"],
        "source_note_raw": fields["SourceNoteRaw"],
        "tags": sorted(note.get("tags", [])),
        "card_ids": [int(card["cardId"]) for card in cards],
    }


def order_key(row: dict[str, Any]) -> tuple[Any, ...]:
    level = {"A1": 0, "A2": 1}.get(row["cefr"], 9)
    raw = str(row["original_order"] or "")
    numeric = int(raw) if raw.isdigit() else 10**9
    return (level, numeric, raw.casefold(), row["lemma"].casefold(), row["anki_note_id"])


def load_live_rows() -> list[dict[str, Any]]:
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
    if len({card_id for row in rows for card_id in row["card_ids"]}) != EXPECTED_CARDS:
        raise ExportError("duplicate card ID")
    for row in rows:
        if row["notetype"] != gw.MODEL or row["cefr"] not in {"A1", "A2"}:
            raise ExportError(f"unexpected note type or CEFR: {row['anki_note_id']}")
        if not row["lemma"] or not row["meaning_en"] or not row["source_refs"]:
            raise ExportError(f"required field missing: {row['anki_note_id']}")
        if len(row["card_ids"]) != 2:
            raise ExportError(f"expected two cards: {row['anki_note_id']}")


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
