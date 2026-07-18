"""Safely replace the stale article audio on the four Goethe ordinal notes."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

import download_duden_a1_audio as duden
import goethe_word_audio as audio
import goethe_werkstatt_migrate as gw


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_word_audio"
SNAPSHOT = STATE / "ordinal_repair_snapshot.json"
TARGETS = {
    1584887177194: "erste",
    1584887177195: "zweite",
    1584887177196: "dritte",
    1584887177197: "vierte",
}
OLD_MEDIA = "_goethe_word_duden_70340c0b2908a30b1479c50ce98311b34f4174306237709e3dc26db43c24b5a6.mp3"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def card_projection(card: dict) -> dict:
    return {key: card.get(key) for key in gw.SCHEDULE_KEYS}


def reviews(card_ids: list[int]) -> dict:
    result = {}
    for batch in gw.chunks(sorted(card_ids), 250):
        result.update(gw.anki("getReviewsOfCards", cards=batch))
    return result


def collect() -> tuple[dict[int, dict], dict[int, dict]]:
    if gw.anki("version") != 6:
        raise RuntimeError("AnkiConnect v6 is required")
    notes = {}
    for note in gw.anki("notesInfo", notes=list(TARGETS)):
        note_id = int(note["noteId"])
        fields = audio.field_values(note)
        if note_id not in TARGETS or fields.get("Lemma") != TARGETS[note_id]:
            raise RuntimeError(f"target note/lemma mismatch: {note_id}")
        if fields.get("WordAudio") != f"[sound:{OLD_MEDIA}]":
            raise RuntimeError(f"unexpected current audio for {note_id}")
        notes[note_id] = {"fields": fields, "tags": sorted(note.get("tags", [])), "model": note["modelName"]}
    if set(notes) != set(TARGETS):
        raise RuntimeError("target note set mismatch")
    cards = {}
    card_ids = []
    for note_id in TARGETS:
        ids = gw.anki("findCards", query=f"nid:{note_id}")
        if len(ids) != 2:
            raise RuntimeError(f"target note must have two cards: {note_id}")
        info = gw.anki("cardsInfo", cards=ids)
        for card in info:
            card_id = int(card["cardId"])
            cards[card_id] = card_projection(card)
            card_ids.append(card_id)
    return notes, cards


async def prepare_media() -> dict[int, dict]:
    groups = {}
    request_keys = {}
    for lemma in TARGETS.values():
        key = audio.canonical_hash({"text": lemma, "pos": "", "gender": ""})
        request_keys[lemma] = key
        groups[key] = {"request_key": key, "spoken_text": lemma, "pos": "", "gender": "", "note_ids": []}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
        # Reuse the validated Commons resolver; Duden is deliberately marked unresolved
        # because these four have no exact Duden audio.
        index = await audio.prepare_commons(groups, {"items": {key: {"status": "unresolved"} for key in groups}})
    result = {}
    for lemma, key in request_keys.items():
        item = index["items"].get(key, {})
        if item.get("status") != "ok":
            raise RuntimeError(f"no exact Commons audio for {lemma}: {item.get('reason')}")
        detail = audio.assignment("commons", Path(item["path"]), detail=item)
        if detail["media_name"] == OLD_MEDIA:
            raise RuntimeError(f"article media reused for {lemma}")
        result[TARGETS_INV[lemma]] = detail
    return result


TARGETS_INV = {lemma: note_id for note_id, lemma in TARGETS.items()}


def make_snapshot(notes: dict, cards: dict) -> dict:
    records = audio.live_records()
    all_cards = [card for record in records.values() for card in record["cards"]]
    all_ids = [int(card["cardId"]) for card in all_cards]
    backup = STATE / f"Goethe_Institute_pre_ordinal_audio_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.apkg"
    if not gw.anki("exportPackage", deck="Goethe Institute", path=backup.as_posix(), includeSched=True) or not backup.exists():
        raise RuntimeError("APKG export failed")
    with zipfile.ZipFile(backup) as archive:
        if archive.testzip() is not None or not any(name in archive.namelist() for name in ("collection.anki2", "collection.anki21")):
            raise RuntimeError("APKG backup is not a valid Anki package")
    snapshot = {
        "created_utc": now(), "backup": str(backup), "backup_sha256": duden.hash_file(backup),
        "notes": {str(key): {"model": value["model"], "fields": value["fields"], "tags": value["tags"]} for key, value in records.items()},
        "cards": {str(card["cardId"]): card_projection(card) for card in all_cards},
        "all_reviews_sha256": audio.canonical_hash(reviews(all_ids)),
        "model": audio.model_snapshot(), "target_ids": sorted(TARGETS),
    }
    audio.atomic_json(SNAPSHOT, snapshot)
    return snapshot


def apply(assignments: dict[int, dict], snapshot: dict) -> None:
    if audio.canonical_hash(reviews([int(card_id) for card_id in snapshot["cards"]])) != snapshot["all_reviews_sha256"]:
        raise RuntimeError("review history changed since snapshot")
    for note_id, item in assignments.items():
        audio.ensure_media({"assignment": item})
    values = {note_id: f"[sound:{item['media_name']}]" for note_id, item in assignments.items()}
    audio.update_word_audio(sorted(values), values)


def verify(assignments: dict[int, dict], snapshot: dict) -> None:
    records = audio.live_records()
    notes = {note_id: {"fields": record["fields"], "tags": record["tags"], "model": record["model"]} for note_id, record in records.items()}
    cards = {str(card["cardId"]): card_projection(card) for record in records.values() for card in record["cards"]}
    if set(notes) != {int(note_id) for note_id in snapshot["notes"]}:
        raise RuntimeError("note inventory changed")
    if cards != snapshot["cards"]:
        raise RuntimeError("card IDs or scheduling changed")
    for note_id, before in snapshot["notes"].items():
        current = notes[int(note_id)]
        if current["model"] != before["model"] or current["tags"] != before["tags"]:
            raise RuntimeError(f"note metadata changed: {note_id}")
        if int(note_id) not in assignments and current["fields"] != before["fields"]:
            raise RuntimeError(f"unexpected field change: {note_id}")
        if int(note_id) in assignments:
            for name, value in before["fields"].items():
                if name != "WordAudio" and current["fields"].get(name, "") != value:
                    raise RuntimeError(f"unexpected field change: {note_id}/{name}")
    for note_id, item in assignments.items():
        expected = f"[sound:{item['media_name']}]"
        if notes[note_id]["fields"].get("WordAudio") != expected:
            raise RuntimeError(f"wrong repaired audio: {note_id}")
        raw = gw.anki("retrieveMediaFile", filename=item["media_name"])
        if not raw or hashlib.sha256(base64.b64decode(raw)).hexdigest() != item["sha256"]:
            raise RuntimeError(f"media hash mismatch: {item['media_name']}")
    if audio.model_snapshot() != snapshot["model"]:
        raise RuntimeError("model/templates/styling changed")
    if audio.canonical_hash(reviews([int(card_id) for card_id in snapshot["cards"]])) != snapshot["all_reviews_sha256"]:
        raise RuntimeError("review history changed")
    if len(notes) != 1525 or len(cards) != 3050:
        raise RuntimeError(f"unexpected inventory: notes={len(notes)} cards={len(cards)}")


def collect_after() -> tuple[dict[int, dict], dict[int, dict]]:
    notes = {}
    for note in gw.anki("notesInfo", notes=list(TARGETS)):
        notes[int(note["noteId"])] = {"fields": audio.field_values(note), "tags": sorted(note.get("tags", [])), "model": note["modelName"]}
    cards = {}
    for note_id in TARGETS:
        for card in gw.anki("cardsInfo", cards=gw.anki("findCards", query=f"nid:{note_id}")):
            cards[int(card["cardId"])] = card_projection(card)
    return notes, cards


def main() -> int:
    notes, cards = collect()
    assignments = asyncio.run(prepare_media())
    if set(assignments) != set(TARGETS):
        raise RuntimeError("assignment set mismatch")
    snapshot = make_snapshot(notes, cards)
    print(json.dumps({"backup": snapshot["backup"], "assignments": {str(k): v["media_name"] for k, v in assignments.items()}}, ensure_ascii=False, indent=2))
    apply(assignments, snapshot)
    verify(assignments, snapshot)
    print(json.dumps({"verified": sorted(TARGETS), "backup_sha256": snapshot["backup_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
