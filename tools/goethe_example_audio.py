"""Generate and safely wire Gemini TTS audio for every Goethe A1-B1 example."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import json
import math
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import goethe_examples
import goethe_apkg as apkg
import goethe_scope as scope
import goethe_werkstatt_migrate as gw
import goethe_word_audio as word_audio
import gemini_tts


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_example_audio"
GEMINI_DIR = ROOT / "audio" / "goethe_example_audio" / "gemini"
MANIFEST_PATH = STATE / "manifest.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
PRONUNCIATION_AUDIO_OVERRIDES = (
    ROOT / "review" / "goethe_example_pronunciation_audio.json"
)
REJECTIONS_PATH = ROOT / "review" / "goethe_example_audio_rejections.json"
REPETITION_AUDIT_PATH = STATE / "repetition_audit.json"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
MANIFEST_SCHEMA_VERSION = 3
EXPECTED_NOTES = scope.EXPECTED_NOTES
EXPECTED_CARDS = scope.EXPECTED_CARDS
EXPECTED_NOTES_BY_LEVEL = dict(scope.EXPECTED_NOTES_BY_LEVEL)
EXPECTED_CARDS_BY_LEVEL = dict(scope.EXPECTED_CARDS_BY_LEVEL)
EXPECTED_OCCURRENCES_BY_LEVEL = dict(scope.EXPECTED_EXAMPLE_OCCURRENCES_BY_LEVEL)
EXPECTED_OCCURRENCES = scope.EXPECTED_EXAMPLE_OCCURRENCES
EXPECTED_UNIQUE = scope.EXPECTED_UNIQUE_EXAMPLE_AUDIO
PILOT_SIZE = 20
# Sustain two parallel turns per configured API key without concurrent
# manifest writers. A two-key worker therefore runs four turns at once.
CONCURRENCY_PER_KEY = 2
AUDIO_FIELDS = tuple(f"Example{index}Audio" for index in range(1, 5)) + ("MoreExamplesHTML",)
EXTERNALLY_OWNED_FIELDS = frozenset({"WordAudio"})
GEMINI_VOICES = tuple(gemini_tts.VOICES)
GEMINI_CONFIG = {
    **gemini_tts.CONFIG,
    "voices": list(GEMINI_VOICES),
    "voice_policy": "sha256-note-id-start-alternating-occurrence-v1",
    "spoken_normalization": "nfc-whitespace-leading-dash-slash-pause-v1",
    "example_config_version": 1,
}
PASSING_QA_STATUSES = frozenset({"exact", "verified_equivalent"})
APPLY_CONFIRMATION = "APPLY_GOETHE_EXAMPLE_AUDIO"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_EXAMPLE_AUDIO"
_REVIEWED_PRONUNCIATION_AUDIO: dict[str, Any] | None = None
_EXAMPLE_AUDIO_REJECTIONS: dict[str, dict[str, str]] | None = None


class ExampleAudioError(RuntimeError):
    pass


def load_rejections() -> dict[str, dict[str, str]]:
    global _EXAMPLE_AUDIO_REJECTIONS
    if _EXAMPLE_AUDIO_REJECTIONS is not None:
        return _EXAMPLE_AUDIO_REJECTIONS
    data = word_audio.load_json(REJECTIONS_PATH, None)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ExampleAudioError("example-audio rejection registry is missing or stale")
    values = data.get("rejections")
    if not isinstance(values, dict):
        raise ExampleAudioError("example-audio rejection registry is invalid")
    result: dict[str, dict[str, str]] = {}
    for audio_id, item in values.items():
        if (
            not isinstance(item, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(audio_id))
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
            or not str(item.get("reason", "")).strip()
        ):
            raise ExampleAudioError(f"invalid example-audio rejection: {audio_id}")
        result[str(audio_id)] = {
            key: str(value)
            for key, value in item.items()
            if isinstance(value, str)
        }
    _EXAMPLE_AUDIO_REJECTIONS = result
    return result


def reviewed_pronunciation_audio(
    text: str, voice: str
) -> dict[str, Any] | None:
    global _REVIEWED_PRONUNCIATION_AUDIO
    if _REVIEWED_PRONUNCIATION_AUDIO is None:
        _REVIEWED_PRONUNCIATION_AUDIO = word_audio.load_json(
            PRONUNCIATION_AUDIO_OVERRIDES, None
        )
    data = _REVIEWED_PRONUNCIATION_AUDIO
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ExampleAudioError(
            "reviewed pronunciation-audio overrides are missing or stale"
        )
    overrides = data.get("overrides")
    if not isinstance(overrides, dict):
        raise ExampleAudioError("reviewed pronunciation-audio overrides are invalid")
    item = overrides.get(text)
    if item is None or item.get("voice") != voice:
        return None
    required = {
        "generation_text", "engine", "model", "prompt_version", "path",
        "size", "sha256", "duration_seconds", "qa_status",
        "asr_transcript", "review_status", "reviewed_utc",
    }
    if (
        not isinstance(item, dict)
        or not required.issubset(item)
        or item.get("review_status") != "human-approved"
        or item.get("qa_status") != "verified_equivalent"
        or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
    ):
        raise ExampleAudioError(
            f"reviewed pronunciation-audio override is invalid: {text!r}"
        )
    return item


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def spoken_text(value: str) -> str:
    text = unicodedata.normalize("NFC", html.unescape(str(value or "")))
    text = re.sub(r"<br\s*/?>\s*[–—-]?\s*", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[-\u2013\u2014]\s*", "", text)
    return re.sub(r"\s+/\s+", " — ", text)


def voice_for(note_id: int | str, occurrence_index: int) -> str:
    """Choose the starting voice by note ID, then alternate by zero-based index."""
    digest = hashlib.sha256(str(note_id).encode("utf-8")).digest()
    return GEMINI_VOICES[((digest[0] & 1) + occurrence_index) & 1]


def request_id(text: str, voice: str) -> str:
    payload = {
        "spoken_text": text,
        "voice": voice,
        "config": GEMINI_CONFIG,
    }
    pronunciation_override = gemini_tts.pronunciation_override_identity(
        text, "example"
    )
    if pronunciation_override is not None:
        payload["pronunciation_override"] = pronunciation_override
    reviewed_audio = reviewed_pronunciation_audio(text, voice)
    if reviewed_audio is not None:
        payload["reviewed_pronunciation_audio"] = {
            "version": 1,
            "engine": reviewed_audio["engine"],
            "model": reviewed_audio["model"],
            "prompt_version": reviewed_audio["prompt_version"],
            "sha256": reviewed_audio["sha256"],
        }
    return canonical_hash(payload)


def media_name_for(sha256: str) -> str:
    return f"_goethe_example_gemini_{sha256}.mp3"


def audio_html(media_name: str) -> str:
    return (
        '<audio class="gw-example-player" controls preload="none" src="'
        + html.escape(media_name, quote=True)
        + '"></audio>'
    )


def audio_field_equivalent(actual: str, expected: str) -> bool:
    """Treat Anki's two serialisations of the boolean ``controls`` attr alike."""
    normalize = lambda value: str(value or "").replace(' controls=""', " controls")
    return normalize(actual) == normalize(expected)


