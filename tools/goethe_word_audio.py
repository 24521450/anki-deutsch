"""Prepare and safely wire Goethe A1-B1 word audio into Anki.

Source precedence is validated Duden (A1 before A2 before B1), newly resolved exact
Duden audio, exact Wikimedia Commons pronunciation, Wiktionary pronunciation,
then Edge TTS.  The only
Anki note field this tool writes is ``WordAudio``.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
from lxml import html as lxml_html

import download_duden_a1_audio as duden
import goethe_completion as completion
import goethe_apkg as apkg
import goethe_scope as scope
import goethe_werkstatt_migrate as gw


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_word_audio"
WORK_AUDIO = ROOT / "audio" / "goethe_word_audio"
DUDEN_EXTRA_DIR = WORK_AUDIO / "duden"
EDGE_DIR = WORK_AUDIO / "edge"
COMMONS_DIR = WORK_AUDIO / "commons"
WIKTIONARY_DIR = WORK_AUDIO / "wiktionary"
PROTECTED_DIR = ROOT / "audio" / "protected"
MANIFEST_PATH = STATE / "manifest.json"
DUDEN_EXTRA_INDEX = STATE / "duden_extra.json"
DUDEN_RESCAN_REPORT = STATE / "duden_fallback_rescan.json"
EDGE_INDEX = STATE / "edge.json"
COMMONS_INDEX = STATE / "commons.json"
WIKTIONARY_INDEX = STATE / "wiktionary.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
OVERRIDES_PATH = ROOT / "review" / "goethe_word_audio_overrides.json"
COMMONS_ATTRIBUTION_PATH = ROOT / "review" / "wikimedia_commons_audio_attribution.json"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
LEVEL_DECKS = scope.LEVEL_DECK
MANIFEST_SCHEMA_VERSION = 6
DUDEN_RESOLVER_VERSION = 2
APPLY_CONFIRMATION = "APPLY_GOETHE_WORD_AUDIO"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_WORD_AUDIO"
EDGE_CONFIG = {
    "engine": "edge-tts",
    "engine_version": "7.2.8",
    "voice": "de-DE-KatjaNeural",
    "rate": "+0%",
    "volume": "+0%",
    "pitch": "+0Hz",
    "config_version": 1,
}
COMMONS_CONFIG = {
    "api": "https://commons.wikimedia.org/w/api.php",
    "user_agent": "anki-deutsch-word-audio/1.0 (https://github.com/24521450/anki-deutsch)",
    "query_interval_seconds": 2.0,
    "download_interval_seconds": 1.0,
    "maxlag": 5,
    "licenses": ["CC0", "Public domain", "CC BY", "CC BY-SA"],
    "human_standard_german_only": True,
    "config_version": 1,
}
WIKTIONARY_CONFIG = {
    "api": "https://en.wiktionary.org/w/api.php",
    "user_agent": "anki-deutsch-word-audio/1.0 (https://github.com/24521450/anki-deutsch)",
    "language_section": "German",
    "config_version": 1,
}
SOURCE_FIELDS = ("Lemma", "POS", "Gender", "AcceptedAnswersDE", "SourceID", "SourceRefs", "CEFR")
PILOT_SIZE = 12
DUDEN_REQUIRED_FIELDS = frozenset({
    "row", "word", "pos", "gender", "output_filename", "source", "status",
})
DUDEN_STABLE_STATUSES = frozenset({"ok", "unresolved"})


class WordAudioError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip())


def _dedupe_spoken_atoms(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = clean(value)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def canonical_spoken_identity(value: Any) -> str:
    """Return the literal pronunciation represented by bound notation.

    Boundary hyphens describe how a Goethe headword combines with another
    word; they are not spoken.  Repeated alternatives such as ``weg/weg-``
    likewise represent one pronunciation, not two recordings.
    """
    text = clean(value)
    if not text:
        return ""
    atoms = [
        clean(atom).strip("-")
        for atom in re.split(r"\s*(?:/|,)\s*", text)
    ]
    return ", ".join(_dedupe_spoken_atoms(atoms))


def bound_spoken_identity(value: Any) -> str | None:
    """Return an authoritative identity when notation only repeats one stem."""
    text = clean(value)
    if not re.search(r"(?:^|[/\s])-[^\s/]|[^\s/]-(?:$|[/\s])", text):
        return None
    atoms = [
        clean(atom).strip("-")
        for atom in re.split(r"\s*/\s*", text)
    ]
    unique = _dedupe_spoken_atoms(atoms)
    if len({atom.casefold() for atom in unique}) == 1:
        return unique[0]
    if text.endswith("-") and "/" not in text:
        return canonical_spoken_identity(text)
    return None


def console_text(value: Any, encoding: str | None = None) -> str:
    """Return progress text that cannot crash on a legacy Windows console."""
    target = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(target, errors="backslashreplace").decode(target)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.replace(name, path)
    except Exception:
        if os.path.exists(name):
            os.unlink(name)
        raise


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def field(note: dict[str, Any], name: str) -> str:
    return note.get("fields", {}).get(name, {}).get("value", "")


def field_values(note: dict[str, Any]) -> dict[str, str]:
    return {name: item.get("value", "") for name, item in note.get("fields", {}).items()}


def require_anki() -> None:
    if gw.anki("version") != 6:
        raise WordAudioError("unexpected AnkiConnect API version")


def live_records() -> dict[int, dict[str, Any]]:
    require_anki()
    note_ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(note_ids):
        notes.extend(gw.anki("notesInfo", notes=batch))
    cards = gw.all_card_info()
    by_note: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        by_note[int(card["note"])].append(card)
    records: dict[int, dict[str, Any]] = {}
    for note in notes:
        note_id = int(note["noteId"])
        level = field(note, "CEFR")
        if level not in LEVEL_DECKS:
            raise WordAudioError(f"Goethe note has unsupported CEFR: {note_id}={level!r}")
        note_cards = by_note.get(note_id, [])
        if not note_cards:
            raise WordAudioError(f"target note has no A1-B1 cards: {note_id}")
        if any(card["deckName"] != LEVEL_DECKS[level] for card in note_cards):
            raise WordAudioError(f"target note is in unexpected deck: {note_id}")
        records[note_id] = {
            "note_id": note_id,
            "model": note["modelName"],
            "fields": field_values(note),
            "tags": sorted(note.get("tags", [])),
            "cards": sorted(note_cards, key=lambda item: item["cardId"]),
        }
    if any(len(item["cards"]) != 2 for item in records.values()):
        raise WordAudioError("every target note must have exactly two cards")
    note_counts = Counter(item["fields"]["CEFR"] for item in records.values())
    card_counts = Counter(
        item["fields"]["CEFR"] for item in records.values() for _ in item["cards"]
    )
    if dict(note_counts) != scope.EXPECTED_NOTES_BY_LEVEL:
        raise WordAudioError(f"Goethe note baseline drift: {dict(note_counts)}")
    if dict(card_counts) != scope.EXPECTED_CARDS_BY_LEVEL:
        raise WordAudioError(f"Goethe card baseline drift: {dict(card_counts)}")
    return records


def split_refs(value: str) -> list[str]:
    return [clean(item) for item in value.split("|") if clean(item)]


def source_signature(fields: dict[str, str]) -> str:
    return canonical_hash({name: fields.get(name, "") for name in SOURCE_FIELDS})


def compatible_gender(source: str, target: str) -> bool:
    source, target = clean(source), clean(target)
    return not source or not target or source == target


def note_variants(fields: dict[str, str]) -> set[str]:
    return set(completion.source_variants(fields.get("Lemma", "")))


def source_matches(fields: dict[str, str], item: dict[str, Any], variants: set[str] | None = None) -> bool:
    word = completion.lemma_key(clean(item.get("word", "")))
    target = completion.lemma_key(clean(fields.get("Lemma", "")))
    if word in {"der", "die", "das"} and target not in {"der", "die", "das"}:
        return False
    expected_spoken = bound_spoken_identity(fields.get("Lemma", ""))
    if (
        expected_spoken is not None
        and canonical_spoken_identity(item.get("word", "")).casefold()
        != expected_spoken.casefold()
    ):
        return False
    if word not in (variants if variants is not None else note_variants(fields)):
        return False
    return completion.compatible_pos(clean(item.get("pos", "")), fields.get("POS", "")) and compatible_gender(
        clean(item.get("gender", "")), fields.get("Gender", "")
    )


def validate_audio(path: Path, sha256: str | None = None, size: int | None = None) -> tuple[int, str]:
    if not path.exists():
        raise WordAudioError(f"audio file missing: {path}")
    actual_size = path.stat().st_size
    if size is not None and actual_size != int(size):
        raise WordAudioError(f"audio size mismatch: {path}")
    with path.open("rb") as handle:
        duden.validate_mp3_bytes(handle.read(16))
    actual_hash = duden.hash_file(path)
    if sha256 and actual_hash != sha256:
        raise WordAudioError(f"audio hash mismatch: {path}")
    return actual_size, actual_hash


def validate_duden_rows(level: str, rows: list[dict[str, Any]]) -> None:
    expected = scope.DUDEN_ROWS[level]
    if len(rows) != expected:
        raise WordAudioError(f"{level} Duden manifest row count mismatch: {len(rows)} != {expected}")
    for expected_row, item in enumerate(rows, 1):
        if not isinstance(item, dict) or not DUDEN_REQUIRED_FIELDS.issubset(item):
            raise WordAudioError(f"{level} Duden manifest row {expected_row} has an incompatible schema")
        if item.get("row") != expected_row:
            raise WordAudioError(f"{level} Duden manifest row sequence mismatch at {expected_row}")
        if not all(isinstance(item.get(name), str) for name in ("word", "pos", "gender", "output_filename", "source", "status")):
            raise WordAudioError(f"{level} Duden manifest row {expected_row} has invalid field types")
        if not clean(item["word"]) or item["source"].casefold() != "duden":
            raise WordAudioError(f"{level} Duden manifest row {expected_row} is not a Duden source row")
        if item["status"] not in DUDEN_STABLE_STATUSES:
            raise WordAudioError(f"{level} Duden manifest row {expected_row} has invalid status")
        if not item["output_filename"].endswith(".mp3"):
            raise WordAudioError(f"{level} Duden manifest row {expected_row} has invalid output filename")
        if item["status"] == "ok":
            if not isinstance(item.get("size"), int) or item["size"] <= 0:
                raise WordAudioError(f"{level} Duden manifest row {expected_row} has invalid audio size")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or "")):
                raise WordAudioError(f"{level} Duden manifest row {expected_row} has invalid audio hash")


def load_level_duden_rows(level: str, root: Path) -> list[dict[str, Any]]:
    candidates = [
        root / "words_manifest.jsonl",
        *sorted(
            (root / "duden_checkpoints").glob("*/words_manifest.jsonl"),
            reverse=True,
        ),
    ]
    errors: list[WordAudioError] = []
    for path in candidates:
        rows = duden.load_existing_manifest_rows(path)
        try:
            validate_duden_rows(level, rows)
        except WordAudioError as exc:
            errors.append(exc)
            continue
        return rows
    if errors:
        raise errors[0]
    raise WordAudioError(f"{level} Duden manifest is missing")


def load_duden_catalog() -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_ref: dict[tuple[str, int], dict[str, Any]] = {}
    ok_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for level in scope.LEVELS:
        root = ROOT / "audio" / level.lower()
        rows = load_level_duden_rows(level, root)
        for item in rows:
            row = dict(item)
            row.update({"level": level, "path": str(root / "words" / item["output_filename"])})
            by_ref[(level, int(item["row"]))] = row
            if item.get("status") == "ok":
                validate_audio(Path(row["path"]), item.get("sha256"), item.get("size"))
                ok_index[completion.lemma_key(clean(row["word"]))].append(row)
    return by_ref, ok_index


MAIN_RE = re.compile(r"^(A1|A2|B1)-MAIN-(\d{4})$")


def duden_sort_key(item: dict[str, Any]) -> tuple[int, int]:
    return scope.LEVEL_RANK[item["level"]], int(item["row"])


def select_local_duden(fields: dict[str, str], by_ref: dict[tuple[str, int], dict[str, Any]], ok_index: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    variants = note_variants(fields)
    direct: list[dict[str, Any]] = []
    for ref in split_refs(fields.get("SourceRefs", "")):
        match = MAIN_RE.match(ref)
        if not match:
            continue
        item = by_ref.get((match.group(1), int(match.group(2))))
        if item and item.get("status") == "ok" and source_matches(fields, item, variants):
            direct.append(item)
    if direct:
        return min(direct, key=duden_sort_key)
    candidates = list({
        (item["level"], int(item["row"])): item
        for variant in variants for item in ok_index.get(variant, []) if source_matches(fields, item, variants)
    }.values())
    if not candidates:
        return None
    candidates.sort(key=duden_sort_key)
    best_level = candidates[0]["level"]
    best = [item for item in candidates if item["level"] == best_level]
    hashes = {item.get("sha256") for item in best}
    if len(best) > 1 and len(hashes) > 1:
        return None
    return best[0]


def source_word(fields: dict[str, str], by_ref: dict[tuple[str, int], dict[str, Any]]) -> str:
    variants = note_variants(fields)
    choices: list[tuple[int, int, str]] = []
    for ref in split_refs(fields.get("SourceRefs", "")):
        match = MAIN_RE.match(ref)
        if match:
            item = by_ref.get((match.group(1), int(match.group(2))))
            if item and source_matches(fields, item, variants):
                choices.append((scope.LEVEL_RANK[match.group(1)], int(match.group(2)), clean(item["word"])))
    if choices:
        return sorted(choices)[0][2]
    return clean(fields.get("Lemma", ""))


def matched_main_rows(fields: dict[str, str], by_ref: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    variants = note_variants(fields)
    rows: list[dict[str, Any]] = []
    for ref in split_refs(fields.get("SourceRefs", "")):
        match = MAIN_RE.match(ref)
        if not match:
            continue
        item = by_ref.get((match.group(1), int(match.group(2))))
        if item and source_matches(fields, item, variants):
            rows.append(item)
    return rows


def load_override_policy() -> dict[str, Any]:
    data = load_json(OVERRIDES_PATH, {"schema_version": 1, "spoken_text": {}})
    if data.get("schema_version") not in {1, 2, 3}:
        raise WordAudioError("unsupported spoken-text override schema")
    return data


def load_overrides() -> dict[str, str]:
    data = load_override_policy()
    values = data.get("spoken_text", {})
    if not isinstance(values, dict):
        raise WordAudioError("spoken_text overrides must be an object")
    return {clean(key): clean(value) for key, value in values.items() if clean(value)}


def load_spoken_equivalences() -> dict[str, dict[str, Any]]:
    values = load_override_policy().get("spoken_equivalences", {})
    if not isinstance(values, dict):
        raise WordAudioError("spoken_equivalences must be an object")
    equivalences: dict[str, dict[str, Any]] = {}
    for raw_source_id, raw in values.items():
        source_id = clean(raw_source_id)
        if not source_id or not isinstance(raw, dict):
            raise WordAudioError(f"invalid spoken equivalence: {raw_source_id!r}")
        expected_lemma = clean(raw.get("expected_lemma", ""))
        spoken_texts = raw.get("spoken_texts")
        reason = clean(raw.get("reason", ""))
        if (
            not expected_lemma
            or not isinstance(spoken_texts, list)
            or not spoken_texts
            or any(not isinstance(value, str) or not clean(value) for value in spoken_texts)
            or not reason
        ):
            raise WordAudioError(f"incomplete spoken equivalence: {source_id}")
        equivalences[source_id] = {
            "expected_lemma": expected_lemma,
            "spoken_texts": list(dict.fromkeys(clean(value) for value in spoken_texts)),
            "reason": reason,
        }
    return equivalences


def load_protected_audio() -> dict[str, dict[str, Any]]:
    data = load_override_policy()
    schema = data.get("schema_version")
    values = data.get("protected_audio", {})
    if schema in {1, 2}:
        values = {
            source_ref: {
                **raw,
                "provider": "commons",
                "spoken_text": raw.get("expected_lemma", ""),
            }
            for source_ref, raw in data.get("provider_pins", {}).items()
        }
    if not isinstance(values, dict):
        raise WordAudioError("protected_audio must be an object")
    protected: dict[str, dict[str, Any]] = {}
    for source_id, raw in values.items():
        if not isinstance(raw, dict) or raw.get("provider") not in {"commons", "local"}:
            raise WordAudioError(f"unsupported protected audio: {source_id}")
        item = {
            str(key): clean(value) if isinstance(value, str) else value
            for key, value in raw.items()
        }
        required = ("expected_lemma", "spoken_text", "sha256", "reason")
        if any(not item.get(name) for name in required) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise WordAudioError(f"incomplete protected audio: {source_id}")
        if item["provider"] == "commons":
            if not item.get("title") or (
                schema == 3 and not re.fullmatch(r"[0-9a-f]{40}", item.get("original_sha1", ""))
            ):
                raise WordAudioError(f"incomplete protected audio: {source_id}")
        elif not isinstance(item.get("size"), int) or item["size"] <= 0:
            raise WordAudioError(f"incomplete protected audio: {source_id}")
        protected[clean(source_id)] = item
    return protected


def protected_audio_for(
    fields: dict[str, str], protected: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    refs = [clean(fields.get("SourceID", "")), *split_refs(fields.get("SourceRefs", ""))]
    matches = [(ref, protected[ref]) for ref in dict.fromkeys(refs) if ref in protected]
    if not matches:
        return None
    lemma = clean(fields.get("Lemma", ""))
    for source_ref, item in matches:
        if lemma != item["expected_lemma"]:
            raise WordAudioError(f"protected audio lemma mismatch: {source_ref}")
    hashes = {item["sha256"] for _, item in matches}
    if len(hashes) > 1:
        raise WordAudioError(f"conflicting protected audio for {fields.get('Lemma')!r}")
    source_ref, item = matches[0]
    return {**item, "source_ref": source_ref}


def validate_protected_commons(protected: dict[str, Any], candidate: dict[str, Any]) -> None:
    if candidate.get("title") != protected["title"]:
        raise WordAudioError("protected Commons title changed")
    if protected.get("original_sha1") and candidate.get("original_sha1") != protected["original_sha1"]:
        raise WordAudioError("protected Commons source revision changed")
    if candidate.get("sha256") and candidate["sha256"] != protected["sha256"]:
        raise WordAudioError("protected Commons MP3 changed")


def local_protected_entry(
    fields: dict[str, str], content: bytes, *, reason: str
) -> dict[str, Any]:
    lemma = clean(fields.get("Lemma", ""))
    reason = clean(reason)
    if not lemma or not clean(fields.get("SourceID", "")) or not reason:
        raise WordAudioError("local protected audio requires lemma, SourceID, and reason")
    duden.validate_mp3_bytes(content[:16])
    return {
        "provider": "local",
        "expected_lemma": lemma,
        "spoken_text": lemma,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "reason": reason,
    }


UNSAFE_SPOKEN_RE = re.compile(r"[()/;]|\d|(^|\s)[A-Za-zÄÖÜäöüß]\.|,$")


def spoken_text(fields: dict[str, str], raw: str, overrides: dict[str, str]) -> str:
    physical_identity = bound_spoken_identity(fields.get("Lemma", ""))
    if physical_identity is not None:
        return physical_identity
    refs = split_refs(fields.get("SourceRefs", ""))
    for key in refs + [fields.get("Lemma", ""), raw]:
        if clean(key) in overrides:
            return canonical_spoken_identity(overrides[clean(key)])
    value = clean(raw)
    if value.endswith("-") or UNSAFE_SPOKEN_RE.search(value):
        raise WordAudioError(f"missing spoken-text override for {fields.get('Lemma')!r}")
    if not value:
        raise WordAudioError("empty spoken text")
    return canonical_spoken_identity(value)


def media_name(source: str, sha256: str) -> str:
    return f"_goethe_word_{source}_{sha256}.mp3"


def assignment(
    source: str,
    path: Path,
    *,
    detail: dict[str, Any],
    lemma_identity: str = "",
    spoken_text: str = "",
) -> dict[str, Any]:
    size, sha256 = validate_audio(path, detail.get("sha256"), detail.get("size"))
    return {
        "source": source,
        "path": str(path),
        "size": size,
        "sha256": sha256,
        "media_name": media_name("duden" if source.startswith("duden") else source, sha256),
        "lemma_identity": clean(lemma_identity),
        "spoken_text": clean(spoken_text),
        "detail": detail,
    }


def preserve_matching_media_name(item: dict[str, Any], audio: dict[str, Any]) -> dict[str, Any]:
    match = re.fullmatch(r"\[sound:([^\[\]]+)\]", clean(item.get("old_word_audio", "")))
    if match and match.group(1).casefold().endswith(f"_{audio['sha256']}.mp3"):
        audio["media_name"] = match.group(1)
    return audio


def validate_assignment_identity(
    fields: dict[str, str],
    item: dict[str, Any],
) -> None:
    assignment_item = item.get("assignment") or {}
    expected_lemma = clean(fields.get("Lemma", ""))
    assigned_lemma = clean(
        assignment_item.get("lemma_identity") or item.get("lemma_identity")
    )
    if assigned_lemma != expected_lemma:
        raise WordAudioError(
            f"audio lemma identity mismatch: {assigned_lemma!r} != {expected_lemma!r}"
        )
    expected_spoken = bound_spoken_identity(expected_lemma)
    assigned_spoken = clean(
        assignment_item.get("spoken_text") or item.get("spoken_text")
    )
    if (
        expected_spoken is not None
        and canonical_spoken_identity(assigned_spoken).casefold()
        != expected_spoken.casefold()
    ):
        raise WordAudioError(
            f"audio spoken identity mismatch: {assigned_spoken!r} != {expected_spoken!r}"
        )


def level_counts(records: dict[int, dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        level: {
            "notes": sum(record["fields"].get("CEFR") == level for record in records.values()),
            "cards": sum(
                len(record["cards"])
                for record in records.values()
                if record["fields"].get("CEFR") == level
            ),
        }
        for level in scope.LEVELS
    }


def expected_level_counts() -> dict[str, dict[str, int]]:
    return {
        level: {
            "notes": scope.EXPECTED_NOTES_BY_LEVEL[level],
            "cards": scope.EXPECTED_CARDS_BY_LEVEL[level],
        }
        for level in scope.LEVELS
    }


def validate_manifest(manifest: dict[str, Any], *, require_prepared: bool = False) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise WordAudioError("word-audio manifest schema is stale; rebuild it")
    if manifest.get("levels") != list(scope.LEVELS):
        raise WordAudioError("word-audio manifest level set is stale; rebuild it")
    if manifest.get("duden_rows") != scope.DUDEN_ROWS:
        raise WordAudioError("word-audio Duden catalog contract is stale; rebuild it")
    if manifest.get("duden_statuses") != sorted(DUDEN_STABLE_STATUSES):
        raise WordAudioError("word-audio Duden status contract is stale; rebuild it")
    if manifest.get("edge_config") != EDGE_CONFIG or manifest.get("commons_config") != COMMONS_CONFIG:
        raise WordAudioError("word-audio generator config is stale; rebuild it")
    if manifest.get("wiktionary_config") != WIKTIONARY_CONFIG:
        raise WordAudioError("word-audio Wiktionary config is stale; rebuild it")
    if manifest.get("source_order") != ["duden_local", "duden_extra", "commons", "wiktionary", "edge"]:
        raise WordAudioError("word-audio source precedence is stale; rebuild it")
    if manifest.get("duden_level_order") != list(scope.LEVELS):
        raise WordAudioError("word-audio Duden level precedence is stale; rebuild it")
    if manifest.get("note_count") != scope.EXPECTED_NOTES or manifest.get("card_count") != scope.EXPECTED_CARDS:
        raise WordAudioError("word-audio manifest corpus totals are stale; rebuild it")
    if manifest.get("level_counts") != expected_level_counts():
        raise WordAudioError("word-audio manifest per-level counts are stale; rebuild it")
    notes = manifest.get("notes")
    if not isinstance(notes, dict) or len(notes) != scope.EXPECTED_NOTES:
        raise WordAudioError("word-audio manifest note set is incomplete")
    actual = Counter(item.get("level") for item in notes.values() if isinstance(item, dict))
    if dict(actual) != scope.EXPECTED_NOTES_BY_LEVEL:
        raise WordAudioError(f"word-audio manifest note levels are invalid: {dict(actual)}")
    if require_prepared:
        if not manifest.get("prepared_utc"):
            raise WordAudioError("word-audio manifest is not prepared")
        prepared_scope = manifest.get("prepared_scope", "full")
        if prepared_scope == "protected":
            required = [item for item in notes.values() if item.get("protected_audio")]
        elif prepared_scope == "targeted":
            prepared_ids = set(map(int, manifest.get("prepared_note_ids", [])))
            if not prepared_ids:
                raise WordAudioError("targeted word-audio manifest has no note IDs")
            required = [
                item for item in notes.values()
                if int(item.get("note_id", 0)) in prepared_ids
            ]
        else:
            required = list(notes.values())
        missing = [item.get("note_id") for item in required if not item.get("assignment")]
        if missing:
            label = "protected notes" if prepared_scope == "protected" else "notes"
            raise WordAudioError(f"word-audio manifest has unassigned {label}: {missing[:5]}")


def build_audit() -> dict[str, Any]:
    records = live_records()
    by_ref, ok_index = load_duden_catalog()
    overrides = load_overrides()
    protected_audio = load_protected_audio()
    notes: dict[str, Any] = {}
    missing_overrides: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for note_id, record in sorted(records.items()):
        fields = record["fields"]
        protected = protected_audio_for(fields, protected_audio)
        item = None if protected else select_local_duden(fields, by_ref, ok_index)
        note_item: dict[str, Any] = {
            "note_id": note_id,
            "card_ids": [int(card["cardId"]) for card in record["cards"]],
            "level": fields["CEFR"],
            "lemma": fields["Lemma"],
            "pos": fields.get("POS", ""),
            "gender": fields.get("Gender", ""),
            "source_refs": split_refs(fields.get("SourceRefs", "")),
            "source_signature": source_signature(fields),
            "old_word_audio": fields.get("WordAudio", ""),
            "lemma_identity": clean(fields["Lemma"]),
        }
        if protected:
            note_item["protected_audio"] = protected
        raw = (
            protected["spoken_text"]
            if protected
            else item["word"] if item
            else source_word(fields, by_ref)
        )
        try:
            text = spoken_text(fields, raw, overrides)
            note_item["spoken_text"] = text
            if item:
                note_item["assignment"] = assignment(
                    "duden_local",
                    Path(item["path"]),
                    detail=item,
                    lemma_identity=note_item["lemma_identity"],
                    spoken_text=text,
                )
                counts["duden_local"] += 1
            elif protected and protected["provider"] == "local":
                path = PROTECTED_DIR / f"{protected['sha256']}.mp3"
                note_item["assignment"] = assignment(
                    "protected",
                    path,
                    detail=protected,
                    lemma_identity=note_item["lemma_identity"],
                    spoken_text=text,
                )
                counts["protected"] += 1
            else:
                note_item.update({"spoken_text": text, "request_key": canonical_hash({
                    "text": text, "pos": fields.get("POS", ""), "gender": fields.get("Gender", "")
                }), "skip_duden": bool(protected)})
                counts["needs_prepare"] += 1
                if protected:
                    counts["protected_audio"] += 1
        except WordAudioError as exc:
            note_item["error"] = str(exc)
            missing_overrides.append({
                "note_id": note_id, "lemma": fields["Lemma"], "source_refs": note_item["source_refs"], "raw": raw,
            })
            counts["missing_override"] += 1
        notes[str(note_id)] = note_item
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_utc": now_utc(),
        "levels": list(scope.LEVELS),
        "level_counts": level_counts(records),
        "duden_rows": dict(scope.DUDEN_ROWS),
        "duden_statuses": sorted(DUDEN_STABLE_STATUSES),
        "edge_config": EDGE_CONFIG,
        "commons_config": COMMONS_CONFIG,
        "wiktionary_config": WIKTIONARY_CONFIG,
        "source_order": ["duden_local", "duden_extra", "commons", "wiktionary", "edge"],
        "duden_level_order": list(scope.LEVELS),
        "note_count": len(notes),
        "card_count": sum(len(record["cards"]) for record in records.values()),
        "counts": dict(counts),
        "missing_overrides": missing_overrides,
        "notes": notes,
    }
    validate_manifest(manifest)
    manifest["live_audio_audit"] = live_assignment_mismatches(records, manifest)
    STATE.mkdir(parents=True, exist_ok=True)
    atomic_json(MANIFEST_PATH, manifest)
    return manifest


def request_groups(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in manifest["notes"].values():
        if item.get("assignment") or item.get("error"):
            continue
        key = item["request_key"]
        group = groups.setdefault(key, {
            "request_key": key, "spoken_text": item["spoken_text"], "pos": item["pos"], "gender": item["gender"],
            "note_ids": [], "skip_duden": True, "required_providers": set(),
        })
        group["note_ids"].append(item["note_id"])
        group["skip_duden"] = group["skip_duden"] and bool(item.get("skip_duden"))
        if item.get("protected_audio"):
            group["required_providers"].add(item["protected_audio"]["provider"])
            current = group.get("protected_audio")
            if current and current["sha256"] != item["protected_audio"]["sha256"]:
                raise WordAudioError(f"conflicting protected audio for {group['spoken_text']!r}")
            group["protected_audio"] = item["protected_audio"]
    for group in groups.values():
        providers = group.pop("required_providers")
        if len(providers) > 1:
            raise WordAudioError(f"conflicting protected audio for {group['spoken_text']!r}")
        group["required_provider"] = next(iter(providers), None)
    return groups


def reuse_duden_cache(cached: dict[str, Any] | None, *, refresh_negative: bool) -> bool:
    if not cached:
        return False
    if cached.get("status") == "ok":
        return True
    return (
        not refresh_negative
        and cached.get("status") in {"unresolved", "ambiguous"}
        and cached.get("resolver_version") == DUDEN_RESOLVER_VERSION
    )


async def prepare_duden(
    groups: dict[str, dict[str, Any]], *, refresh_negative: bool = False
) -> dict[str, Any]:
    index = load_json(DUDEN_EXTRA_INDEX, {"schema_version": 2, "items": {}})
    index["schema_version"] = 2
    index["resolver_version"] = DUDEN_RESOLVER_VERSION
    cooldown_raw = index.get("cooldown_until")
    if cooldown_raw:
        cooldown_until = datetime.fromisoformat(cooldown_raw)
        if datetime.now(timezone.utc) < cooldown_until:
            raise WordAudioError(f"Duden cooldown active until {cooldown_until.isoformat()}")
        index.pop("cooldown_until", None)
    items = index.setdefault("items", {})
    duden.PREFER_FIRST_EXACT_CANDIDATE = False
    # Deck-only/Wortgruppen lookup is a fresh crawl; be more conservative than
    # the source-list downloader to avoid triggering Duden's request guard.
    duden.PAGE_REQUEST_MIN_INTERVAL = 5.0
    duden.CDN_REQUEST_MIN_INTERVAL = 2.0
    DUDEN_EXTRA_DIR.mkdir(parents=True, exist_ok=True)
    pending: dict[str, tuple[int, dict[str, Any], duden.SourceRow]] = {}
    for number, (key, group) in enumerate(sorted(groups.items()), 1):
        if group.get("skip_duden"):
            items[key] = {
                "request_key": key, "spoken_text": group["spoken_text"],
                "status": "unresolved", "reason": "provider policy excludes Duden",
                "match_method": "provider-policy", "resolver_version": DUDEN_RESOLVER_VERSION,
                "updated_utc": now_utc(),
            }
            continue
        cached = items.get(key)
        if cached and cached.get("status") == "ok":
            try:
                validate_audio(Path(cached["path"]), cached.get("sha256"), cached.get("size"))
            except (KeyError, WordAudioError):
                cached = None
            else:
                continue
        if reuse_duden_cache(cached, refresh_negative=refresh_negative):
            continue
        pending[key] = (
            number,
            group,
            duden.SourceRow(number, group["spoken_text"], group["pos"], group["gender"], "", "", ""),
        )
    atomic_json(DUDEN_EXTRA_INDEX, index)
    if not pending:
        return index

    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        throttle = duden.RequestThrottle()
        try:
            lexeme_index = await duden.build_lexeme_index_for_rows(
                session, [entry[2] for entry in pending.values()], throttle=throttle
            )
        except duden.TechnicalError as exc:
            raise WordAudioError(f"Duden sitemap technical error: {exc}") from exc
        for progress, (key, (number, group, row)) in enumerate(sorted(pending.items()), 1):
            resolution, pages = await duden.resolve_exact_sitemap_row(
                session, row, lexeme_index, throttle=throttle
            )
            result = duden.resolution_to_row(resolution)
            result.update({
                "request_key": key, "spoken_text": group["spoken_text"],
                "resolver_version": DUDEN_RESOLVER_VERSION, "updated_utc": now_utc(),
                "candidate_pages": [{
                    "canonical_url": page.canonical_url, "headword": page.headword,
                    "wordart": page.wordart, "pos_labels": list(page.pos_labels),
                    "gender": page.h1_gender,
                    "audio": list(page.audio_candidates),
                } for page in pages],
            })
            if resolution.status == "ok" and resolution.duden_audio_url:
                target = DUDEN_EXTRA_DIR / f"{key}.mp3"
                try:
                    size, sha256, content_type, etag = await duden.download_audio(
                        session, resolution.duden_audio_url, target, throttle=throttle
                    )
                except Exception as exc:
                    result.update({"status": "technical_error", "reason": str(exc)})
                else:
                    result.update({"path": str(target), "size": size, "sha256": sha256, "content_type": content_type, "etag": etag})
            items[key] = result
            if result["status"] == "technical_error":
                index["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
            else:
                index.pop("cooldown_until", None)
            atomic_json(DUDEN_EXTRA_INDEX, index)
            print(console_text(f"duden {progress}/{len(pending)} {group['spoken_text']!r}: {result['status']}"))
            if result["status"] == "technical_error":
                raise WordAudioError(f"Duden technical error for {group['spoken_text']!r}: {result['reason']}")
    return index


def metadata_value(metadata: dict[str, Any], name: str) -> str:
    return clean(metadata.get(name, {}).get("value", ""))


def plain_metadata(metadata: dict[str, Any], name: str) -> str:
    return clean(re.sub(r"<[^>]+>", " ", metadata_value(metadata, name)))


def commons_title(text: str, extension: str) -> str:
    return f"File:De-{text}.{extension}"


def commons_license_allowed(value: str) -> bool:
    normalized = clean(value).casefold()
    return normalized == "cc0" or normalized.startswith("public domain") or normalized.startswith("cc by ") or normalized.startswith("cc by-sa ")


POS_CATEGORY = {
    "n": "german pronunciation of nouns",
    "v": "german pronunciation of verbs",
    "adj": "german pronunciation of adjectives",
    "adv": "german pronunciation of adverbs",
    "pron": "german pronunciation of pronouns",
    "prep": "german pronunciation of prepositions",
    "conj": "german pronunciation of conjunctions",
    "interj": "german pronunciation of interjections",
}
DIALECT_MARKERS = ("de-at-", "de-ch-", "austrian", "swiss", "bavarian", "alemannic", "kÃ¶lsch", "dialect", "liechtenstein", "rhineland")
AI_MARKERS = ("ai-generated", "artificial intelligence", "synthetic voice", "text-to-speech", " tts ")


def evaluate_commons_page(page: dict[str, Any], group: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    info = (page.get("videoinfo") or [{}])[0]
    if info.get("mediatype") != "AUDIO":
        return None, "not audio"
    duration = float(info.get("duration") or 0)
    if not 0.15 <= duration <= 15:
        return None, f"duration outside word-audio range: {duration}"
    categories = [clean(item.get("title", "")) for item in page.get("categories", [])]
    category_text = " ".join(categories).casefold()
    metadata = info.get("extmetadata") or {}
    description = plain_metadata(metadata, "ImageDescription")
    evidence = " ".join((page.get("title", ""), category_text, description)).casefold()
    if "german pronunciation" not in category_text and "lingua libre pronunciation-deu" not in category_text:
        return None, "missing German pronunciation category"
    if any(marker in evidence for marker in DIALECT_MARKERS):
        return None, "dialect or non-standard German recording"
    if any(marker in f" {evidence} " for marker in AI_MARKERS):
        return None, "AI or synthetic recording"
    pos = clean(group.get("pos", "")).casefold().split(".")[0]
    expected_category = POS_CATEGORY.get(pos)
    grammar_categories = [value.casefold() for value in categories if "german pronunciation of " in value.casefold()]
    if expected_category and grammar_categories and not any(expected_category in value for value in grammar_categories):
        return None, f"POS category mismatch: expected {expected_category}"
    license_name = metadata_value(metadata, "LicenseShortName")
    license_url = metadata_value(metadata, "LicenseUrl")
    if not commons_license_allowed(license_name) or not license_url:
        return None, f"unsupported or incomplete license: {license_name or 'missing'}"
    artist = plain_metadata(metadata, "Artist") or plain_metadata(metadata, "Credit")
    if not artist:
        return None, "missing artist/credit"
    derivatives = info.get("derivatives") or []
    mp3 = next((item for item in derivatives if item.get("transcodekey") == "mp3" or item.get("type") == "audio/mpeg"), None)
    if not mp3 or not mp3.get("src"):
        return None, "missing Wikimedia MP3 derivative"
    return {
        "status": "available",
        "request_key": group["request_key"],
        "spoken_text": group["spoken_text"],
        "page_id": page.get("pageid"),
        "title": page.get("title"),
        "description_url": info.get("descriptionurl"),
        "original_url": info.get("url"),
        "original_sha1": info.get("sha1"),
        "original_size": info.get("size"),
        "duration": duration,
        "mime": info.get("mime"),
        "derivative_url": mp3["src"],
        "artist": artist,
        "credit": plain_metadata(metadata, "Credit"),
        "attribution": plain_metadata(metadata, "Attribution"),
        "attribution_required": metadata_value(metadata, "AttributionRequired"),
        "license_short_name": license_name,
        "license_url": license_url,
        "usage_terms": metadata_value(metadata, "UsageTerms"),
        "category_evidence": categories,
        "checked_utc": now_utc(),
        "match_method": "exact-De-title-standard-German",
    }, "accepted"


async def commons_query(session: aiohttp.ClientSession, titles: list[str]) -> list[dict[str, Any]]:
    fields = {
        "action": "query", "prop": "videoinfo|categories", "titles": "|".join(titles),
        "viprop": "url|size|sha1|mime|mediatype|extmetadata|derivatives", "cllimit": "max",
        "format": "json", "formatversion": "2", "maxlag": str(COMMONS_CONFIG["maxlag"]),
    }
    headers = {"User-Agent": COMMONS_CONFIG["user_agent"]}
    last_error = "Commons query failed"
    for attempt in range(3):
        async with session.post(COMMONS_CONFIG["api"], data=fields, headers=headers) as response:
            retry_after = response.headers.get("Retry-After")
            if response.status == 429 or 500 <= response.status < 600:
                last_error = f"HTTP {response.status} from Commons API"
                await asyncio.sleep(min(int(retry_after or (5 * (attempt + 1))), 60))
                continue
            if response.status != 200:
                raise WordAudioError(f"Commons API HTTP {response.status}")
            payload = await response.json()
        error = payload.get("error")
        if error:
            if error.get("code") in {"maxlag", "ratelimited"}:
                last_error = f"Commons API {error.get('code')}"
                await asyncio.sleep(5 * (attempt + 1))
                continue
            raise WordAudioError(f"Commons API error: {error}")
        return payload.get("query", {}).get("pages", [])
    raise WordAudioError(last_error)


async def download_commons(session: aiohttp.ClientSession, item: dict[str, Any], target: Path) -> tuple[int, str]:
    headers = {"User-Agent": COMMONS_CONFIG["user_agent"]}
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        async with session.get(item["derivative_url"], headers=headers) as response:
            if response.status == 429 or 500 <= response.status < 600:
                await asyncio.sleep(min(int(response.headers.get("Retry-After") or (5 * (attempt + 1))), 60))
                continue
            if response.status != 200:
                raise WordAudioError(f"Commons media HTTP {response.status}")
            content = await response.read()
        duden.validate_mp3_bytes(content[:16])
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".mp3.tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return len(content), hashlib.sha256(content).hexdigest()
    raise WordAudioError("Commons media download failed after retries")


def write_commons_attribution(index: dict[str, Any]) -> None:
    selected = []
    seen: set[str] = set()
    attribution_indexes = [index, load_json(WIKTIONARY_INDEX, {"items": {}})]
    for source_index in attribution_indexes:
        for item in source_index.get("items", {}).values():
            if item.get("status") != "ok" or item.get("sha256") in seen:
                continue
            seen.add(item["sha256"])
            selected.append({key: item.get(key) for key in (
                "sha256", "spoken_text", "title", "description_url", "original_url", "artist", "credit",
                "attribution", "license_short_name", "license_url", "usage_terms", "checked_utc",
            )})
    atomic_json(COMMONS_ATTRIBUTION_PATH, {
        "schema_version": 1,
        "generated_utc": now_utc(),
        "notice": "This file must accompany any redistributed APKG containing the referenced Wikimedia Commons audio.",
        "items": sorted(selected, key=lambda item: (clean(item.get("spoken_text")).casefold(), item.get("sha256") or "")),
    })


async def prepare_commons(groups: dict[str, dict[str, Any]], duden_index: dict[str, Any]) -> dict[str, Any]:
    index = load_json(COMMONS_INDEX, {"schema_version": 1, "config": COMMONS_CONFIG, "items": {}})
    if index.get("config") != COMMONS_CONFIG:
        raise WordAudioError("existing Commons index uses a different configuration")
    items = index.setdefault("items", {})
    targets = {
        key: group for key, group in groups.items()
        if group.get("required_provider") == "commons"
        or duden_index["items"].get(key, {}).get("status") != "ok"
    }
    pending = {}
    for key, group in targets.items():
        cached = items.get(key, {})
        protected = group.get("protected_audio")
        if protected:
            reusable = (
                cached.get("status") == "ok"
                and cached.get("title") == protected["title"]
                and cached.get("sha256") == protected["sha256"]
                and (
                    not protected.get("original_sha1")
                    or cached.get("original_sha1") == protected["original_sha1"]
                )
            )
        else:
            reusable = cached.get("status") in {"ok", "unresolved", "ambiguous"}
        if not reusable:
            pending[key] = group
    title_map: dict[str, str] = {}
    for key, group in pending.items():
        protected = group.get("protected_audio")
        if protected:
            title_map[protected["title"]] = key
        else:
            for extension in ("ogg", "oga", "wav", "mp3"):
                title_map[commons_title(group["spoken_text"], extension)] = key
    pages_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        query_titles = list(title_map)
        for start in range(0, len(query_titles), 25):
            pages = await commons_query(session, query_titles[start:start + 25])
            for page in pages:
                key = title_map.get(clean(page.get("title", "")))
                if key and not page.get("missing"):
                    pages_by_key[key].append(page)
            await asyncio.sleep(COMMONS_CONFIG["query_interval_seconds"])
        for number, (key, group) in enumerate(sorted(pending.items()), 1):
            accepted: list[dict[str, Any]] = []
            rejected: list[str] = []
            protected = group.get("protected_audio")
            for page in pages_by_key.get(key, []):
                candidate, reason = evaluate_commons_page(page, group)
                if candidate:
                    if protected:
                        validate_protected_commons(protected, candidate)
                    accepted.append(candidate)
                else:
                    rejected.append(f"{page.get('title')}: {reason}")
            accepted.sort(key=lambda item: (not str(item["title"]).casefold().endswith(".ogg"), str(item["title"])))
            if accepted:
                best_rank = not str(accepted[0]["title"]).casefold().endswith(".ogg")
                tied = [item for item in accepted if (not str(item["title"]).casefold().endswith(".ogg")) == best_rank]
                if len(tied) > 1:
                    result = {"status": "ambiguous", "request_key": key, "spoken_text": group["spoken_text"], "reason": "multiple equally ranked exact Commons recordings", "candidates": [item["title"] for item in tied], "checked_utc": now_utc()}
                else:
                    result = tied[0]
                    target = COMMONS_DIR / f"{key}.mp3"
                    size, sha256 = await download_commons(session, result, target)
                    result.update({"status": "ok", "path": str(target), "size": size, "sha256": sha256})
                    if protected:
                        validate_protected_commons(protected, result)
                    await asyncio.sleep(COMMONS_CONFIG["download_interval_seconds"])
            else:
                result = {"status": "unresolved", "request_key": key, "spoken_text": group["spoken_text"], "reason": "; ".join(rejected[:5]) or "no exact Commons pronunciation file", "checked_utc": now_utc()}
            items[key] = result
            atomic_json(COMMONS_INDEX, index)
            print(console_text(f"commons {number}/{len(pending)} {group['spoken_text']!r}: {result['status']}"))
    for key, item in items.items():
        if item.get("status") == "ok":
            validate_audio(Path(item["path"]), item.get("sha256"), item.get("size"))
    write_commons_attribution(index)
    return index


async def wiktionary_parse(session: aiohttp.ClientSession, lemma: str) -> dict[str, Any]:
    params = {
        "action": "parse", "page": lemma, "prop": "text|revid", "format": "json",
        "formatversion": "2", "redirects": "1",
    }
    headers = {"User-Agent": WIKTIONARY_CONFIG["user_agent"]}
    async with session.get(WIKTIONARY_CONFIG["api"], params=params, headers=headers) as response:
        if response.status != 200:
            raise WordAudioError(f"Wiktionary API HTTP {response.status}")
        payload = await response.json()
    if payload.get("error"):
        raise WordAudioError(f"Wiktionary API error: {payload['error']}")
    return payload.get("parse", {})


def wiktionary_audio_candidates(parse: dict[str, Any], lemma: str) -> list[dict[str, Any]]:
    raw_text = parse.get("text") or ""
    html_text = raw_text.get("*") if isinstance(raw_text, dict) else raw_text
    if not html_text:
        return []
    root = lxml_html.fromstring(html_text)
    german = root.xpath("//h2[@id='German'] | //h2[.//span[@id='German']]")
    if not german:
        return []
    section = german[0].getparent()
    candidates: list[dict[str, Any]] = []
    for node in section.itersiblings():
        if node.xpath(".//h2"):
            break
        for audio_node in node.xpath(".//audio[@data-mwtitle]"):
            title = clean("File:" + audio_node.get("data-mwtitle", ""))
            if not re.match(r"^File:De-[^/]+\.(?:ogg|oga|wav|mp3)$", title, flags=re.I):
                continue
            context_node = next((ancestor for ancestor in audio_node.iterancestors() if ancestor.tag in {"li", "table"}), node)
            context = clean(context_node.text_content()).casefold()
            rank = 0 if "germany" in context or "berlin" in context else 1
            candidates.append({"title": title, "rank": rank, "lemma": lemma})
    dedup: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        old = dedup.get(candidate["title"])
        if old is None or candidate["rank"] < old["rank"]:
            dedup[candidate["title"]] = candidate
    return sorted(dedup.values(), key=lambda item: (item["rank"], item["title"].casefold()))


async def prepare_wiktionary(groups: dict[str, dict[str, Any]], duden_index: dict[str, Any], commons_index: dict[str, Any]) -> dict[str, Any]:
    index = load_json(WIKTIONARY_INDEX, {"schema_version": 1, "config": WIKTIONARY_CONFIG, "items": {}})
    if index.get("config") != WIKTIONARY_CONFIG:
        raise WordAudioError("existing Wiktionary index uses a different configuration")
    items = index.setdefault("items", {})
    pending = {
        key: group for key, group in groups.items()
        if (
            group.get("required_provider") == "wiktionary"
            or (
                duden_index["items"].get(key, {}).get("status") != "ok"
                and commons_index["items"].get(key, {}).get("status") != "ok"
            )
        )
        and items.get(key, {}).get("status") not in {"ok", "unresolved", "ambiguous"}
    }
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for number, (key, group) in enumerate(sorted(pending.items()), 1):
            try:
                parsed = await wiktionary_parse(session, group["spoken_text"])
                candidates = wiktionary_audio_candidates(parsed, group["spoken_text"])
                if not candidates:
                    result = {"status": "unresolved", "request_key": key, "spoken_text": group["spoken_text"], "reason": "no German pronunciation audio", "checked_utc": now_utc()}
                else:
                    pages = await commons_query(session, [item["title"] for item in candidates])
                    by_title = {clean(page.get("title", "")): page for page in pages if not page.get("missing")}
                    accepted = []
                    rejected = []
                    for candidate in candidates:
                        page = by_title.get(candidate["title"])
                        if not page:
                            rejected.append(f"{candidate['title']}: missing Commons page")
                            continue
                        accepted_page, reason = evaluate_commons_page(page, group)
                        if accepted_page:
                            accepted_page["wiktionary_page"] = f"https://en.wiktionary.org/wiki/{group['spoken_text']}"
                            accepted_page["wiktionary_revision"] = parsed.get("revid")
                            accepted_page["wiktionary_rank"] = candidate["rank"]
                            accepted.append(accepted_page)
                        else:
                            rejected.append(f"{candidate['title']}: {reason}")
                    if not accepted:
                        result = {"status": "unresolved", "request_key": key, "spoken_text": group["spoken_text"], "reason": "; ".join(rejected[:5]) or "no valid Wiktionary audio", "checked_utc": now_utc()}
                    else:
                        best_rank = accepted[0]["wiktionary_rank"]
                        tied = [item for item in accepted if item["wiktionary_rank"] == best_rank]
                        if len(tied) > 1:
                            result = {"status": "ambiguous", "request_key": key, "spoken_text": group["spoken_text"], "candidates": [item["title"] for item in tied], "reason": "multiple equally ranked Wiktionary recordings", "checked_utc": now_utc()}
                        else:
                            result = tied[0]
                            target = WIKTIONARY_DIR / f"{key}.mp3"
                            size, sha256 = await download_commons(session, result, target)
                            result.update({"status": "ok", "path": str(target), "size": size, "sha256": sha256})
            except (aiohttp.ClientError, WordAudioError) as exc:
                result = {"status": "unresolved", "request_key": key, "spoken_text": group["spoken_text"], "reason": str(exc), "checked_utc": now_utc()}
            items[key] = result
            atomic_json(WIKTIONARY_INDEX, index)
            print(console_text(f"wiktionary {number}/{len(pending)} {group['spoken_text']!r}: {result['status']}"))
    for item in items.values():
        if item.get("status") == "ok":
            validate_audio(Path(item["path"]), item.get("sha256"), item.get("size"))
    return index


def edge_audio_id(text: str) -> str:
    return canonical_hash({"spoken_text": text, **EDGE_CONFIG})


async def prepare_edge(groups: dict[str, dict[str, Any]], duden_index: dict[str, Any], commons_index: dict[str, Any], wiktionary_index: dict[str, Any] | None = None) -> dict[str, Any]:
    wiktionary_index = wiktionary_index or {"items": {}}
    try:
        import edge_tts
        from importlib.metadata import version
    except ImportError as exc:
        raise WordAudioError("edge-tts is not installed") from exc
    if version("edge-tts") != EDGE_CONFIG["engine_version"]:
        raise WordAudioError(f"edge-tts {EDGE_CONFIG['engine_version']} is required")
    voices = await edge_tts.list_voices()
    if not any(item.get("ShortName") == EDGE_CONFIG["voice"] and item.get("Locale") == "de-DE" for item in voices):
        raise WordAudioError(f"Edge voice unavailable: {EDGE_CONFIG['voice']}")
    index = load_json(EDGE_INDEX, {"schema_version": 1, "config": EDGE_CONFIG, "items": {}})
    if index.get("config") != EDGE_CONFIG:
        raise WordAudioError("existing Edge index uses a different configuration")
    items = index.setdefault("items", {})
    EDGE_DIR.mkdir(parents=True, exist_ok=True)
    needed = [
        group for key, group in sorted(groups.items())
        if duden_index["items"].get(key, {}).get("status") != "ok"
        and commons_index["items"].get(key, {}).get("status") != "ok"
        and wiktionary_index["items"].get(key, {}).get("status") != "ok"
    ]
    for number, group in enumerate(needed, 1):
        audio_id = edge_audio_id(group["spoken_text"])
        cached = items.get(audio_id)
        if cached:
            try:
                validate_audio(Path(cached["path"]), cached.get("sha256"), cached.get("size"))
                continue
            except WordAudioError:
                pass
        last_error: Exception | None = None
        for delay in (0, 2, 5, 10):
            if delay:
                await asyncio.sleep(delay)
            fd, tmp_name = tempfile.mkstemp(dir=EDGE_DIR, suffix=".mp3.tmp")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                communicate = edge_tts.Communicate(
                    group["spoken_text"], EDGE_CONFIG["voice"], rate=EDGE_CONFIG["rate"],
                    volume=EDGE_CONFIG["volume"], pitch=EDGE_CONFIG["pitch"],
                )
                await communicate.save(str(tmp))
                size, sha256 = validate_audio(tmp)
                final = EDGE_DIR / f"{audio_id}.mp3"
                os.replace(tmp, final)
                items[audio_id] = {
                    "audio_id": audio_id, "spoken_text": group["spoken_text"], "path": str(final),
                    "size": size, "sha256": sha256, "status": "ok", "created_utc": now_utc(),
                }
                atomic_json(EDGE_INDEX, index)
                print(console_text(f"edge {number}/{len(needed)} {group['spoken_text']!r}: ok"))
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if tmp.exists():
                    tmp.unlink()
        if last_error is not None:
            raise WordAudioError(f"Edge TTS failed for {group['spoken_text']!r}: {last_error}")
    return index


def word_audio_provider(value: str) -> str:
    text = value.casefold()
    for provider in ("duden", "commons", "wiktionary", "edge"):
        if f"_goethe_word_{provider}_" in text or (provider == "duden" and "[sound:duden-" in text):
            return provider
    return "unknown"


def word_audio_sha256(value: str) -> str:
    match = re.search(r"_([0-9a-f]{64})\.mp3(?:\]|$)", clean(value), flags=re.I)
    return match.group(1).lower() if match else ""


def cached_audio_provenance() -> dict[str, dict[str, list[str]]]:
    entries: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"spoken_texts": set(), "providers": set()}
    )

    def add(sha256: Any, spoken: Any, provider: str) -> None:
        digest = clean(sha256).lower()
        text = clean(spoken)
        if re.fullmatch(r"[0-9a-f]{64}", digest) and text:
            entries[digest]["spoken_texts"].add(text)
            entries[digest]["providers"].add(provider)

    by_ref, _ = load_duden_catalog()
    for row in by_ref.values():
        if row.get("status") == "ok":
            add(row.get("sha256"), row.get("word"), "duden")
    for provider, path in (
        ("edge", EDGE_INDEX),
        ("commons", COMMONS_INDEX),
        ("wiktionary", WIKTIONARY_INDEX),
    ):
        for item in load_json(path, {"items": {}}).get("items", {}).values():
            if isinstance(item, dict) and item.get("status") == "ok":
                add(item.get("sha256"), item.get("spoken_text"), provider)
    for item in load_protected_audio().values():
        add(item.get("sha256"), item.get("spoken_text"), "protected")
    return {
        digest: {
            "spoken_texts": sorted(value["spoken_texts"], key=str.casefold),
            "providers": sorted(value["providers"]),
        }
        for digest, value in entries.items()
    }


def live_assignment_mismatches(
    records: dict[int, dict[str, Any]],
    manifest: dict[str, Any],
    provenance: dict[str, dict[str, list[str]]] | None = None,
    equivalences: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provenance = provenance if provenance is not None else cached_audio_provenance()
    equivalences = equivalences if equivalences is not None else load_spoken_equivalences()
    semantic_mismatches: list[dict[str, Any]] = []
    semantic_candidates: list[dict[str, Any]] = []
    reviewed_equivalences: list[dict[str, Any]] = []
    provider_drift: list[dict[str, Any]] = []
    unknown_provenance: list[dict[str, Any]] = []
    valid = 0
    for note_id, record in sorted(records.items()):
        item = manifest["notes"].get(str(note_id))
        if not item:
            continue
        fields = record["fields"]
        current_sha = word_audio_sha256(fields.get("WordAudio", ""))
        expected = clean(
            (item.get("assignment") or {}).get("spoken_text")
            or item.get("spoken_text")
        )
        desired_sha = clean((item.get("assignment") or {}).get("sha256")).lower()
        if not current_sha or not expected:
            continue
        if desired_sha and current_sha == desired_sha:
            valid += 1
            continue
        current = provenance.get(current_sha)
        row = {
            "note_id": note_id,
            "lemma": fields.get("Lemma", ""),
            "source_id": fields.get("SourceID", ""),
            "expected_spoken_text": expected,
            "current_sha256": current_sha,
            "desired_sha256": desired_sha,
            "current_provenance": current or {},
        }
        if not current:
            unknown_provenance.append(row)
            continue
        expected_key = canonical_spoken_identity(expected).casefold()
        current_keys = {
            canonical_spoken_identity(value).casefold()
            for value in current.get("spoken_texts", [])
        }
        equivalence = equivalences.get(clean(fields.get("SourceID", "")))
        if equivalence and clean(fields.get("Lemma", "")) != equivalence["expected_lemma"]:
            raise WordAudioError(
                f"spoken equivalence lemma mismatch: {fields.get('SourceID', '')}"
            )
        equivalent_keys = {
            canonical_spoken_identity(value).casefold()
            for value in (equivalence or {}).get("spoken_texts", [])
        }
        if expected_key in current_keys:
            provider_drift.append(row)
        elif equivalence and current_keys & equivalent_keys:
            row["reason"] = equivalence["reason"]
            reviewed_equivalences.append(row)
        elif bound_spoken_identity(fields.get("Lemma", "")) is not None:
            row["reason"] = "cached spoken text does not match current lemma"
            semantic_mismatches.append(row)
        else:
            row["reason"] = "transcript differs; manual semantic review required"
            semantic_candidates.append(row)
    return {
        "checked": len(records),
        "valid": valid,
        "semantic_mismatches": semantic_mismatches,
        "semantic_candidates": semantic_candidates,
        "reviewed_equivalences": reviewed_equivalences,
        "provider_drift": provider_drift,
        "unknown_provenance": unknown_provenance,
    }


def assignment_provider(item: dict[str, Any]) -> str:
    source = item["assignment"]["source"]
    return "duden" if source.startswith("duden") else source


def validate_change_set(manifest: dict[str, Any]) -> None:
    semantic_repairs = {
        int(item["note_id"])
        for item in manifest.get("live_audio_audit", {}).get(
            "semantic_mismatches", []
        )
    }
    prepared_ids = (
        set(map(int, manifest.get("prepared_note_ids", [])))
        if manifest.get("prepared_scope") == "targeted"
        else None
    )
    for item in manifest["notes"].values():
        if prepared_ids is not None and int(item["note_id"]) not in prepared_ids:
            continue
        if not item.get("assignment"):
            continue
        desired = f"[sound:{item['assignment']['media_name']}]"
        old = item.get("old_word_audio", "")
        if old == desired:
            continue
        old_provider = word_audio_provider(old)
        desired_provider = assignment_provider(item)
        protected = item.get("protected_audio")
        if protected and (
            desired_provider == protected["provider"]
            or protected["provider"] == "local" and desired_provider == "protected"
        ):
            continue
        if int(item["note_id"]) in semantic_repairs:
            continue
        if old_provider in {"commons", "wiktionary", "edge"} and desired_provider == "duden":
            continue
        raise WordAudioError(
            f"unapproved audio transition: note={item['note_id']} {old_provider}->{desired_provider}"
        )


def write_duden_rescan_report(manifest: dict[str, Any], duden_index: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in manifest["notes"].values():
        if not item.get("assignment"):
            continue
        old_provider = word_audio_provider(item.get("old_word_audio", ""))
        if old_provider == "duden" and not item.get("protected_audio"):
            continue
        duden_item = duden_index.get("items", {}).get(item.get("request_key", ""), {})
        desired_provider = assignment_provider(item)
        if item.get("protected_audio"):
            decision = "intentional_fallback"
        elif desired_provider == "duden":
            decision = "duden_audio_found"
        elif duden_item.get("status") == "ok":
            decision = "duden_audio_found"
        elif duden_item.get("status") == "ambiguous":
            decision = "ambiguous"
        elif duden_item.get("match_method") == "sitemap-page-no-audio":
            decision = "exact_page_no_audio"
        elif duden_item.get("match_method") == "sitemap-metadata-conflict":
            decision = "metadata_conflict"
        elif duden_item.get("status") == "technical_error":
            decision = "technical_error"
        else:
            decision = "no_exact_lexeme"
        rows.append({
            "note_id": item["note_id"], "card_ids": item.get("card_ids", []),
            "level": item["level"], "lemma": item["lemma"],
            "spoken_text": item.get("spoken_text"), "current_provider": old_provider,
            "desired_provider": desired_provider, "decision": decision,
            "duden": {key: duden_item.get(key) for key in (
                "status", "reason", "match_method", "duden_page_url", "duden_audio_url",
                "file_id", "candidate_pages", "resolver_version",
            )},
            "protected_audio": item.get("protected_audio"),
        })
    report = {
        "schema_version": 1, "created_utc": now_utc(),
        "resolver_version": DUDEN_RESOLVER_VERSION,
        "notes": len(rows), "requests": len({
            item["request_key"] for item in manifest["notes"].values()
            if word_audio_provider(item.get("old_word_audio", "")) != "duden" and item.get("request_key")
        }),
        "counts": dict(Counter(row["decision"] for row in rows)), "items": rows,
    }
    atomic_json(DUDEN_RESCAN_REPORT, report)
    return report


def finalize_manifest(
    manifest: dict[str, Any],
    duden_index: dict[str, Any],
    commons_index: dict[str, Any],
    edge_index: dict[str, Any],
    wiktionary_index: dict[str, Any] | None = None,
    *,
    note_ids: list[int] | None = None,
) -> dict[str, Any]:
    validate_manifest(manifest)
    wiktionary_index = wiktionary_index or {"items": {}}
    counts: Counter[str] = Counter()
    selected = set(map(int, note_ids or []))
    items = [
        item for item in manifest["notes"].values()
        if not selected or int(item["note_id"]) in selected
    ]
    for item in items:
        if item.get("assignment"):
            counts[item["assignment"]["source"]] += 1
            continue
        key = item["request_key"]
        protected = item.get("protected_audio")
        if protected:
            pinned = commons_index["items"].get(key, {})
            if pinned.get("status") != "ok":
                raise WordAudioError(f"protected Commons audio is unavailable: {item['lemma']}")
            validate_protected_commons(protected, pinned)
            item["assignment"] = preserve_matching_media_name(
                item,
                assignment(
                    "commons",
                    Path(pinned["path"]),
                    detail=pinned,
                    lemma_identity=item["lemma_identity"],
                    spoken_text=item["spoken_text"],
                ),
            )
            counts["commons"] += 1
            continue
        extra = duden_index["items"].get(key, {})
        if extra.get("status") == "ok":
            item["assignment"] = assignment(
                "duden_extra",
                Path(extra["path"]),
                detail=extra,
                lemma_identity=item["lemma_identity"],
                spoken_text=item["spoken_text"],
            )
        elif commons_index["items"].get(key, {}).get("status") == "ok":
            commons = commons_index["items"][key]
            item["assignment"] = assignment(
                "commons",
                Path(commons["path"]),
                detail=commons,
                lemma_identity=item["lemma_identity"],
                spoken_text=item["spoken_text"],
            )
        elif wiktionary_index["items"].get(key, {}).get("status") == "ok":
            wiktionary = wiktionary_index["items"][key]
            item["assignment"] = assignment(
                "wiktionary",
                Path(wiktionary["path"]),
                detail=wiktionary,
                lemma_identity=item["lemma_identity"],
                spoken_text=item["spoken_text"],
            )
        else:
            edge_id = edge_audio_id(item["spoken_text"])
            edge = edge_index["items"].get(edge_id)
            if not edge or edge.get("status") != "ok":
                raise WordAudioError(f"missing Edge result for {item['lemma']!r}")
            item["assignment"] = assignment(
                "edge",
                Path(edge["path"]),
                detail=edge,
                lemma_identity=item["lemma_identity"],
                spoken_text=item["spoken_text"],
            )
        counts[item["assignment"]["source"]] += 1
    expected = len(items)
    if sum(counts.values()) != expected:
        raise WordAudioError("prepared manifest is incomplete")
    manifest.update({
        "prepared_utc": now_utc(),
        "prepared_scope": "targeted" if selected else "full",
        "prepared_note_ids": sorted(selected),
        "counts": dict(counts),
        "missing_overrides": [],
    })
    validate_manifest(manifest, require_prepared=True)
    validate_change_set(manifest)
    report = write_duden_rescan_report(manifest, duden_index)
    manifest["duden_rescan_report"] = str(DUDEN_RESCAN_REPORT)
    manifest["duden_rescan_counts"] = report["counts"]
    atomic_json(MANIFEST_PATH, manifest)
    return manifest


def finalize_protected_manifest(
    manifest: dict[str, Any], commons_index: dict[str, Any]
) -> dict[str, Any]:
    validate_manifest(manifest)
    counts: Counter[str] = Counter()
    for item in manifest["notes"].values():
        protected = item.get("protected_audio")
        if not protected:
            continue
        if not item.get("assignment"):
            pinned = commons_index.get("items", {}).get(item["request_key"], {})
            if pinned.get("status") != "ok":
                raise WordAudioError(f"protected Commons audio is unavailable: {item['lemma']}")
            validate_protected_commons(protected, pinned)
            item["assignment"] = preserve_matching_media_name(
                item,
                assignment(
                    "commons",
                    Path(pinned["path"]),
                    detail=pinned,
                    lemma_identity=item["lemma_identity"],
                    spoken_text=item["spoken_text"],
                ),
            )
        counts[item["assignment"]["source"]] += 1
    manifest.update({
        "prepared_utc": now_utc(),
        "prepared_scope": "protected",
        "counts": dict(counts),
        "missing_overrides": [],
    })
    validate_manifest(manifest, require_prepared=True)
    validate_change_set(manifest)
    atomic_json(MANIFEST_PATH, manifest)
    return manifest


async def command_prepare(_: argparse.Namespace) -> None:
    if not _.confirm_commons_license:
        raise WordAudioError("Commons preparation requires --confirm-commons-license")
    if _.offline and _.refresh_duden_fallbacks:
        raise WordAudioError("Duden fallback refresh cannot run offline")
    manifest = load_json(MANIFEST_PATH, None) if _.offline else build_audit()
    if not manifest:
        raise WordAudioError("offline preparation requires a complete prior audit manifest")
    validate_manifest(manifest)
    if manifest["missing_overrides"]:
        raise WordAudioError(
            f"{len(manifest['missing_overrides'])} notes need spoken-text overrides; see {MANIFEST_PATH}"
        )
    groups = request_groups(manifest)
    target_ids = selected_ids(manifest, "full", _.note_id) if _.note_id else []
    if target_ids:
        target_set = set(target_ids)
        groups = {
            key: group for key, group in groups.items()
            if target_set.intersection(map(int, group["note_ids"]))
        }
    if _.scope == "protected":
        if target_ids:
            raise WordAudioError("--note-id cannot be combined with protected scope")
        groups = {
            key: group for key, group in groups.items()
            if group.get("protected_audio")
        }
        duden_index = {
            "items": {
                key: {"status": "unresolved", "reason": "protected audio excludes Duden"}
                for key in groups
            }
        }
        commons_index = await prepare_commons(groups, duden_index)
        final = finalize_protected_manifest(manifest, commons_index)
        print(json.dumps({
            "notes": len(selected_ids(final, "protected")),
            "scope": "protected",
            "counts": final["counts"],
        }, ensure_ascii=False, indent=2))
        return
    duden_index = await prepare_duden(groups, refresh_negative=_.refresh_duden_fallbacks)
    commons_index = await prepare_commons(groups, duden_index)
    wiktionary_index = await prepare_wiktionary(groups, duden_index, commons_index)
    edge_index = await prepare_edge(groups, duden_index, commons_index, wiktionary_index)
    final = finalize_manifest(
        manifest,
        duden_index,
        commons_index,
        edge_index,
        wiktionary_index,
        note_ids=target_ids,
    )
    print(json.dumps({
        "notes": len(target_ids) if target_ids else final["note_count"],
        "scope": final["prepared_scope"],
        "counts": final["counts"],
    }, ensure_ascii=False, indent=2))


def command_audit(_: argparse.Namespace) -> None:
    manifest = build_audit()
    live_audio = manifest["live_audio_audit"]
    payload = {
        "notes": manifest["note_count"], "counts": manifest["counts"],
        "levels": manifest["level_counts"], "duden_rows": manifest["duden_rows"],
        "missing_overrides": manifest["missing_overrides"], "manifest": str(MANIFEST_PATH),
        "live_audio_audit": {
            "valid": live_audio["valid"],
            "semantic_mismatches": live_audio["semantic_mismatches"],
            "semantic_candidates": len(live_audio["semantic_candidates"]),
            "reviewed_equivalences": len(live_audio["reviewed_equivalences"]),
            "provider_drift": len(live_audio["provider_drift"]),
            "unknown_provenance": len(live_audio["unknown_provenance"]),
        },
    }
    print(console_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        getattr(sys.stdout, "encoding", None),
    ))


def command_commons_audit(_: argparse.Namespace) -> None:
    index = load_json(COMMONS_INDEX, {"items": {}})
    counts = Counter(item.get("status", "unknown") for item in index.get("items", {}).values())
    print(json.dumps({"counts": dict(counts), "index": str(COMMONS_INDEX), "attribution": str(COMMONS_ATTRIBUTION_PATH)}, ensure_ascii=False, indent=2))


def all_reviews(card_ids: list[int]) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for batch in gw.chunks(sorted(card_ids), 250):
        reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    return reviews


def appended_review_cards(
    before: dict[str, list[dict[str, Any]]],
    after: dict[str, list[dict[str, Any]]],
) -> set[str]:
    appended: set[str] = set()
    for card_id, old in before.items():
        current = after.get(card_id)
        if current is None or current[:len(old)] != old:
            raise WordAudioError("review history changed")
        if len(current) > len(old):
            appended.add(card_id)
    return appended


def schedule_projection(card: dict[str, Any]) -> dict[str, Any]:
    return {key: card.get(key) for key in gw.SCHEDULE_KEYS}


def model_snapshot() -> dict[str, Any]:
    return {
        "fields": gw.anki("modelFieldNames", modelName=MODEL),
        "templates": gw.anki("modelTemplates", modelName=MODEL),
        "styling": gw.anki("modelStyling", modelName=MODEL),
    }


def command_snapshot(_: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST_PATH, None)
    if not manifest:
        raise WordAudioError("prepared manifest missing")
    validate_manifest(manifest, require_prepared=True)
    records = live_records()
    if set(map(int, manifest["notes"])) != set(records):
        raise WordAudioError("prepared note ID set differs from live deck")
    for note_id, record in records.items():
        if manifest["notes"][str(note_id)]["source_signature"] != source_signature(record["fields"]):
            raise WordAudioError(f"source fields changed after preparation: {note_id}")
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{time.time_ns() % 1_000_000_000:09d}"
    backup = STATE / f"Goethe_Institute_pre_word_audio_{stamp}.apkg"
    if backup.exists():
        raise WordAudioError(f"backup destination already exists: {backup}")
    try:
        result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.as_posix(), includeSched=True)
    except gw.MigrationError as exc:
        if "timed out" not in str(exc).casefold() and "timeout" not in str(exc).casefold():
            raise
        result = True
    if not result or not apkg.wait_for_valid_apkg(backup):
        raise WordAudioError("Anki APKG export failed")
    cards = [card for record in records.values() for card in record["cards"]]
    card_ids = [int(card["cardId"]) for card in cards]
    reviews = all_reviews(card_ids)
    snapshot = {
        "schema_version": 1, "created_utc": now_utc(), "backup": str(backup),
        "backup_sha256": apkg.hash_file(backup), "manifest_sha256": duden.hash_file(MANIFEST_PATH),
        "notes": {str(note_id): {"model": record["model"], "fields": record["fields"], "tags": record["tags"]} for note_id, record in records.items()},
        "cards": {str(card["cardId"]): schedule_projection(card) for card in cards},
        "reviews": reviews, "reviews_sha256": canonical_hash(reviews), "model": model_snapshot(),
    }
    atomic_json(SNAPSHOT_PATH, snapshot)
    print(json.dumps({"backup": str(backup), "notes": len(records), "cards": len(cards), "reviews_sha256": snapshot["reviews_sha256"]}, indent=2))


def load_ready() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_json(MANIFEST_PATH, None)
    snapshot = load_json(SNAPSHOT_PATH, None)
    if not manifest or not manifest.get("prepared_utc") or not snapshot:
        raise WordAudioError("prepared manifest or snapshot missing")
    validate_manifest(manifest, require_prepared=True)
    if snapshot.get("manifest_sha256") != duden.hash_file(MANIFEST_PATH):
        raise WordAudioError("prepared manifest changed after snapshot")
    backup = Path(str(snapshot.get("backup", "")))
    if not apkg.valid_apkg(backup) or snapshot.get("backup_sha256") != apkg.hash_file(backup):
        raise WordAudioError("scheduled APKG backup is missing, corrupt, or changed")
    return manifest, snapshot


def pilot_ids(manifest: dict[str, Any]) -> list[int]:
    candidates = sorted(manifest["notes"].values(), key=lambda item: (
        scope.LEVEL_RANK[item["level"]], item["assignment"]["source"] != "commons",
        bool(item["old_word_audio"]), item["note_id"]
    ))
    changes = [
        item for item in candidates
        if item.get("old_word_audio", "") != f"[sound:{item['assignment']['media_name']}]"
    ]
    selected: list[int] = []
    for item in changes:
        if item.get("protected_audio") or item.get("lemma") == "alle":
            selected.append(int(item["note_id"]))
    seen: set[tuple[str, str, bool]] = set()
    for level in scope.LEVELS:
        item = next((candidate for candidate in changes if candidate["level"] == level), None)
        if item and int(item["note_id"]) not in selected:
            selected.append(int(item["note_id"]))
    for item in changes + candidates:
        key = (item["level"], item["assignment"]["source"], bool(item["old_word_audio"]))
        if int(item["note_id"]) not in selected and key not in seen:
            selected.append(int(item["note_id"]))
        seen.add(key)
        if len(selected) == PILOT_SIZE:
            break
    if len(selected) < PILOT_SIZE:
        selected.extend(
            int(item["note_id"])
            for item in candidates
            if int(item["note_id"]) not in selected
        )
    return selected[:PILOT_SIZE]


def selected_ids(
    manifest: dict[str, Any],
    scope: str,
    note_ids: list[int] | None = None,
) -> list[int]:
    if scope == "pilot":
        selected = pilot_ids(manifest)
    elif scope == "protected":
        selected = sorted(
            int(item["note_id"])
            for item in manifest["notes"].values()
            if item.get("protected_audio")
        )
    else:
        selected = sorted(map(int, manifest["notes"]))
    if note_ids:
        requested = sorted(set(map(int, note_ids)))
        missing = sorted(set(requested) - set(selected))
        if missing:
            raise WordAudioError(
                f"requested note IDs are not present in {scope} scope: {missing}"
            )
        return requested
    return selected


def require_prepared_scope(
    manifest: dict[str, Any],
    requested: str,
    note_ids: list[int] | None = None,
) -> None:
    prepared = manifest.get("prepared_scope", "full")
    if prepared == "protected" and requested != "protected":
        raise WordAudioError("manifest is prepared only for protected audio")
    if prepared == "targeted":
        allowed = set(map(int, manifest.get("prepared_note_ids", [])))
        requested_ids = set(map(int, note_ids or []))
        if not requested_ids or not requested_ids <= allowed:
            raise WordAudioError(
                "targeted manifest requires an explicit prepared --note-id subset"
            )


def verify_baseline(records: dict[int, dict[str, Any]], snapshot: dict[str, Any], manifest: dict[str, Any]) -> None:
    if set(map(int, snapshot["notes"])) != set(records):
        raise WordAudioError("live note ID set changed")
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        if record["model"] != before["model"] or record["tags"] != before["tags"]:
            raise WordAudioError(f"live note changed since snapshot: {note_id}")
        item = manifest["notes"][str(note_id)]
        if item.get("assignment"):
            validate_assignment_identity(record["fields"], item)
        for name, value in before["fields"].items():
            actual = record["fields"].get(name, "")
            if name == "WordAudio":
                audio = manifest["notes"][str(note_id)].get("assignment")
                expected = f"[sound:{audio['media_name']}]" if audio else value
                if actual in (value, expected):
                    continue
            if actual != value:
                raise WordAudioError(f"live note changed since snapshot: {note_id}")


def ensure_media(item: dict[str, Any]) -> None:
    audio = item["assignment"]
    path = Path(audio["path"])
    _, sha256 = validate_audio(path, audio["sha256"], audio["size"])
    existing = gw.anki("retrieveMediaFile", filename=audio["media_name"])
    if existing:
        data = base64.b64decode(existing)
        if hashlib.sha256(data).hexdigest() != sha256:
            raise WordAudioError(f"Anki media hash conflict: {audio['media_name']}")
        return
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    stored = gw.anki("storeMediaFile", filename=audio["media_name"], data=encoded)
    if stored != audio["media_name"]:
        raise WordAudioError(f"unexpected Anki media name: {stored}")
    retrieved = gw.anki("retrieveMediaFile", filename=audio["media_name"])
    if not retrieved or hashlib.sha256(base64.b64decode(retrieved)).hexdigest() != sha256:
        raise WordAudioError(f"Anki media verification failed: {audio['media_name']}")


def update_word_audio(note_ids: list[int], values: dict[int, str]) -> None:
    actions = [{"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": {"WordAudio": values[note_id]}}}} for note_id in note_ids]
    for batch in gw.chunks(actions, 60):
        results = gw.anki("multi", actions=batch)
        errors = [result.get("error") for result in results if isinstance(result, dict) and result.get("error")]
        if errors:
            raise WordAudioError(f"Anki update errors: {errors[:3]}")


def command_apply(args: argparse.Namespace) -> None:
    if not args.dry_run and args.confirmation != APPLY_CONFIRMATION:
        raise WordAudioError(f"confirmation must equal {APPLY_CONFIRMATION}")
    manifest, snapshot = load_ready()
    require_prepared_scope(manifest, args.scope, args.note_id)
    records = live_records()
    verify_baseline(records, snapshot, manifest)
    ids = selected_ids(manifest, args.scope, args.note_id)
    changes = [note_id for note_id in ids if records[note_id]["fields"].get("WordAudio", "") != f"[sound:{manifest['notes'][str(note_id)]['assignment']['media_name']}]" ]
    print(json.dumps({"scope": args.scope, "selected": len(ids), "changes": len(changes), "dry_run": args.dry_run}, indent=2))
    if args.dry_run:
        return
    for note_id in changes:
        ensure_media(manifest["notes"][str(note_id)])
    values = {note_id: f"[sound:{manifest['notes'][str(note_id)]['assignment']['media_name']}]" for note_id in changes}
    try:
        update_word_audio(changes, values)
    except Exception:
        old = {note_id: snapshot["notes"][str(note_id)]["fields"].get("WordAudio", "") for note_id in changes}
        update_word_audio(changes, old)
        raise


def verify_state(
    scope: str,
    expect_baseline: bool = False,
    note_ids: list[int] | None = None,
) -> dict[str, Any]:
    manifest, snapshot = load_ready()
    require_prepared_scope(manifest, scope, note_ids)
    records = live_records()
    selected = set(selected_ids(manifest, scope, note_ids))
    targeted = scope == "protected" or bool(note_ids)
    if set(records) != set(map(int, snapshot["notes"])):
        raise WordAudioError("note ID set changed")
    checked_records = (
        {note_id: records[note_id] for note_id in selected}
        if targeted
        else records
    )
    for note_id, record in checked_records.items():
        before = snapshot["notes"][str(note_id)]
        if record["model"] != before["model"] or record["tags"] != before["tags"]:
            raise WordAudioError(f"model or tags changed: {note_id}")
        validate_assignment_identity(
            record["fields"],
            manifest["notes"][str(note_id)],
        )
        for name, value in before["fields"].items():
            actual = record["fields"].get(name, "")
            if name == "WordAudio" and not expect_baseline and note_id in selected:
                expected = f"[sound:{manifest['notes'][str(note_id)]['assignment']['media_name']}]"
            else:
                expected = value
            if actual != expected:
                raise WordAudioError(f"field changed unexpectedly: note={note_id} field={name}")
    cards = [card for record in records.values() for card in record["cards"]]
    checked_cards = (
        [card for card in cards if int(card["note"]) in selected]
        if targeted
        else cards
    )
    checked_card_ids = {str(card["cardId"]) for card in checked_cards}
    current_cards = {str(card["cardId"]): schedule_projection(card) for card in checked_cards}
    expected_cards = {
        card_id: value for card_id, value in snapshot["cards"].items()
        if card_id in checked_card_ids
    }
    reviews = all_reviews([int(card["cardId"]) for card in checked_cards])
    concurrent_review_notes: list[int] = []
    if targeted:
        expected_reviews = {
            card_id: value for card_id, value in snapshot["reviews"].items()
            if card_id in checked_card_ids
        }
        appended = appended_review_cards(expected_reviews, reviews)
        card_notes = {str(card["cardId"]): int(card["note"]) for card in checked_cards}
        reviewed_notes = {card_notes[card_id] for card_id in appended}
        changed_cards = {
            card_id for card_id in checked_card_ids
            if current_cards.get(card_id) != expected_cards.get(card_id)
        }
        changed_notes = {card_notes[card_id] for card_id in changed_cards}
        if changed_notes - reviewed_notes:
            raise WordAudioError("card IDs or scheduling changed")
        concurrent_review_notes = sorted(changed_notes)
        reviews_match = True
    else:
        if current_cards != expected_cards:
            raise WordAudioError("card IDs or scheduling changed")
        reviews_match = canonical_hash(reviews) == snapshot["reviews_sha256"]
    if not reviews_match:
        raise WordAudioError("review history changed")
    if model_snapshot() != snapshot["model"]:
        raise WordAudioError("model fields/templates/styling changed")
    if not expect_baseline:
        for note_id in selected:
            item = manifest["notes"][str(note_id)]["assignment"]
            retrieved = gw.anki("retrieveMediaFile", filename=item["media_name"])
            if not retrieved or hashlib.sha256(base64.b64decode(retrieved)).hexdigest() != item["sha256"]:
                raise WordAudioError(f"missing or corrupt Anki media: {item['media_name']}")
    return {
        "scope": scope,
        "baseline": expect_baseline,
        "notes": len(records),
        "cards": len(cards),
        "verified": len(selected),
        "concurrent_review_notes": concurrent_review_notes,
    }


def command_verify(args: argparse.Namespace) -> None:
    print(json.dumps(
        verify_state(
            args.scope,
            expect_baseline=args.baseline,
            note_ids=args.note_id,
        ),
        indent=2,
    ))


def command_protect_current(args: argparse.Namespace) -> None:
    require_anki()
    notes = gw.anki("notesInfo", notes=[args.note_id])
    if len(notes) != 1 or int(notes[0]["noteId"]) != args.note_id:
        raise WordAudioError(f"note not found: {args.note_id}")
    note = notes[0]
    if note.get("modelName") != MODEL:
        raise WordAudioError(f"note uses unexpected model: {args.note_id}")
    fields = field_values(note)
    match = re.fullmatch(r"\[sound:([^\[\]]+\.mp3)\]", clean(fields.get("WordAudio", "")), flags=re.I)
    if not match:
        raise WordAudioError("WordAudio must contain exactly one MP3 sound reference")
    encoded = gw.anki("retrieveMediaFile", filename=match.group(1))
    if not encoded:
        raise WordAudioError(f"Anki media is missing: {match.group(1)}")
    content = base64.b64decode(encoded)
    entry = local_protected_entry(fields, content, reason=args.reason)
    target = PROTECTED_DIR / f"{entry['sha256']}.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        validate_audio(target, entry["sha256"], entry["size"])
    else:
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".mp3.tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
    policy = load_override_policy()
    if policy.get("schema_version") != 3:
        raise WordAudioError("protected-audio policy must be migrated to schema 3")
    values = policy.setdefault("protected_audio", {})
    source_id = clean(fields.get("SourceID", ""))
    current = values.get(source_id)
    if current and current.get("sha256") != entry["sha256"]:
        raise WordAudioError(f"protected audio already exists for {source_id}")
    values[source_id] = entry
    atomic_json(OVERRIDES_PATH, policy)
    print(json.dumps({
        "note_id": args.note_id,
        "source_id": source_id,
        "lemma": fields["Lemma"],
        "sha256": entry["sha256"],
        "path": str(target),
    }, ensure_ascii=False, indent=2))


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise WordAudioError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    manifest, snapshot = load_ready()
    require_prepared_scope(manifest, args.scope, args.note_id)
    records = live_records()
    ids = [
        note_id
        for note_id in selected_ids(manifest, args.scope, args.note_id)
        if records[note_id]["fields"].get("WordAudio", "")
        != snapshot["notes"][str(note_id)]["fields"].get("WordAudio", "")
    ]
    old = {note_id: snapshot["notes"][str(note_id)]["fields"].get("WordAudio", "") for note_id in ids}
    update_word_audio(ids, old)
    print(json.dumps(
        verify_state(
            args.scope,
            expect_baseline=True,
            note_ids=args.note_id,
        ),
        indent=2,
    ))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=command_audit)
    sub.add_parser("commons-audit").set_defaults(func=command_commons_audit)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--confirm-duden-usage", action="store_true", required=True)
    prepare.add_argument("--confirm-commons-license", action="store_true")
    prepare.add_argument("--offline", action="store_true", help="Resume from the existing audit manifest without AnkiConnect.")
    prepare.add_argument("--scope", choices=("protected", "full"), default="full")
    prepare.add_argument("--note-id", type=int, action="append")
    prepare.add_argument(
        "--refresh-duden-fallbacks", action="store_true",
        help="Re-probe cached non-Duden results through the exact Duden lexeme sitemap.",
    )
    prepare.set_defaults(func=command_prepare)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    apply = sub.add_parser("apply")
    apply.add_argument("--scope", choices=("pilot", "protected", "full"), default="full")
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--confirmation")
    apply.add_argument("--note-id", type=int, action="append")
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify")
    verify.add_argument("--scope", choices=("pilot", "protected", "full"), default="full")
    verify.add_argument("--baseline", action="store_true")
    verify.add_argument("--note-id", type=int, action="append")
    verify.set_defaults(func=command_verify)
    protect = sub.add_parser("protect-current")
    protect.add_argument("--note-id", type=int, required=True)
    protect.add_argument("--reason", required=True)
    protect.set_defaults(func=command_protect_current)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--scope", choices=("pilot", "protected", "full"), default="full")
    rollback.add_argument("--confirmation", required=True)
    rollback.add_argument("--note-id", type=int, action="append")
    rollback.set_defaults(func=command_rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except (WordAudioError, gw.MigrationError, RuntimeError) as exc:
        print(console_text(f"ERROR: {exc}", getattr(sys.stderr, "encoding", None)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
