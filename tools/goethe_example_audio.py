"""Generate and safely wire Edge TTS audio for every Goethe A1/A2 example."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_examples
import goethe_werkstatt_migrate as gw
import goethe_word_audio as word_audio


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_example_audio"
EDGE_DIR = ROOT / "audio" / "goethe_example_audio" / "edge"
MANIFEST_PATH = STATE / "manifest.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
EXPECTED_NOTES = 1596
EXPECTED_CARDS = 3192
EXPECTED_OCCURRENCES = 2689
EXPECTED_UNIQUE = 2510
PILOT_SIZE = 20
CONCURRENCY = 4
AUDIO_FIELDS = tuple(f"Example{index}Audio" for index in range(1, 5)) + ("MoreExamplesHTML",)
EDGE_CONFIG = {
    "engine": "edge-tts",
    "engine_version": "7.2.8",
    "voices": ["de-DE-KatjaNeural", "de-DE-ConradNeural"],
    "voice_policy": "sha256-parity",
    "rate": "+0%",
    "volume": "+0%",
    "pitch": "+0Hz",
    "spoken_normalization": "nfc-whitespace-leading-dash-slash-pause-v1",
    "config_version": 1,
}
APPLY_CONFIRMATION = "APPLY_GOETHE_EXAMPLE_AUDIO"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_EXAMPLE_AUDIO"


class ExampleAudioError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def spoken_text(value: str) -> str:
    text = unicodedata.normalize("NFC", html.unescape(str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[-\u2013\u2014]\s*", "", text)
    return re.sub(r"\s+/\s+", " — ", text)


def voice_for(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return EDGE_CONFIG["voices"][digest[0] & 1]


def request_id(text: str, voice: str) -> str:
    return canonical_hash({"spoken_text": text, "voice": voice, **EDGE_CONFIG})


def audio_html(media_name: str) -> str:
    return (
        '<audio class="gw-example-player" controls preload="none" src="'
        + html.escape(media_name, quote=True)
        + '"></audio>'
    )


def example_signature(fields: dict[str, str]) -> str:
    return canonical_hash([
        {"de": item["de"], "en": item["en"]}
        for item in goethe_examples.parse_fields(fields)
    ])


def build_manifest(records: dict[int, dict[str, Any]], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    unique: dict[str, Any] = {}
    occurrence_count = 0
    previous_unique = (previous or {}).get("unique", {}) if (previous or {}).get("config") == EDGE_CONFIG else {}
    for note_id, record in sorted(records.items()):
        examples = goethe_examples.parse_fields(record["fields"])
        occurrences = []
        for index, example in enumerate(examples, 1):
            spoken = spoken_text(example["de"])
            if not spoken:
                raise ExampleAudioError(f"blank spoken text: note={note_id} example={index}")
            voice = voice_for(spoken)
            audio_id = request_id(spoken, voice)
            occurrences.append({
                "index": index, "de": example["de"], "en": example["en"],
                "spoken_text": spoken, "voice": voice, "audio_id": audio_id,
                "overflow": index > 4,
            })
            entry = unique.setdefault(audio_id, {
                "audio_id": audio_id, "spoken_text": spoken, "voice": voice,
                "levels": [], "occurrences": 0, "status": "pending",
            })
            entry["occurrences"] += 1
            if record["fields"]["CEFR"] not in entry["levels"]:
                entry["levels"].append(record["fields"]["CEFR"])
            cached = previous_unique.get(audio_id)
            if cached:
                entry.update({key: cached[key] for key in ("status", "path", "size", "sha256", "media_name", "created_utc") if key in cached})
            occurrence_count += 1
        notes[str(note_id)] = {
            "note_id": note_id, "level": record["fields"]["CEFR"],
            "source_signature": example_signature(record["fields"]), "occurrences": occurrences,
        }
    card_count = sum(len(record["cards"]) for record in records.values())
    if (len(records), card_count, occurrence_count, len(unique)) != (
        EXPECTED_NOTES, EXPECTED_CARDS, EXPECTED_OCCURRENCES, EXPECTED_UNIQUE,
    ):
        raise ExampleAudioError(
            "baseline drift: "
            f"notes={len(records)} cards={card_count} occurrences={occurrence_count} unique={len(unique)}"
        )
    pilot_audio_ids = choose_pilot(unique, notes)
    pilot_note_ids = []
    remaining = set(pilot_audio_ids)
    for note_id, item in notes.items():
        if any(occurrence["audio_id"] in remaining for occurrence in item["occurrences"]):
            pilot_note_ids.append(int(note_id))
            remaining -= {occurrence["audio_id"] for occurrence in item["occurrences"]}
        if not remaining:
            break
    return {
        "schema_version": 1, "created_utc": now_utc(), "config": EDGE_CONFIG,
        "counts": {"notes": len(records), "cards": card_count, "occurrences": occurrence_count, "unique": len(unique)},
        "pilot_audio_ids": pilot_audio_ids, "pilot_note_ids": pilot_note_ids,
        "notes": notes, "unique": unique,
    }


def choose_pilot(unique: dict[str, Any], notes: dict[str, Any]) -> list[str]:
    overflow_ids = {
        occurrence["audio_id"]
        for note in notes.values() for occurrence in note["occurrences"] if occurrence["overflow"]
    }
    changed_ids = {
        occurrence["audio_id"]
        for note in notes.values() for occurrence in note["occurrences"]
        if occurrence["spoken_text"] != occurrence["de"]
    }
    selected: list[str] = []
    categories = []
    for level in ("A1", "A2"):
        for voice in EDGE_CONFIG["voices"]:
            categories.append([key for key, item in sorted(unique.items()) if level in item["levels"] and item["voice"] == voice])
    categories.extend([sorted(overflow_ids), sorted(changed_ids)])
    for candidates in categories:
        candidate = next((key for key in candidates if key not in selected), None)
        if candidate:
            selected.append(candidate)
    selected.extend(key for key in sorted(unique) if key not in selected)
    return selected[:PILOT_SIZE]


def validate_cached(item: dict[str, Any]) -> bool:
    try:
        word_audio.validate_audio(Path(item["path"]), item.get("sha256"), item.get("size"))
        return item.get("status") == "ok" and item.get("media_name") == f"_goethe_example_edge_{item['sha256']}.mp3"
    except (KeyError, word_audio.WordAudioError):
        return False


async def generate_one(item: dict[str, Any], edge_tts: Any, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    if validate_cached(item):
        return item
    EDGE_DIR.mkdir(parents=True, exist_ok=True)
    existing = EDGE_DIR / f"{item['audio_id']}.mp3"
    if existing.exists():
        try:
            size, sha256 = word_audio.validate_audio(existing)
            return {**item, "status": "ok", "path": str(existing), "size": size, "sha256": sha256,
                    "media_name": f"_goethe_example_edge_{sha256}.mp3", "created_utc": now_utc()}
        except word_audio.WordAudioError:
            existing.unlink(missing_ok=True)
    last_error: Exception | None = None
    async with semaphore:
        for delay in (0, 2, 5, 10):
            if delay:
                await asyncio.sleep(delay)
            fd, tmp_name = tempfile.mkstemp(dir=EDGE_DIR, suffix=".mp3.tmp")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                communicate = edge_tts.Communicate(
                    item["spoken_text"], item["voice"], rate=EDGE_CONFIG["rate"],
                    volume=EDGE_CONFIG["volume"], pitch=EDGE_CONFIG["pitch"],
                )
                await communicate.save(str(tmp))
                size, sha256 = word_audio.validate_audio(tmp)
                os.replace(tmp, existing)
                return {**item, "status": "ok", "path": str(existing), "size": size, "sha256": sha256,
                        "media_name": f"_goethe_example_edge_{sha256}.mp3", "created_utc": now_utc()}
            except Exception as exc:  # network/codec errors are retried and surfaced
                last_error = exc
                tmp.unlink(missing_ok=True)
    raise ExampleAudioError(f"Edge TTS failed for {item['spoken_text']!r}: {last_error}")


async def generate_scope(manifest: dict[str, Any], scope: str) -> None:
    try:
        import edge_tts
        from importlib.metadata import version
    except ImportError as exc:
        raise ExampleAudioError("edge-tts is not installed") from exc
    if version("edge-tts") != EDGE_CONFIG["engine_version"]:
        raise ExampleAudioError(f"edge-tts {EDGE_CONFIG['engine_version']} is required")
    voices = await edge_tts.list_voices()
    available = {item.get("ShortName") for item in voices if item.get("Locale") == "de-DE"}
    missing = set(EDGE_CONFIG["voices"]) - available
    if missing:
        raise ExampleAudioError(f"Edge voices unavailable: {sorted(missing)}")
    ids = manifest["pilot_audio_ids"] if scope == "pilot" else sorted(manifest["unique"])
    pending = [audio_id for audio_id in ids if not validate_cached(manifest["unique"][audio_id])]
    print(json.dumps({"scope": scope, "selected": len(ids), "pending": len(pending)}, ensure_ascii=False))
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [asyncio.create_task(generate_one(manifest["unique"][audio_id], edge_tts, semaphore)) for audio_id in pending]
    completed = 0
    for future in asyncio.as_completed(tasks):
        item = await future
        manifest["unique"][item["audio_id"]] = item
        completed += 1
        if completed % 10 == 0 or completed == len(pending):
            word_audio.atomic_json(MANIFEST_PATH, manifest)
        if completed % 25 == 0 or completed == len(pending):
            print(f"edge {completed}/{len(pending)}")
    word_audio.atomic_json(MANIFEST_PATH, manifest)


def live_records() -> dict[int, dict[str, Any]]:
    try:
        return word_audio.live_records()
    except word_audio.WordAudioError as exc:
        raise ExampleAudioError(str(exc)) from exc


def command_audit(_: argparse.Namespace) -> None:
    records = live_records()
    examples = [item for record in records.values() for item in goethe_examples.parse_fields(record["fields"])]
    sources = Counter()
    for item in examples:
        audio = item["audio"]
        if "_goethe_example_edge_" in audio:
            sources["edge-example"] += 1
        elif "googletts" in audio:
            sources["googletts"] += 1
        elif "yandex" in audio:
            sources["yandex"] += 1
        elif audio:
            sources["other"] += 1
        else:
            sources["blank"] += 1
    print(json.dumps({"notes": len(records), "cards": sum(len(r["cards"]) for r in records.values()),
                      "occurrences": len(examples), "unique": len({item["de"] for item in examples}),
                      "sources": sources}, ensure_ascii=False, indent=2))


async def command_prepare(args: argparse.Namespace) -> None:
    records = live_records()
    previous = word_audio.load_json(MANIFEST_PATH, None)
    manifest = build_manifest(records, previous)
    word_audio.atomic_json(MANIFEST_PATH, manifest)
    await generate_scope(manifest, args.scope)


def require_full_ready(manifest: dict[str, Any]) -> None:
    bad = [key for key, item in manifest["unique"].items() if not validate_cached(item)]
    if bad:
        raise ExampleAudioError(f"audio preparation incomplete: {len(bad)} missing or invalid")


def command_snapshot(_: argparse.Namespace) -> None:
    manifest = word_audio.load_json(MANIFEST_PATH, None)
    if not manifest or manifest.get("config") != EDGE_CONFIG:
        raise ExampleAudioError("prepared manifest missing or incompatible")
    require_full_ready(manifest)
    records = live_records()
    if set(map(int, manifest["notes"])) != set(records):
        raise ExampleAudioError("live note ID set changed")
    for note_id, record in records.items():
        if manifest["notes"][str(note_id)]["source_signature"] != example_signature(record["fields"]):
            raise ExampleAudioError(f"example text changed after preparation: {note_id}")
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_pre_example_audio_{stamp}.apkg"
    result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.as_posix(), includeSched=True)
    if not result or not backup.exists():
        raise ExampleAudioError("Anki APKG export failed")
    cards = [card for record in records.values() for card in record["cards"]]
    reviews = word_audio.all_reviews([int(card["cardId"]) for card in cards])
    snapshot = {
        "schema_version": 1, "created_utc": now_utc(), "backup": str(backup),
        "backup_sha256": word_audio.duden.hash_file(backup),
        "manifest_sha256": word_audio.duden.hash_file(MANIFEST_PATH),
        "notes": {str(note_id): {"model": record["model"], "fields": record["fields"], "tags": record["tags"]}
                  for note_id, record in records.items()},
        "cards": {str(card["cardId"]): word_audio.schedule_projection(card) for card in cards},
        "reviews": reviews, "reviews_sha256": canonical_hash(reviews), "model": word_audio.model_snapshot(),
    }
    word_audio.atomic_json(SNAPSHOT_PATH, snapshot)
    print(json.dumps({"backup": str(backup), "sha256": snapshot["backup_sha256"],
                      "notes": len(records), "cards": len(cards)}, indent=2))


def load_ready() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = word_audio.load_json(MANIFEST_PATH, None)
    snapshot = word_audio.load_json(SNAPSHOT_PATH, None)
    if not manifest or not snapshot:
        raise ExampleAudioError("manifest or snapshot missing")
    if snapshot.get("manifest_sha256") != word_audio.duden.hash_file(MANIFEST_PATH):
        raise ExampleAudioError("manifest changed after snapshot")
    require_full_ready(manifest)
    return manifest, snapshot


def expected_audio_fields(note_id: int, manifest: dict[str, Any], base_fields: dict[str, str]) -> dict[str, str]:
    examples = goethe_examples.parse_fields(base_fields)
    occurrences = manifest["notes"][str(note_id)]["occurrences"]
    if len(examples) != len(occurrences):
        raise ExampleAudioError(f"example count changed: {note_id}")
    for example, occurrence in zip(examples, occurrences):
        if (example["de"], example["en"]) != (occurrence["de"], occurrence["en"]):
            raise ExampleAudioError(f"example text changed: {note_id}")
        media_name = manifest["unique"][occurrence["audio_id"]]["media_name"]
        example["audio"] = audio_html(media_name)
    rendered = dict(base_fields)
    goethe_examples.render_fields(rendered, examples)
    return {name: rendered[name] for name in AUDIO_FIELDS}


def selected_note_ids(manifest: dict[str, Any], scope: str) -> list[int]:
    return manifest["pilot_note_ids"] if scope == "pilot" else sorted(map(int, manifest["notes"]))


def verify_baseline(records: dict[int, dict[str, Any]], manifest: dict[str, Any], snapshot: dict[str, Any]) -> None:
    if set(records) != set(map(int, snapshot["notes"])):
        raise ExampleAudioError("live note ID set changed")
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        expected = expected_audio_fields(note_id, manifest, before["fields"])
        if record["model"] != before["model"] or record["tags"] != before["tags"]:
            raise ExampleAudioError(f"model or tags changed: {note_id}")
        for name, value in before["fields"].items():
            if name in AUDIO_FIELDS and record["fields"].get(name, "") in (value, expected[name]):
                continue
            if record["fields"].get(name, "") != value:
                raise ExampleAudioError(f"field changed unexpectedly: note={note_id} field={name}")


def ensure_media(item: dict[str, Any]) -> None:
    path = Path(item["path"])
    word_audio.validate_audio(path, item["sha256"], item["size"])
    existing = gw.anki("retrieveMediaFile", filename=item["media_name"])
    if existing:
        if hashlib.sha256(base64.b64decode(existing)).hexdigest() != item["sha256"]:
            raise ExampleAudioError(f"Anki media hash conflict: {item['media_name']}")
        return
    stored = gw.anki("storeMediaFile", filename=item["media_name"], data=base64.b64encode(path.read_bytes()).decode("ascii"))
    if stored != item["media_name"]:
        raise ExampleAudioError(f"unexpected stored media name: {stored}")


def update_notes(values: dict[int, dict[str, str]]) -> None:
    actions = [
        {"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": fields}}}
        for note_id, fields in values.items()
    ]
    for batch in gw.chunks(actions, 40):
        results = gw.anki("multi", actions=batch)
        errors = [item.get("error") for item in results if isinstance(item, dict) and item.get("error")]
        if errors:
            raise ExampleAudioError(f"Anki update errors: {errors[:3]}")


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise ExampleAudioError(f"confirmation must equal {APPLY_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    verify_baseline(records, manifest, snapshot)
    note_ids = selected_note_ids(manifest, args.scope)
    audio_ids = {
        occurrence["audio_id"] for note_id in note_ids
        for occurrence in manifest["notes"][str(note_id)]["occurrences"]
    }
    for number, audio_id in enumerate(sorted(audio_ids), 1):
        ensure_media(manifest["unique"][audio_id])
        if number % 100 == 0 or number == len(audio_ids):
            print(f"media {number}/{len(audio_ids)}")
    values = {}
    for note_id in note_ids:
        expected = expected_audio_fields(note_id, manifest, snapshot["notes"][str(note_id)]["fields"])
        if any(records[note_id]["fields"].get(name, "") != value for name, value in expected.items()):
            values[note_id] = expected
    print(json.dumps({"scope": args.scope, "selected_notes": len(note_ids), "changed_notes": len(values)}, indent=2))
    try:
        update_notes(values)
    except Exception:
        update_notes({note_id: {name: snapshot["notes"][str(note_id)]["fields"][name] for name in AUDIO_FIELDS}
                      for note_id in values})
        raise


def verify_state(scope: str, baseline: bool = False) -> dict[str, Any]:
    manifest, snapshot = load_ready()
    records = live_records()
    selected = set() if baseline else set(selected_note_ids(manifest, scope))
    if set(records) != set(map(int, snapshot["notes"])):
        raise ExampleAudioError("live note ID set changed")
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        expected_audio = expected_audio_fields(note_id, manifest, before["fields"])
        if record["model"] != before["model"] or record["tags"] != before["tags"]:
            raise ExampleAudioError(f"model or tags changed: {note_id}")
        for name, value in before["fields"].items():
            expected = expected_audio[name] if name in AUDIO_FIELDS and note_id in selected else value
            if record["fields"].get(name, "") != expected:
                raise ExampleAudioError(f"field mismatch: note={note_id} field={name}")
    cards = [card for record in records.values() for card in record["cards"]]
    if {str(card["cardId"]): word_audio.schedule_projection(card) for card in cards} != snapshot["cards"]:
        raise ExampleAudioError("card IDs or scheduling changed")
    reviews = word_audio.all_reviews([int(card["cardId"]) for card in cards])
    if canonical_hash(reviews) != snapshot["reviews_sha256"]:
        raise ExampleAudioError("review history changed")
    if word_audio.model_snapshot() != snapshot["model"]:
        raise ExampleAudioError("model fields/templates/styling changed")
    for note_id in selected:
        for occurrence in manifest["notes"][str(note_id)]["occurrences"]:
            item = manifest["unique"][occurrence["audio_id"]]
            media = gw.anki("retrieveMediaFile", filename=item["media_name"])
            if not media or hashlib.sha256(base64.b64decode(media)).hexdigest() != item["sha256"]:
                raise ExampleAudioError(f"missing or corrupt Anki media: {item['media_name']}")
    return {"scope": scope, "baseline": baseline, "notes": len(records), "cards": len(cards),
            "verified_notes": len(selected)}


def command_verify(args: argparse.Namespace) -> None:
    print(json.dumps(verify_state(args.scope, args.baseline), indent=2))


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise ExampleAudioError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    verify_baseline(records, manifest, snapshot)
    values = {
        note_id: {name: snapshot["notes"][str(note_id)]["fields"][name] for name in AUDIO_FIELDS}
        for note_id in records
        if any(records[note_id]["fields"][name] != snapshot["notes"][str(note_id)]["fields"][name] for name in AUDIO_FIELDS)
    }
    update_notes(values)
    print(json.dumps(verify_state("full", baseline=True), indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=command_audit)
    for name in ("prepare", "resume"):
        prepare = sub.add_parser(name)
        prepare.add_argument("--scope", choices=("pilot", "full"), default="full")
        prepare.set_defaults(func=command_prepare)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    apply = sub.add_parser("apply")
    apply.add_argument("--scope", choices=("pilot", "full"), default="full")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify")
    verify.add_argument("--scope", choices=("pilot", "full"), default="full")
    verify.add_argument("--baseline", action="store_true")
    verify.set_defaults(func=command_verify)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except (ExampleAudioError, word_audio.WordAudioError, gw.MigrationError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
