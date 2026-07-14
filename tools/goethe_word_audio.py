"""Prepare and safely wire Goethe A1/A2 word audio into Anki.

Source precedence is validated Duden (A1 before A2), newly resolved exact
Duden audio, exact Wikimedia Commons pronunciation, then Edge TTS.  The only
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

import download_duden_a1_audio as duden
import goethe_completion as completion
import goethe_werkstatt_migrate as gw


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tools" / ".goethe_word_audio"
WORK_AUDIO = ROOT / "audio" / "goethe_word_audio"
DUDEN_EXTRA_DIR = WORK_AUDIO / "duden"
EDGE_DIR = WORK_AUDIO / "edge"
COMMONS_DIR = WORK_AUDIO / "commons"
MANIFEST_PATH = STATE / "manifest.json"
DUDEN_EXTRA_INDEX = STATE / "duden_extra.json"
EDGE_INDEX = STATE / "edge.json"
COMMONS_INDEX = STATE / "commons.json"
SNAPSHOT_PATH = STATE / "snapshot.json"
OVERRIDES_PATH = ROOT / "review" / "goethe_word_audio_overrides.json"
COMMONS_ATTRIBUTION_PATH = ROOT / "review" / "wikimedia_commons_audio_attribution.json"
MODEL = "Goethe Werkstatt"
PARENT_DECK = "Goethe Institute"
LEVEL_DECKS = {"A1": gw.A1_DECK, "A2": gw.A2_DECK}
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
SOURCE_FIELDS = ("Lemma", "POS", "Gender", "AcceptedAnswersDE", "SourceRefs", "CEFR")
PILOT_SIZE = 12
LIVE_NOTE_COUNT = 1530
LIVE_CARD_COUNT = 3060


class WordAudioError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip())


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
            continue
        note_cards = by_note.get(note_id, [])
        if not note_cards:
            raise WordAudioError(f"target note has no A1/A2 cards: {note_id}")
        if any(card["deckName"] != LEVEL_DECKS[level] for card in note_cards):
            raise WordAudioError(f"target note is in unexpected deck: {note_id}")
        records[note_id] = {
            "note_id": note_id,
            "model": note["modelName"],
            "fields": field_values(note),
            "tags": sorted(note.get("tags", [])),
            "cards": sorted(note_cards, key=lambda item: item["cardId"]),
        }
    card_count = sum(len(item["cards"]) for item in records.values())
    if len(records) != LIVE_NOTE_COUNT or card_count != LIVE_CARD_COUNT:
        raise WordAudioError(
            f"expected live baseline {LIVE_NOTE_COUNT} notes / {LIVE_CARD_COUNT} cards, got "
            f"{len(records)} / {card_count}"
        )
    return records


def split_refs(value: str) -> list[str]:
    return [clean(item) for item in value.split("|") if clean(item)]


def source_signature(fields: dict[str, str]) -> str:
    return canonical_hash({name: fields.get(name, "") for name in SOURCE_FIELDS})


def compatible_gender(source: str, target: str) -> bool:
    source, target = clean(source), clean(target)
    return not source or not target or source == target


def note_variants(fields: dict[str, str]) -> set[str]:
    values = [fields.get("Lemma", "")] + completion.split_answers(fields.get("AcceptedAnswersDE", ""))
    return {item for value in values for item in completion.source_variants(value)}


def source_matches(fields: dict[str, str], item: dict[str, Any], variants: set[str] | None = None) -> bool:
    word = completion.lemma_key(clean(item.get("word", "")))
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


def load_duden_catalog() -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_ref: dict[tuple[str, int], dict[str, Any]] = {}
    ok_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for level in ("A1", "A2"):
        root = ROOT / "audio" / level.lower()
        manifest_path = root / "words_manifest.jsonl"
        rows = duden.load_existing_manifest_rows(manifest_path)
        expected = 685 if level == "A1" else 1147
        if len(rows) != expected:
            raise WordAudioError(f"{level} Duden manifest row count mismatch")
        for item in rows:
            row = dict(item)
            row.update({"level": level, "path": str(root / "words" / item["output_filename"])})
            by_ref[(level, int(item["row"]))] = row
            if item.get("status") == "ok":
                validate_audio(Path(row["path"]), item.get("sha256"), item.get("size"))
                ok_index[completion.lemma_key(clean(row["word"]))].append(row)
    return by_ref, ok_index


MAIN_RE = re.compile(r"^(A[12])-MAIN-(\d{4})$")


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
        return sorted(direct, key=lambda item: (item["level"] != "A1", int(item["row"])))[0]
    candidates = list({
        (item["level"], int(item["row"])): item
        for variant in variants for item in ok_index.get(variant, []) if source_matches(fields, item, variants)
    }.values())
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item["level"] != "A1", int(item["row"])))
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
                choices.append((match.group(1) != "A1", int(match.group(2)), clean(item["word"])))
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


def load_overrides() -> dict[str, str]:
    data = load_json(OVERRIDES_PATH, {"spoken_text": {}})
    values = data.get("spoken_text", {})
    if not isinstance(values, dict):
        raise WordAudioError("spoken_text overrides must be an object")
    return {clean(key): clean(value) for key, value in values.items() if clean(value)}


UNSAFE_SPOKEN_RE = re.compile(r"[()/;]|\d|(^|\s)[A-Za-zÄÖÜäöüß]\.|,$")


def spoken_text(fields: dict[str, str], raw: str, overrides: dict[str, str]) -> str:
    refs = split_refs(fields.get("SourceRefs", ""))
    for key in refs + [fields.get("Lemma", ""), raw]:
        if clean(key) in overrides:
            return overrides[clean(key)]
    value = clean(raw)
    if value.endswith("-") or UNSAFE_SPOKEN_RE.search(value):
        raise WordAudioError(f"missing spoken-text override for {fields.get('Lemma')!r}")
    if not value:
        raise WordAudioError("empty spoken text")
    return value


def media_name(source: str, sha256: str) -> str:
    return f"_goethe_word_{source}_{sha256}.mp3"


def assignment(source: str, path: Path, *, detail: dict[str, Any]) -> dict[str, Any]:
    size, sha256 = validate_audio(path, detail.get("sha256"), detail.get("size"))
    return {
        "source": source,
        "path": str(path),
        "size": size,
        "sha256": sha256,
        "media_name": media_name("duden" if source.startswith("duden") else source, sha256),
        "detail": detail,
    }


def build_audit() -> dict[str, Any]:
    records = live_records()
    by_ref, ok_index = load_duden_catalog()
    overrides = load_overrides()
    notes: dict[str, Any] = {}
    missing_overrides: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for note_id, record in sorted(records.items()):
        fields = record["fields"]
        item = select_local_duden(fields, by_ref, ok_index)
        note_item: dict[str, Any] = {
            "note_id": note_id,
            "level": fields["CEFR"],
            "lemma": fields["Lemma"],
            "pos": fields.get("POS", ""),
            "gender": fields.get("Gender", ""),
            "source_refs": split_refs(fields.get("SourceRefs", "")),
            "source_signature": source_signature(fields),
            "old_word_audio": fields.get("WordAudio", ""),
        }
        if item:
            note_item["assignment"] = assignment("duden_local", Path(item["path"]), detail=item)
            counts["duden_local"] += 1
        else:
            raw = source_word(fields, by_ref)
            main_rows = matched_main_rows(fields, by_ref)
            try:
                text = spoken_text(fields, raw, overrides)
                non_lexeme_display = bool(re.search(r"\d|,", text))
                note_item.update({"spoken_text": text, "request_key": canonical_hash({
                    "text": text, "pos": fields.get("POS", ""), "gender": fields.get("Gender", "")
                }), "skip_duden": non_lexeme_display or (
                    bool(main_rows) and all(row.get("status") in {"unresolved", "ambiguous"} for row in main_rows)
                )})
                counts["needs_prepare"] += 1
            except WordAudioError as exc:
                note_item["error"] = str(exc)
                missing_overrides.append({
                    "note_id": note_id, "lemma": fields["Lemma"], "source_refs": note_item["source_refs"], "raw": raw,
                })
                counts["missing_override"] += 1
        notes[str(note_id)] = note_item
    manifest = {
        "schema_version": 2,
        "created_utc": now_utc(),
        "edge_config": EDGE_CONFIG,
        "commons_config": COMMONS_CONFIG,
        "source_order": ["duden_local", "duden_extra", "commons", "edge"],
        "note_count": len(notes),
        "counts": dict(counts),
        "missing_overrides": missing_overrides,
        "notes": notes,
    }
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
            "note_ids": [], "skip_duden": True,
        })
        group["note_ids"].append(item["note_id"])
        group["skip_duden"] = group["skip_duden"] and bool(item.get("skip_duden"))
    return groups


async def prepare_duden(groups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    index = load_json(DUDEN_EXTRA_INDEX, {"schema_version": 1, "items": {}})
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
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        throttle = duden.RequestThrottle()
        for number, (key, group) in enumerate(sorted(groups.items()), 1):
            cached = items.get(key)
            prior_technical_attempts = 0
            if cached and cached.get("status") == "technical_error":
                prior_technical_attempts = int(cached.get("technical_attempts") or 1)
            if cached and cached.get("status") == "ok":
                path = Path(cached["path"])
                try:
                    validate_audio(path, cached.get("sha256"), cached.get("size"))
                    continue
                except WordAudioError:
                    pass
            if cached and cached.get("status") in {"unresolved", "ambiguous"}:
                continue
            if group.get("skip_duden"):
                items[key] = {
                    "request_key": key, "spoken_text": group["spoken_text"], "status": "unresolved",
                    "reason": "existing A1/A2 MAIN Duden audit found no accepted audio", "match_method": "main-audit-cache",
                    "updated_utc": now_utc(),
                }
                atomic_json(DUDEN_EXTRA_INDEX, index)
                continue
            row = duden.SourceRow(number, group["spoken_text"], group["pos"], group["gender"], "", "", "")
            resolution, _ = await duden.resolve_row(session, row, {}, throttle=throttle)
            result = duden.resolution_to_row(resolution)
            result.update({"request_key": key, "spoken_text": group["spoken_text"], "updated_utc": now_utc()})
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
            if result["status"] == "technical_error":
                result["technical_attempts"] = prior_technical_attempts + 1
                if result["technical_attempts"] >= 2:
                    result.update({
                        "status": "unresolved",
                        "match_method": "technical-fallback-after-two-cooldowns",
                        "reason": f"Duden unavailable after two cooldowns: {result['reason']}",
                    })
            items[key] = result
            if result["status"] == "technical_error":
                index["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
            else:
                index.pop("cooldown_until", None)
            atomic_json(DUDEN_EXTRA_INDEX, index)
            print(f"duden {number}/{len(groups)} {group['spoken_text']!r}: {result['status']}")
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
    for item in index.get("items", {}).values():
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
    targets = {key: group for key, group in groups.items() if duden_index["items"].get(key, {}).get("status") != "ok"}
    pending = {key: group for key, group in targets.items() if items.get(key, {}).get("status") not in {"ok", "unresolved", "ambiguous"}}
    title_map: dict[str, str] = {}
    for key, group in pending.items():
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
            for page in pages_by_key.get(key, []):
                candidate, reason = evaluate_commons_page(page, group)
                if candidate:
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
                    await asyncio.sleep(COMMONS_CONFIG["download_interval_seconds"])
            else:
                result = {"status": "unresolved", "request_key": key, "spoken_text": group["spoken_text"], "reason": "; ".join(rejected[:5]) or "no exact Commons pronunciation file", "checked_utc": now_utc()}
            items[key] = result
            atomic_json(COMMONS_INDEX, index)
            print(f"commons {number}/{len(pending)} {group['spoken_text']!r}: {result['status']}")
    for key, item in items.items():
        if item.get("status") == "ok":
            validate_audio(Path(item["path"]), item.get("sha256"), item.get("size"))
    write_commons_attribution(index)
    return index


def edge_audio_id(text: str) -> str:
    return canonical_hash({"spoken_text": text, **EDGE_CONFIG})


async def prepare_edge(groups: dict[str, dict[str, Any]], duden_index: dict[str, Any], commons_index: dict[str, Any]) -> dict[str, Any]:
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
                print(f"edge {number}/{len(needed)} {group['spoken_text']!r}: ok")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if tmp.exists():
                    tmp.unlink()
        if last_error is not None:
            raise WordAudioError(f"Edge TTS failed for {group['spoken_text']!r}: {last_error}")
    return index


def finalize_manifest(manifest: dict[str, Any], duden_index: dict[str, Any], commons_index: dict[str, Any], edge_index: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for item in manifest["notes"].values():
        if item.get("assignment"):
            counts[item["assignment"]["source"]] += 1
            continue
        key = item["request_key"]
        extra = duden_index["items"].get(key, {})
        if extra.get("status") == "ok":
            item["assignment"] = assignment("duden_extra", Path(extra["path"]), detail=extra)
        elif commons_index["items"].get(key, {}).get("status") == "ok":
            commons = commons_index["items"][key]
            item["assignment"] = assignment("commons", Path(commons["path"]), detail=commons)
        else:
            edge_id = edge_audio_id(item["spoken_text"])
            edge = edge_index["items"].get(edge_id)
            if not edge or edge.get("status") != "ok":
                raise WordAudioError(f"missing Edge result for {item['lemma']!r}")
            item["assignment"] = assignment("edge", Path(edge["path"]), detail=edge)
        counts[item["assignment"]["source"]] += 1
    if len(manifest["notes"]) != LIVE_NOTE_COUNT or sum(counts.values()) != LIVE_NOTE_COUNT:
        raise WordAudioError("prepared manifest is incomplete")
    manifest.update({"prepared_utc": now_utc(), "counts": dict(counts), "missing_overrides": []})
    atomic_json(MANIFEST_PATH, manifest)
    return manifest


async def command_prepare(_: argparse.Namespace) -> None:
    if not _.confirm_commons_license:
        raise WordAudioError("Commons preparation requires --confirm-commons-license")
    manifest = load_json(MANIFEST_PATH, None) if _.offline else build_audit()
    if not manifest or manifest.get("note_count") != LIVE_NOTE_COUNT:
        raise WordAudioError("offline preparation requires a complete prior audit manifest")
    if manifest["missing_overrides"]:
        raise WordAudioError(
            f"{len(manifest['missing_overrides'])} notes need spoken-text overrides; see {MANIFEST_PATH}"
        )
    groups = request_groups(manifest)
    duden_index = await prepare_duden(groups)
    commons_index = await prepare_commons(groups, duden_index)
    edge_index = await prepare_edge(groups, duden_index, commons_index)
    final = finalize_manifest(manifest, duden_index, commons_index, edge_index)
    print(json.dumps({"notes": final["note_count"], "counts": final["counts"]}, ensure_ascii=False, indent=2))


def command_audit(_: argparse.Namespace) -> None:
    manifest = build_audit()
    print(json.dumps({
        "notes": manifest["note_count"], "counts": manifest["counts"],
        "missing_overrides": manifest["missing_overrides"], "manifest": str(MANIFEST_PATH),
    }, ensure_ascii=False, indent=2))


def command_commons_audit(_: argparse.Namespace) -> None:
    index = load_json(COMMONS_INDEX, {"items": {}})
    counts = Counter(item.get("status", "unknown") for item in index.get("items", {}).values())
    print(json.dumps({"counts": dict(counts), "index": str(COMMONS_INDEX), "attribution": str(COMMONS_ATTRIBUTION_PATH)}, ensure_ascii=False, indent=2))


def all_reviews(card_ids: list[int]) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for batch in gw.chunks(sorted(card_ids), 250):
        reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    return reviews


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
    if not manifest or not manifest.get("prepared_utc"):
        raise WordAudioError("prepared manifest missing")
    records = live_records()
    if set(map(int, manifest["notes"])) != set(records):
        raise WordAudioError("prepared note ID set differs from live deck")
    for note_id, record in records.items():
        if manifest["notes"][str(note_id)]["source_signature"] != source_signature(record["fields"]):
            raise WordAudioError(f"source fields changed after preparation: {note_id}")
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_pre_word_audio_{stamp}.apkg"
    result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.as_posix(), includeSched=True)
    if not result or not backup.exists():
        raise WordAudioError("Anki APKG export failed")
    cards = [card for record in records.values() for card in record["cards"]]
    card_ids = [int(card["cardId"]) for card in cards]
    reviews = all_reviews(card_ids)
    snapshot = {
        "schema_version": 1, "created_utc": now_utc(), "backup": str(backup),
        "backup_sha256": duden.hash_file(backup), "manifest_sha256": duden.hash_file(MANIFEST_PATH),
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
    if snapshot.get("manifest_sha256") != duden.hash_file(MANIFEST_PATH):
        raise WordAudioError("prepared manifest changed after snapshot")
    return manifest, snapshot


def pilot_ids(manifest: dict[str, Any]) -> list[int]:
    candidates = sorted(manifest["notes"].values(), key=lambda item: (
        item["assignment"]["source"] != "commons", item["level"], bool(item["old_word_audio"]), item["note_id"]
    ))
    selected: list[int] = []
    seen: set[tuple[str, str, bool]] = set()
    for item in candidates:
        key = (item["level"], item["assignment"]["source"], bool(item["old_word_audio"]))
        if key not in seen or len(selected) < PILOT_SIZE:
            selected.append(int(item["note_id"]))
            seen.add(key)
        if len(selected) == PILOT_SIZE:
            break
    return selected


def selected_ids(manifest: dict[str, Any], scope: str) -> list[int]:
    return pilot_ids(manifest) if scope == "pilot" else sorted(map(int, manifest["notes"]))


def verify_baseline(records: dict[int, dict[str, Any]], snapshot: dict[str, Any], manifest: dict[str, Any]) -> None:
    if set(map(int, snapshot["notes"])) != set(records):
        raise WordAudioError("live note ID set changed")
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        if record["model"] != before["model"] or record["tags"] != before["tags"]:
            raise WordAudioError(f"live note changed since snapshot: {note_id}")
        for name, value in before["fields"].items():
            actual = record["fields"].get(name, "")
            if name == "WordAudio":
                expected = f"[sound:{manifest['notes'][str(note_id)]['assignment']['media_name']}]"
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
    records = live_records()
    verify_baseline(records, snapshot, manifest)
    ids = selected_ids(manifest, args.scope)
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


def verify_state(scope: str, expect_baseline: bool = False) -> dict[str, Any]:
    manifest, snapshot = load_ready()
    records = live_records()
    selected = set(selected_ids(manifest, scope))
    if set(records) != set(map(int, snapshot["notes"])):
        raise WordAudioError("note ID set changed")
    for note_id, record in records.items():
        before = snapshot["notes"][str(note_id)]
        if record["model"] != before["model"] or record["tags"] != before["tags"]:
            raise WordAudioError(f"model or tags changed: {note_id}")
        for name, value in before["fields"].items():
            actual = record["fields"].get(name, "")
            if name == "WordAudio" and not expect_baseline and note_id in selected:
                expected = f"[sound:{manifest['notes'][str(note_id)]['assignment']['media_name']}]"
            else:
                expected = value
            if actual != expected:
                raise WordAudioError(f"field changed unexpectedly: note={note_id} field={name}")
    cards = [card for record in records.values() for card in record["cards"]]
    current_cards = {str(card["cardId"]): schedule_projection(card) for card in cards}
    if current_cards != snapshot["cards"]:
        raise WordAudioError("card IDs or scheduling changed")
    reviews = all_reviews([int(card["cardId"]) for card in cards])
    if canonical_hash(reviews) != snapshot["reviews_sha256"]:
        raise WordAudioError("review history changed")
    if model_snapshot() != snapshot["model"]:
        raise WordAudioError("model fields/templates/styling changed")
    if not expect_baseline:
        for note_id in selected:
            item = manifest["notes"][str(note_id)]["assignment"]
            retrieved = gw.anki("retrieveMediaFile", filename=item["media_name"])
            if not retrieved or hashlib.sha256(base64.b64decode(retrieved)).hexdigest() != item["sha256"]:
                raise WordAudioError(f"missing or corrupt Anki media: {item['media_name']}")
    return {"scope": scope, "baseline": expect_baseline, "notes": len(records), "cards": len(cards), "verified": len(selected)}


def command_verify(args: argparse.Namespace) -> None:
    print(json.dumps(verify_state(args.scope, expect_baseline=args.baseline), indent=2))


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise WordAudioError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    ids = [note_id for note_id in selected_ids(manifest, args.scope) if records[note_id]["fields"].get("WordAudio", "") != snapshot["notes"][str(note_id)]["fields"].get("WordAudio", "")]
    old = {note_id: snapshot["notes"][str(note_id)]["fields"].get("WordAudio", "") for note_id in ids}
    update_word_audio(ids, old)
    print(json.dumps(verify_state(args.scope, expect_baseline=True), indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit").set_defaults(func=command_audit)
    sub.add_parser("commons-audit").set_defaults(func=command_commons_audit)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--confirm-duden-usage", action="store_true", required=True)
    prepare.add_argument("--confirm-commons-license", action="store_true")
    prepare.add_argument("--offline", action="store_true", help="Resume from the existing audit manifest without AnkiConnect.")
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
    rollback.add_argument("--scope", choices=("pilot", "full"), default="full")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except (WordAudioError, gw.MigrationError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
