"""Replace unambiguous Goethe word Edge TTS with Wiktionary audio."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import goethe_word_audio as audio
import goethe_werkstatt_migrate as gw
from repair_goethe_ordinal_audio import card_projection, reviews


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_word_audio"
SNAPSHOT = STATE / "edge_to_wiktionary_snapshot.json"
TARGETS = {
    1584887177160: "eins",
    1783863834502: "Kaufmann",
    1783863835323: "Schweiz",
}


def live_target_fields() -> dict[int, dict[str, str]]:
    if gw.anki("version") != 6:
        raise RuntimeError("AnkiConnect v6 is required")
    notes = gw.anki("notesInfo", notes=sorted(TARGETS))
    result = {}
    for note in notes:
        note_id = int(note["noteId"])
        fields = audio.field_values(note)
        if fields.get("Lemma") != TARGETS.get(note_id):
            raise RuntimeError(f"target note/lemma mismatch: {note_id}")
        if "_goethe_word_edge_" not in fields.get("WordAudio", ""):
            raise RuntimeError(f"target no longer uses Edge word audio: {note_id}")
        result[note_id] = fields
    if set(result) != set(TARGETS):
        raise RuntimeError("target note set mismatch")
    return result


async def prepare(fields_by_id: dict[int, dict[str, str]]) -> dict[int, dict]:
    groups = {}
    key_to_note = {}
    for note_id, fields in fields_by_id.items():
        lemma = fields["Lemma"]
        key = audio.canonical_hash({"text": lemma, "pos": fields.get("POS", ""), "gender": fields.get("Gender", "")})
        groups[key] = {
            "request_key": key, "spoken_text": lemma, "pos": fields.get("POS", ""),
            "gender": fields.get("Gender", ""), "note_ids": [note_id],
        }
        key_to_note[key] = note_id
    unresolved = {"items": {key: {"status": "unresolved"} for key in groups}}
    index = await audio.prepare_wiktionary(groups, unresolved, unresolved)
    assignments = {}
    for key, note_id in key_to_note.items():
        item = index["items"].get(key, {})
        if item.get("status") != "ok":
            raise RuntimeError(f"Wiktionary audio unresolved for {TARGETS[note_id]}: {item.get('reason')}")
        assignments[note_id] = audio.assignment("wiktionary", Path(item["path"]), detail=item)
    return assignments


def snapshot() -> dict:
    records = audio.live_records()
    cards = [card for record in records.values() for card in record["cards"]]
    card_ids = [int(card["cardId"]) for card in cards]
    backup = STATE / f"Goethe_Institute_pre_edge_to_wiktionary_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.apkg"
    if not gw.anki("exportPackage", deck="Goethe Institute", path=backup.as_posix(), includeSched=True) or not backup.exists():
        raise RuntimeError("APKG export failed")
    with zipfile.ZipFile(backup) as archive:
        if archive.testzip() is not None or not any(name in archive.namelist() for name in ("collection.anki2", "collection.anki21")):
            raise RuntimeError("invalid APKG backup")
    state = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "backup": str(backup),
        "backup_sha256": audio.duden.hash_file(backup), "target_ids": sorted(TARGETS),
        "notes": {str(note_id): {"model": record["model"], "tags": record["tags"], "fields": record["fields"]} for note_id, record in records.items()},
        "cards": {str(card["cardId"]): card_projection(card) for card in cards},
        "reviews_sha256": audio.canonical_hash(reviews(card_ids)), "model": audio.model_snapshot(),
    }
    audio.atomic_json(SNAPSHOT, state)
    return state


def verify(assignments: dict[int, dict], before: dict) -> None:
    records = audio.live_records()
    cards = [card for record in records.values() for card in record["cards"]]
    if {str(card["cardId"]): card_projection(card) for card in cards} != before["cards"]:
        raise RuntimeError("card IDs or scheduling changed")
    if audio.canonical_hash(reviews([int(card["cardId"]) for card in cards])) != before["reviews_sha256"]:
        raise RuntimeError("review history changed")
    if audio.model_snapshot() != before["model"]:
        raise RuntimeError("model/templates/styling changed")
    for note_id, record in records.items():
        old = before["notes"][str(note_id)]
        if record["model"] != old["model"] or record["tags"] != old["tags"]:
            raise RuntimeError(f"note metadata changed: {note_id}")
        for name, value in old["fields"].items():
            expected = f"[sound:{assignments[note_id]['media_name']}]" if note_id in assignments and name == "WordAudio" else value
            if record["fields"].get(name, "") != expected:
                raise RuntimeError(f"unexpected field change: {note_id}/{name}")
    for item in assignments.values():
        raw = gw.anki("retrieveMediaFile", filename=item["media_name"])
        if not raw or hashlib.sha256(base64.b64decode(raw)).hexdigest() != item["sha256"]:
            raise RuntimeError(f"media hash mismatch: {item['media_name']}")


def main() -> int:
    fields = live_target_fields()
    assignments = asyncio.run(prepare(fields))
    before = snapshot()
    for item in assignments.values():
        audio.ensure_media({"assignment": item})
    values = {note_id: f"[sound:{item['media_name']}]" for note_id, item in assignments.items()}
    try:
        audio.update_word_audio(sorted(values), values)
        verify(assignments, before)
    except Exception:
        old = {note_id: before["notes"][str(note_id)]["fields"].get("WordAudio", "") for note_id in assignments}
        audio.update_word_audio(sorted(old), old)
        raise
    print(json.dumps({
        "backup": before["backup"], "backup_sha256": before["backup_sha256"],
        "replaced": {TARGETS[note_id]: item["media_name"] for note_id, item in assignments.items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
