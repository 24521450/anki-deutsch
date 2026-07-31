"""Prepare and safely wire Goethe A1-B1 word audio into Anki.

Source precedence is validated Duden (A1 before A2 before B1), newly resolved exact
Duden audio, exact Wikimedia Commons pronunciation, Wiktionary pronunciation,
then Gemini TTS.  The only
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
import gemini_tts
import goethe_completion as completion
import goethe_apkg as apkg
import goethe_scope as scope
import goethe_werkstatt_migrate as gw


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_word_audio"
WORK_AUDIO = ROOT / "audio" / "goethe_word_audio"
DUDEN_EXTRA_DIR = WORK_AUDIO / "duden"
GEMINI_DIR = WORK_AUDIO / "gemini"
COMMONS_DIR = WORK_AUDIO / "commons"
WIKTIONARY_DIR = WORK_AUDIO / "wiktionary"
PROTECTED_DIR = ROOT / "audio" / "protected"
MANIFEST_PATH = STATE / "manifest.json"
DUDEN_EXTRA_INDEX = STATE / "duden_extra.json"
DUDEN_RESCAN_REPORT = STATE / "duden_fallback_rescan.json"
GEMINI_AUDIT_REPORT = STATE / "gemini_audit_report.json"
WORD_ASR_INDEX = STATE / "semantic_asr.json"
# Retained only to identify provenance for historical media already in Anki.
EDGE_INDEX = STATE / "edge.json"
GEMINI_INDEX = STATE / "gemini.json"
COMMONS_INDEX = STATE / "commons.json"
WIKTIONARY_INDEX = STATE / "wiktionary.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
OVERRIDES_PATH = ROOT / "review" / "goethe_word_audio_overrides.json"
APPROVED_AUDIO_PATH = ROOT / "review" / "goethe_word_audio_approved.json"
SEIN_AUDIT_PATH = ROOT / "review" / "goethe_sein_audio_audit.json"
COMMONS_ATTRIBUTION_PATH = ROOT / "review" / "wikimedia_commons_audio_attribution.json"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
LEVEL_DECKS = scope.LEVEL_DECK
MANIFEST_SCHEMA_VERSION = 7
DUDEN_RESOLVER_VERSION = 3
APPLY_CONFIRMATION = "APPLY_GOETHE_WORD_AUDIO"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_WORD_AUDIO"
GEMINI_VOICES = tuple(gemini_tts.VOICES)
GEMINI_CONFIG = {
    **gemini_tts.CONFIG,
    "voices": list(GEMINI_VOICES),
    "voice_policy": "sha256-canonical-spoken-text-parity-v1",
    "spoken_normalization": "nfc-bound-markers-alternative-dedupe-v1",
    "word_config_version": 1,
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
    if data.get("schema_version") not in {1, 2, 3, 4}:
        raise WordAudioError("unsupported spoken-text override schema")
    return data


def load_overrides() -> dict[str, str]:
    data = load_override_policy()
    values = data.get("spoken_text", {})
    if not isinstance(values, dict):
        raise WordAudioError("spoken_text overrides must be an object")
    return {clean(key): clean(value) for key, value in values.items() if clean(value)}


def load_sein_audio_audit() -> dict[int, dict[str, Any]]:
    data = load_json(SEIN_AUDIT_PATH, None)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise WordAudioError("sein audio audit is missing or stale")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise WordAudioError("sein audio audit entries must be a list")
    result: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise WordAudioError("sein audio audit entry must be an object")
        try:
            note_id = int(entry["note_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WordAudioError("sein audio audit note_id is invalid") from exc
        if note_id in result:
            raise WordAudioError(f"duplicate sein audio audit note: {note_id}")
        if entry.get("decision") not in {"approved_strip", "keep", "pending"}:
            raise WordAudioError(f"invalid sein audio decision: {note_id}")
        if not clean(entry.get("lemma")) or not clean(entry.get("spoken_text")):
            raise WordAudioError(f"sein audio audit identity is blank: {note_id}")
        result[note_id] = entry
    return result


def load_duden_page_overrides() -> dict[str, dict[str, str]]:
    values = load_override_policy().get("duden_pages", {})
    if not isinstance(values, dict):
        raise WordAudioError("duden_pages must be an object")
    pages: dict[str, dict[str, str]] = {}
    for raw_source_ref, raw in values.items():
        source_ref = clean(raw_source_ref)
        if not source_ref or not isinstance(raw, dict):
            raise WordAudioError(f"invalid Duden page override: {raw_source_ref!r}")
        item = {
            key: clean(raw.get(key, ""))
            for key in ("expected_lemma", "spoken_text", "url", "headword")
        }
        if (
            any(not item[key] for key in item)
            or not item["url"].startswith("https://www.duden.de/rechtschreibung/")
        ):
            raise WordAudioError(f"incomplete Duden page override: {source_ref}")
        pages[source_ref] = item
    return pages


def load_approved_audio() -> dict[str, dict[str, str]]:
    data = load_json(APPROVED_AUDIO_PATH, {"schema_version": 1, "entries": {}})
    entries = data.get("entries")
    if data.get("schema_version") != 1 or not isinstance(entries, dict):
        raise WordAudioError("unsupported approved word-audio registry")
    approved: dict[str, dict[str, str]] = {}
    required = (
        "expected_lemma", "spoken_text", "provider", "sha256",
        "source_url", "audio_url", "source_revision",
        "semantic_model", "semantic_transcript",
    )
    for raw_source_id, raw in entries.items():
        source_id = clean(raw_source_id)
        if not source_id or not isinstance(raw, dict):
            raise WordAudioError(f"invalid approved word audio: {raw_source_id!r}")
        item = {key: clean(raw.get(key, "")) for key in required}
        if (
            any(not item[key] for key in required)
            or item["provider"] not in {"duden", "commons", "wiktionary"}
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            or not item["source_url"].startswith("https://")
            or not item["audio_url"].startswith("https://")
            or gemini_tts._normalized_spoken_text(item["semantic_transcript"])
            != gemini_tts._normalized_spoken_text(item["spoken_text"])
        ):
            raise WordAudioError(f"incomplete approved word audio: {source_id}")
        approved[source_id] = item
    return approved


def approved_audio_for(
    fields: dict[str, str], spoken: str, approved: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    refs = dict.fromkeys([
        clean(fields.get("SourceID", "")),
        *split_refs(fields.get("SourceRefs", "")),
    ])
    matches = [(source_id, approved[source_id]) for source_id in refs if source_id in approved]
    if not matches:
        return None
    hashes = {item["sha256"] for _, item in matches}
    if len(hashes) != 1:
        raise WordAudioError(f"conflicting approved audio for {fields.get('Lemma')!r}")
    source_id, item = matches[0]
    if clean(fields.get("Lemma", "")) != item["expected_lemma"]:
        raise WordAudioError(f"approved audio lemma mismatch: {source_id}")
    if clean(spoken) != item["spoken_text"]:
        raise WordAudioError(f"approved audio spoken-text mismatch: {source_id}")
    return {**item, "source_id": source_id}


def enforce_approved_assignment(item: dict[str, Any]) -> None:
    approved = item.get("approved_audio")
    if not approved:
        return
    assigned = item.get("assignment", {})
    if (
        assignment_provider(item) != approved["provider"]
        or clean(assigned.get("sha256", "")) != approved["sha256"]
    ):
        raise WordAudioError(
            f"approved audio drift: {approved['source_id']} expected "
            f"{approved['provider']} {approved['sha256']}"
        )
    assigned["semantic_qa"] = {
        "status": "exact",
        "transcript": approved["semantic_transcript"],
        "expected_spoken_text": approved["spoken_text"],
        "model": approved["semantic_model"],
        "source_revision": approved["source_revision"],
    }


def load_wortgruppen_duden_pages() -> dict[str, list[str]]:
    pages: dict[str, list[str]] = {}
    for path in completion.WG_FILES.values():
        for row in completion.parse_wortgruppen(path):
            values = re.split(
                r"<br\s*/?>|\s+",
                html.unescape(row.get("dictionary_sources", "")),
                flags=re.I,
            )
            urls = list(dict.fromkeys(
                value.strip()
                for value in values
                if value.startswith("https://www.duden.de/rechtschreibung/")
            ))
            if urls:
                pages[row["id"]] = urls
    return pages


def duden_page_specs(
    fields: dict[str, str],
    spoken: str,
    overrides: dict[str, dict[str, str]],
    *,
    source_pages: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    refs = [
        clean(fields.get("SourceID", "")),
        *split_refs(fields.get("SourceRefs", "")),
    ]
    reviewed: list[dict[str, Any]] = []
    for source_ref in dict.fromkeys(refs):
        item = overrides.get(source_ref)
        if item is None:
            continue
        if clean(fields.get("Lemma", "")) != item["expected_lemma"]:
            raise WordAudioError(f"Duden page lemma mismatch: {source_ref}")
        if clean(spoken) != item["spoken_text"]:
            raise WordAudioError(f"Duden page spoken-text mismatch: {source_ref}")
        reviewed.append({**item, "source_ref": source_ref, "reviewed": True})
    if reviewed:
        urls = {item["url"] for item in reviewed}
        if len(urls) != 1:
            raise WordAudioError(
                f"conflicting Duden page overrides for {fields.get('Lemma')!r}"
            )
        return reviewed[:1]

    source_pages = (
        source_pages
        if source_pages is not None
        else load_wortgruppen_duden_pages()
    )
    return [
        {
            "source_ref": source_ref,
            "expected_lemma": clean(fields.get("Lemma", "")),
            "spoken_text": clean(spoken),
            "url": url,
            "headword": "",
            "reviewed": False,
        }
        for source_ref in dict.fromkeys(refs)
        for url in source_pages.get(source_ref, [])
    ]


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
                schema in {3, 4}
                and not re.fullmatch(
                    r"[0-9a-f]{40}", item.get("original_sha1", "")
                )
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
    keys = [fields.get("SourceID", ""), *refs, fields.get("Lemma", ""), raw]
    for key in dict.fromkeys(clean(value) for value in keys if clean(value)):
        if clean(key) in overrides:
            return canonical_spoken_identity(overrides[clean(key)])
    value = clean(raw)
    if value.endswith("-") or UNSAFE_SPOKEN_RE.search(value):
        raise WordAudioError(f"missing spoken-text override for {fields.get('Lemma')!r}")
    if not value:
        raise WordAudioError("empty spoken text")
    return canonical_spoken_identity(value)


def reviewed_spoken_override(
    fields: dict[str, str], spoken: str, overrides: dict[str, str]
) -> bool:
    expected = canonical_spoken_identity(spoken).casefold()
    keys = split_refs(fields.get("SourceRefs", "")) + [
        clean(fields.get("SourceID", "")),
        clean(fields.get("Lemma", "")),
    ]
    return any(
        key in overrides
        and canonical_spoken_identity(overrides[key]).casefold() == expected
        for key in keys
    )


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
        != canonical_spoken_identity(expected_spoken).casefold()
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
    if manifest.get("gemini_config") != GEMINI_CONFIG or manifest.get("commons_config") != COMMONS_CONFIG:
        raise WordAudioError("word-audio generator config is stale; rebuild it")
    if manifest.get("wiktionary_config") != WIKTIONARY_CONFIG:
        raise WordAudioError("word-audio Wiktionary config is stale; rebuild it")
    if manifest.get("source_order") != ["duden_local", "duden_extra", "commons", "wiktionary", "gemini"]:
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
        if prepared_scope not in {
            "full", "protected", "targeted", "edge", "gemini-audit",
        }:
            raise WordAudioError("word-audio manifest has an unsupported prepared scope")
        if prepared_scope == "protected":
            required = [item for item in notes.values() if item.get("protected_audio")]
        elif prepared_scope in {"targeted", "edge", "gemini-audit"}:
            prepared_ids = set(map(int, manifest.get("prepared_note_ids", [])))
            if not prepared_ids:
                raise WordAudioError(f"{prepared_scope} word-audio manifest has no note IDs")
            if not prepared_ids <= set(map(int, notes)):
                raise WordAudioError(f"{prepared_scope} word-audio manifest has unknown note IDs")
            if prepared_scope == "edge":
                expected_ids = {
                    int(item["note_id"])
                    for item in notes.values()
                    if word_audio_provider(item.get("old_word_audio", "")) == "edge"
                }
                if prepared_ids != expected_ids:
                    raise WordAudioError(
                        "edge word-audio manifest does not preserve the exact audited Edge note IDs"
                    )
            if prepared_scope == "gemini-audit":
                expected_ids = {
                    int(item["note_id"])
                    for item in notes.values()
                    if word_audio_provider(item.get("old_word_audio", "")) == "gemini"
                }
                if prepared_ids != expected_ids:
                    raise WordAudioError(
                        "Gemini audit manifest does not preserve the exact live Gemini note IDs"
                    )
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
    sein_audio_audit = load_sein_audio_audit()
    duden_page_overrides = load_duden_page_overrides()
    wortgruppen_pages = load_wortgruppen_duden_pages()
    protected_audio = load_protected_audio()
    approved_audio = load_approved_audio()
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
            note_item["spoken_override_reviewed"] = reviewed_spoken_override(
                fields, text, overrides
            )
            note_item["lookup_pos"] = (
                "" if note_item["spoken_override_reviewed"] else fields.get("POS", "")
            )
            note_item["lookup_gender"] = (
                "" if note_item["spoken_override_reviewed"] else fields.get("Gender", "")
            )
            approved = approved_audio_for(fields, text, approved_audio)
            if approved:
                note_item["approved_audio"] = approved
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
                pages = [] if protected else duden_page_specs(
                    fields,
                    text,
                    duden_page_overrides,
                    source_pages=wortgruppen_pages,
                )
                request = {
                    "text": text,
                    "pos": note_item["lookup_pos"],
                    "gender": note_item["lookup_gender"],
                }
                if pages:
                    request["duden_pages"] = [
                        {
                            key: page.get(key)
                            for key in ("url", "headword", "reviewed")
                        }
                        for page in pages
                    ]
                note_item.update({
                    "spoken_text": text,
                    "duden_pages": pages,
                    "request_key": canonical_hash(request),
                    "skip_duden": bool(protected),
                })
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
        "gemini_config": GEMINI_CONFIG,
        "commons_config": COMMONS_CONFIG,
        "wiktionary_config": WIKTIONARY_CONFIG,
        "source_order": ["duden_local", "duden_extra", "commons", "wiktionary", "gemini"],
        "sein_audio_audit": sein_audio_audit,
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
        reviewed = bool(item.get("spoken_override_reviewed"))
        group = groups.setdefault(key, {
            "request_key": key, "spoken_text": item["spoken_text"],
            "pos": item.get("lookup_pos", "" if reviewed else item["pos"]),
            "gender": item.get("lookup_gender", "" if reviewed else item["gender"]),
            "note_ids": [], "skip_duden": True, "required_providers": set(),
            "duden_pages": item.get("duden_pages", []),
            "spoken_override_reviewed": bool(item.get("spoken_override_reviewed")),
        })
        if group["duden_pages"] != item.get("duden_pages", []):
            raise WordAudioError(
                f"conflicting Duden page policy for {group['spoken_text']!r}"
            )
        group["note_ids"].append(item["note_id"])
        group["skip_duden"] = group["skip_duden"] and bool(item.get("skip_duden"))
        group["spoken_override_reviewed"] = (
            group["spoken_override_reviewed"]
            or bool(item.get("spoken_override_reviewed"))
        )
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
    if cached.get("status") == "technical_error":
        return not refresh_negative
    if cached.get("status") not in {"unresolved", "ambiguous"}:
        return False
    # These outcomes are conclusive exact-identity results and remain valid
    # across resolver revisions.  Re-probing them on every audit caused long,
    # repeated Duden crawls without adding evidence.
    if cached.get("match_method") in {
        "sitemap-not-found",
        "sitemap-page-no-audio",
        "provider-policy",
    }:
        return True
    if cached.get("resolver_version") != DUDEN_RESOLVER_VERSION:
        return False
    if not refresh_negative:
        return True
    return cached.get("match_method") in {
        "sitemap-not-found",
        "sitemap-page-no-audio",
        "provider-policy",
    }


async def resolve_direct_duden_page(
    session: aiohttp.ClientSession,
    key: str,
    group: dict[str, Any],
    *,
    throttle: duden.RequestThrottle,
) -> dict[str, Any] | None:
    specs = group.get("duden_pages", [])
    if not specs:
        return None
    accepted: list[tuple[dict[str, Any], duden.DudenPage]] = []
    candidates: list[dict[str, Any]] = []
    technical_errors: list[str] = []
    reviewed = any(spec.get("reviewed") for spec in specs)
    for spec in specs:
        try:
            status, html_text, _ = await duden.fetch_page(
                session, spec["url"], throttle=throttle
            )
        except Exception as exc:
            if reviewed:
                return {
                    "status": "technical_error",
                    "reason": f"reviewed Duden page request failed: {exc}",
                    "match_method": "reviewed-page-technical-error",
                    "duden_page_url": spec["url"],
                }
            technical_errors.append(f"{spec['url']}: {exc}")
            continue
        if status != 200:
            if reviewed:
                return {
                    "status": "technical_error",
                    "reason": f"reviewed Duden page returned HTTP {status}",
                    "match_method": "reviewed-page-technical-error",
                    "duden_page_url": spec["url"],
                }
            technical_errors.append(f"{spec['url']}: HTTP {status}")
            continue
        try:
            page = duden.parse_duden_page(html_text, requested_url=spec["url"])
        except Exception as exc:
            if reviewed:
                return {
                    "status": "technical_error",
                    "reason": f"reviewed Duden page parse failed: {exc}",
                    "match_method": "reviewed-page-technical-error",
                    "duden_page_url": spec["url"],
                }
            technical_errors.append(f"{spec['url']}: parse failed: {exc}")
            continue
        candidate = {
            "canonical_url": page.canonical_url,
            "headword": page.headword,
            "wordart": page.wordart,
            "pos_labels": list(page.pos_labels),
            "gender": page.h1_gender,
            "audio": list(page.audio_candidates),
            "reviewed": bool(spec.get("reviewed")),
        }
        candidates.append(candidate)
        expected_headword = (
            spec["headword"] if spec.get("reviewed") else group["spoken_text"]
        )
        if not duden.exact_audit_headword_matches(
            expected_headword, page.headword
        ):
            continue
        if not spec.get("reviewed"):
            if group.get("pos") and page.pos_labels and not duden.pos_matches(
                group["pos"], page.pos_labels
            ):
                continue
            actual_gender = (
                "pl"
                if "pluralwort" in duden.normalize_text(page.wordart).lower()
                else page.h1_gender
            )
            if (
                group.get("gender")
                and actual_gender is not None
                and not duden.gender_matches(group["gender"], actual_gender)
            ):
                continue
        if page.audio_candidates:
            accepted.append((spec, page))
    if not accepted:
        if reviewed:
            return {
                "status": "unresolved",
                "reason": "reviewed Duden page has no matching audio",
                "match_method": "reviewed-page-no-audio",
                "duden_page_url": specs[0]["url"],
                "candidate_pages": candidates,
            }
        if technical_errors:
            return {
                "status": "technical_error",
                "reason": "; ".join(technical_errors[:5]),
                "match_method": "direct-page-technical-error",
                "duden_page_url": specs[0]["url"],
                "candidate_pages": candidates,
            }
        return None
    audio_urls = {
        page.audio_candidates[0]["audio_url"] for _, page in accepted
    }
    if len(audio_urls) != 1:
        return {
            "status": "ambiguous",
            "reason": "direct Duden pages have different audio",
            "match_method": "direct-page-ambiguous-audio",
            "duden_page_url": accepted[0][1].canonical_url,
            "candidate_pages": candidates,
        }
    spec, page = accepted[0]
    audio = page.audio_candidates[0]
    target = DUDEN_EXTRA_DIR / f"{key}.mp3"
    size, sha256, content_type, etag = await duden.download_audio(
        session, audio["audio_url"], target, throttle=throttle
    )
    return {
        "status": "ok",
        "reason": (
            "reviewed canonical Duden page"
            if spec.get("reviewed")
            else "exact Wortgruppen Duden page"
        ),
        "match_method": (
            "reviewed-canonical-page"
            if spec.get("reviewed")
            else "wortgruppen-direct-page"
        ),
        "duden_page_url": page.canonical_url,
        "duden_audio_url": audio["audio_url"],
        "file_id": audio.get("file_id"),
        "candidate_pages": candidates,
        "path": str(target),
        "size": size,
        "sha256": sha256,
        "content_type": content_type,
        "etag": etag,
    }


async def prepare_duden(
    groups: dict[str, dict[str, Any]], *, refresh_negative: bool = False,
    fail_on_technical_error: bool = True,
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
        if (
            refresh_negative
            and cached
            and not group.get("duden_pages")
            and cached.get("match_method") in {
                "sitemap-ambiguous-audio",
                "sitemap-metadata-conflict",
            }
        ):
            candidate_urls = list(dict.fromkeys(
                clean(page.get("canonical_url", ""))
                for page in cached.get("candidate_pages", [])
                if clean(page.get("canonical_url", "")).startswith(
                    "https://www.duden.de/rechtschreibung/"
                )
            ))
            if candidate_urls:
                group = {
                    **group,
                    "duden_pages": [{
                        "source_ref": "",
                        "expected_lemma": "",
                        "spoken_text": group["spoken_text"],
                        "url": url,
                        "headword": "",
                        "reviewed": False,
                    } for url in candidate_urls],
                    "cached_page_refresh": True,
                }
        pending[key] = (
            number,
            group,
            duden.SourceRow(
                number,
                group["spoken_text"],
                "" if group.get("spoken_override_reviewed") else group["pos"],
                "" if group.get("spoken_override_reviewed") else group["gender"],
                "",
                "",
                "",
            ),
        )
    atomic_json(DUDEN_EXTRA_INDEX, index)
    if not pending:
        return index

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        throttle = duden.RequestThrottle()
        direct_keys: set[str] = set()
        for progress, (key, (number, group, row)) in enumerate(
            sorted(pending.items()), 1
        ):
            result = await resolve_direct_duden_page(
                session, key, group, throttle=throttle
            )
            if result is None:
                if group.get("cached_page_refresh"):
                    direct_keys.add(key)
                continue
            direct_keys.add(key)
            result.update({
                "request_key": key,
                "spoken_text": group["spoken_text"],
                "resolver_version": DUDEN_RESOLVER_VERSION,
                "updated_utc": now_utc(),
            })
            items[key] = result
            index.pop("cooldown_until", None)
            atomic_json(DUDEN_EXTRA_INDEX, index)
            print(console_text(
                f"duden direct {progress}/{len(pending)} "
                f"{group['spoken_text']!r}: {result['status']}"
            ))
        pending = {
            key: value for key, value in pending.items()
            if key not in direct_keys
        }
        if not pending:
            return index
        try:
            lexeme_index = await duden.build_lexeme_index_for_rows(
                session, [entry[2] for entry in pending.values()], throttle=throttle
            )
        except duden.TechnicalError as exc:
            if fail_on_technical_error:
                raise WordAudioError(f"Duden sitemap technical error: {exc}") from exc
            for key, (_, group, _) in pending.items():
                items[key] = {
                    "request_key": key,
                    "spoken_text": group["spoken_text"],
                    "status": "technical_error",
                    "reason": f"Duden sitemap technical error: {exc}",
                    "match_method": "sitemap-technical-error",
                    "resolver_version": DUDEN_RESOLVER_VERSION,
                    "updated_utc": now_utc(),
                }
            atomic_json(DUDEN_EXTRA_INDEX, index)
            return index
        for progress, (key, (number, group, row)) in enumerate(sorted(pending.items()), 1):
            result = await resolve_direct_duden_page(
                session, key, group, throttle=throttle
            )
            if result is None:
                try:
                    resolution, pages = await duden.resolve_exact_sitemap_row(
                        session, row, lexeme_index, throttle=throttle
                    )
                except Exception as exc:
                    result = {
                        "status": "technical_error",
                        "reason": f"Duden exact lookup failed: {exc}",
                        "match_method": "sitemap-technical-error",
                    }
                else:
                    result = duden.resolution_to_row(resolution)
                    result["candidate_pages"] = [{
                        "canonical_url": page.canonical_url,
                        "headword": page.headword,
                        "wordart": page.wordart,
                        "pos_labels": list(page.pos_labels),
                        "gender": page.h1_gender,
                        "audio": list(page.audio_candidates),
                    } for page in pages]
            result.update({
                "request_key": key, "spoken_text": group["spoken_text"],
                "resolver_version": DUDEN_RESOLVER_VERSION,
                "updated_utc": now_utc(),
            })
            if (
                result.get("status") == "ok"
                and result.get("duden_audio_url")
                and not result.get("path")
            ):
                target = DUDEN_EXTRA_DIR / f"{key}.mp3"
                try:
                    size, sha256, content_type, etag = await duden.download_audio(
                        session,
                        result["duden_audio_url"],
                        target,
                        throttle=throttle,
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
            if result["status"] == "technical_error" and fail_on_technical_error:
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


async def prepare_commons(
    groups: dict[str, dict[str, Any]],
    duden_index: dict[str, Any],
    *,
    refresh_negative: bool = False,
) -> dict[str, Any]:
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
            reusable = (
                cached.get("status") == "ok"
                or (
                    not refresh_negative
                    and cached.get("status") in {"unresolved", "ambiguous"}
                )
            )
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


async def prepare_wiktionary(
    groups: dict[str, dict[str, Any]],
    duden_index: dict[str, Any],
    commons_index: dict[str, Any],
    *,
    refresh_negative: bool = False,
) -> dict[str, Any]:
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
        and (
            items.get(key, {}).get("status") != "ok"
            if refresh_negative
            else items.get(key, {}).get("status") not in {"ok", "unresolved", "ambiguous"}
        )
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


def gemini_voice_for(text: str) -> str:
    identity = canonical_spoken_identity(text)
    parity = hashlib.sha256(identity.encode("utf-8")).digest()[0] & 1
    return GEMINI_VOICES[parity]


def gemini_audio_id(text: str) -> str:
    spoken_text = canonical_spoken_identity(text)
    return canonical_hash({
        "spoken_text": spoken_text,
        "voice": gemini_voice_for(spoken_text),
        "config": GEMINI_CONFIG,
    })


def valid_gemini_item(
    item: dict[str, Any], *, spoken_text: str, voice: str
) -> bool:
    try:
        validate_audio(Path(item["path"]), item.get("sha256"), item.get("size"))
        return (
            item.get("status") == "ok"
            and item.get("spoken_text") == spoken_text
            and item.get("voice") == voice
            and item.get("qa_status") in {"exact", "verified_equivalent"}
            and bool(clean(item.get("asr_transcript")))
            and float(item.get("duration_seconds", 0)) > 0
        )
    except (KeyError, TypeError, ValueError, WordAudioError):
        return False


async def prepare_gemini(
    groups: dict[str, dict[str, Any]],
    duden_index: dict[str, Any],
    commons_index: dict[str, Any],
    wiktionary_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wiktionary_index = wiktionary_index or {"items": {}}
    if GEMINI_VOICES != ("Kore", "Charon"):
        raise WordAudioError(
            f"Gemini word voices must be Kore then Charon, got {GEMINI_VOICES}"
        )
    index = load_json(
        GEMINI_INDEX,
        {"schema_version": 1, "config": GEMINI_CONFIG, "items": {}},
    )
    if index.get("schema_version") != 1 or index.get("config") != GEMINI_CONFIG:
        raise WordAudioError("existing Gemini index uses a different configuration")
    items = index.setdefault("items", {})
    GEMINI_DIR.mkdir(parents=True, exist_ok=True)
    needed = [
        group for key, group in sorted(groups.items())
        if duden_index["items"].get(key, {}).get("status") != "ok"
        and commons_index["items"].get(key, {}).get("status") != "ok"
        and wiktionary_index["items"].get(key, {}).get("status") != "ok"
    ]
    pending: list[tuple[dict[str, Any], str, str, str, Path]] = []
    for group in needed:
        spoken_text = canonical_spoken_identity(group["spoken_text"])
        voice = gemini_voice_for(spoken_text)
        audio_id = gemini_audio_id(spoken_text)
        cached = items.get(audio_id)
        if cached and valid_gemini_item(
            cached, spoken_text=spoken_text, voice=voice
        ):
            continue
        target = GEMINI_DIR / f"{audio_id}.mp3"
        pending.append((group, spoken_text, voice, audio_id, target))

    if not pending:
        atomic_json(GEMINI_INDEX, index)
        return index

    semaphore = asyncio.Semaphore(2 * len(gemini_tts._api_keys()))

    async def generate_word(
        request: tuple[dict[str, Any], str, str, str, Path]
    ) -> dict[str, Any]:
        _, spoken_text, voice, audio_id, target = request
        try:
            async with semaphore:
                generated = await gemini_tts.generate_verified_mp3(
                    text=spoken_text,
                    voice=voice,
                    purpose="word",
                    target=target,
                )
        except gemini_tts.GeminiTTSError as exc:
            raise WordAudioError(
                f"Gemini TTS failed for {spoken_text!r}: {exc}"
            ) from exc
        item = {
            **generated,
            "audio_id": audio_id,
            "spoken_text": spoken_text,
            "voice": voice,
        }
        if not valid_gemini_item(item, spoken_text=spoken_text, voice=voice):
            raise WordAudioError(
                f"Gemini TTS returned incomplete QA metadata for {spoken_text!r}"
            )
        return item

    tasks = [
        asyncio.create_task(generate_word(request))
        for request in pending
    ]
    failures: list[Exception] = []
    completed = 0
    for future in asyncio.as_completed(tasks):
        try:
            item = await future
        except Exception as exc:
            failures.append(exc)
            continue
        audio_id = item["audio_id"]
        items[audio_id] = item
        completed += 1
        if completed % 25 == 0:
            atomic_json(GEMINI_INDEX, index)
        print(console_text(
            f"gemini {completed}/{len(pending)} "
            f"{item['spoken_text']!r} ({item['voice']}): ok"
        ))
    atomic_json(GEMINI_INDEX, index)
    if failures:
        raise WordAudioError(
            f"Gemini generation completed with {completed} successes and "
            f"{len(failures)} failures: {failures[0]}"
        ) from failures[0]
    return index


def word_audio_provider(value: str) -> str:
    text = value.casefold()
    for provider in ("duden", "commons", "wiktionary", "gemini", "edge"):
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
        ("gemini", GEMINI_INDEX),
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
    spoken_overrides = load_overrides()
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
        reviewed_override = any(
            canonical_spoken_identity(spoken_overrides[key]).casefold()
            == expected_key
            for key in (
                split_refs(fields.get("SourceRefs", ""))
                + [clean(fields.get("SourceID", "")), clean(fields.get("Lemma", ""))]
            )
            if key in spoken_overrides
        )
        if expected_key in current_keys:
            provider_drift.append(row)
        elif equivalence and current_keys & equivalent_keys:
            row["reason"] = equivalence["reason"]
            reviewed_equivalences.append(row)
        elif reviewed_override:
            row["reason"] = "tracked spoken-text override changes the audio identity"
            semantic_mismatches.append(row)
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


def _conclusive_source_absence(item: dict[str, Any]) -> bool:
    if item.get("status") != "unresolved":
        return False
    evidence = clean(" ".join((
        str(item.get("reason", "")), str(item.get("match_method", "")),
    ))).casefold()
    uncertain = (
        "technical", "timeout", "rate", "cooldown", "failed", "error",
        "ambiguous", "conflict", "mismatch",
    )
    return not any(marker in evidence for marker in uncertain)


async def verify_human_assignment_semantics(
    manifest: dict[str, Any],
    note_ids: list[int],
    *,
    cache: dict[str, Any] | None = None,
    transcribe: Any | None = None,
    timeout_seconds: float = 90.0,
    checkpoint: Any | None = None,
    model_name: str = "gemini-3.6-flash",
) -> dict[str, Any]:
    cache = cache if cache is not None else {}
    transcribe = transcribe or gemini_tts.transcribe_strict_mp3
    for note_id in sorted(set(map(int, note_ids))):
        item = manifest["notes"][str(note_id)]
        assignment_item = item.get("assignment", {})
        if assignment_provider(item) not in {"duden", "commons", "wiktionary"}:
            continue
        sha256 = clean(assignment_item.get("sha256", ""))
        spoken = clean(item.get("spoken_text", ""))
        cached = cache.get(sha256, {})
        transcript = clean(cached.get("transcript", ""))
        error = clean(cached.get("error", ""))
        if not transcript and not error:
            try:
                transcript = clean(await asyncio.wait_for(
                    transcribe(Path(assignment_item["path"])),
                    timeout=timeout_seconds,
                ))
            except asyncio.TimeoutError:
                error = f"strict ASR timed out after {timeout_seconds:g} seconds"
            except Exception as exc:
                error = str(exc)
            if transcript:
                cache[sha256] = {
                    "status": "ok",
                    "transcript": transcript,
                    "model": model_name,
                    "checked_utc": now_utc(),
                }
            elif error:
                cache[sha256] = {
                    "status": "error",
                    "error": error,
                    "model": model_name,
                    "checked_utc": now_utc(),
                }
            if checkpoint is not None:
                checkpoint(cache)
        if error:
            qa = {"status": "error", "error": error}
        else:
            exact = (
                gemini_tts._normalized_spoken_text(transcript)
                == gemini_tts._normalized_spoken_text(spoken)
            )
            qa = {
                "status": "exact" if exact else "mismatch",
                "transcript": transcript,
                "expected_spoken_text": spoken,
                "model": clean(cache.get(sha256, {}).get("model", "")) or model_name,
            }
        assignment_item["semantic_qa"] = qa
    return cache


def build_gemini_audit_report(
    manifest: dict[str, Any],
    duden_index: dict[str, Any],
    commons_index: dict[str, Any],
    wiktionary_index: dict[str, Any],
) -> dict[str, Any]:
    """Classify live Gemini word audio without guessing through uncertainty."""
    semantic = manifest.get("live_audio_audit", {})
    review_ids = {
        int(item["note_id"])
        for name in ("semantic_candidates", "unknown_provenance")
        for item in semantic.get(name, [])
    }
    mismatch_ids = {
        int(item["note_id"])
        for item in semantic.get("semantic_mismatches", [])
    }
    result = {
        "schema_version": 1,
        "wrong_certain": [],
        "needs_review": [],
        "valid_fallback": [],
    }
    indexes = {
        "duden": duden_index.get("items", {}),
        "commons": commons_index.get("items", {}),
        "wiktionary": wiktionary_index.get("items", {}),
    }
    for item in sorted(
        manifest.get("notes", {}).values(), key=lambda value: int(value["note_id"])
    ):
        if word_audio_provider(item.get("old_word_audio", "")) != "gemini":
            continue
        note_id = int(item["note_id"])
        key = item.get("request_key", "")
        evidence = {provider: values.get(key, {}) for provider, values in indexes.items()}
        row = {
            "note_id": note_id,
            "level": item.get("level", ""),
            "lemma": item.get("lemma", ""),
            "pos": item.get("pos", ""),
            "spoken_text": item.get("spoken_text", ""),
            "current_sha256": word_audio_sha256(item.get("old_word_audio", "")),
            "desired_provider": assignment_provider(item),
            "desired_sha256": item.get("assignment", {}).get("sha256", ""),
            "source_evidence": evidence,
        }
        provider_order = ("duden", "commons", "wiktionary")
        desired_provider = row["desired_provider"]
        if desired_provider in provider_order:
            # A selected exact assignment is affirmative evidence for its own
            # provider.  Only unresolved higher-priority providers can block
            # promotion; lower-priority search noise is irrelevant.
            considered_providers = provider_order[:provider_order.index(desired_provider)]
        else:
            considered_providers = provider_order
        uncertain = any(
            source.get("status") in {"ambiguous", "technical_error", "invalid"}
            or (
                source.get("status") == "unresolved"
                and not _conclusive_source_absence(source)
            )
            for provider in considered_providers
            for source in (evidence[provider],)
            if source
        )
        if (
            row["desired_provider"] in {"duden", "commons", "wiktionary"}
            and item.get("assignment", {}).get("semantic_qa", {}).get("status")
            != "exact"
        ):
            uncertain = True
        if note_id in review_ids or uncertain:
            row["reason"] = "source or semantic evidence requires review"
            result["needs_review"].append(row)
        elif row["desired_provider"] in {"duden", "commons", "wiktionary"}:
            row["reason"] = "unique exact human recording replaces Gemini"
            result["wrong_certain"].append(row)
        elif note_id in mismatch_ids and row["desired_sha256"] != row["current_sha256"]:
            row["reason"] = "semantic mismatch has a newly verified replacement"
            result["wrong_certain"].append(row)
        elif all(_conclusive_source_absence(source) for source in evidence.values()):
            row["reason"] = "all exact human providers are conclusively unavailable"
            result["valid_fallback"].append(row)
        else:
            row["reason"] = "source evidence is incomplete"
            result["needs_review"].append(row)
    result["counts"] = {
        name: len(result[name])
        for name in ("wrong_certain", "needs_review", "valid_fallback")
    }
    return result


def validate_change_set(manifest: dict[str, Any]) -> None:
    semantic_repairs = {
        int(item["note_id"])
        for item in manifest.get("live_audio_audit", {}).get(
            "semantic_mismatches", []
        )
    }
    prepared_scope = manifest.get("prepared_scope")
    if prepared_scope == "gemini-audit":
        audit = manifest.get("gemini_audit")
        if not audit:
            # Candidate transitions are deliberately validated only after the
            # classification/ASR gate has produced an approved change set.
            return
        prepared_ids = {
            int(item["note_id"]) for item in audit.get("wrong_certain", [])
        }
    else:
        prepared_ids = (
            set(map(int, manifest.get("prepared_note_ids", [])))
            if prepared_scope in {"targeted", "edge"}
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
        if (
            prepared_scope == "gemini-audit"
            and old_provider == "gemini"
            and desired_provider in {"duden", "commons", "wiktionary"}
            and item.get("assignment", {}).get("semantic_qa", {}).get("status")
            == "exact"
        ):
            continue
        if old_provider in {"commons", "wiktionary", "edge", "gemini"} and desired_provider == "duden":
            continue
        if old_provider == "edge" and desired_provider in {"commons", "wiktionary", "gemini"}:
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
    gemini_index: dict[str, Any],
    wiktionary_index: dict[str, Any] | None = None,
    *,
    note_ids: list[int] | None = None,
    prepared_scope: str | None = None,
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
            enforce_approved_assignment(item)
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
            enforce_approved_assignment(item)
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
            gemini_id = gemini_audio_id(item["spoken_text"])
            gemini = gemini_index["items"].get(gemini_id)
            if not gemini or gemini.get("status") != "ok":
                raise WordAudioError(f"missing Gemini result for {item['lemma']!r}")
            item["assignment"] = assignment(
                "gemini",
                Path(gemini["path"]),
                detail=gemini,
                lemma_identity=item["lemma_identity"],
                spoken_text=item["spoken_text"],
            )
        enforce_approved_assignment(item)
        counts[item["assignment"]["source"]] += 1
    expected = len(items)
    if sum(counts.values()) != expected:
        raise WordAudioError("prepared manifest is incomplete")
    manifest.update({
        "prepared_utc": now_utc(),
        "prepared_scope": prepared_scope or ("targeted" if selected else "full"),
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
    if _.scope in {"protected", "edge", "gemini-audit"} and _.note_id:
        raise WordAudioError(f"--note-id cannot be combined with {_.scope} scope")
    if _.scope == "edge":
        if _.offline:
            raise WordAudioError("Edge migration scope must be discovered from the live deck")
        target_ids = selected_ids(manifest, "edge")
        if not target_ids:
            raise WordAudioError("live deck has no historical Edge WordAudio notes")
    elif _.scope == "gemini-audit":
        # Preparation always reclassifies the full live Gemini baseline.  The
        # prior report narrows apply/verify, but must never narrow a resumed
        # audit or an empty old approval set becomes self-locking.
        target_ids = gemini_baseline_ids(manifest)
        if not target_ids:
            raise WordAudioError("live deck has no Gemini WordAudio notes")
    else:
        target_ids = selected_ids(manifest, "full", _.note_id) if _.note_id else []
    if target_ids:
        target_set = set(target_ids)
        groups = {
            key: group for key, group in groups.items()
            if target_set.intersection(map(int, group["note_ids"]))
    }
    if _.scope == "protected":
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
    refresh_negative = (
        _.refresh_duden_fallbacks
        or (_.scope == "gemini-audit" and not _.offline)
    )
    duden_index = await prepare_duden(
        groups,
        refresh_negative=refresh_negative,
        fail_on_technical_error=_.scope != "gemini-audit",
    )
    commons_index = await prepare_commons(
        groups, duden_index, refresh_negative=refresh_negative
    )
    wiktionary_index = await prepare_wiktionary(
        groups, duden_index, commons_index, refresh_negative=refresh_negative
    )
    gemini_index = await prepare_gemini(groups, duden_index, commons_index, wiktionary_index)
    final = finalize_manifest(
        manifest,
        duden_index,
        commons_index,
        gemini_index,
        wiktionary_index,
        note_ids=target_ids,
        prepared_scope=(
            "edge" if _.scope == "edge"
            else "gemini-audit" if _.scope == "gemini-audit"
            else None
        ),
    )
    if _.scope == "gemini-audit":
        asr_document = load_json(
            WORD_ASR_INDEX, {"schema_version": 1, "items": {}}
        )
        if asr_document.get("schema_version") != 1:
            raise WordAudioError("unsupported semantic ASR cache schema")
        await verify_human_assignment_semantics(
            final,
            target_ids,
            cache=asr_document.setdefault("items", {}),
            checkpoint=lambda _items: atomic_json(WORD_ASR_INDEX, asr_document),
        )
        atomic_json(WORD_ASR_INDEX, asr_document)
        report = build_gemini_audit_report(
            final, duden_index, commons_index, wiktionary_index
        )
        report.update({
            "created_utc": now_utc(),
            "baseline_gemini_note_ids": target_ids,
        })
        report["report_sha256"] = canonical_hash(report)
        final["gemini_audit"] = report
        final["gemini_audit_report"] = str(GEMINI_AUDIT_REPORT)
        validate_change_set(final)
        atomic_json(GEMINI_AUDIT_REPORT, report)
        atomic_json(MANIFEST_PATH, final)
    print(json.dumps({
        "notes": len(target_ids) if target_ids else final["note_count"],
        "scope": final["prepared_scope"],
        "counts": (
            final["gemini_audit"]["counts"]
            if _.scope == "gemini-audit" else final["counts"]
        ),
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


def validate_prepared_live_baseline(
    manifest: dict[str, Any], records: dict[int, dict[str, Any]]
) -> None:
    if set(map(int, manifest["notes"])) != set(records):
        raise WordAudioError("prepared note ID set differs from live deck")
    for note_id, record in records.items():
        prepared = manifest["notes"][str(note_id)]
        if prepared["source_signature"] != source_signature(record["fields"]):
            raise WordAudioError(f"source fields changed after preparation: {note_id}")
        if prepared.get("old_word_audio", "") != record["fields"].get("WordAudio", ""):
            raise WordAudioError(f"WordAudio changed after preparation: {note_id}")


def command_snapshot(_: argparse.Namespace) -> None:
    manifest = load_json(MANIFEST_PATH, None)
    if not manifest:
        raise WordAudioError("prepared manifest missing")
    validate_manifest(manifest, require_prepared=True)
    records = live_records()
    validate_prepared_live_baseline(manifest, records)
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
    elif scope == "edge":
        if manifest.get("prepared_scope") == "edge":
            selected = sorted(set(map(int, manifest.get("prepared_note_ids", []))))
        else:
            selected = sorted(
                int(item["note_id"])
                for item in manifest["notes"].values()
                if word_audio_provider(item.get("old_word_audio", "")) == "edge"
            )
    elif scope == "gemini-audit":
        audit = manifest.get("gemini_audit")
        if audit:
            selected = sorted(
                int(item["note_id"])
                for item in audit.get("wrong_certain", [])
            )
        else:
            selected = sorted(
                int(item["note_id"])
                for item in manifest["notes"].values()
                if word_audio_provider(item.get("old_word_audio", "")) == "gemini"
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


def gemini_baseline_ids(manifest: dict[str, Any]) -> list[int]:
    """Return every live Gemini note, independent of any prior audit report."""
    return sorted(
        int(item["note_id"])
        for item in manifest.get("notes", {}).values()
        if word_audio_provider(item.get("old_word_audio", "")) == "gemini"
    )


def require_prepared_scope(
    manifest: dict[str, Any],
    requested: str,
    note_ids: list[int] | None = None,
) -> None:
    prepared = manifest.get("prepared_scope", "full")
    if prepared == "protected" and requested != "protected":
        raise WordAudioError("manifest is prepared only for protected audio")
    if prepared == "edge" and requested != "edge":
        raise WordAudioError("manifest is prepared only for the audited Edge migration scope")
    if prepared == "gemini-audit" and requested != "gemini-audit":
        raise WordAudioError(
            "manifest is prepared only for the audited Gemini correction scope"
        )
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

    def read_values(ids: list[int]) -> dict[int, str]:
        notes: list[dict[str, Any]] = []
        for batch in gw.chunks(ids, 60):
            notes.extend(gw.anki("notesInfo", notes=batch))
        return {
            int(note["noteId"]): note.get("fields", {}).get("WordAudio", {}).get("value", "")
            for note in notes
        }

    actual = read_values(note_ids)
    missing = [note_id for note_id in note_ids if actual.get(note_id) != values[note_id]]
    for note_id in missing:
        gw.anki(
            "updateNoteFields",
            note={"id": note_id, "fields": {"WordAudio": values[note_id]}},
        )
    if missing:
        actual = read_values(note_ids)
    failed = [note_id for note_id in note_ids if actual.get(note_id) != values[note_id]]
    if failed:
        raise WordAudioError(f"Anki WordAudio write verification failed: {failed[:5]}")


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
    targeted = scope in {"protected", "edge", "gemini-audit"} or bool(note_ids)
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
    verify_baseline(records, snapshot, manifest)
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
    prepare.add_argument(
        "--scope", choices=("edge", "protected", "gemini-audit", "full"),
        default="full",
    )
    prepare.add_argument("--note-id", type=int, action="append")
    prepare.add_argument(
        "--refresh-duden-fallbacks", action="store_true",
        help="Re-probe cached non-Duden results through the exact Duden lexeme sitemap.",
    )
    prepare.set_defaults(func=command_prepare)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    apply = sub.add_parser("apply")
    apply.add_argument(
        "--scope", choices=("edge", "pilot", "protected", "gemini-audit", "full"),
        default="full",
    )
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--confirmation")
    apply.add_argument("--note-id", type=int, action="append")
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify")
    verify.add_argument(
        "--scope", choices=("edge", "pilot", "protected", "gemini-audit", "full"),
        default="full",
    )
    verify.add_argument("--baseline", action="store_true")
    verify.add_argument("--note-id", type=int, action="append")
    verify.set_defaults(func=command_verify)
    protect = sub.add_parser("protect-current")
    protect.add_argument("--note-id", type=int, required=True)
    protect.add_argument("--reason", required=True)
    protect.set_defaults(func=command_protect_current)
    rollback = sub.add_parser("rollback")
    rollback.add_argument(
        "--scope", choices=("edge", "pilot", "protected", "gemini-audit", "full"),
        default="full",
    )
    rollback.add_argument("--confirmation", required=True)
    rollback.add_argument("--note-id", type=int, action="append")
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
    except (WordAudioError, gw.MigrationError, RuntimeError) as exc:
        print(console_text(f"ERROR: {exc}", getattr(sys.stderr, "encoding", None)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
