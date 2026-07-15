#!/usr/bin/env python3
"""Prepare and install B1 word/example audio without changing scheduling."""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any

import goethe_example_audio as example_audio
import goethe_examples
import goethe_werkstatt_migrate as gw
import goethe_word_audio as word_audio
import download_duden_a1_audio as duden


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_b1_media"
WORD_MANIFEST = STATE / "word_manifest.json"
EXAMPLE_MANIFEST = STATE / "example_manifest.json"
SNAPSHOT = STATE / "snapshot.json"
CONFIRMATION = "ADD_B1_MEDIA"
EXPECTED_NOTES = 1992
EXPECTED_CARDS = 3984


class B1MediaError(RuntimeError):
    pass


def live_records() -> dict[int, dict[str, Any]]:
    note_ids = gw.anki("findNotes", query=f'deck:"{gw.B1_DECK}" note:"{gw.MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(note_ids, 250):
        notes.extend(gw.anki("notesInfo", notes=batch))
    card_ids = gw.anki("findCards", query=f'deck:"{gw.B1_DECK}" note:"{gw.MODEL}"')
    cards: list[dict[str, Any]] = []
    for batch in gw.chunks(card_ids, 250):
        cards.extend(gw.anki("cardsInfo", cards=batch))
    by_note: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        by_note[int(card["note"])].append(card)
    if len(notes) != EXPECTED_NOTES or len(cards) != EXPECTED_CARDS:
        raise B1MediaError(f"B1 baseline drift: notes={len(notes)} cards={len(cards)}")
    result: dict[int, dict[str, Any]] = {}
    for note in notes:
        note_id = int(note["noteId"])
        note_cards = by_note[note_id]
        fields = word_audio.field_values(note)
        if fields.get("CEFR") != "B1" or len(note_cards) != 2:
            raise B1MediaError(f"invalid B1 note/card shape: {note_id}")
        result[note_id] = {
            "model": note.get("modelName"), "tags": sorted(note.get("tags", [])),
            "fields": fields, "cards": note_cards,
        }
    return result


def load_b1_duden() -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    path = ROOT / "audio" / "b1" / "words_manifest.jsonl"
    rows = duden.load_existing_manifest_rows(path)
    if len(rows) != 2969:
        raise B1MediaError(f"B1 Duden manifest drift: {len(rows)} rows")
    by_row: dict[int, dict[str, Any]] = {}
    ok: list[dict[str, Any]] = []
    for item in rows:
        row = int(item["row"])
        enriched = dict(item)
        enriched["level"] = "B1"
        enriched["path"] = str(ROOT / "audio" / "b1" / "words" / item["output_filename"])
        by_row[row] = enriched
        if item.get("status") == "ok":
            word_audio.validate_audio(Path(enriched["path"]), item.get("sha256"), item.get("size"))
            ok.append(enriched)
    return by_row, ok


def direct_row(fields: dict[str, str], by_row: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    for ref in word_audio.split_refs(fields.get("SourceRefs", "")):
        if ref.startswith("B1-MAIN-"):
            item = by_row.get(int(ref.rsplit("-", 1)[1]))
            if item and item.get("status") == "ok":
                return item
    return None


def index_duden_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        index[word_audio.completion.lemma_key(word_audio.clean(item.get("word", "")))].append(item)
    return index


def reusable_row(fields: dict[str, str], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    candidates = {
        id(item): item
        for variant in word_audio.note_variants(fields)
        for item in index.get(variant, [])
    }
    matches = [item for item in candidates.values() if word_audio.source_matches(fields, item)]
    if not matches:
        return None
    hashes = {item.get("sha256") for item in matches}
    return matches[0] if len(hashes) == 1 else None


async def prepare_words(records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    by_row, ok_rows = load_b1_duden()
    row_index = index_duden_rows(ok_rows)
    notes: dict[str, Any] = {}
    groups: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for note_id, record in sorted(records.items()):
        fields = record["fields"]
        item = direct_row(fields, by_row) or reusable_row(fields, row_index)
        note = {
            "note_id": note_id, "lemma": fields["Lemma"], "pos": fields.get("POS", ""),
            "gender": fields.get("Gender", ""),
            "source_signature": word_audio.source_signature(fields),
        }
        if item:
            note["assignment"] = word_audio.assignment("duden_local", Path(item["path"]), detail=item)
            counts["duden_local"] += 1
        else:
            spoken = word_audio.clean(fields["Lemma"])
            if not spoken:
                raise B1MediaError(f"blank B1 lemma: {note_id}")
            key = word_audio.canonical_hash({"text": spoken, "pos": fields.get("POS", ""), "gender": fields.get("Gender", "")})
            note.update({"spoken_text": spoken, "request_key": key})
            group = groups.setdefault(key, {
                "request_key": key, "spoken_text": spoken, "pos": fields.get("POS", ""),
                "gender": fields.get("Gender", ""), "note_ids": [], "skip_duden": True,
            })
            group["note_ids"].append(note_id)
            counts["needs_fallback"] += 1
        notes[str(note_id)] = note
    fake_duden = {"items": {key: {"status": "unresolved"} for key in groups}}
    commons = await word_audio.prepare_commons(groups, fake_duden)
    edge = await word_audio.prepare_edge(groups, fake_duden, commons)
    for note in notes.values():
        if note.get("assignment"):
            continue
        key = note["request_key"]
        common = commons["items"].get(key, {})
        if common.get("status") == "ok":
            note["assignment"] = word_audio.assignment("commons", Path(common["path"]), detail=common)
            counts["commons"] += 1
        else:
            item = edge["items"].get(word_audio.edge_audio_id(note["spoken_text"]), {})
            if item.get("status") != "ok":
                raise B1MediaError(f"missing word fallback: {note['lemma']}")
            note["assignment"] = word_audio.assignment("edge", Path(item["path"]), detail=item)
            counts["edge"] += 1
    manifest = {"schema_version": 1, "notes": notes, "counts": dict(counts)}
    word_audio.atomic_json(WORD_MANIFEST, manifest)
    return manifest


async def prepare_examples(records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    previous = word_audio.load_json(EXAMPLE_MANIFEST, {})
    old_unique = previous.get("unique", {}) if previous.get("config") == example_audio.EDGE_CONFIG else {}
    notes: dict[str, Any] = {}
    unique: dict[str, Any] = {}
    occurrences = 0
    for note_id, record in sorted(records.items()):
        note_occurrences = []
        for index, example in enumerate(goethe_examples.parse_fields(record["fields"]), 1):
            spoken = example_audio.spoken_text(example["de"])
            voice = example_audio.voice_for(spoken)
            audio_id = example_audio.request_id(spoken, voice)
            note_occurrences.append({
                "index": index, "de": example["de"], "en": example["en"],
                "spoken_text": spoken, "voice": voice, "audio_id": audio_id,
            })
            item = unique.setdefault(audio_id, {
                "audio_id": audio_id, "spoken_text": spoken, "voice": voice,
                "levels": ["B1"], "occurrences": 0, "status": "pending",
            })
            item["occurrences"] += 1
            cached = old_unique.get(audio_id)
            if cached:
                item.update({k: cached[k] for k in ("status", "path", "size", "sha256", "media_name", "created_utc") if k in cached})
            occurrences += 1
        notes[str(note_id)] = {
            "note_id": note_id, "source_signature": example_audio.example_signature(record["fields"]),
            "occurrences": note_occurrences,
        }
    manifest = {
        "schema_version": 1, "config": example_audio.EDGE_CONFIG,
        "counts": {"notes": len(notes), "occurrences": occurrences, "unique": len(unique)},
        "notes": notes, "unique": unique,
    }
    word_audio.atomic_json(EXAMPLE_MANIFEST, manifest)
    pending = [item for item in unique.values() if not example_audio.validate_cached(item)]
    print(json.dumps({"example_occurrences": occurrences, "unique": len(unique), "pending": len(pending)}, ensure_ascii=False))
    if pending:
        import edge_tts
        semaphore = asyncio.Semaphore(example_audio.CONCURRENCY)
        tasks = [asyncio.create_task(example_audio.generate_one(item, edge_tts, semaphore)) for item in pending]
        done = 0
        for future in asyncio.as_completed(tasks):
            item = await future
            manifest["unique"][item["audio_id"]] = item
            done += 1
            if done % 10 == 0 or done == len(pending):
                word_audio.atomic_json(EXAMPLE_MANIFEST, manifest)
            if done % 50 == 0 or done == len(pending):
                print(f"example edge {done}/{len(pending)}")
    return manifest


async def command_prepare(_: argparse.Namespace) -> None:
    if gw.anki("version") != 6:
        raise B1MediaError("unexpected AnkiConnect version")
    records = live_records()
    words = await prepare_words(records)
    examples = await prepare_examples(records)
    print(json.dumps({"word": words["counts"], "examples": examples["counts"]}, ensure_ascii=False, indent=2))


async def command_prepare_examples(_: argparse.Namespace) -> None:
    if gw.anki("version") != 6:
        raise B1MediaError("unexpected AnkiConnect version")
    examples = await prepare_examples(live_records())
    print(json.dumps({"examples": examples["counts"]}, ensure_ascii=False, indent=2))


def media_items(words: dict[str, Any], examples: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = {note["assignment"]["media_name"]: note["assignment"] for note in words["notes"].values()}
    items.update({item["media_name"]: item for item in examples["unique"].values()})
    return items


def expected_fields(note_id: int, record: dict[str, Any], words: dict[str, Any], examples: dict[str, Any]) -> dict[str, str]:
    fields = record["fields"]
    result = {"WordAudio": f"[sound:{words['notes'][str(note_id)]['assignment']['media_name']}]"}
    parsed = goethe_examples.parse_fields(fields)
    occurrences = examples["notes"][str(note_id)]["occurrences"]
    if len(parsed) != len(occurrences):
        raise B1MediaError(f"example count changed: {note_id}")
    for example, occurrence in zip(parsed, occurrences):
        if (example["de"], example["en"]) != (occurrence["de"], occurrence["en"]):
            raise B1MediaError(f"example changed: {note_id}")
        example["audio"] = example_audio.audio_html(examples["unique"][occurrence["audio_id"]]["media_name"])
    rendered = dict(fields)
    goethe_examples.render_fields(rendered, parsed)
    for name in example_audio.AUDIO_FIELDS:
        result[name] = rendered[name]
    return result


def schedule_snapshot(records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        str(card["cardId"]): word_audio.schedule_projection(card)
        for record in records.values() for card in record["cards"]
    }


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != CONFIRMATION:
        raise B1MediaError(f"confirmation must equal {CONFIRMATION}")
    if gw.anki("version") != 6:
        raise B1MediaError("unexpected AnkiConnect version")
    records = live_records()
    words = word_audio.load_json(WORD_MANIFEST, None)
    examples = word_audio.load_json(EXAMPLE_MANIFEST, None)
    if not words or not examples or set(map(int, words["notes"])) != set(records) or set(map(int, examples["notes"])) != set(records):
        raise B1MediaError("prepared manifests do not match live B1 notes")
    for item in examples["unique"].values():
        if not example_audio.validate_cached(item):
            raise B1MediaError("example preparation incomplete")
    before = schedule_snapshot(records)
    word_audio.atomic_json(SNAPSHOT, {"cards": before})
    items = media_items(words, examples)
    for number, (name, item) in enumerate(sorted(items.items()), 1):
        path = Path(item["path"])
        word_audio.validate_audio(path, item["sha256"], item["size"])
        stored = gw.anki("storeMediaFile", filename=name, data=base64.b64encode(path.read_bytes()).decode("ascii"))
        if stored != name:
            raise B1MediaError(f"failed to store media: {name}")
        if number % 100 == 0 or number == len(items):
            print(f"media {number}/{len(items)}")
    actions = [
        {"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": expected_fields(note_id, record, words, examples)}}}
        for note_id, record in sorted(records.items())
    ]
    for batch in gw.chunks(actions, 30):
        results = gw.anki("multi", actions=batch)
        errors = [item.get("error") for item in results if isinstance(item, dict) and item.get("error")]
        if errors:
            raise B1MediaError(f"note update failed: {errors[:3]}")
    after_records = live_records()
    if schedule_snapshot(after_records) != before:
        raise B1MediaError("B1 card scheduling changed during media install")
    print(json.dumps({"notes_updated": len(records), "media": len(items), "scheduling_unchanged": True}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare").set_defaults(func=lambda args: asyncio.run(command_prepare(args)))
    sub.add_parser("prepare-examples").set_defaults(func=lambda args: asyncio.run(command_prepare_examples(args)))
    apply = sub.add_parser("apply")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (B1MediaError, word_audio.WordAudioError, example_audio.ExampleAudioError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