def example_signature(fields: dict[str, str]) -> str:
    return canonical_hash([
        {"de": item["de"], "en": item["en"]}
        for item in goethe_examples.parse_fields(fields)
    ])


def build_manifest(records: dict[int, dict[str, Any]], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    unique: dict[str, Any] = {}
    occurrence_count = 0
    previous_compatible = (
        (previous or {}).get("schema_version") == MANIFEST_SCHEMA_VERSION
        and (previous or {}).get("levels") == list(scope.LEVELS)
        and (previous or {}).get("config") == GEMINI_CONFIG
    )
    previous_unique = (previous or {}).get("unique", {}) if previous_compatible else {}
    occurrences_by_level: Counter[str] = Counter()
    for note_id, record in sorted(records.items()):
        examples = goethe_examples.parse_fields(record["fields"])
        occurrences = []
        for occurrence_index, example in enumerate(examples):
            spoken = spoken_text(example["de"])
            if not spoken:
                raise ExampleAudioError(
                    f"blank spoken text: note={note_id} example={occurrence_index + 1}"
                )
            voice = voice_for(note_id, occurrence_index)
            audio_id = request_id(spoken, voice)
            occurrences.append({
                "index": occurrence_index + 1, "de": example["de"], "en": example["en"],
                "spoken_text": spoken, "voice": voice, "audio_id": audio_id,
                "overflow": occurrence_index >= 4,
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
                entry.update({
                    key: cached[key]
                    for key in (
                        "status", "path", "size", "sha256", "media_name",
                        "duration_seconds", "qa_status", "asr_transcript",
                        "live_transcript", "strict_transcript", "qa_source",
                        "qa_version", "created_utc",
                    )
                    if key in cached
                })
            reviewed_audio = reviewed_pronunciation_audio(spoken, voice)
            if reviewed_audio is not None:
                entry.update({
                    "status": "ok",
                    "path": str(ROOT / reviewed_audio["path"]),
                    "size": reviewed_audio["size"],
                    "sha256": reviewed_audio["sha256"],
                    "media_name": media_name_for(reviewed_audio["sha256"]),
                    "duration_seconds": reviewed_audio["duration_seconds"],
                    "qa_status": reviewed_audio["qa_status"],
                    "asr_transcript": reviewed_audio["asr_transcript"],
                    "generation_text": reviewed_audio["generation_text"],
                    "generation_engine": reviewed_audio["engine"],
                    "generation_model": reviewed_audio["model"],
                    "prompt_version": reviewed_audio["prompt_version"],
                    "review_status": reviewed_audio["review_status"],
                    "reviewed_utc": reviewed_audio["reviewed_utc"],
                })
            occurrence_count += 1
            occurrences_by_level[record["fields"]["CEFR"]] += 1
        notes[str(note_id)] = {
            "note_id": note_id, "level": record["fields"]["CEFR"],
            "source_signature": example_signature(record["fields"]), "occurrences": occurrences,
        }
    for entry in unique.values():
        entry["levels"].sort(key=scope.LEVEL_RANK.__getitem__)
    card_count = sum(len(record["cards"]) for record in records.values())
    if (len(records), card_count, occurrence_count, len(unique)) != (
        EXPECTED_NOTES, EXPECTED_CARDS, EXPECTED_OCCURRENCES, EXPECTED_UNIQUE,
    ):
        raise ExampleAudioError(
            "baseline drift: "
            f"notes={len(records)} cards={card_count} occurrences={occurrence_count} unique={len(unique)}"
        )
    level_counts = {
        level: {
            "notes": sum(record["fields"]["CEFR"] == level for record in records.values()),
            "cards": sum(
                len(record["cards"])
                for record in records.values()
                if record["fields"]["CEFR"] == level
            ),
            "occurrences": occurrences_by_level[level],
        }
        for level in scope.LEVELS
    }
    if level_counts != expected_level_counts():
        raise ExampleAudioError(f"per-level baseline drift: {level_counts}")
    pilot_audio_ids = choose_pilot(unique, notes)
    pilot_note_ids = choose_pilot_notes(notes, pilot_audio_ids)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION, "created_utc": now_utc(), "config": GEMINI_CONFIG,
        "levels": list(scope.LEVELS), "level_counts": level_counts,
        "counts": {"notes": len(records), "cards": card_count, "occurrences": occurrence_count, "unique": len(unique)},
        "pilot_audio_ids": pilot_audio_ids, "pilot_note_ids": pilot_note_ids,
        "notes": notes, "unique": unique,
    }
    validate_manifest(manifest)
    return manifest


def expected_level_counts() -> dict[str, dict[str, int]]:
    return {
        level: {
            "notes": EXPECTED_NOTES_BY_LEVEL[level],
            "cards": EXPECTED_CARDS_BY_LEVEL[level],
            "occurrences": EXPECTED_OCCURRENCES_BY_LEVEL[level],
        }
        for level in scope.LEVELS
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
    for level in scope.LEVELS:
        for voice in GEMINI_VOICES:
            categories.append([key for key, item in sorted(unique.items()) if level in item["levels"] and item["voice"] == voice])
    categories.extend([sorted(overflow_ids), sorted(changed_ids)])
    for candidates in categories:
        candidate = next((key for key in candidates if key not in selected), None)
        if candidate:
            selected.append(candidate)
    selected.extend(key for key in sorted(unique) if key not in selected)
    return selected[:PILOT_SIZE]


def choose_pilot_notes(notes: dict[str, Any], pilot_audio_ids: list[str]) -> list[int]:
    selected: list[int] = []
    pilot_set = set(pilot_audio_ids)
    for level in scope.LEVELS:
        candidate = next((
            (note_id, item)
            for note_id, item in notes.items()
            if item["level"] == level
            and any(occurrence["audio_id"] in pilot_set for occurrence in item["occurrences"])
        ), None)
        if candidate:
            note_id, _ = candidate
            selected.append(int(note_id))
    remaining = set(pilot_audio_ids)
    for note_id in selected:
        remaining -= {occurrence["audio_id"] for occurrence in notes[str(note_id)]["occurrences"]}
    for note_id, item in notes.items():
        if int(note_id) in selected:
            continue
        if any(occurrence["audio_id"] in remaining for occurrence in item["occurrences"]):
            selected.append(int(note_id))
            remaining -= {occurrence["audio_id"] for occurrence in item["occurrences"]}
        if not remaining:
            break
    return selected


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ExampleAudioError("example-audio manifest schema is stale; rebuild it")
    if manifest.get("levels") != list(scope.LEVELS):
        raise ExampleAudioError("example-audio manifest level set is stale; rebuild it")
    if manifest.get("config") != GEMINI_CONFIG:
        raise ExampleAudioError("example-audio TTS config is stale; rebuild it")
    expected_counts = {
        "notes": EXPECTED_NOTES, "cards": EXPECTED_CARDS,
        "occurrences": EXPECTED_OCCURRENCES, "unique": EXPECTED_UNIQUE,
    }
    if manifest.get("counts") != expected_counts:
        raise ExampleAudioError("example-audio manifest corpus totals are stale; rebuild it")
    if manifest.get("level_counts") != expected_level_counts():
        raise ExampleAudioError("example-audio manifest per-level counts are stale; rebuild it")
    notes = manifest.get("notes")
    unique = manifest.get("unique")
    if not isinstance(notes, dict) or not isinstance(unique, dict):
        raise ExampleAudioError("example-audio manifest content is invalid")
    note_counts = Counter(item.get("level") for item in notes.values() if isinstance(item, dict))
    if dict(note_counts) != EXPECTED_NOTES_BY_LEVEL:
        raise ExampleAudioError(f"example-audio manifest note levels are invalid: {dict(note_counts)}")
    occurrences = [occurrence for item in notes.values() for occurrence in item.get("occurrences", [])]
    if len(occurrences) != EXPECTED_OCCURRENCES or len(unique) != EXPECTED_UNIQUE:
        raise ExampleAudioError("example-audio manifest occurrence index is incomplete")
    for note_id, note in notes.items():
        for occurrence_index, occurrence in enumerate(note.get("occurrences", [])):
            expected_voice = voice_for(note_id, occurrence_index)
            expected_audio_id = request_id(occurrence.get("spoken_text", ""), expected_voice)
            if (
                occurrence.get("index") != occurrence_index + 1
                or occurrence.get("voice") != expected_voice
                or occurrence.get("audio_id") != expected_audio_id
            ):
                raise ExampleAudioError(
                    f"example-audio voice assignment is stale: note={note_id} "
                    f"example={occurrence_index + 1}"
                )
            item = unique.get(expected_audio_id)
            if (
                not isinstance(item, dict)
                or item.get("spoken_text") != occurrence.get("spoken_text")
                or item.get("voice") != expected_voice
            ):
                raise ExampleAudioError("example-audio manifest references an unknown audio ID")
    usage = Counter(occurrence["audio_id"] for occurrence in occurrences)
    usage_levels: dict[str, set[str]] = {audio_id: set() for audio_id in usage}
    for note in notes.values():
        for occurrence in note.get("occurrences", []):
            usage_levels[occurrence["audio_id"]].add(note["level"])
    if set(usage) != set(unique):
        raise ExampleAudioError("example-audio manifest has unreferenced audio IDs")
    for audio_id, item in unique.items():
        levels = sorted(usage_levels[audio_id], key=scope.LEVEL_RANK.__getitem__)
        if item.get("occurrences") != usage[audio_id] or item.get("levels") != levels:
            raise ExampleAudioError(f"example-audio dedupe metadata is stale: {audio_id}")
    if any(audio_id not in unique for audio_id in manifest.get("pilot_audio_ids", [])):
        raise ExampleAudioError("example-audio pilot references an unknown audio ID")
    pilot_levels = {
        notes[str(note_id)]["level"]
        for note_id in manifest.get("pilot_note_ids", [])
        if str(note_id) in notes
    }
    if pilot_levels != set(scope.LEVELS):
        raise ExampleAudioError("example-audio pilot does not cover A1, A2, and B1")


def validate_cached(item: dict[str, Any]) -> bool:
    try:
        word_audio.validate_audio(Path(item["path"]), item.get("sha256"), item.get("size"))
        rejected = load_rejections().get(str(item.get("audio_id", "")))
        if rejected and rejected["sha256"] == item.get("sha256"):
            return False
        qa_version = item.get("qa_version")
        if qa_version is not None and (
            qa_version != gemini_tts.EXAMPLE_QA_VERSION
            or gemini_tts._normalized_spoken_text(
                str(item.get("strict_transcript", ""))
            )
            != gemini_tts._normalized_spoken_text(
                str(item.get("spoken_text", ""))
            )
        ):
            return False
        return (
            item.get("status") == "ok"
            and item.get("voice") in GEMINI_VOICES
            and item.get("qa_status") in PASSING_QA_STATUSES
            and isinstance(item.get("asr_transcript"), str)
            and bool(item["asr_transcript"].strip())
            and float(item.get("duration_seconds", 0)) > 0
            and item.get("media_name") == media_name_for(item["sha256"])
        )
    except (KeyError, TypeError, ValueError, word_audio.WordAudioError):
        return False


def _word_count_bucket(text: str) -> str:
    count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    if count <= 5:
        return "1-5"
    if count <= 10:
        return "6-10"
    if count <= 20:
        return "11-20"
    return "21+"


def repetition_outlier_ids(
    manifest: dict[str, Any], *, percentile: float = 95.0
) -> list[str]:
    if not 0 < percentile < 100:
        raise ExampleAudioError("repetition percentile must be between 0 and 100")
    groups: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for audio_id, item in manifest.get("unique", {}).items():
        text = str(item.get("spoken_text", ""))
        words = re.findall(r"\w+", text, flags=re.UNICODE)
        duration = float(item.get("duration_seconds") or 0)
        if not words or duration <= 0:
            continue
        key = (str(item.get("voice", "")), _word_count_bucket(text))
        groups.setdefault(key, []).append((duration / len(words), audio_id))
    selected: set[str] = set()
    fraction = (100.0 - percentile) / 100.0
    for values in groups.values():
        count = max(1, math.ceil(len(values) * fraction - 1e-12))
        selected.update(
            audio_id
            for _, audio_id in sorted(values, reverse=True)[:count]
        )
    selected.update(
        audio_id
        for audio_id in load_rejections()
        if audio_id in manifest.get("unique", {})
    )
    return sorted(selected)


async def generate_one(item: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
    if validate_cached(item):
        return item
    GEMINI_DIR.mkdir(parents=True, exist_ok=True)
    target = GEMINI_DIR / f"{item['audio_id']}.mp3"
    async with semaphore:
        try:
            metadata = await gemini_tts.generate_verified_mp3(
                item["spoken_text"],
                item["voice"],
                "example",
                target,
            )
        except gemini_tts.GeminiTTSError as exc:
            raise ExampleAudioError(
                f"Gemini TTS failed for {item['spoken_text']!r}: {exc}"
            ) from exc
    generated = {
        **item,
        **metadata,
        "media_name": media_name_for(metadata["sha256"]),
    }
    if not validate_cached(generated):
        raise ExampleAudioError(
            f"Gemini TTS returned incomplete QA metadata for {item['spoken_text']!r}"
        )
    return generated


async def checkpoint_manifest(manifest: dict[str, Any]) -> None:
    delays = (0.05, 0.1, 0.2, 0.4, 0.8)
    for attempt in range(len(delays) + 1):
        try:
            word_audio.atomic_json(MANIFEST_PATH, manifest)
            return
        except PermissionError:
            if attempt == len(delays):
                raise
            await asyncio.sleep(delays[attempt])


async def generate_scope(manifest: dict[str, Any], scope: str) -> None:
    validate_manifest(manifest)
    if GEMINI_VOICES != ("Kore", "Charon"):
        raise ExampleAudioError(
            f"Gemini example voices must be Kore then Charon, got {GEMINI_VOICES}"
        )
    ids = manifest["pilot_audio_ids"] if scope == "pilot" else sorted(manifest["unique"])
    pending = [audio_id for audio_id in ids if not validate_cached(manifest["unique"][audio_id])]
    print(json.dumps({"scope": scope, "selected": len(ids), "pending": len(pending)}, ensure_ascii=False))
    key_count = len(gemini_tts._api_keys())
    concurrency = CONCURRENCY_PER_KEY * key_count
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    tasks = [
        asyncio.create_task(
            generate_one(manifest["unique"][audio_id], semaphore)
        )
        for audio_id in pending
    ]
    failures: list[Exception] = []
    try:
        for future in asyncio.as_completed(tasks):
            try:
                result = await future
            except Exception as exc:
                failures.append(exc)
                continue
            manifest["unique"][result["audio_id"]] = result
            completed += 1
            if completed % 25 == 0:
                await checkpoint_manifest(manifest)
            if completed % 25 == 0 or completed == len(pending):
                print(f"gemini {completed}/{len(pending)}")
    finally:
        await checkpoint_manifest(manifest)
    if failures:
        raise ExampleAudioError(
            f"Gemini generation completed after checkpointing {completed} successful "
            f"items with {len(failures)} failures: {failures[0]}"
        ) from failures[0]


def live_records() -> dict[int, dict[str, Any]]:
    try:
        return word_audio.live_records()
    except word_audio.WordAudioError as exc:
        raise ExampleAudioError(str(exc)) from exc


def command_audit(_: argparse.Namespace) -> None:
    records = live_records()
    examples = [
        (note_id, record["fields"]["CEFR"], occurrence_index, item)
        for note_id, record in records.items()
        for occurrence_index, item in enumerate(
            goethe_examples.parse_fields(record["fields"])
        )
    ]
    occurrences_by_level = Counter(level for _, level, _, _ in examples)
    if sum(occurrences_by_level.values()) != EXPECTED_OCCURRENCES or dict(occurrences_by_level) != {
        level: EXPECTED_OCCURRENCES_BY_LEVEL[level] for level in scope.LEVELS
    }:
        raise ExampleAudioError(f"example baseline drift: occurrences={dict(occurrences_by_level)}")
    sources = Counter()
    unique_ids = set()
    for note_id, _, occurrence_index, item in examples:
        audio = item["audio"]
        spoken = spoken_text(item["de"])
        unique_ids.add(request_id(spoken, voice_for(note_id, occurrence_index)))
        if "_goethe_example_gemini_" in audio:
            sources["gemini-example"] += 1
        elif "_goethe_example_edge_" in audio:
            sources["edge-example"] += 1
        elif "googletts" in audio:
            sources["googletts"] += 1
        elif "yandex" in audio:
            sources["yandex"] += 1
        elif audio:
            sources["other"] += 1
        else:
            sources["blank"] += 1
    print(json.dumps({
        "notes": len(records), "cards": sum(len(r["cards"]) for r in records.values()),
        "occurrences": len(examples), "unique": len(unique_ids),
        "levels": {level: {
            "notes": sum(record["fields"]["CEFR"] == level for record in records.values()),
            "cards": sum(len(record["cards"]) for record in records.values() if record["fields"]["CEFR"] == level),
            "occurrences": occurrences_by_level[level],
        } for level in scope.LEVELS},
        "sources": sources,
    }, ensure_ascii=False, indent=2))


async def command_audit_repetitions(args: argparse.Namespace) -> None:
    manifest = word_audio.load_json(MANIFEST_PATH, None)
    if not manifest:
        raise ExampleAudioError("example-audio manifest is missing")
    validate_manifest(manifest)
    selected = repetition_outlier_ids(
        manifest, percentile=args.percentile
    )
    report = word_audio.load_json(REPETITION_AUDIT_PATH, {
        "schema_version": 1,
        "percentile": args.percentile,
        "items": {},
    })
    if (
        not isinstance(report, dict)
        or report.get("schema_version") != 1
        or float(report.get("percentile", -1)) != args.percentile
        or not isinstance(report.get("items"), dict)
    ):
        report = {
            "schema_version": 1,
            "percentile": args.percentile,
            "items": {},
        }
    report.update({
        "created_utc": report.get("created_utc") or now_utc(),
        "updated_utc": now_utc(),
        "selected_audio_ids": selected,
    })
    pending = [
        audio_id
        for audio_id in selected
        if (
            report["items"].get(audio_id, {}).get("sha256")
            != manifest["unique"][audio_id].get("sha256")
            or report["items"].get(audio_id, {}).get("status")
            not in {"exact", "mismatch"}
        )
    ]
    print(json.dumps({
        "selected": len(selected),
        "pending": len(pending),
        "percentile": args.percentile,
    }))
    word_audio.atomic_json(REPETITION_AUDIT_PATH, report)
    semaphore = asyncio.Semaphore(
        CONCURRENCY_PER_KEY * len(gemini_tts._api_keys())
    )

    async def inspect(audio_id: str) -> tuple[str, dict[str, Any]]:
        item = manifest["unique"][audio_id]
        async with semaphore:
            transcript = await gemini_tts.transcribe_strict_mp3(
                Path(item["path"])
            )
        status = (
            "exact"
            if gemini_tts._normalized_spoken_text(transcript)
            == gemini_tts._normalized_spoken_text(item["spoken_text"])
            else "mismatch"
        )
        return audio_id, {
            "audio_id": audio_id,
            "sha256": item["sha256"],
            "spoken_text": item["spoken_text"],
            "voice": item["voice"],
            "duration_seconds": item["duration_seconds"],
            "strict_transcript": transcript,
            "status": status,
            "audited_utc": now_utc(),
        }

    failures: list[Exception] = []
    completed = 0
    tasks = [asyncio.create_task(inspect(audio_id)) for audio_id in pending]
    for future in asyncio.as_completed(tasks):
        try:
            audio_id, result = await future
        except Exception as exc:
            failures.append(exc)
            continue
        report["items"][audio_id] = result
        completed += 1
        if completed % 10 == 0 or completed == len(pending):
            report["updated_utc"] = now_utc()
            word_audio.atomic_json(REPETITION_AUDIT_PATH, report)
            print(f"strict-asr {completed}/{len(pending)}")
    counts = Counter(
        item.get("status") for item in report["items"].values()
        if item.get("audio_id") in selected
    )
    report["counts"] = dict(counts)
    report["updated_utc"] = now_utc()
    word_audio.atomic_json(REPETITION_AUDIT_PATH, report)
    if failures:
        raise ExampleAudioError(
            f"repetition audit checkpointed {completed} items with "
            f"{len(failures)} failures: {failures[0]}"
        )
    print(json.dumps({
        "selected": len(selected),
        "counts": dict(counts),
        "report": str(REPETITION_AUDIT_PATH),
    }, ensure_ascii=False, indent=2))


async def command_prepare(args: argparse.Namespace) -> None:
    records = live_records()
    previous = word_audio.load_json(MANIFEST_PATH, None)
    manifest = build_manifest(records, previous)
    word_audio.atomic_json(MANIFEST_PATH, manifest)
    await generate_scope(manifest, args.scope)


def require_full_ready(manifest: dict[str, Any]) -> None:
    validate_manifest(manifest)
    bad = [key for key, item in manifest["unique"].items() if not validate_cached(item)]
    if bad:
        raise ExampleAudioError(f"audio preparation incomplete: {len(bad)} missing or invalid")


def command_snapshot(_: argparse.Namespace) -> None:
    manifest = word_audio.load_json(MANIFEST_PATH, None)
    if not manifest:
        raise ExampleAudioError("prepared manifest missing or incompatible")
    validate_manifest(manifest)
    if manifest.get("config") != GEMINI_CONFIG:
        raise ExampleAudioError("prepared manifest TTS config is incompatible")
    require_full_ready(manifest)
    records = live_records()
    if set(map(int, manifest["notes"])) != set(records):
        raise ExampleAudioError("live note ID set changed")
    for note_id, record in records.items():
        if manifest["notes"][str(note_id)]["source_signature"] != example_signature(record["fields"]):
            raise ExampleAudioError(f"example text changed after preparation: {note_id}")
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{time.time_ns() % 1_000_000_000:09d}"
    backup = STATE / f"Goethe_Institute_pre_example_audio_{stamp}.apkg"
    if backup.exists():
        raise ExampleAudioError(f"backup destination already exists: {backup}")
    try:
        result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.as_posix(), includeSched=True)
    except gw.MigrationError as exc:
        if "timed out" not in str(exc).casefold() and "timeout" not in str(exc).casefold():
            raise
        result = True
    if not result or not apkg.wait_for_valid_apkg(backup):
        raise ExampleAudioError("Anki APKG export failed")
    cards = [card for record in records.values() for card in record["cards"]]
    reviews = word_audio.all_reviews([int(card["cardId"]) for card in cards])
    snapshot = {
        "schema_version": 1, "created_utc": now_utc(), "backup": str(backup),
        "backup_sha256": apkg.hash_file(backup),
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
    validate_manifest(manifest)
    if snapshot.get("manifest_sha256") != word_audio.duden.hash_file(MANIFEST_PATH):
        raise ExampleAudioError("manifest changed after snapshot")
    require_full_ready(manifest)
    backup = Path(str(snapshot.get("backup", "")))
    if not apkg.valid_apkg(backup) or snapshot.get("backup_sha256") != apkg.hash_file(backup):
        raise ExampleAudioError("scheduled APKG backup is missing, corrupt, or changed")
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
            if name in EXTERNALLY_OWNED_FIELDS:
                continue
            actual = record["fields"].get(name, "")
            if name in AUDIO_FIELDS and any(
                audio_field_equivalent(actual, candidate)
                for candidate in (value, expected[name])
            ):
                continue
            if actual != value:
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
    retrieved = gw.anki("retrieveMediaFile", filename=item["media_name"])
    if not retrieved or hashlib.sha256(base64.b64decode(retrieved)).hexdigest() != item["sha256"]:
        raise ExampleAudioError(f"Anki media verification failed: {item['media_name']}")


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
    if not args.dry_run and args.confirmation != APPLY_CONFIRMATION:
        raise ExampleAudioError(f"confirmation must equal {APPLY_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    verify_baseline(records, manifest, snapshot)
    note_ids = selected_note_ids(manifest, args.scope)
    values = {}
    for note_id in note_ids:
        expected = expected_audio_fields(note_id, manifest, snapshot["notes"][str(note_id)]["fields"])
        if any(
            not audio_field_equivalent(
                records[note_id]["fields"].get(name, ""), value
            )
            for name, value in expected.items()
        ):
            values[note_id] = expected
    print(json.dumps({
        "scope": args.scope, "selected_notes": len(note_ids),
        "changed_notes": len(values), "dry_run": args.dry_run,
    }, indent=2))
    if args.dry_run:
        return
    audio_ids = {
        occurrence["audio_id"] for note_id in note_ids
        for occurrence in manifest["notes"][str(note_id)]["occurrences"]
    }
    for number, audio_id in enumerate(sorted(audio_ids), 1):
        ensure_media(manifest["unique"][audio_id])
        if number % 100 == 0 or number == len(audio_ids):
            print(f"media {number}/{len(audio_ids)}")
    try:
        update_notes(values)
    except Exception:
        update_notes({note_id: {name: snapshot["notes"][str(note_id)]["fields"][name] for name in AUDIO_FIELDS}
                      for note_id in values})
        raise


def verify_review_progress(
    cards: list[dict[str, Any]],
    reviews: dict[str, list[dict[str, Any]]],
    snapshot: dict[str, Any],
) -> list[int]:
    current_cards = {
        str(card["cardId"]): word_audio.schedule_projection(card)
        for card in cards
    }
    expected_cards = snapshot["cards"]
    if set(current_cards) != set(expected_cards):
        raise ExampleAudioError("card ID set changed")
    try:
        appended = word_audio.appended_review_cards(
            snapshot["reviews"],
            reviews,
        )
    except word_audio.WordAudioError as exc:
        raise ExampleAudioError(str(exc)) from exc
    card_notes = {
        str(card["cardId"]): int(card["note"])
        for card in cards
    }
    changed_cards = {
        card_id
        for card_id in current_cards
        if current_cards[card_id] != expected_cards[card_id]
    }
    changed_notes = {card_notes[card_id] for card_id in changed_cards}
    reviewed_notes = {card_notes[card_id] for card_id in appended}
    if changed_notes - reviewed_notes:
        raise ExampleAudioError("card IDs or scheduling changed")
    return sorted(changed_notes)


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
            if name in EXTERNALLY_OWNED_FIELDS:
                continue
            expected = expected_audio[name] if name in AUDIO_FIELDS and note_id in selected else value
            actual = record["fields"].get(name, "")
            matches = (
                audio_field_equivalent(actual, expected)
                if name in AUDIO_FIELDS
                else actual == expected
            )
            if not matches:
                raise ExampleAudioError(f"field mismatch: note={note_id} field={name}")
    cards = [card for record in records.values() for card in record["cards"]]
    reviews = word_audio.all_reviews([int(card["cardId"]) for card in cards])
    concurrent_review_notes = verify_review_progress(
        cards,
        reviews,
        snapshot,
    )
    if word_audio.model_snapshot() != snapshot["model"]:
        raise ExampleAudioError("model fields/templates/styling changed")
    for note_id in selected:
        for occurrence in manifest["notes"][str(note_id)]["occurrences"]:
            item = manifest["unique"][occurrence["audio_id"]]
            media = gw.anki("retrieveMediaFile", filename=item["media_name"])
            if not media or hashlib.sha256(base64.b64decode(media)).hexdigest() != item["sha256"]:
                raise ExampleAudioError(f"missing or corrupt Anki media: {item['media_name']}")
    return {"scope": scope, "baseline": baseline, "notes": len(records), "cards": len(cards),
            "verified_notes": len(selected),
            "concurrent_review_notes": concurrent_review_notes}


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
        if any(
            not audio_field_equivalent(
                records[note_id]["fields"][name],
                snapshot["notes"][str(note_id)]["fields"][name],
            )
            for name in AUDIO_FIELDS
        )
    }
    update_notes(values)
    print(json.dumps(verify_state("full", baseline=True), indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=command_audit)
    repetitions = sub.add_parser("audit-repetitions")
    repetitions.add_argument("--percentile", type=float, default=95.0)
    repetitions.set_defaults(func=command_audit_repetitions)
    for name in ("prepare", "resume"):
        prepare = sub.add_parser(name)
        prepare.add_argument("--scope", choices=("pilot", "full"), default="full")
        prepare.set_defaults(func=command_prepare)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    apply = sub.add_parser("apply")
    apply.add_argument("--scope", choices=("pilot", "full"), default="full")
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--confirmation")
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify")
    verify.add_argument("--scope", choices=("pilot", "full"), default="full")
    verify.add_argument("--baseline", action="store_true")
    verify.set_defaults(func=command_verify)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


async def run_async_command(command: Any) -> None:
    try:
        await command
    finally:
        await gemini_tts.close_client()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        if asyncio.iscoroutine(result):
            asyncio.run(run_async_command(result))
    except (ExampleAudioError, word_audio.WordAudioError, gw.MigrationError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
