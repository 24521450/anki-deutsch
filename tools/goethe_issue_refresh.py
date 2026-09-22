"""Apply the reviewed September Goethe content/audio issue batch.

The command is deliberately exact-ID based.  It exports a scheduled APKG
before any live write, verifies every before-image, updates only the listed
fields, and deletes only the reviewed redundant ``zusammen`` note.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import json
import re
import shutil
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

import goethe_apkg as apkg
import goethe_examples
import goethe_source_examples
import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
MODEL = gw.MODEL
REGISTRY = ROOT / "review" / "goethe_word_audio_variants.json"
COMMONS_ATTRIBUTION = ROOT / "review" / "wikimedia_commons_audio_attribution.json"
STATE = ROOT / "tools" / ".goethe_issue_refresh"
VARIANT_AUDIO = ROOT / "audio" / "goethe_word_audio" / "variants"
USER_AGENT = "anki-deutsch-issue-refresh/1.0"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
APPLY_CONFIRMATION = "APPLY_GOETHE_ISSUE_REFRESH"

CONTENT_FIELDS: dict[int, dict[str, str]] = {
    1584886454894: {"MeaningEN": "long; for a long time"},
    1497484860765: {"Example1EN": "I would like to train as a nurse."},
    1497484861104: {"MeaningEN": "opposite; across from"},
    1497484861529: {
        "Lemma": "reiten",
        "AcceptedAnswersDE": "reiten",
        "AcceptedFullAnswersDE": "reiten",
        "MeaningEN": "to ride (an animal)",
        "Example1EN": "Would you like to learn to ride?",
    },
}

BEFORE_FIELDS: dict[int, dict[str, str]] = {
    1584886454894: {"MeaningEN": "for a long time"},
    1497484860765: {"Example1EN": "I would like to become a nurse."},
    1497484861104: {"MeaningEN": "across; across from"},
    1497484861529: {
        "Lemma": "reiten (animal)",
        "AcceptedAnswersDE": "reiten (animal)",
        "AcceptedFullAnswersDE": "reiten (animal)",
        "MeaningEN": "to ride a horse",
        "Example1EN": "Would you like to ride a horse?",
    },
}

DUDEN_AUDIO_URLS = {
    "Frühling": "https://cdn.duden.de/_media_/audio/ID4171509_297876523.mp3",
    "das Frühjahr": "https://cdn.duden.de/_media_/audio/ID4171506_125760681.mp3",
    "ICE": "https://cdn.duden.de/_media_/audio/ID4289731_518580423.mp3",
}


class IssueRefreshError(RuntimeError):
    pass


def clean(value: Any) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip())


def normalize(value: Any) -> str:
    return clean(value).casefold().replace("-", "")


def canonical_url(value: Any) -> str:
    parsed = urlsplit(clean(value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def example_key(value: Any) -> str:
    """Compare reviewed example sentences without punctuation/spacing noise."""
    return re.sub(r"[^\w]+", " ", clean(value), flags=re.UNICODE).strip().casefold()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def note_fields(note: dict[str, Any]) -> dict[str, str]:
    return {name: item.get("value", "") for name, item in note.get("fields", {}).items()}


def all_notes() -> dict[int, dict[str, Any]]:
    if gw.anki("version") != 6:
        raise IssueRefreshError("unexpected AnkiConnect API version")
    ids = gw.anki("findNotes", query=f'note:{chr(34)}{MODEL}{chr(34)}')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(ids, 500):
        notes.extend(gw.anki("notesInfo", notes=batch))
    return {int(note["noteId"]): note for note in notes}


def export_backup(label: str) -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = STATE / f"Goethe_Institute_pre_{label}_{stamp}.apkg"
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise IssueRefreshError(f"cannot clear backup path: {path}") from exc
    try:
        result = gw.anki("exportPackage", deck="Goethe Institute", path=path.resolve().as_posix(), includeSched=True)
    except gw.MigrationError as exc:
        if "timed out" not in str(exc).casefold() and "timeout" not in str(exc).casefold():
            raise IssueRefreshError(str(exc)) from exc
        result = False
    if not result and not apkg.wait_for_valid_apkg(path, timeout=180):
        raise IssueRefreshError("Anki scheduled APKG export failed")
    if not apkg.wait_for_valid_apkg(path, timeout=180):
        raise IssueRefreshError("Anki scheduled APKG backup is invalid")
    return path


def by_source(notes: dict[int, dict[str, Any]]) -> dict[str, tuple[int, dict[str, Any]]]:
    result: dict[str, tuple[int, dict[str, Any]]] = {}
    for note_id, note in notes.items():
        fields = note_fields(note)
        for source_ref in [fields.get("SourceID", ""), *fields.get("SourceRefs", "").split("|")]:
            source_ref = clean(source_ref)
            if not source_ref:
                continue
            prior = result.get(source_ref)
            if prior is not None and prior[0] != note_id:
                raise IssueRefreshError(f"ambiguous source reference: {source_ref}")
            result[source_ref] = (note_id, note)
    return result


def verify_before(notes: dict[int, dict[str, Any]]) -> None:
    for note_id, expected in BEFORE_FIELDS.items():
        note = notes.get(note_id)
        if note is None or note.get("modelName") != MODEL:
            raise IssueRefreshError(f"target note is missing or has the wrong model: {note_id}")
        fields = note_fields(note)
        for name, value in expected.items():
            if fields.get(name, "") != value:
                raise IssueRefreshError(
                    f"before-image drift for {note_id} {name}: {fields.get(name, '')!r} != {value!r}"
                )


def update_fields(updates: dict[int, dict[str, str]]) -> None:
    actions = [
        {"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": fields}}}
        for note_id, fields in sorted(updates.items())
    ]
    for batch in gw.chunks(actions, 40):
        results = gw.anki("multi", actions=batch)
        errors = [item.get("error") for item in results if isinstance(item, dict) and item.get("error")]
        if errors:
            raise IssueRefreshError(f"Anki field update failed: {errors[:3]}")
    current = all_notes()
    for note_id, fields in updates.items():
        actual = note_fields(current[note_id])
        for name, value in fields.items():
            if actual.get(name, "") != value:
                raise IssueRefreshError(f"field write verification failed: {note_id} {name}")


def apply_content() -> Path:
    notes = all_notes()
    verify_before(notes)
    backup = export_backup("issue_content")
    update_fields(CONTENT_FIELDS)
    print(json.dumps({"backup": str(backup), "updated_notes": sorted(CONTENT_FIELDS)}, ensure_ascii=False, indent=2))
    return backup


def merge_together() -> Path:
    notes = all_notes()
    survivor_id, duplicate_id = 1584886455261, 1785175712341
    survivor = notes.get(survivor_id)
    duplicate = notes.get(duplicate_id)
    if survivor is None or duplicate is None:
        raise IssueRefreshError("zusammen merge target is missing")
    sf, df = note_fields(survivor), note_fields(duplicate)
    if sf.get("Lemma") != "zusammen" or df.get("Lemma") != "zusammen/zusammen-":
        raise IssueRefreshError("zusammen merge lemma identity drift")
    if "A1-84886455260" not in sf.get("SourceRefs", "").split("|"):
        raise IssueRefreshError("zusammen survivor source identity drift")
    if "B1-MAIN-2947" not in df.get("SourceRefs", "").split("|"):
        raise IssueRefreshError("zusammen duplicate source identity drift")

    examples = goethe_examples.parse_fields(sf)
    duplicate_examples = goethe_examples.parse_fields(df)
    seen = {goethe_source_examples.sentence_key(item["de"]) for item in examples}
    for item in duplicate_examples:
        key = goethe_source_examples.sentence_key(item["de"])
        if key not in seen:
            examples.append(item)
            seen.add(key)
    extra = {
        "de": "Fuß und Ball musst du zusammenschreiben – Fußball!",
        "en": "You have to write Fuß and Ball together – Fußball!",
        "audio": "",
    }
    if goethe_source_examples.sentence_key(extra["de"]) not in seen:
        examples.append(extra)
    merged = dict(sf)
    merged["SourceRefs"] = "|".join(dict.fromkeys([*sf.get("SourceRefs", "").split("|"), "B1-MAIN-2947"]))
    merged["FormOrVariantNote"] = "zusammen | also used as a separable verb prefix: zusammen-"
    goethe_examples.render_fields(merged, examples)
    backup = export_backup("zusammen_merge")
    update_fields({survivor_id: {name: merged[name] for name in gw.FIELDS if merged.get(name, "") != sf.get(name, "")}})

    # Verify the exact duplicate cards immediately before destructive deletion.
    duplicate_cards = [int(card_id) for card_id in duplicate.get("cards", [])]
    if not duplicate_cards or len(duplicate_cards) != 2:
        raise IssueRefreshError("zusammen duplicate card identity is invalid")
    current_duplicate = all_notes().get(duplicate_id)
    if current_duplicate is None or [int(card_id) for card_id in current_duplicate.get("cards", [])] != duplicate_cards:
        raise IssueRefreshError("zusammen duplicate changed before deletion")
    result = gw.anki("deleteNotes", notes=[duplicate_id])
    if result is not None and result is not True:
        raise IssueRefreshError(f"zusammen duplicate deletion failed: {result!r}")
    if duplicate_id in all_notes():
        raise IssueRefreshError("zusammen duplicate remains after deletion")
    print(json.dumps({"backup": str(backup), "survivor": survivor_id, "deleted_note": duplicate_id, "deleted_cards": duplicate_cards}, ensure_ascii=False, indent=2))
    return backup


def cleanup_together() -> Path:
    """Remove the punctuation-only duplicate retained by the live merge."""
    notes = all_notes()
    survivor_id = 1584886455261
    survivor = notes.get(survivor_id)
    if survivor is None:
        raise IssueRefreshError("zusammen survivor is missing")
    fields = note_fields(survivor)
    if fields.get("Lemma") != "zusammen" or "B1-MAIN-2947" not in fields.get("SourceRefs", "").split("|"):
        raise IssueRefreshError("zusammen cleanup identity drift")
    examples = goethe_examples.parse_fields(fields)
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for item in examples:
        key = example_key(item.get("de", ""))
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    expected_spans = "[[[11,19]],[[10,18]],[[11,19]],[[17,25]],[[10,18]],[[25,33]],[[22,30]]]"
    if len(kept) == len(examples) and fields.get("ExampleTargetSpansJSON", "") == expected_spans:
        print(json.dumps({"changed": False, "survivor": survivor_id, "examples": len(examples)}, ensure_ascii=False, indent=2))
        return Path()
    merged = dict(fields)
    goethe_examples.render_fields(merged, kept)
    merged["ExampleTargetSpansJSON"] = expected_spans
    backup = export_backup("zusammen_cleanup")
    update_fields({survivor_id: {name: merged[name] for name in gw.FIELDS if merged.get(name, "") != fields.get(name, "")}})
    print(json.dumps({"backup": str(backup), "survivor": survivor_id, "removed_examples": len(examples) - len(kept), "examples": len(kept)}, ensure_ascii=False, indent=2))
    return backup


def load_registry() -> dict[str, dict[str, Any]]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if data.get("schema_version") != 2 or not isinstance(data.get("entries"), dict):
        raise IssueRefreshError("invalid pronunciation-variant registry")
    for source_id, entry in data["entries"].items():
        if not isinstance(entry, dict) or not entry.get("expected_lemma") or not isinstance(entry.get("variants"), list):
            raise IssueRefreshError(f"invalid pronunciation-variant entry: {source_id}")
        if len(entry["variants"]) < 2:
            raise IssueRefreshError(f"variant entry has fewer than two labels: {source_id}")
        if sum(bool(item.get("primary")) for item in entry["variants"]) != 1:
            raise IssueRefreshError(f"variant entry must have exactly one primary: {source_id}")
        labels = [clean(item.get("label")) for item in entry["variants"]]
        if len(labels) != len(set(labels)):
            raise IssueRefreshError(f"variant entry has duplicate visible labels: {source_id}")
        for variant in entry["variants"]:
            if not isinstance(variant, dict) or not variant.get("label") or not variant.get("spoken_text"):
                raise IssueRefreshError(f"invalid pronunciation variant: {source_id}")
            pin = variant.get("audio_source")
            if pin is None:
                continue
            if not isinstance(pin, dict) or pin.get("provider") != "commons":
                raise IssueRefreshError(f"unsupported pinned audio provider: {source_id}/{variant.get('label')}")
            required = ("title", "source_url", "audio_url", "original_sha1", "license_short_name", "license_url")
            if any(not clean(pin.get(name)) for name in required):
                raise IssueRefreshError(f"incomplete pinned audio source: {source_id}/{variant.get('label')}")
            if not re.match(r"^File:De-[^/]+\.(?:ogg|oga|wav|mp3)$", clean(pin["title"]), flags=re.I):
                raise IssueRefreshError(f"invalid Commons audio title: {pin['title']}")
            if canonical_url(pin["source_url"]) != f"https://commons.wikimedia.org/wiki/{clean(pin['title']).replace(' ', '_')}".replace("File:", "File:"):
                raise IssueRefreshError(f"pinned Commons page does not match title: {pin['title']}")
            if not canonical_url(pin["audio_url"]).startswith("https://upload.wikimedia.org/wikipedia/commons/"):
                raise IssueRefreshError(f"invalid pinned Commons media URL: {pin['audio_url']}")
            if not re.match(r"^[0-9a-f]{40}$", clean(pin["original_sha1"]), flags=re.I):
                raise IssueRefreshError(f"invalid pinned Commons SHA-1: {pin['title']}")
            if clean(pin["license_short_name"]).casefold() not in {"cc by-sa 4.0", "cc by-sa 3.0", "cc by 4.0", "cc by 3.0"}:
                raise IssueRefreshError(f"unsupported pinned Commons license: {pin['license_short_name']}")
            if not canonical_url(pin["license_url"]).startswith("https://creativecommons.org/licenses/"):
                raise IssueRefreshError(f"invalid pinned Commons license URL: {pin['license_url']}")
    return data["entries"]


def local_duden_assets() -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for level in ("a1", "a2", "b1"):
        manifest = ROOT / "audio" / level / "words_manifest.jsonl"
        if not manifest.exists():
            continue
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            item = json.loads(raw)
            if item.get("status") != "ok":
                continue
            filename = clean(item.get("output_filename", ""))
            path = ROOT / "audio" / level / "words" / filename
            if not filename or not path.exists():
                continue
            key = normalize(item.get("word") or item.get("spoken_text"))
            if key and key not in assets:
                assets[key] = {"path": path, "provider": "duden", "source_url": item.get("duden_audio_url", "")}
    return assets


def validate_mp3_bytes(data: bytes, label: str) -> tuple[int, str]:
    if len(data) < 128:
        raise IssueRefreshError(f"audio file is missing or too small: {label}")
    if not (data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}):
        raise IssueRefreshError(f"audio file is not an MP3: {label}")
    return len(data), hashlib.sha256(data).hexdigest()


def validate_mp3(path: Path) -> tuple[int, str]:
    if not path.exists():
        raise IssueRefreshError(f"audio file is missing: {path}")
    return validate_mp3_bytes(path.read_bytes(), str(path))


def _plain_metadata(value: Any) -> str:
    return clean(re.sub(r"<[^>]+>", " ", str(value or "")))


def _commons_request(params: dict[str, str]) -> dict[str, Any]:
    last_error = "Commons API request failed"
    for attempt in range(3):
        try:
            response = requests.get(
                COMMONS_API,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise IssueRefreshError(f"Commons API request failed: {exc}") from exc
        if response.status_code == 429 or 500 <= response.status_code < 600:
            last_error = f"HTTP {response.status_code} from Commons API"
            if attempt < 2:
                try:
                    delay = max(1, min(int(response.headers.get("Retry-After", "0")), 30))
                except (TypeError, ValueError):
                    delay = 2 * (attempt + 1)
                time.sleep(delay)
                continue
        if response.status_code != 200:
            raise IssueRefreshError(f"Commons API HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise IssueRefreshError("Commons API returned invalid JSON") from exc
        if payload.get("error"):
            raise IssueRefreshError(f"Commons API error: {payload['error']}")
        return payload
    raise IssueRefreshError(last_error)


def _commons_pin_page(pin: dict[str, Any], spoken_text: str) -> dict[str, Any]:
    payload = _commons_request({
        "action": "query",
        "prop": "videoinfo|categories",
        "titles": clean(pin["title"]),
        "viprop": "url|size|sha1|mime|mediatype|extmetadata|derivatives",
        "cllimit": "max",
        "format": "json",
        "formatversion": "2",
    })
    pages = payload.get("query", {}).get("pages", [])
    page = next((item for item in pages if not item.get("missing") and clean(item.get("title")) == clean(pin["title"])), None)
    if page is None:
        raise IssueRefreshError(f"pinned Commons file is missing: {pin['title']}")
    info = (page.get("videoinfo") or [{}])[0]
    if info.get("mediatype") != "AUDIO":
        raise IssueRefreshError(f"pinned Commons file is not audio: {pin['title']}")
    try:
        duration = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if not 0.15 <= duration <= 15:
        raise IssueRefreshError(f"pinned Commons duration is invalid: {pin['title']}")
    categories = [clean(item.get("title")) for item in page.get("categories", [])]
    category_text = " ".join(categories).casefold()
    if "german pronunciation" not in category_text:
        raise IssueRefreshError(f"pinned Commons file lacks German pronunciation category: {pin['title']}")
    expected_category = clean(pin.get("expected_category")).casefold()
    if expected_category and expected_category not in category_text:
        raise IssueRefreshError(f"pinned Commons POS category mismatch for {spoken_text}: {pin['title']}")
    metadata = info.get("extmetadata") or {}
    license_name = _plain_metadata((metadata.get("LicenseShortName") or {}).get("value"))
    license_url = canonical_url((metadata.get("LicenseUrl") or {}).get("value"))
    if license_name.casefold() != clean(pin["license_short_name"]).casefold() or license_url != canonical_url(pin["license_url"]):
        raise IssueRefreshError(f"pinned Commons license drift: {pin['title']}")
    original_sha1 = clean(info.get("sha1")).casefold()
    if original_sha1 != clean(pin["original_sha1"]).casefold():
        raise IssueRefreshError(f"pinned Commons content drift: {pin['title']}")
    if canonical_url(info.get("url")) != canonical_url(pin["audio_url"]):
        raise IssueRefreshError(f"pinned Commons URL drift: {pin['title']}")
    derivatives = info.get("derivatives") or []
    derivative = next((item for item in derivatives if item.get("transcodekey") == "mp3" or item.get("type") == "audio/mpeg"), None)
    if not derivative or not derivative.get("src"):
        raise IssueRefreshError(f"pinned Commons file has no MP3 derivative: {pin['title']}")
    artist = _plain_metadata((metadata.get("Artist") or {}).get("value")) or _plain_metadata((metadata.get("Credit") or {}).get("value"))
    if not artist:
        raise IssueRefreshError(f"pinned Commons file has no artist/credit: {pin['title']}")
    return {
        "title": clean(page.get("title")),
        "description_url": clean(info.get("descriptionurl")),
        "original_url": canonical_url(info.get("url")),
        "original_sha1": original_sha1,
        "duration": duration,
        "derivative_url": clean(derivative["src"]),
        "artist": artist,
        "credit": _plain_metadata((metadata.get("Credit") or {}).get("value")),
        "attribution": _plain_metadata((metadata.get("Attribution") or {}).get("value")),
        "license_short_name": license_name,
        "license_url": license_url,
        "usage_terms": _plain_metadata((metadata.get("UsageTerms") or {}).get("value")),
        "category_evidence": categories,
        "checked_utc": now_utc(),
    }


def download_commons_pin(spoken_text: str, pin: dict[str, Any]) -> dict[str, Any]:
    detail = _commons_pin_page(pin, spoken_text)
    last_error = "Commons media download failed"
    content: bytes | None = None
    for attempt in range(3):
        try:
            response = requests.get(detail["derivative_url"], headers={"User-Agent": USER_AGENT}, timeout=30)
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise IssueRefreshError(f"Commons media download failed: {exc}") from exc
        if response.status_code == 429 or 500 <= response.status_code < 600:
            last_error = f"HTTP {response.status_code} from Commons media"
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
        if response.status_code != 200:
            raise IssueRefreshError(f"Commons media HTTP {response.status_code}")
        content = response.content
        break
    if content is None:
        raise IssueRefreshError(last_error)
    size, digest = validate_mp3_bytes(content, detail["derivative_url"])
    VARIANT_AUDIO.mkdir(parents=True, exist_ok=True)
    path = VARIANT_AUDIO / f"commons_{digest}.mp3"
    if path.exists():
        old_size, old_digest = validate_mp3(path)
        if old_size != size or old_digest != digest:
            raise IssueRefreshError(f"Commons cache hash conflict: {path}")
    else:
        path.write_bytes(content)
    detail.update({
        "path": path,
        "provider": "commons",
        "source_url": pin["source_url"],
        "audio_url": pin["audio_url"],
        "sha256": digest,
        "size": size,
        "spoken_text": spoken_text,
    })
    return detail


def download_duden(label: str, url: str) -> Path:
    VARIANT_AUDIO.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if response.status_code != 200:
        raise IssueRefreshError(f"Duden audio request failed for {label}: HTTP {response.status_code}")
    digest = hashlib.sha256(response.content).hexdigest()
    path = VARIANT_AUDIO / f"duden_{digest}.mp3"
    if not path.exists():
        path.write_bytes(response.content)
    validate_mp3(path)
    return path


async def generate_edge_one(text: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, "de-DE-ConradNeural").save(str(path))


async def generate_edge(texts: dict[str, Path]) -> None:
    pending = [(text, path) for text, path in texts.items() if not path.exists()]
    for start in range(0, len(pending), 5):
        await asyncio.gather(*(generate_edge_one(text, path) for text, path in pending[start:start + 5]))


def prepare_variant_audio(
    spoken_texts: set[str],
    source_pins: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    local = local_duden_assets()
    resolved: dict[str, dict[str, Any]] = {}
    edge_paths: dict[str, Path] = {}
    source_pins = source_pins or {}
    VARIANT_AUDIO.mkdir(parents=True, exist_ok=True)
    for text in sorted(spoken_texts, key=lambda value: (normalize(value), value)):
        pin = source_pins.get(text)
        if pin:
            # A reviewed pin is authoritative.  If it cannot be validated or
            # downloaded, fail closed instead of silently replacing it with TTS.
            resolved[text] = download_commons_pin(text, pin)
            continue
        if text in DUDEN_AUDIO_URLS:
            path = download_duden(text, DUDEN_AUDIO_URLS[text])
            resolved[text] = {"path": path, "provider": "duden", "source_url": DUDEN_AUDIO_URLS[text]}
            continue
        asset = local.get(normalize(text))
        if asset:
            resolved[text] = asset
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        edge_paths[text] = VARIANT_AUDIO / f"edge_{digest}.mp3"
    if edge_paths:
        asyncio.run(generate_edge(edge_paths))
    for text, path in edge_paths.items():
        validate_mp3(path)
        resolved[text] = {"path": path, "provider": "edge_tts", "source_url": "https://azure.microsoft.com/products/ai-services/text-to-speech"}
    missing = sorted(set(spoken_texts) - set(resolved))
    if missing:
        raise IssueRefreshError(f"no audio prepared for variants: {missing[:8]}")
    return resolved


def ensure_media(path: Path, filename: str, digest: str) -> None:
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest:
        raise IssueRefreshError(f"local audio hash drift: {filename}")
    existing = gw.anki("retrieveMediaFile", filename=filename)
    if existing:
        if hashlib.sha256(base64.b64decode(existing)).hexdigest() != digest:
            raise IssueRefreshError(f"Anki media hash conflict: {filename}")
        return
    stored = gw.anki("storeMediaFile", filename=filename, data=base64.b64encode(data).decode("ascii"))
    if stored != filename:
        raise IssueRefreshError(f"unexpected Anki media name: {stored}")


VARIANT_MEDIA_RE = re.compile(r"^_goethe_variant_([0-9a-f]{64})\.mp3$")
VARIANT_SEPARATOR = '<span class="gw-word-variant-separator" aria-hidden="true">/</span>'


def render_variant_html(variants: list[dict[str, Any]], filenames: list[str]) -> str:
    if len(variants) != len(filenames) or len(variants) < 2:
        raise IssueRefreshError("variant labels and media filenames do not align")
    buttons: list[str] = []
    for variant, filename in zip(variants, filenames):
        if not VARIANT_MEDIA_RE.fullmatch(filename):
            raise IssueRefreshError(f"invalid variant media filename: {filename}")
        label = html.escape(clean(variant["label"]), quote=True)
        buttons.append(
            f'<button type="button" class="gw-word-variant" data-variant-label="{label}">'
            f'<span class="gw-word-variant-label">{label}</span>'
            f'<audio class="gw-word-variant-audio" preload="none" src="{filename}"></audio></button>'
        )
    return f" {VARIANT_SEPARATOR} ".join(buttons)


class _VariantMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_variant = False
        self.current_src = ""
        self.filenames: list[str] = []

    @staticmethod
    def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {name: value or "" for name, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = self._attributes(attrs)
        classes = attributes.get("class", "").split()
        if tag == "button" and "gw-word-variant" in classes:
            if self.in_variant:
                raise IssueRefreshError("nested pronunciation-variant buttons are invalid")
            self.in_variant = True
            self.current_src = ""
        elif tag == "audio" and self.in_variant and "gw-word-variant-audio" in classes:
            if self.current_src or not attributes.get("src"):
                raise IssueRefreshError("pronunciation variant must contain exactly one audio source")
            self.current_src = attributes["src"]

    def handle_endtag(self, tag: str) -> None:
        if tag != "button" or not self.in_variant:
            return
        if not self.current_src:
            raise IssueRefreshError("pronunciation variant has no audio source")
        self.filenames.append(self.current_src)
        self.in_variant = False
        self.current_src = ""


def extract_live_variant_filenames(markup: str) -> list[str]:
    parser = _VariantMarkupParser()
    try:
        parser.feed(markup)
        parser.close()
    except (IssueRefreshError, ValueError) as exc:
        raise IssueRefreshError(f"invalid live pronunciation-variant markup: {exc}") from exc
    if parser.in_variant:
        raise IssueRefreshError("live pronunciation-variant button is not closed")
    if len(parser.filenames) < 2:
        raise IssueRefreshError("live pronunciation-variant markup has fewer than two buttons")
    return parser.filenames


def verify_live_variant_media(filename: str) -> None:
    match = VARIANT_MEDIA_RE.fullmatch(filename)
    if match is None:
        raise IssueRefreshError(f"invalid live variant media filename: {filename}")
    encoded = gw.anki("retrieveMediaFile", filename=filename)
    if not encoded:
        raise IssueRefreshError(f"live variant media is missing: {filename}")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise IssueRefreshError(f"live variant media is not valid base64: {filename}") from exc
    _, digest = validate_mp3_bytes(data, filename)
    if digest != match.group(1):
        raise IssueRefreshError(f"live variant media hash does not match filename: {filename}")


def update_variant_attribution(audio: dict[str, dict[str, Any]]) -> None:
    commons = [item for item in audio.values() if item.get("provider") == "commons"]
    if not commons:
        return
    try:
        data = json.loads(COMMONS_ATTRIBUTION.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IssueRefreshError(f"cannot read Commons attribution registry: {COMMONS_ATTRIBUTION}") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("items"), list):
        raise IssueRefreshError("invalid Commons attribution registry")
    by_hash = {clean(item.get("sha256")): item for item in data["items"] if isinstance(item, dict) and clean(item.get("sha256"))}
    for item in commons:
        by_hash[clean(item["sha256"])] = {
            "sha256": item["sha256"],
            "spoken_text": item["spoken_text"],
            "title": item["title"],
            "description_url": item["description_url"],
            "original_url": item["original_url"],
            "original_sha1": item["original_sha1"],
            "artist": item["artist"],
            "credit": item.get("credit", ""),
            "attribution": item.get("attribution", ""),
            "license_short_name": item["license_short_name"],
            "license_url": item["license_url"],
            "usage_terms": item.get("usage_terms", ""),
            "checked_utc": item["checked_utc"],
        }
    data["generated_utc"] = now_utc()
    data["items"] = sorted(by_hash.values(), key=lambda item: (clean(item.get("spoken_text")).casefold(), item.get("sha256", "")))
    try:
        COMMONS_ATTRIBUTION.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise IssueRefreshError(f"cannot update Commons attribution registry: {COMMONS_ATTRIBUTION}") from exc


def source_pins_for_entries(entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    pins: dict[str, dict[str, Any]] = {}
    for entry in entries.values():
        for variant in entry["variants"]:
            pin = variant.get("audio_source")
            if not pin:
                continue
            spoken = clean(variant["spoken_text"])
            previous = pins.get(spoken)
            if previous is not None and any(
                canonical_url(previous.get(name)) != canonical_url(pin.get(name))
                for name in ("source_url", "audio_url")
            ):
                raise IssueRefreshError(f"conflicting audio pins for variant: {spoken}")
            if previous is not None and clean(previous.get("original_sha1")).casefold() != clean(pin.get("original_sha1")).casefold():
                raise IssueRefreshError(f"conflicting pinned content for variant: {spoken}")
            pins[spoken] = pin
    return pins


def apply_variant_markup(source_ids: set[str] | None = None) -> Path:
    """Refresh visible labels/separators while preserving every live audio file."""
    registry = load_registry()
    if source_ids:
        unknown = sorted(source_ids - set(registry))
        if unknown:
            raise IssueRefreshError(f"unknown pronunciation-variant source ID: {unknown[0]}")
        selected_registry = {source_id: registry[source_id] for source_id in sorted(source_ids)}
    else:
        selected_registry = registry

    notes = all_notes()
    source_map = by_source(notes)
    updates: dict[int, dict[str, str]] = {}
    before_images: dict[int, dict[str, str]] = {}
    verified_media: set[str] = set()
    active_entries = 0
    for source_id, entry in selected_registry.items():
        if entry.get("retired_after_merge"):
            continue
        active_entries += 1
        match = source_map.get(source_id)
        if match is None:
            raise IssueRefreshError(f"variant SourceID is absent from live notes: {source_id}")
        note_id, note = match
        fields = note_fields(note)
        if fields.get("Lemma") != entry["expected_lemma"]:
            raise IssueRefreshError(f"variant lemma drift for {source_id}: {fields.get('Lemma')!r}")
        current_markup = fields.get("WordAudioVariantsHTML", "")
        filenames = extract_live_variant_filenames(current_markup)
        if len(filenames) != len(entry["variants"]):
            raise IssueRefreshError(
                f"live variant count drift for {source_id}: {len(filenames)} != {len(entry['variants'])}"
            )
        for filename in filenames:
            if filename not in verified_media:
                verify_live_variant_media(filename)
                verified_media.add(filename)
        primary_index = next(
            (index for index, variant in enumerate(entry["variants"]) if variant.get("primary")),
            -1,
        )
        if primary_index < 0:
            raise IssueRefreshError(f"variant entry has no primary media: {source_id}")
        expected_word_audio = f"[sound:{filenames[primary_index]}]"
        if fields.get("WordAudio", "") != expected_word_audio:
            raise IssueRefreshError(f"live primary variant media drift for {source_id}")
        candidate = render_variant_html(entry["variants"], filenames)
        if candidate != current_markup:
            updates[note_id] = {"WordAudioVariantsHTML": candidate}
            before_images[note_id] = {
                "WordAudio": fields.get("WordAudio", ""),
                "WordAudioVariantsHTML": current_markup,
            }

    if not updates:
        print(json.dumps({"changed": False, "variant_notes": active_entries}, ensure_ascii=False, indent=2))
        return Path()

    backup = export_backup("variant_markup")
    current = all_notes()
    for note_id, expected in before_images.items():
        actual = note_fields(current.get(note_id, {}))
        for name, value in expected.items():
            if actual.get(name, "") != value:
                raise IssueRefreshError(f"variant markup changed after backup: {note_id} {name}")
    update_fields(updates)
    print(json.dumps({
        "backup": str(backup),
        "variant_notes": active_entries,
        "updated_notes": len(updates),
        "verified_media": len(verified_media),
    }, ensure_ascii=False, indent=2))
    return backup


def apply_variants(source_ids: set[str] | None = None) -> Path:
    registry = load_registry()
    if source_ids:
        unknown = sorted(source_ids - set(registry))
        if unknown:
            raise IssueRefreshError(f"unknown pronunciation-variant source ID: {unknown[0]}")
        selected_registry = {source_id: registry[source_id] for source_id in sorted(source_ids)}
    else:
        selected_registry = registry
    notes = all_notes()
    source_map = by_source(notes)
    targets: dict[int, dict[str, Any]] = {}
    for source_id, entry in selected_registry.items():
        if entry.get("retired_after_merge"):
            continue
        match = source_map.get(source_id)
        if match is None:
            raise IssueRefreshError(f"variant SourceID is absent from live notes: {source_id}")
        note_id, note = match
        fields = note_fields(note)
        if fields.get("Lemma") != entry["expected_lemma"]:
            raise IssueRefreshError(f"variant lemma drift for {source_id}: {fields.get('Lemma')!r}")
        targets[note_id] = entry
    spoken = {
        clean(variant["spoken_text"])
        for entry in targets.values()
        for variant in entry["variants"]
    }
    if not source_ids:
        spoken.update({"ICE"})
    audio = prepare_variant_audio(spoken, source_pins_for_entries(selected_registry))
    updates: dict[int, dict[str, str]] = {}
    for note_id, entry in targets.items():
        filenames: list[str] = []
        primary_filename = ""
        for variant in entry["variants"]:
            text = clean(variant["spoken_text"])
            data = audio[text]
            raw = data["path"].read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            filename = f"_goethe_variant_{digest}.mp3"
            ensure_media(data["path"], filename, digest)
            if variant.get("primary"):
                primary_filename = filename
            filenames.append(filename)
        if not primary_filename:
            raise IssueRefreshError(f"variant entry has no primary media: {note_id}")
        candidate_fields = {
            "WordAudio": f"[sound:{primary_filename}]",
            "WordAudioVariantsHTML": render_variant_html(entry["variants"], filenames),
        }
        current_fields = note_fields(notes[note_id])
        changed = {name: value for name, value in candidate_fields.items() if current_fields.get(name, "") != value}
        if changed:
            updates[note_id] = changed
    if not source_ids:
        ice_path = audio["ICE"]["path"]
        ice_data = ice_path.read_bytes()
        ice_digest = hashlib.sha256(ice_data).hexdigest()
        ice_filename = f"_goethe_word_duden_{ice_digest}.mp3"
        ensure_media(ice_path, ice_filename, ice_digest)
        current_ice = note_fields(notes.get(1783863834037, {}))
        ice_field = {"WordAudio": f"[sound:{ice_filename}]"}
        if current_ice.get("WordAudio", "") != ice_field["WordAudio"]:
            updates[1783863834037] = ice_field
    backup = export_backup("variants")
    update_fields(updates)
    update_variant_attribution(audio)
    print(json.dumps({"backup": str(backup), "variant_notes": len(targets), "updated_notes": len(updates), "providers": sorted({audio[item]["provider"] for item in spoken})}, ensure_ascii=False, indent=2))
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("content", "merge", "cleanup", "variants", "variant-markup", "all"))
    parser.add_argument("--source-id", action="append", default=[], help="limit variant work to one or more exact SourceID values")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args(argv)
    if args.command == "all" and args.confirmation != APPLY_CONFIRMATION:
        raise SystemExit(f"confirmation must equal {APPLY_CONFIRMATION}")
    if args.command == "content":
        apply_content()
    elif args.command == "merge":
        merge_together()
    elif args.command == "cleanup":
        cleanup_together()
    elif args.command == "variants":
        apply_variants(set(args.source_id) or None)
    elif args.command == "variant-markup":
        apply_variant_markup(set(args.source_id) or None)
    else:
        notes = all_notes()
        verify_before(notes)
        export_backup("all")
        update_fields(CONTENT_FIELDS)
        merge_together()
        apply_variants()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IssueRefreshError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
