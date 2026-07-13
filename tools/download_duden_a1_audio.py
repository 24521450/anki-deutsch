from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import aiohttp
from lxml import html as lxml_html

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "sources" / "goethe" / "Goethe_A1.md"
AUDIO_ROOT = ROOT / "audio" / "a1"
LIVE_WORDS_DIR = AUDIO_ROOT / "words"
STAGING_WORDS_DIR = AUDIO_ROOT / "words_duden_staging"
LIVE_MANIFEST_PATH = AUDIO_ROOT / "words_manifest.jsonl"
LIVE_META_PATH = AUDIO_ROOT / "words_manifest.meta.json"
OVERRIDES_PATH = ROOT / "review" / "duden_overrides.json"
BACKUP_ROOT = AUDIO_ROOT / "matrix_backup"
DUDEN_CHECKPOINT_ROOT = AUDIO_ROOT / "duden_checkpoints"
MISSING_AUDIT_PATH = AUDIO_ROOT / "duden_missing_audit.jsonl"
REUSE_LIVE_WORDS_DIR: Path | None = None
REUSE_LIVE_MANIFEST_PATH: Path | None = None
REUSE_LIVE_SOURCES: tuple[tuple[Path, Path], ...] = ()
_REUSE_INDEX_CACHE: dict[tuple[str, str, str], dict[str, Any]] | None = None
PREFER_FIRST_EXACT_CANDIDATE = False

EXPECTED_ROWS = 685
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=45)
MAX_RETRIES = 3
CHECKPOINT_EVERY = 25
PAGE_REQUEST_MIN_INTERVAL = 2.0
CDN_REQUEST_MIN_INTERVAL = 1.0
MAX_RETRY_AFTER_SECONDS = 60
COOLDOWN_SECONDS = 15 * 60
ATOMIC_REPLACE_RETRIES = 10
ATOMIC_REPLACE_SLEEP = 0.2
DUDEN_LEXEME_BASE_URL = "https://www.duden.de/sitemap-lexeme"
DUDEN_BASE_URL = "https://www.duden.de"
EXIT_SUCCESS = 0
EXIT_UNRESOLVED = 1
EXIT_COOLDOWN = 2
EXIT_TECHNICAL_ERROR = 3
PILOT_WORDS = [
    "Abfahrt",
    "Straße",
    "Frühstück",
    "an sein",
    "Pommes frites",
    "Ausländer",
    "sie",
    "Sie",
    "essen",
    "Essen",
    "all-",
    "sich kümmern",
]
ALLOWED_STATUSES = {"ok", "unresolved", "ambiguous", "invalid", "technical_error", "pending"}
AUDIT_STATUSES = {
    "exact_audio_found",
    "exact_page_no_audio",
    "no_exact_lexeme",
    "metadata_conflict",
    "ambiguous_page",
    "technical_error",
}


@dataclass(frozen=True, slots=True)
class SourceRow:
    row: int
    word: str
    pos: str
    gender: str
    cefr: str
    sentence: str
    note: str


@dataclass(frozen=True, slots=True)
class DudenPage:
    canonical_url: str
    headword: str
    h1_gender: str | None
    wordart: str
    pos_labels: tuple[str, ...]
    audio_candidates: tuple[dict[str, str], ...]
    disambiguation_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Resolution:
    row: int
    word: str
    pos: str
    gender: str
    output_filename: str
    status: str
    reason: str
    match_method: str | None
    duden_page_url: str | None
    duden_audio_url: str | None
    file_id: str | None
    size: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    etag: str | None = None


@dataclass(slots=True)
class RequestThrottle:
    now_fn: Any = time.monotonic
    sleep_fn: Any = asyncio.sleep
    _next_page_at: float = 0.0
    _next_cdn_at: float = 0.0

    async def wait_for_page(self) -> None:
        await self._wait("page")

    async def wait_for_cdn(self) -> None:
        await self._wait("cdn")

    async def _wait(self, kind: str) -> None:
        interval = PAGE_REQUEST_MIN_INTERVAL if kind == "page" else CDN_REQUEST_MIN_INTERVAL
        attr = "_next_page_at" if kind == "page" else "_next_cdn_at"
        now = self.now_fn()
        next_at = getattr(self, attr)
        if now < next_at:
            await self.sleep_fn(next_at - now)
            now = self.now_fn()
        setattr(self, attr, now + interval)


class TechnicalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LexemeCandidate:
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class AuditDecision:
    row: SourceRow
    status: str
    reason: str
    candidates: tuple[dict[str, Any], ...]
    selected_page_url: str | None = None
    selected_audio_url: str | None = None
    selected_file_id: str | None = None

    def to_json_row(self) -> dict[str, Any]:
        if self.status not in AUDIT_STATUSES:
            raise ValueError(f"invalid audit status: {self.status}")
        return {
            "row": self.row.row,
            "word": self.row.word,
            "pos": self.row.pos,
            "gender": self.row.gender,
            "status": self.status,
            "reason": self.reason,
            "selected_page_url": self.selected_page_url,
            "selected_audio_url": self.selected_audio_url,
            "selected_file_id": self.selected_file_id,
            "candidates": list(self.candidates),
        }


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = value.replace("\u00ad", "")
    value = value.replace("\u200b", "")
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def normalize_word_for_file(word: str) -> str:
    value = normalize_text(word)
    replacements = {
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "sz",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    value = value.replace(" ", "_").replace("-", "_").replace("/", "_")
    value = value.replace("(", "").replace(")", "")
    value = re.sub(r"[^0-9A-Za-z_]+", "", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value.lower()


def normalize_word_for_url(word: str) -> str:
    value = normalize_text(word)
    value = value.replace(" ", "_").replace("/", "_")
    value = value.replace("(", "").replace(")", "")
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def transliterate_duden_slug(word: str) -> str:
    value = normalize_word_for_url(word)
    replacements = {
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "sz",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value


def parse_markdown_wordlist(path: Path) -> list[SourceRow]:
    rows: list[SourceRow] = []
    in_table = False
    saw_header = False
    row_num = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line.startswith("|"):
            in_table = False
            saw_header = False
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not in_table:
            if cells and cells[0].lstrip().startswith("Word"):
                in_table = True
            continue
        if not saw_header:
            saw_header = True
            continue
        if len(cells) < 6:
            continue
        row_num += 1
        rows.append(
            SourceRow(
                row=row_num,
                word=normalize_text(cells[0].strip("*")),
                pos=normalize_text(cells[1]),
                gender=normalize_text(cells[2]),
                cefr=normalize_text(cells[3]),
                sentence=normalize_text(cells[4]),
                note=normalize_text(cells[5]),
            )
        )
    return rows


def source_pos_tokens(source_pos: str) -> set[str]:
    tokens: set[str] = set()
    for chunk in source_pos.split(","):
        token = normalize_text(chunk).lower().rstrip(".")
        if not token:
            continue
        if token in {"n", "noun"}:
            tokens.add("noun")
        elif token in {"v", "verb"}:
            tokens.add("verb")
        elif token in {"adj", "adjective"}:
            tokens.add("adjective")
        elif token in {"adv", "adverb"}:
            tokens.add("adverb")
        elif token in {"pron", "pronoun"}:
            tokens.add("pronoun")
        elif token in {"det", "determiner"}:
            tokens.add("determiner")
        elif token in {"prep", "preposition"}:
            tokens.add("preposition")
        elif token in {"conj", "conjunction"}:
            tokens.add("conjunction")
        elif token in {"interj", "interjection"}:
            tokens.add("interjection")
        elif token in {"part", "particle"}:
            tokens.add("particle")
        elif token in {"phrase"}:
            tokens.add("phrase")
        else:
            tokens.add(token)
    return tokens


def page_pos_tokens(wordart: str) -> set[str]:
    text = normalize_text(wordart).lower()
    tokens: set[str] = set()
    if "substantiv" in text or "pluralwort" in text or text == "noun":
        tokens.add("noun")
    if "verb" in text:
        tokens.add("verb")
    if "adjektiv" in text:
        tokens.add("adjective")
    if "adverb" in text:
        tokens.add("adverb")
    if "pronomen" in text:
        tokens.add("pronoun")
    if "präposition" in text or "praeposition" in text or "preposition" in text:
        tokens.add("preposition")
    if "konjunktion" in text:
        tokens.add("conjunction")
    if "interjektion" in text:
        tokens.add("interjection")
    if "partikel" in text:
        tokens.add("particle")
    if "zahlwort" in text:
        tokens.add("number")
    if "artikel" in text:
        tokens.add("determiner")
    if "phrase" in text or "wortgruppe" in text:
        tokens.add("phrase")
    return tokens


def normalize_gender_value(value: str | None) -> str | None:
    text = normalize_text(value or "").lower()
    if not text:
        return None
    if text in {"m", "m.", "maskulin", "mask.", "masculine", "der"}:
        return "m"
    if text in {"f", "f.", "feminin", "fem.", "feminine", "die"}:
        return "f"
    if text in {"n", "n.", "neutrum", "neut.", "neuter", "das"}:
        return "n"
    if "plural" in text:
        return "pl"
    if "m./f." in text or "der/die" in text or "die/der" in text:
        return "mixed"
    return text


def gender_value_options(value: str | None) -> set[str]:
    text = normalize_text(value or "").lower()
    if not text:
        return set()
    options: set[str] = set()
    tokens = set(re.findall(r"[\w.]+", text, flags=re.UNICODE))
    if tokens & {"m", "m.", "maskulin", "mask.", "masculine", "der"} or "substantiv, maskulin" in text:
        options.add("m")
    if tokens & {"f", "f.", "feminin", "fem.", "feminine", "die"} or "substantiv, feminin" in text:
        options.add("f")
    if tokens & {"n", "n.", "neutrum", "neut.", "neuter", "das"} or "substantiv, neutrum" in text:
        options.add("n")
    if "plural" in text:
        options.add("pl")
    normalized = normalize_gender_value(text)
    if normalized in {"m", "f", "n", "pl"}:
        options.add(normalized)
    if normalized == "mixed":
        options.update({"m", "f"})
    return options


def gender_from_h1(h1_text: str) -> str | None:
    text = normalize_text(h1_text)
    if "," not in text:
        return None
    tail = normalize_text(text.rsplit(",", 1)[1])
    return normalize_gender_value(tail)


def extract_headword(h1_text: str) -> str:
    text = normalize_text(h1_text)
    if "," in text:
        text = normalize_text(text.rsplit(",", 1)[0])
    return text


def parse_duden_page(html_text: str, requested_url: str | None = None) -> DudenPage:
    root = lxml_html.fromstring(html_text)
    canonical = root.xpath("string(//link[@rel='canonical']/@href)").strip()
    if not canonical and requested_url:
        canonical = requested_url
    h1 = normalize_text(root.xpath("string(//h1[1])"))
    headword = extract_headword(h1)
    h1_gender = gender_from_h1(h1)

    wordart = ""
    for label in root.cssselect("dt.tuple__key"):
        key = normalize_text(label.text_content())
        if key.startswith("Wortart"):
            dd = label.getparent().xpath("string(dd[@class='tuple__val'][1])")
            wordart = normalize_text(dd)
            break

    pos_labels = tuple(sorted(page_pos_tokens(wordart)))
    audio_candidates: list[dict[str, str]] = []
    for button in root.cssselect("button.pronunciation-guide__sound[data-href]"):
        audio_url = normalize_text(button.get("data-href") or "")
        if not audio_url:
            continue
        audio_candidates.append(
            {
                "audio_url": audio_url,
                "file_id": normalize_text(button.get("data-file-id") or ""),
                "label": normalize_text(button.text_content()),
            }
        )
    unique_audio: list[dict[str, str]] = []
    seen_audio: set[str] = set()
    for item in audio_candidates:
        if item["audio_url"] in seen_audio:
            continue
        seen_audio.add(item["audio_url"])
        unique_audio.append(item)

    disambiguation_urls: list[str] = []
    for link in root.cssselect("dl.disambiguation a[href]"):
        href = normalize_text(link.get("href") or "")
        if href.startswith("/rechtschreibung/"):
            disambiguation_urls.append("https://www.duden.de" + href)

    return DudenPage(
        canonical_url=canonical,
        headword=headword,
        h1_gender=h1_gender,
        wordart=wordart,
        pos_labels=tuple(pos_labels),
        audio_candidates=tuple(unique_audio),
        disambiguation_urls=tuple(dict.fromkeys(disambiguation_urls)),
    )


def filename_for_row(row: SourceRow) -> str:
    return f"{row.row:04d}_{normalize_word_for_file(row.word)}.mp3"


def build_candidate_urls(word: str) -> list[str]:
    base = normalize_word_for_url(word)
    translit = transliterate_duden_slug(word)
    candidates: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    add(base)
    add(translit)
    add(base.lower())
    add(translit.lower())

    if base.endswith("-"):
        stripped = base[:-1]
        stripped_translit = translit[:-1] if translit.endswith("-") else translit.rstrip("-")
        add(stripped)
        add(stripped_translit)
        add(stripped.lower())
        add(stripped_translit.lower())

    if normalize_text(word) == "Sie":
        add("Sie_Anrede")

    return [f"https://www.duden.de/rechtschreibung/{candidate}" for candidate in candidates]


def load_overrides(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows", data)
    overrides: dict[int, dict[str, Any]] = {}
    if isinstance(rows, dict):
        for key, value in rows.items():
            try:
                overrides[int(key)] = dict(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                overrides[int(item["row"])] = dict(item)
            except (KeyError, TypeError, ValueError):
                continue
    return overrides


def write_overrides(path: Path, overrides: dict[int, dict[str, Any]]) -> None:
    payload = {
        "rows": {
            str(row): value
            for row, value in sorted(overrides.items())
        }
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def audit_decision_to_override(decision: AuditDecision) -> dict[str, Any]:
    if decision.status not in {"exact_audio_found", "ambiguous_page"} or not decision.selected_audio_url:
        raise ValueError("audit decision has no accepted audio")
    match_method = "audit-sitemap-exact"
    if decision.status == "ambiguous_page":
        match_method = "audit-sitemap-ambiguous-first-override"
    if decision.status == "exact_audio_found" and "metadata override" in decision.reason:
        match_method = "exact-headword-metadata-override"
    return {
        "status": "ok",
        "reason": decision.reason,
        "match_method": match_method,
        "duden_page_url": decision.selected_page_url,
        "duden_audio_url": decision.selected_audio_url,
        "file_id": decision.selected_file_id,
    }


def override_to_resolution(row: SourceRow, override: dict[str, Any]) -> Resolution | None:
    if normalize_text(str(override.get("status") or "")) != "ok":
        return None
    audio_url = normalize_text(str(override.get("duden_audio_url") or ""))
    if not audio_url:
        return None
    return Resolution(
        row=row.row,
        word=row.word,
        pos=row.pos,
        gender=row.gender,
        output_filename=filename_for_row(row),
        status="ok",
        reason=normalize_text(str(override.get("reason") or "Duden override")),
        match_method=normalize_text(str(override.get("match_method") or "override")),
        duden_page_url=normalize_text(str(override.get("duden_page_url") or "")) or None,
        duden_audio_url=audio_url,
        file_id=normalize_text(str(override.get("file_id") or "")) or None,
    )


def _normalize_expected_gender(value: str) -> str | None:
    text = normalize_text(value).lower()
    if text in {"", " "}:
        return None
    if text == "m.":
        return "m"
    if text == "f.":
        return "f"
    if text == "n.":
        return "n"
    if text in {"m./f.", "f./m.", "m/f.", "f/m."}:
        return "mixed"
    if text == "pl.":
        return "pl"
    return normalize_gender_value(text)


def gender_matches(expected: str, actual: str | None) -> bool:
    expected_norm = _normalize_expected_gender(expected)
    if expected_norm is None:
        return True
    if actual is None:
        return False
    actual_options = gender_value_options(actual)
    if expected_norm in actual_options:
        return True
    if expected_norm == "mixed" and actual_options & {"m", "f"}:
        return True
    return False


def pos_sets_compatible(expected: set[str], actual: set[str]) -> bool:
    if not expected:
        return True
    if expected & actual:
        return True
    if expected <= {"determiner", "pronoun"} and actual & {"determiner", "pronoun"}:
        return True
    if "interjection" in expected and actual & {"particle", "adjective"}:
        return True
    return False


def pos_matches(source_pos: str, page_pos_labels: tuple[str, ...]) -> bool:
    expected = source_pos_tokens(source_pos)
    actual = set(page_pos_labels)
    if pos_sets_compatible(expected, actual):
        return True
    if expected == {"noun"} and "number" in actual and "noun" not in actual:
        return False
    return False


def headword_matches(source_word: str, page_headword: str) -> bool:
    source = normalize_text(source_word)
    page = normalize_text(page_headword)
    if source == page:
        return True
    if source.endswith("-") and source.rstrip("-") == page:
        return True
    return False


def exact_audit_headword_matches(source_word: str, page_headword: str) -> bool:
    return normalize_text(source_word) == normalize_text(page_headword)


def lexeme_bucket_for_word(word: str) -> str:
    text = normalize_text(word)
    if not text:
        return "0"
    first = text[0]
    replacements = {
        "Ä": "a",
        "ä": "a",
        "Ö": "o",
        "ö": "o",
        "Ü": "u",
        "ü": "u",
        "ẞ": "s",
        "ß": "s",
    }
    first = replacements.get(first, first)
    first = unicodedata.normalize("NFKD", first)
    first = "".join(ch for ch in first if ch.isascii())
    first = first[:1].lower()
    return first if first and first.isalnum() else "0"


def parse_lexeme_sitemap_page(html_text: str) -> list[LexemeCandidate]:
    root = lxml_html.fromstring(html_text)
    candidates: list[LexemeCandidate] = []
    seen: set[str] = set()
    for link in root.cssselect("a[href]"):
        href = normalize_text(link.get("href") or "")
        title = normalize_text(link.text_content())
        if not href or not title or "/rechtschreibung/" not in href:
            continue
        if href.startswith("/"):
            url = DUDEN_BASE_URL + href
        else:
            url = href
        if url in seen:
            continue
        seen.add(url)
        candidates.append(LexemeCandidate(title=title, url=url))
    return candidates


async def load_lexeme_candidates_for_bucket(
    session: aiohttp.ClientSession,
    bucket: str,
    *,
    throttle: RequestThrottle | None = None,
    max_empty_pages: int = 1,
) -> list[LexemeCandidate]:
    page = 0
    empty_pages = 0
    candidates: list[LexemeCandidate] = []
    while True:
        url = f"{DUDEN_LEXEME_BASE_URL}/{bucket}"
        if page:
            url = f"{url}?_wrapper_format=html&page={page}"
        status, html_text, headers = await fetch_page(session, url, throttle=throttle)
        if status == 404:
            break
        if status == 403:
            raise TechnicalError(f"HTTP 403 while fetching {url}")
        if status == 429:
            retry_after = parse_retry_after(headers.get("retry-after"))
            if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
                raise TechnicalError(f"HTTP 429 retry-after {retry_after:.0f}s while fetching {url}")
            await asyncio.sleep(min(retry_after or 1.0, 10.0))
            continue
        if 500 <= status < 600:
            raise TechnicalError(f"HTTP {status} while fetching {url}")
        if status != 200:
            raise TechnicalError(f"HTTP {status} while fetching {url}")
        page_candidates = parse_lexeme_sitemap_page(html_text)
        if not page_candidates:
            empty_pages += 1
            if empty_pages >= max_empty_pages:
                break
        else:
            empty_pages = 0
            candidates.extend(page_candidates)
        page += 1
    return candidates


async def build_lexeme_index_for_rows(
    session: aiohttp.ClientSession,
    rows: list[SourceRow],
    *,
    throttle: RequestThrottle | None = None,
) -> dict[str, list[LexemeCandidate]]:
    buckets = sorted({lexeme_bucket_for_word(row.word) for row in rows})
    index: dict[str, list[LexemeCandidate]] = {}
    for bucket in buckets:
        print(f"[sitemap] bucket={bucket}")
        for candidate in await load_lexeme_candidates_for_bucket(session, bucket, throttle=throttle):
            title = normalize_text(candidate.title)
            index.setdefault(title, []).append(candidate)
    return index


def plural_identical_word_allowed(row: SourceRow, page: DudenPage, candidate_title: str | None) -> bool:
    if _normalize_expected_gender(row.gender) != "pl":
        return False
    if "noun" not in set(page.pos_labels):
        return False
    if candidate_title is None or not exact_audit_headword_matches(row.word, candidate_title):
        return False
    return True


def page_metadata_matches_for_audit(
    row: SourceRow,
    page: DudenPage,
    candidate_title: str | None = None,
) -> tuple[bool, str]:
    title_exact = candidate_title is not None and exact_audit_headword_matches(row.word, candidate_title)
    if not title_exact and not exact_audit_headword_matches(row.word, page.headword):
        return False, f"headword mismatch: {page.headword}"
    expected_pos = source_pos_tokens(row.pos)
    actual_pos = set(page.pos_labels)
    mismatch_reasons: list[str] = []
    if expected_pos and actual_pos and not pos_matches(row.pos, page.pos_labels):
        mismatch_reasons.append(f"POS mismatch: expected {row.pos}, got {page.wordart or page.pos_labels}")
    actual_gender = "pl" if "pluralwort" in normalize_text(page.wordart).lower() else page.h1_gender
    if (
        actual_gender is not None
        and not gender_matches(row.gender, actual_gender)
        and not plural_identical_word_allowed(row, page, candidate_title)
    ):
        mismatch_reasons.append(f"gender mismatch: expected {row.gender}, got {actual_gender}")
    if mismatch_reasons:
        if title_exact:
            return True, "exact sitemap title metadata override: " + "; ".join(mismatch_reasons)
        return False, "; ".join(mismatch_reasons)
    return True, "metadata matched"


def page_to_audit_candidate(candidate: LexemeCandidate, page: DudenPage, accepted: bool, reason: str) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "url": candidate.url,
        "canonical_url": page.canonical_url,
        "headword": page.headword,
        "wordart": page.wordart,
        "pos_labels": list(page.pos_labels),
        "gender": page.h1_gender,
        "audio": list(page.audio_candidates),
        "accepted": accepted,
        "reason": reason,
    }


PREFERRED_AUDIT_PAGE_SLUGS: dict[str, tuple[str, ...]] = {
    "der": ("der__die_das_bestimmte_Artikel",),
    "Fax": ("Fax_Dokument__Geraet",),
    "Foto": ("Foto_Fotografie",),
}


def audit_page_selection_score(
    row: SourceRow,
    candidate: LexemeCandidate,
    page: DudenPage,
    audit_candidate: dict[str, Any],
) -> int:
    score = 0
    preferred_slugs = PREFERRED_AUDIT_PAGE_SLUGS.get(row.word, ())
    if any(slug in candidate.url or slug in page.canonical_url for slug in preferred_slugs):
        score += 1000
    if audit_candidate.get("reason") == "metadata matched":
        score += 100
    expected_pos = source_pos_tokens(row.pos)
    actual_pos = set(page.pos_labels)
    if expected_pos and actual_pos and pos_matches(row.pos, page.pos_labels):
        score += 20
    actual_gender = "pl" if "pluralwort" in normalize_text(page.wordart).lower() else page.h1_gender
    if actual_gender is not None and (
        gender_matches(row.gender, actual_gender) or plural_identical_word_allowed(row, page, candidate.title)
    ):
        score += 10
    return score


def choose_audit_audio(row: SourceRow, accepted: list[tuple[LexemeCandidate, DudenPage, dict[str, Any]]]) -> AuditDecision:
    candidates = tuple(item[2] for item in accepted)
    strict_pages = [
        (candidate, page, audit_candidate)
        for candidate, page, audit_candidate in accepted
        if audit_candidate.get("reason") == "metadata matched"
    ]
    if strict_pages and not any(page.audio_candidates for _, page, _ in strict_pages):
        return AuditDecision(
            row=row,
            status="exact_page_no_audio",
            reason="exact Duden page found but no audio",
            candidates=candidates,
        )
    pages_with_audio = [
        (candidate, page, audit_candidate)
        for candidate, page, audit_candidate in accepted
        if page.audio_candidates
    ]
    if not pages_with_audio:
        return AuditDecision(
            row=row,
            status="exact_page_no_audio",
            reason="exact Duden page found but no audio",
            candidates=candidates,
        )
    first_audio_by_page = [
        (candidate, page, audit_candidate, page.audio_candidates[0])
        for candidate, page, audit_candidate in pages_with_audio
    ]
    scored = [
        (audit_page_selection_score(row, candidate, page, audit_candidate), index, candidate, page, audit_candidate, audio)
        for index, (candidate, page, audit_candidate, audio) in enumerate(first_audio_by_page)
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score, _, candidate, page, audit_candidate, audio = scored[0]
    audio_urls = {item[5]["audio_url"] for item in scored}
    tied_different_audio = [
        item
        for item in scored
        if item[0] == top_score and item[5]["audio_url"] != audio["audio_url"]
    ]
    if len(first_audio_by_page) > 1 and len(audio_urls) > 1 and tied_different_audio:
        return AuditDecision(
            row=row,
            status="ambiguous_page",
            reason="multiple exact pages with different first audio",
            candidates=candidates,
            selected_page_url=page.canonical_url,
            selected_audio_url=audio["audio_url"],
            selected_file_id=audio.get("file_id") or None,
        )
    reason = "exact Duden headword with accepted metadata and first pronunciation"
    if audit_candidate.get("reason") != "metadata matched":
        reason = str(audit_candidate.get("reason") or "exact sitemap title metadata override")
    return AuditDecision(
        row=row,
        status="exact_audio_found",
        reason=reason,
        candidates=candidates,
        selected_page_url=page.canonical_url,
        selected_audio_url=audio["audio_url"],
        selected_file_id=audio.get("file_id") or None,
    )


async def audit_missing_row(
    session: aiohttp.ClientSession,
    row: SourceRow,
    lexeme_index: dict[str, list[LexemeCandidate]],
    *,
    throttle: RequestThrottle | None = None,
) -> AuditDecision:
    exact_candidates = [
        candidate
        for candidate in lexeme_index.get(normalize_text(row.word), [])
        if normalize_text(candidate.title) == normalize_text(row.word)
    ]
    if not exact_candidates:
        return AuditDecision(
            row=row,
            status="no_exact_lexeme",
            reason="no exact headword in Duden lexeme sitemap",
            candidates=(),
        )

    accepted: list[tuple[LexemeCandidate, DudenPage, dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in exact_candidates:
        try:
            status, html_text, headers = await fetch_page(session, candidate.url, throttle=throttle)
        except Exception as exc:
            return AuditDecision(
                row=row,
                status="technical_error",
                reason=f"network error while fetching {candidate.url}: {exc}",
                candidates=tuple(rejected),
            )
        if status == 403:
            return AuditDecision(
                row=row,
                status="technical_error",
                reason=f"HTTP 403 while fetching {candidate.url}",
                candidates=tuple(rejected),
            )
        if status == 429:
            retry_after = parse_retry_after(headers.get("retry-after"))
            if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
                return AuditDecision(
                    row=row,
                    status="technical_error",
                    reason=f"HTTP 429 retry-after {retry_after:.0f}s while fetching {candidate.url}",
                    candidates=tuple(rejected),
                )
            await asyncio.sleep(min(retry_after or 1.0, 10.0))
            status, html_text, headers = await fetch_page(session, candidate.url, throttle=throttle)
        if status != 200:
            return AuditDecision(
                row=row,
                status="technical_error",
                reason=f"HTTP {status} while fetching {candidate.url}",
                candidates=tuple(rejected),
            )
        try:
            page = parse_duden_page(html_text, requested_url=candidate.url)
        except Exception as exc:
            return AuditDecision(
                row=row,
                status="technical_error",
                reason=f"parse error while fetching {candidate.url}: {exc}",
                candidates=tuple(rejected),
            )
        ok, reason = page_metadata_matches_for_audit(row, page, candidate.title)
        audit_candidate = page_to_audit_candidate(candidate, page, ok, reason)
        if ok:
            accepted.append((candidate, page, audit_candidate))
        else:
            rejected.append(audit_candidate)

    if not accepted:
        return AuditDecision(
            row=row,
            status="metadata_conflict",
            reason="exact lexeme found but all candidates conflict with POS or gender",
            candidates=tuple(rejected),
        )
    return choose_audit_audio(row, accepted)


def make_manifest_row(
    row: SourceRow,
    *,
    status: str,
    reason: str,
    match_method: str | None = None,
    duden_page_url: str | None = None,
    duden_audio_url: str | None = None,
    file_id: str | None = None,
    size: int | None = None,
    sha256: str | None = None,
    content_type: str | None = None,
    etag: str | None = None,
) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status}")
    return {
        "row": row.row,
        "word": row.word,
        "pos": row.pos,
        "gender": row.gender,
        "output_filename": filename_for_row(row),
        "source": "duden",
        "duden_page_url": duden_page_url,
        "duden_audio_url": duden_audio_url,
        "file_id": file_id,
        "match_method": match_method,
        "status": status,
        "reason": reason,
        "size": size,
        "sha256": sha256,
        "content_type": content_type,
        "etag": etag,
    }


def make_pending_row(row: SourceRow, reason: str = "pending") -> dict[str, Any]:
    return make_manifest_row(row, status="pending", reason=reason)


def make_technical_error_row(
    row: SourceRow,
    *,
    reason: str,
    match_method: str = "technical-error",
    duden_page_url: str | None = None,
    duden_audio_url: str | None = None,
    file_id: str | None = None,
) -> dict[str, Any]:
    return make_manifest_row(
        row,
        status="technical_error",
        reason=reason,
        match_method=match_method,
        duden_page_url=duden_page_url,
        duden_audio_url=duden_audio_url,
        file_id=file_id,
    )


def parse_cooldown_until(meta: dict[str, Any] | None) -> datetime | None:
    if not meta:
        return None
    raw = normalize_text(str(meta.get("cooldown_until") or ""))
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_manifest_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        last_exc: OSError | None = None
        for _ in range(ATOMIC_REPLACE_RETRIES):
            try:
                os.replace(tmp_name, path)
                return
            except PermissionError as exc:
                last_exc = exc
                time.sleep(ATOMIC_REPLACE_SLEEP)
        if last_exc is not None:
            raise last_exc
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    atomic_write_text(path, payload)


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_mp3_bytes(content: bytes) -> str:
    if len(content) < 8:
        raise ValueError("mp3 too small")
    if content.startswith(b"ID3"):
        return "id3"
    if content[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "mpeg"
    raise ValueError("bad mp3 signature")


def parse_retry_after(header_value: str | None) -> float | None:
    if not header_value:
        return None
    header_value = header_value.strip()
    if not header_value:
        return None
    if header_value.isdigit():
        return float(int(header_value))
    try:
        dt = parsedate_to_datetime(header_value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


def should_retry(status: int) -> bool:
    return status == 429 or 500 <= status < 600


async def fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    *,
    throttle: RequestThrottle | None = None,
) -> tuple[int, str, dict[str, str]]:
    if throttle is not None:
        await throttle.wait_for_page()
    async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
        text = await resp.text(errors="replace")
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, text, headers


async def download_audio(
    session: aiohttp.ClientSession,
    url: str,
    dest_path: Path,
    *,
    throttle: RequestThrottle | None = None,
) -> tuple[int, str, str | None, str | None]:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: str | None = None
    for attempt in range(MAX_RETRIES):
        if throttle is not None:
            await throttle.wait_for_cdn()
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
            content_type = normalize_text(resp.headers.get("content-type") or "")
            etag = resp.headers.get("etag")
            status = resp.status
            if status == 403:
                raise TechnicalError(f"HTTP 403 on {url}")
            if status == 429:
                retry_after = parse_retry_after(resp.headers.get("retry-after"))
                if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
                    raise TechnicalError(f"HTTP 429 retry-after {retry_after:.0f}s on {url}")
                last_error = f"HTTP 429 on {url}"
                await asyncio.sleep(min(retry_after or (1.0 + attempt), 10.0))
                continue
            if 500 <= status < 600:
                retry_after = parse_retry_after(resp.headers.get("retry-after"))
                last_error = f"HTTP {status} on {url}"
                await asyncio.sleep(min(retry_after or (1.0 + attempt), 10.0))
                continue
            if status != 200:
                body = await resp.text(errors="replace")
                raise TechnicalError(f"HTTP {status}: {body[:200]}")
            if content_type and not (
                content_type.startswith("audio/") or content_type.startswith("application/octet-stream")
            ):
                raise ValueError(f"unexpected content-type: {content_type}")
            fd, tmp_name = tempfile.mkstemp(dir=dest_path.parent, suffix=".mp3.tmp")
            try:
                size = 0
                hasher = hashlib.sha256()
                with os.fdopen(fd, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        size += len(chunk)
                        hasher.update(chunk)
                with open(tmp_name, "rb") as fh:
                    validate_mp3_bytes(fh.read(16))
                os.replace(tmp_name, dest_path)
                return size, hasher.hexdigest(), content_type or None, etag
            except Exception:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
                raise
    raise TechnicalError(last_error or "download failed")


def existing_file_is_valid(path: Path, expected_sha256: str | None = None, expected_size: int | None = None) -> bool:
    if not path.exists():
        return False
    if expected_size is not None and path.stat().st_size != expected_size:
        return False
    with path.open("rb") as fh:
        try:
            validate_mp3_bytes(fh.read(16))
        except ValueError:
            return False
    if expected_sha256 is not None and hash_file(path) != expected_sha256:
        return False
    return True


def copy_file_atomic(source_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=str(output_path.parent),
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp_path = Path(tmp.name)
        with source_path.open("rb") as src:
            shutil.copyfileobj(src, tmp)
    os.replace(tmp_path, output_path)


def reuse_live_sources() -> tuple[tuple[Path, Path], ...]:
    if REUSE_LIVE_SOURCES:
        return REUSE_LIVE_SOURCES
    if REUSE_LIVE_WORDS_DIR is not None and REUSE_LIVE_MANIFEST_PATH is not None:
        return ((REUSE_LIVE_WORDS_DIR, REUSE_LIVE_MANIFEST_PATH),)
    return ()


def load_reuse_index() -> dict[tuple[str, str, str], dict[str, Any]]:
    global _REUSE_INDEX_CACHE
    if _REUSE_INDEX_CACHE is not None:
        return _REUSE_INDEX_CACHE
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for words_dir, manifest_path in reuse_live_sources():
        if not manifest_path.exists():
            continue
        for item in load_existing_manifest_rows(manifest_path):
            if normalize_text(str(item.get("status") or "")).lower() != "ok":
                continue
            if normalize_text(str(item.get("source") or "")).lower() != "duden":
                continue
            key = (
                normalize_text(str(item.get("word") or "")),
                normalize_text(str(item.get("pos") or "")),
                normalize_text(str(item.get("gender") or "")),
            )
            if key not in index:
                index[key] = {
                    **item,
                    "_reuse_words_dir": words_dir,
                    "_reuse_manifest_path": manifest_path,
                }
    _REUSE_INDEX_CACHE = index
    return index


def reuse_existing_duden_audio(row: SourceRow) -> Resolution | None:
    if not reuse_live_sources():
        return None
    key = (row.word, row.pos, row.gender)
    item = load_reuse_index().get(key)
    if item is None:
        return None
    source_filename = normalize_text(str(item.get("output_filename") or ""))
    if not source_filename:
        return None
    source_words_dir = item["_reuse_words_dir"]
    source_manifest_path = item["_reuse_manifest_path"]
    source_path = source_words_dir / source_filename
    source_sha256 = normalize_text(str(item.get("sha256") or "")) or None
    source_size_raw = item.get("size")
    source_size = int(source_size_raw) if source_size_raw is not None else None
    if not existing_file_is_valid(source_path, source_sha256, source_size):
        return None
    output_path = STAGING_WORDS_DIR / filename_for_row(row)
    if not existing_file_is_valid(output_path, source_sha256, source_size):
        copy_file_atomic(source_path, output_path)
    return Resolution(
        row=row.row,
        word=row.word,
        pos=row.pos,
        gender=row.gender,
        output_filename=filename_for_row(row),
        status="ok",
        reason=f"reused Duden audio from {display_path(source_manifest_path)}",
        match_method="reuse-duden-manifest",
        duden_page_url=normalize_text(str(item.get("duden_page_url") or "")) or None,
        duden_audio_url=normalize_text(str(item.get("duden_audio_url") or "")) or None,
        file_id=normalize_text(str(item.get("file_id") or "")) or None,
        size=source_size,
        sha256=source_sha256,
        content_type=normalize_text(str(item.get("content_type") or "")) or "audio/mpeg",
        etag=item.get("etag"),
    )


def inventory_tree(path: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    if path.exists():
        for item in sorted(path.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(path).as_posix()
            files.append(
                {
                    "path": rel,
                    "size": item.stat().st_size,
                    "sha256": hash_file(item),
                }
            )
    return {
        "path": str(path),
        "exists": path.exists(),
        "file_count": len(files),
        "files": files,
    }


def current_matrix_inventory() -> dict[str, Any]:
    return {
        "live_words": inventory_tree(LIVE_WORDS_DIR),
        "live_manifest": inventory_tree(LIVE_MANIFEST_PATH.parent if LIVE_MANIFEST_PATH.exists() else AUDIO_ROOT),
        "manifest_hash": hash_file(LIVE_MANIFEST_PATH) if LIVE_MANIFEST_PATH.exists() else None,
        "meta_hash": hash_file(LIVE_META_PATH) if LIVE_META_PATH.exists() else None,
        "generation_log_hash": hash_file(AUDIO_ROOT / "generation.log") if (AUDIO_ROOT / "generation.log").exists() else None,
    }


def live_manifest_source() -> str | None:
    if not LIVE_MANIFEST_PATH.exists():
        return None
    try:
        first_line = next((line for line in LIVE_MANIFEST_PATH.read_text(encoding="utf-8").splitlines() if line.strip()), None)
        if not first_line:
            return None
        row = json.loads(first_line)
    except (OSError, json.JSONDecodeError, StopIteration):
        return None
    return normalize_text(str(row.get("source") or "")).lower() or None


def should_backup_live_state() -> bool:
    source = live_manifest_source()
    return source != "duden"


def backup_current_matrix_state() -> Path | None:
    if not LIVE_WORDS_DIR.exists() and not LIVE_MANIFEST_PATH.exists() and not LIVE_META_PATH.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_ROOT / f"matrix_{stamp}"
    backup_words = backup_dir / "words"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if LIVE_WORDS_DIR.exists():
        shutil.copytree(LIVE_WORDS_DIR, backup_words, copy_function=shutil.copy2)
    if LIVE_MANIFEST_PATH.exists():
        shutil.copy2(LIVE_MANIFEST_PATH, backup_dir / LIVE_MANIFEST_PATH.name)
    if LIVE_META_PATH.exists():
        shutil.copy2(LIVE_META_PATH, backup_dir / LIVE_META_PATH.name)
    generation_log = AUDIO_ROOT / "generation.log"
    if generation_log.exists():
        shutil.copy2(generation_log, backup_dir / generation_log.name)
    inventory = current_matrix_inventory()
    inventory["backup_created_at"] = stamp
    atomic_write_text(backup_dir / "inventory.json", json.dumps(inventory, ensure_ascii=False, indent=2))
    return backup_dir


def bootstrap_resume_staging() -> None:
    if STAGING_WORDS_DIR.exists():
        return
    if LIVE_WORDS_DIR.exists():
        shutil.copytree(LIVE_WORDS_DIR, STAGING_WORDS_DIR, copy_function=shutil.copy2)
    else:
        STAGING_WORDS_DIR.mkdir(parents=True, exist_ok=True)
    if LIVE_MANIFEST_PATH.exists() and not STAGING_MANIFEST_PATH.exists():
        shutil.copy2(LIVE_MANIFEST_PATH, STAGING_MANIFEST_PATH)
    if LIVE_META_PATH.exists() and not STAGING_META_PATH.exists():
        shutil.copy2(LIVE_META_PATH, STAGING_META_PATH)


def reset_resume_rows_to_pending(rows: list[dict[str, Any]], reset_row_numbers: set[int]) -> None:
    for row in rows:
        if int(row["row"]) not in reset_row_numbers:
            continue
        row.update(
            {
                "status": "pending",
                "reason": "resume pending after technical error cooldown",
                "match_method": None,
                "duden_page_url": None,
                "duden_audio_url": None,
                "file_id": None,
                "size": None,
                "sha256": None,
                "content_type": None,
                "etag": None,
            }
        )


def manifest_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = normalize_text(str(row.get("status") or "")).lower() or "pending"
        counts[status] = counts.get(status, 0) + 1
    return counts


async def resolve_row(
    session: aiohttp.ClientSession,
    row: SourceRow,
    overrides: dict[int, dict[str, Any]],
    *,
    throttle: RequestThrottle | None = None,
) -> tuple[Resolution, DudenPage | None]:
    override = overrides.get(row.row)
    if override:
        return _apply_override(row, override), None

    candidate_urls = build_candidate_urls(row.word)
    page_hits: list[DudenPage] = []
    for url in candidate_urls:
        html_text = ""
        headers: dict[str, str] = {}
        for attempt in range(MAX_RETRIES):
            try:
                if throttle is None:
                    status, html_text, headers = await fetch_page(session, url)
                else:
                    status, html_text, headers = await fetch_page(session, url, throttle=throttle)
            except Exception as exc:  # network / parser issue
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.0)
                    continue
                return (
                    Resolution(
                        row=row.row,
                        word=row.word,
                        pos=row.pos,
                        gender=row.gender,
                        output_filename=filename_for_row(row),
                        status="technical_error",
                        reason=f"network error while fetching {url}: {exc}",
                        match_method="page-network-error",
                        duden_page_url=url,
                        duden_audio_url=None,
                        file_id=None,
                    ),
                    None,
                )
            if status == 404:
                break
            if status == 403:
                return (
                    Resolution(
                        row=row.row,
                        word=row.word,
                        pos=row.pos,
                        gender=row.gender,
                        output_filename=filename_for_row(row),
                        status="technical_error",
                        reason=f"HTTP 403 while fetching {url}",
                        match_method="page-http-403",
                        duden_page_url=url,
                        duden_audio_url=None,
                        file_id=None,
                    ),
                    None,
                )
            if status == 429:
                retry_after = parse_retry_after(headers.get("retry-after"))
                if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
                    return (
                        Resolution(
                            row=row.row,
                            word=row.word,
                            pos=row.pos,
                            gender=row.gender,
                            output_filename=filename_for_row(row),
                            status="technical_error",
                            reason=f"HTTP 429 retry-after {retry_after:.0f}s while fetching {url}",
                            match_method="page-http-429",
                            duden_page_url=url,
                            duden_audio_url=None,
                            file_id=None,
                        ),
                        None,
                    )
                await asyncio.sleep(min(retry_after or (1.0 + attempt), 10.0))
                continue
            if 500 <= status < 600:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.0 + attempt)
                    continue
                return (
                    Resolution(
                        row=row.row,
                        word=row.word,
                        pos=row.pos,
                        gender=row.gender,
                        output_filename=filename_for_row(row),
                        status="technical_error",
                        reason=f"HTTP {status} while fetching {url}",
                        match_method="page-http-5xx",
                        duden_page_url=url,
                        duden_audio_url=None,
                        file_id=None,
                    ),
                        None,
            )
        if status == 404:
            continue
        if status != 200:
            return (
                Resolution(
                    row=row.row,
                    word=row.word,
                    pos=row.pos,
                    gender=row.gender,
                    output_filename=filename_for_row(row),
                    status="technical_error",
                    reason=f"HTTP {status} while fetching {url}",
                    match_method="page-http-error",
                    duden_page_url=url,
                    duden_audio_url=None,
                    file_id=None,
                ),
                None,
            )
        try:
            page = parse_duden_page(html_text, requested_url=url)
        except Exception as exc:
            return (
                Resolution(
                    row=row.row,
                    word=row.word,
                    pos=row.pos,
                    gender=row.gender,
                    output_filename=filename_for_row(row),
                    status="technical_error",
                    reason=f"parse error while fetching {url}: {exc}",
                    match_method="page-parse-error",
                    duden_page_url=url,
                    duden_audio_url=None,
                    file_id=None,
                ),
                None,
            )
        if not page.canonical_url:
            return (
                Resolution(
                    row=row.row,
                    word=row.word,
                    pos=row.pos,
                    gender=row.gender,
                    output_filename=filename_for_row(row),
                    status="technical_error",
                    reason=f"missing canonical url while fetching {url}",
                    match_method="page-missing-canonical",
                    duden_page_url=url,
                    duden_audio_url=None,
                    file_id=None,
                ),
                None,
            )
        if not headword_matches(row.word, page.headword):
            continue
        if not pos_matches(row.pos, page.pos_labels):
            continue
        if not gender_matches(row.gender, page.h1_gender):
            continue
        page_hits.append(page)
        # Candidate URLs differ only by spelling/case variants.  Once the
        # exact headword plus POS/gender has matched, fetching a lower-cased
        # duplicate cannot improve the decision and wastes a page request.
        break

    if not page_hits:
        return (
            Resolution(
                row=row.row,
                word=row.word,
                pos=row.pos,
                gender=row.gender,
                output_filename=filename_for_row(row),
                status="unresolved",
                reason="no matching Duden page",
                match_method="not-found",
                duden_page_url=None,
                duden_audio_url=None,
                file_id=None,
            ),
            None,
        )

    if len(page_hits) > 1:
        if not PREFER_FIRST_EXACT_CANDIDATE:
            urls = ", ".join(page.canonical_url for page in page_hits)
            return (
                Resolution(
                    row=row.row,
                    word=row.word,
                    pos=row.pos,
                    gender=row.gender,
                    output_filename=filename_for_row(row),
                    status="ambiguous",
                    reason=f"multiple matching pages: {urls}",
                    match_method="page-ambiguous",
                    duden_page_url=page_hits[0].canonical_url,
                    duden_audio_url=None,
                    file_id=None,
                ),
                page_hits[0],
            )

    page = page_hits[0]
    if len(page.audio_candidates) == 0:
        return (
            Resolution(
                row=row.row,
                word=row.word,
                pos=row.pos,
                gender=row.gender,
                output_filename=filename_for_row(row),
                status="unresolved",
                reason="matching page has no audio",
                match_method="page-no-audio",
                duden_page_url=page.canonical_url,
                duden_audio_url=None,
                file_id=None,
            ),
            page,
        )

    if len(page.audio_candidates) > 1:
        if not PREFER_FIRST_EXACT_CANDIDATE:
            file_ids = ", ".join(item["file_id"] for item in page.audio_candidates if item.get("file_id"))
            return (
                Resolution(
                    row=row.row,
                    word=row.word,
                    pos=row.pos,
                    gender=row.gender,
                    output_filename=filename_for_row(row),
                    status="ambiguous",
                    reason=f"multiple audio candidates: {file_ids or page.audio_candidates[0]['audio_url']}",
                    match_method="page-multi-audio",
                    duden_page_url=page.canonical_url,
                    duden_audio_url=None,
                    file_id=None,
                ),
                page,
            )

    audio = page.audio_candidates[0]
    match_method = "exact-page"
    reason = "matched exact headword, pos, and gender"
    if PREFER_FIRST_EXACT_CANDIDATE and (len(page_hits) > 1 or len(page.audio_candidates) > 1):
        match_method = "exact-headword-first-candidate"
        reason = "matched exact headword; selected first Duden candidate"
    return (
        Resolution(
            row=row.row,
            word=row.word,
            pos=row.pos,
            gender=row.gender,
            output_filename=filename_for_row(row),
            status="ok",
            reason=reason,
            match_method=match_method,
            duden_page_url=page.canonical_url,
            duden_audio_url=audio["audio_url"],
            file_id=audio.get("file_id") or None,
        ),
        page,
    )


def _apply_override(row: SourceRow, override: dict[str, Any]) -> Resolution:
    status = normalize_text(str(override.get("status", "ok"))).lower() or "ok"
    if status not in ALLOWED_STATUSES:
        status = "invalid"
    reason = normalize_text(str(override.get("reason", "manual override")))
    return Resolution(
        row=row.row,
        word=row.word,
        pos=row.pos,
        gender=row.gender,
        output_filename=filename_for_row(row),
        status=status,
        reason=reason,
        match_method=normalize_text(str(override.get("match_method") or "manual-override")) or "manual-override",
        duden_page_url=override.get("duden_page_url"),
        duden_audio_url=override.get("duden_audio_url"),
        file_id=override.get("file_id"),
    )


def resolution_to_row(resolution: Resolution) -> dict[str, Any]:
    return asdict(resolution)


def load_existing_manifest_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rows.append(json.loads(raw))
    return rows


def validate_live_duden_manifest(expected_rows: int | None = None) -> list[dict[str, Any]]:
    """Validate the live Duden manifest before any resume-style mutation."""
    rows = load_existing_manifest_rows(LIVE_MANIFEST_PATH)
    expected = EXPECTED_ROWS if expected_rows is None else expected_rows
    if len(rows) != expected:
        raise RuntimeError(f"live Duden manifest expected {expected} rows, got {len(rows)}")
    required = {"row", "word", "output_filename", "source", "status"}
    bad_schema = [int(item.get("row") or 0) for item in rows if not required.issubset(item)]
    if bad_schema:
        raise RuntimeError(
            "live manifest is not a Duden manifest; repair it before resume/audit/fill "
            f"(first bad rows: {bad_schema[:5]})"
        )
    if any(normalize_text(str(item.get("source") or "")).lower() != "duden" for item in rows):
        raise RuntimeError("live manifest contains non-Duden rows; repair it before resume/audit/fill")
    invalid_statuses = sorted({
        normalize_text(str(item.get("status") or "")).lower()
        for item in rows
        if normalize_text(str(item.get("status") or "")).lower() not in ALLOWED_STATUSES
    })
    if invalid_statuses:
        raise RuntimeError(f"live Duden manifest has invalid statuses: {invalid_statuses}")
    return rows


def repair_live_duden_manifest(
    source_rows: list[SourceRow], *, dry_run: bool, confirmation: str | None,
) -> dict[str, Any]:
    """Reconstruct a lost Duden manifest from the latest valid checkpoint and audit."""
    candidates: list[tuple[int, Path, list[dict[str, Any]]]] = []
    for checkpoint in sorted(DUDEN_CHECKPOINT_ROOT.glob("duden_*")):
        manifest_path = checkpoint / LIVE_MANIFEST_PATH.name
        rows = load_existing_manifest_rows(manifest_path)
        if len(rows) != len(source_rows):
            continue
        if any(normalize_text(str(item.get("source") or "")).lower() != "duden" for item in rows):
            continue
        candidates.append((sum(item.get("status") == "ok" for item in rows), checkpoint, rows))
    if not candidates:
        raise RuntimeError("no valid Duden checkpoint manifest available for repair")
    _, checkpoint, base_rows = max(candidates, key=lambda item: (item[0], item[1].name))
    audit_rows = load_audit_rows(MISSING_AUDIT_PATH)
    by_source = source_row_by_number(source_rows)
    repaired: list[dict[str, Any]] = []
    expected_files: set[str] = set()
    for item in base_rows:
        row_num = int(item["row"])
        source_row = by_source[row_num]
        filename = filename_for_row(source_row)
        live_path = LIVE_WORDS_DIR / filename
        if item.get("status") == "ok":
            if not existing_file_is_valid(live_path, item.get("sha256"), item.get("size")):
                raise RuntimeError(f"checkpoint-backed Duden file failed validation: {filename}")
            current = dict(item)
        else:
            audit = audit_rows.get(row_num)
            if audit and audit.get("status") == "exact_audio_found" and live_path.exists():
                if not existing_file_is_valid(live_path):
                    raise RuntimeError(f"audited Duden file failed validation: {filename}")
                current = make_manifest_row(
                    source_row,
                    status="ok",
                    reason=normalize_text(str(audit.get("reason") or "repaired from Duden audit")),
                    match_method="audit-missing-repair",
                    duden_page_url=audit.get("selected_page_url"),
                    duden_audio_url=audit.get("selected_audio_url"),
                    file_id=audit.get("selected_file_id"),
                    size=live_path.stat().st_size,
                    sha256=hash_file(live_path),
                    content_type="audio/mpeg",
                )
            else:
                status = normalize_text(str((audit or {}).get("status") or "unresolved"))
                current = make_manifest_row(
                    source_row,
                    status="unresolved",
                    reason=f"{status}: {normalize_text(str((audit or {}).get('reason') or item.get('reason') or 'no accepted Duden audio'))}",
                    match_method=f"audit-{status or 'unresolved'}",
                    duden_page_url=(audit or {}).get("selected_page_url"),
                )
        if current.get("status") == "ok":
            expected_files.add(filename)
        repaired.append(current)
    actual_files = {path.name for path in LIVE_WORDS_DIR.glob("*.mp3")}
    if actual_files != expected_files:
        raise RuntimeError(
            "live Duden file set does not match reconstructed manifest: "
            f"missing={sorted(expected_files - actual_files)[:5]} extra={sorted(actual_files - expected_files)[:5]}"
        )
    counts = manifest_status_counts(repaired)
    result = {
        "checkpoint": str(checkpoint),
        "rows": len(repaired),
        "files": len(actual_files),
        "status_counts": counts,
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    if confirmation != "REPAIR_A1_DUDEN_MANIFEST":
        raise RuntimeError("confirmation must equal REPAIR_A1_DUDEN_MANIFEST")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = DUDEN_CHECKPOINT_ROOT / f"manifest_repair_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(LIVE_WORDS_DIR, backup_dir / "words", copy_function=shutil.copy2)
    if LIVE_MANIFEST_PATH.exists():
        shutil.copy2(LIVE_MANIFEST_PATH, backup_dir / LIVE_MANIFEST_PATH.name)
    if LIVE_META_PATH.exists():
        shutil.copy2(LIVE_META_PATH, backup_dir / LIVE_META_PATH.name)
    write_manifest(LIVE_MANIFEST_PATH, repaired)
    atomic_write_text(
        LIVE_META_PATH,
        json.dumps(
            {
                "phase": "repair-manifest",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source_rows": len(source_rows),
                "status_counts": counts,
                "checkpoint": str(checkpoint),
                "backup": str(backup_dir),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    result["backup"] = str(backup_dir)
    return result


def find_row_index(rows: list[dict[str, Any]], row_num: int) -> int:
    for idx, item in enumerate(rows):
        if int(item["row"]) == row_num:
            return idx
    raise KeyError(row_num)


def update_existing_manifest_row(
    rows: list[dict[str, Any]],
    resolution: Resolution,
) -> None:
    idx = find_row_index(rows, resolution.row)
    rows[idx].update(resolution_to_row(resolution))


def source_row_by_number(rows: list[SourceRow]) -> dict[int, SourceRow]:
    return {row.row: row for row in rows}


def live_missing_rows(source_rows: list[SourceRow]) -> list[SourceRow]:
    by_row = source_row_by_number(source_rows)
    manifest_rows = load_existing_manifest_rows(LIVE_MANIFEST_PATH)
    missing: list[SourceRow] = []
    for item in manifest_rows:
        if item.get("status") != "ok":
            source_row = by_row.get(int(item["row"]))
            if source_row is not None:
                missing.append(source_row)
    return missing


def load_audit_rows(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[int, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows[int(item["row"])] = item
    return rows


def audit_decision_from_item(row: SourceRow, item: dict[str, Any]) -> AuditDecision:
    return AuditDecision(
        row=row,
        status=normalize_text(str(item.get("status") or "")),
        reason=normalize_text(str(item.get("reason") or "")),
        candidates=tuple(item.get("candidates") or ()),
        selected_page_url=item.get("selected_page_url"),
        selected_audio_url=item.get("selected_audio_url"),
        selected_file_id=item.get("selected_file_id"),
    )


def write_audit_rows(path: Path, decisions: list[AuditDecision]) -> None:
    payload = "\n".join(json.dumps(decision.to_json_row(), ensure_ascii=False) for decision in decisions) + "\n"
    atomic_write_text(path, payload)


def audit_item_to_resolution(row: SourceRow, item: dict[str, Any]) -> Resolution:
    status = normalize_text(str(item.get("status") or ""))
    if status == "exact_audio_found":
        return Resolution(
            row=row.row,
            word=row.word,
            pos=row.pos,
            gender=row.gender,
            output_filename=filename_for_row(row),
            status="ok",
            reason=normalize_text(str(item.get("reason") or "exact Duden audio found")),
            match_method="audit-sitemap-exact",
            duden_page_url=item.get("selected_page_url"),
            duden_audio_url=item.get("selected_audio_url"),
            file_id=item.get("selected_file_id"),
        )
    if status == "ambiguous_page":
        return Resolution(
            row=row.row,
            word=row.word,
            pos=row.pos,
            gender=row.gender,
            output_filename=filename_for_row(row),
            status="ambiguous",
            reason=normalize_text(str(item.get("reason") or "ambiguous exact Duden pages")),
            match_method="audit-ambiguous-page",
            duden_page_url=item.get("selected_page_url"),
            duden_audio_url=None,
            file_id=None,
        )
    if status == "technical_error":
        return Resolution(
            row=row.row,
            word=row.word,
            pos=row.pos,
            gender=row.gender,
            output_filename=filename_for_row(row),
            status="technical_error",
            reason=normalize_text(str(item.get("reason") or "technical error")),
            match_method="audit-technical-error",
            duden_page_url=item.get("selected_page_url"),
            duden_audio_url=None,
            file_id=None,
        )
    return Resolution(
        row=row.row,
        word=row.word,
        pos=row.pos,
        gender=row.gender,
        output_filename=filename_for_row(row),
        status="unresolved",
        reason=f"{status}: {normalize_text(str(item.get('reason') or 'no accepted Duden audio'))}",
        match_method=f"audit-{status or 'unresolved'}",
        duden_page_url=item.get("selected_page_url"),
        duden_audio_url=None,
        file_id=None,
    )


def checkpoint_current_duden_state() -> Path | None:
    if live_manifest_source() != "duden":
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    checkpoint_dir = DUDEN_CHECKPOINT_ROOT / f"duden_{stamp}"
    checkpoint_words = checkpoint_dir / "words"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    if LIVE_WORDS_DIR.exists():
        shutil.copytree(LIVE_WORDS_DIR, checkpoint_words, copy_function=shutil.copy2)
    if LIVE_MANIFEST_PATH.exists():
        shutil.copy2(LIVE_MANIFEST_PATH, checkpoint_dir / LIVE_MANIFEST_PATH.name)
    if LIVE_META_PATH.exists():
        shutil.copy2(LIVE_META_PATH, checkpoint_dir / LIVE_META_PATH.name)
    inventory = current_matrix_inventory()
    inventory["checkpoint_created_at"] = stamp
    inventory["checkpoint_type"] = "duden-live"
    atomic_write_text(checkpoint_dir / "inventory.json", json.dumps(inventory, ensure_ascii=False, indent=2))
    return checkpoint_dir


def reset_staging_from_live() -> None:
    if STAGING_WORDS_DIR.exists():
        shutil.rmtree(STAGING_WORDS_DIR)
    STAGING_WORDS_DIR.mkdir(parents=True, exist_ok=True)
    if LIVE_WORDS_DIR.exists():
        for item in LIVE_WORDS_DIR.iterdir():
            if item.is_file():
                shutil.copy2(item, STAGING_WORDS_DIR / item.name)
    if LIVE_MANIFEST_PATH.exists():
        shutil.copy2(LIVE_MANIFEST_PATH, STAGING_MANIFEST_PATH)
    if LIVE_META_PATH.exists():
        shutil.copy2(LIVE_META_PATH, STAGING_META_PATH)


def rows_for_pilot(rows: list[SourceRow]) -> list[SourceRow]:
    by_word = {row.word: row for row in rows}
    selected: list[SourceRow] = []
    for word in PILOT_WORDS:
        row = by_word.get(word)
        if row is not None:
            selected.append(row)
    return selected


def manifest_meta(
    source_rows: list[SourceRow],
    inventory: dict[str, Any],
    phase: str,
    *,
    cooldown_until: str | None = None,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "source_path": display_path(SOURCE_PATH),
        "source_sha256": hash_file(SOURCE_PATH),
        "source_row_count": len(source_rows),
        "expected_rows": EXPECTED_ROWS,
        "output_live_dir": display_path(LIVE_WORDS_DIR),
        "output_staging_dir": display_path(STAGING_WORDS_DIR),
        "manifest_path": display_path(STAGING_MANIFEST_PATH),
        "live_manifest_path": display_path(LIVE_MANIFEST_PATH),
        "live_meta_path": display_path(LIVE_META_PATH),
        "overrides_path": display_path(OVERRIDES_PATH),
        "cooldown_until": cooldown_until,
        "inventory": inventory,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


STAGING_MANIFEST_PATH = STAGING_WORDS_DIR / "manifest.jsonl"
STAGING_META_PATH = STAGING_WORDS_DIR / "manifest.meta.json"


def build_initial_manifest(rows: list[SourceRow]) -> list[dict[str, Any]]:
    return [
        make_pending_row(row, reason="preflight placeholder")
        for row in rows
    ]


def finalize_staging_to_live() -> Path | None:
    if not STAGING_WORDS_DIR.exists():
        return None
    backup_dir = backup_current_matrix_state() if should_backup_live_state() else None
    if LIVE_WORDS_DIR.exists():
        shutil.rmtree(LIVE_WORDS_DIR)
    LIVE_WORDS_DIR.parent.mkdir(parents=True, exist_ok=True)
    os.replace(STAGING_WORDS_DIR, LIVE_WORDS_DIR)
    staged_manifest = LIVE_WORDS_DIR / "manifest.jsonl"
    staged_meta = LIVE_WORDS_DIR / "manifest.meta.json"
    if staged_manifest.exists():
        if LIVE_MANIFEST_PATH.exists():
            LIVE_MANIFEST_PATH.unlink()
        os.replace(staged_manifest, LIVE_MANIFEST_PATH)
    if staged_meta.exists():
        if LIVE_META_PATH.exists():
            LIVE_META_PATH.unlink()
        os.replace(staged_meta, LIVE_META_PATH)
    return backup_dir


async def process_rows(
    rows: list[SourceRow],
    *,
    mode: str,
    confirm_usage: bool,
) -> int:
    if mode in {"pilot", "full", "resume"} and not confirm_usage:
        raise RuntimeError(
            "Duden downloads require --confirm-usage and a manual check of "
            "https://www.duden.de/form/license-request plus https://www.duden.de/robots.txt."
        )
    if mode == "resume" and LIVE_MANIFEST_PATH.exists():
        validate_live_duden_manifest(len(rows))

    overrides = load_overrides(OVERRIDES_PATH)
    if mode == "resume":
        bootstrap_resume_staging()
    STAGING_WORDS_DIR.mkdir(parents=True, exist_ok=True)
    if STAGING_MANIFEST_PATH.exists():
        manifest_rows = load_existing_manifest_rows(STAGING_MANIFEST_PATH)
    else:
        manifest_rows = build_initial_manifest(rows)
        write_manifest(STAGING_MANIFEST_PATH, manifest_rows)

    meta_source = load_manifest_meta(STAGING_META_PATH) or load_manifest_meta(LIVE_META_PATH)
    cooldown_until = parse_cooldown_until(meta_source)
    if mode == "resume" and cooldown_until is not None and datetime.now(timezone.utc) < cooldown_until:
        print(f"cooldown_until={cooldown_until.isoformat()}")
        return EXIT_COOLDOWN

    inventory = current_matrix_inventory()
    atomic_write_text(
        STAGING_META_PATH,
        json.dumps(
            manifest_meta(
                rows,
                inventory,
                mode,
                cooldown_until=cooldown_until.isoformat() if cooldown_until else None,
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )

    if mode == "preflight":
        print(f"rows={len(rows)}")
        print(f"manifest={STAGING_MANIFEST_PATH}")
        print(f"meta={STAGING_META_PATH}")
        print(f"matrix_live_files={inventory['live_words']['file_count']}")
        return 0

    targets = rows_for_pilot(rows) if mode == "pilot" else rows
    if mode == "pilot":
        selected_words = {row.word for row in targets}
        missing_words = [word for word in PILOT_WORDS if word not in selected_words]
        if missing_words:
            print(f"pilot_missing={missing_words}")
    if mode == "resume":
        by_row = {int(row["row"]): row for row in manifest_rows}
        reset_rows = {
            row.row
            for row in rows
            if by_row.get(row.row, {}).get("status") in {"unresolved", "technical_error", "invalid", "pending"}
            or not existing_file_is_valid(
                STAGING_WORDS_DIR / filename_for_row(row),
                by_row.get(row.row, {}).get("sha256") if by_row.get(row.row) else None,
                by_row.get(row.row, {}).get("size") if by_row.get(row.row) else None,
            )
        }
        reset_resume_rows_to_pending(manifest_rows, reset_rows)
        write_manifest(STAGING_MANIFEST_PATH, manifest_rows)
        targets = [
            row
            for row in targets
            if row.row in reset_rows
        ]

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        ok_count = 0
        fail_count = 0
        current_rows = load_existing_manifest_rows(STAGING_MANIFEST_PATH) or build_initial_manifest(rows)
        throttle = RequestThrottle()
        for index, row in enumerate(targets, 1):
            print(f"[{index}/{len(targets)}] row={row.row:3d} word={row.word!r}")
            try:
                resolution = reuse_existing_duden_audio(row)
                page = None
                if resolution is None:
                    resolution, page = await resolve_row(session, row, overrides, throttle=throttle)
                out_path = STAGING_WORDS_DIR / resolution.output_filename
                if resolution.status == "ok" and resolution.duden_audio_url:
                    if existing_file_is_valid(out_path, resolution.sha256, resolution.size):
                        pass
                    else:
                        try:
                            size, sha256, content_type, etag = await download_audio(
                                session,
                                resolution.duden_audio_url,
                                out_path,
                                throttle=throttle,
                            )
                            resolution = Resolution(
                                **{
                                    **asdict(resolution),
                                    "size": size,
                                    "sha256": sha256,
                                    "content_type": content_type,
                                    "etag": etag,
                                }
                            )
                        except TechnicalError as exc:
                            resolution = Resolution(
                                **{
                                    **asdict(resolution),
                                    "status": "technical_error",
                                    "reason": str(exc),
                                    "match_method": "download-technical-error",
                                }
                            )
                        except Exception as exc:
                            resolution = Resolution(
                                **{
                                    **asdict(resolution),
                                    "status": "invalid",
                                    "reason": f"download failed: {exc}",
                                }
                            )
                    if resolution.status == "ok" and resolution.sha256 is None and out_path.exists():
                        resolution = Resolution(
                            **{
                                **asdict(resolution),
                                "size": out_path.stat().st_size,
                                "sha256": hash_file(out_path),
                                "content_type": "audio/mpeg",
                            }
                        )
                elif out_path.exists():
                    out_path.unlink()
                update_existing_manifest_row(current_rows, resolution)
                write_manifest(STAGING_MANIFEST_PATH, current_rows)
                if resolution.status == "technical_error":
                    cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SECONDS)
                    remaining_rows = {item.row for item in targets[index:]}
                    reset_resume_rows_to_pending(current_rows, remaining_rows)
                    write_manifest(STAGING_MANIFEST_PATH, current_rows)
                    atomic_write_text(
                        STAGING_META_PATH,
                        json.dumps(
                            manifest_meta(
                                rows,
                                current_matrix_inventory(),
                                mode,
                                cooldown_until=cooldown_until.isoformat(),
                            ),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    print(f"cooldown_until={cooldown_until.isoformat()}")
                    print(f"technical_error_row={row.row}")
                    return EXIT_TECHNICAL_ERROR
                if resolution.status == "ok":
                    ok_count += 1
                else:
                    fail_count += 1 if resolution.status in {"invalid", "unresolved", "ambiguous"} else 0
            except Exception as exc:
                fail_count += 1
                out_path = STAGING_WORDS_DIR / filename_for_row(row)
                if out_path.exists():
                    out_path.unlink()
                update_existing_manifest_row(
                    current_rows,
                    Resolution(
                        row=row.row,
                        word=row.word,
                        pos=row.pos,
                        gender=row.gender,
                        output_filename=filename_for_row(row),
                        status="invalid",
                        reason=str(exc),
                        match_method="exception",
                        duden_page_url=None,
                        duden_audio_url=None,
                        file_id=None,
                    ),
                )
            if index % CHECKPOINT_EVERY == 0:
                print(f"[checkpoint] manifest written at {row.row}")

        write_manifest(STAGING_MANIFEST_PATH, current_rows)
        atomic_write_text(
            STAGING_META_PATH,
            json.dumps(
                manifest_meta(rows, current_matrix_inventory(), mode, cooldown_until=None),
                ensure_ascii=False,
                indent=2,
            ),
        )

    if mode in {"full", "resume"}:
        status_counts = manifest_status_counts(current_rows)
        blocked_statuses = {
            status: count
            for status, count in status_counts.items()
            if status in {"pending", "technical_error", "invalid"}
        }
        if blocked_statuses:
            print(f"blocked_statuses={blocked_statuses}")
            return EXIT_TECHNICAL_ERROR
        backup_dir = finalize_staging_to_live()
        if backup_dir:
            print(f"backup={backup_dir}")

    print(f"ok={ok_count} fail={fail_count} total={len(targets)}")
    return EXIT_SUCCESS if fail_count == 0 else EXIT_UNRESOLVED


async def process_audit_missing(rows: list[SourceRow], *, confirm_usage: bool) -> int:
    if not confirm_usage:
        raise RuntimeError("Duden audit requires --confirm-usage.")
    validate_live_duden_manifest(len(rows))
    targets = live_missing_rows(rows)
    print(f"audit_targets={len(targets)}")
    decisions: list[AuditDecision] = []
    overrides = load_overrides(OVERRIDES_PATH)
    existing_audit = load_audit_rows(MISSING_AUDIT_PATH)
    completed_rows: set[int] = set()
    for row in targets:
        item = existing_audit.get(row.row)
        if not item or item.get("status") == "technical_error":
            continue
        decision = audit_decision_from_item(row, item)
        decisions.append(decision)
        completed_rows.add(row.row)
        if decision.status in {"exact_audio_found", "ambiguous_page"} and decision.selected_audio_url:
            overrides[row.row] = audit_decision_to_override(decision)
    remaining_targets = [row for row in targets if row.row not in completed_rows]
    if completed_rows:
        print(f"audit_resume_skipped={len(completed_rows)}")
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        throttle = RequestThrottle()
        try:
            lexeme_index = await build_lexeme_index_for_rows(session, remaining_targets, throttle=throttle)
        except TechnicalError as exc:
            for row in remaining_targets:
                decisions.append(
                    AuditDecision(
                        row=row,
                        status="technical_error",
                        reason=str(exc),
                        candidates=(),
                    )
                )
            write_audit_rows(MISSING_AUDIT_PATH, decisions)
            return EXIT_TECHNICAL_ERROR
        for index, row in enumerate(remaining_targets, 1):
            print(f"[audit {index}/{len(remaining_targets)}] row={row.row:3d} word={row.word!r}")
            decision = await audit_missing_row(session, row, lexeme_index, throttle=throttle)
            decisions.append(decision)
            write_audit_rows(MISSING_AUDIT_PATH, decisions)
            if decision.status in {"exact_audio_found", "ambiguous_page"} and decision.selected_audio_url:
                overrides[row.row] = audit_decision_to_override(decision)
            if decision.status == "technical_error":
                write_overrides(OVERRIDES_PATH, overrides)
                return EXIT_TECHNICAL_ERROR
    write_overrides(OVERRIDES_PATH, overrides)
    counts: dict[str, int] = {}
    for decision in decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    print(f"audit_counts={counts}")
    print(f"audit_path={MISSING_AUDIT_PATH}")
    print(f"overrides={OVERRIDES_PATH}")
    return EXIT_SUCCESS if not counts.get("technical_error") else EXIT_TECHNICAL_ERROR


async def process_fill_missing(rows: list[SourceRow], *, confirm_usage: bool) -> int:
    if not confirm_usage:
        raise RuntimeError("Duden fill requires --confirm-usage.")
    validate_live_duden_manifest(len(rows))
    audit_rows = load_audit_rows(MISSING_AUDIT_PATH)
    if not audit_rows:
        raise RuntimeError(f"missing audit file: {MISSING_AUDIT_PATH}")
    overrides = load_overrides(OVERRIDES_PATH)

    reset_staging_from_live()
    manifest_rows = load_existing_manifest_rows(STAGING_MANIFEST_PATH)
    by_source_row = source_row_by_number(rows)
    targets = [
        by_source_row[int(item["row"])]
        for item in load_existing_manifest_rows(LIVE_MANIFEST_PATH)
        if item.get("status") != "ok" and int(item["row"]) in by_source_row
    ]
    original_ok_hashes = {
        item["output_filename"]: item.get("sha256")
        for item in manifest_rows
        if item.get("status") == "ok"
    }
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        throttle = RequestThrottle()
        for index, row in enumerate(targets, 1):
            item = audit_rows.get(row.row)
            override_resolution = override_to_resolution(row, overrides.get(row.row, {}))
            if override_resolution is not None:
                resolution = override_resolution
            elif item is None:
                resolution = Resolution(
                    row=row.row,
                    word=row.word,
                    pos=row.pos,
                    gender=row.gender,
                    output_filename=filename_for_row(row),
                    status="pending",
                    reason="missing audit decision",
                    match_method="audit-missing",
                    duden_page_url=None,
                    duden_audio_url=None,
                    file_id=None,
                )
            else:
                resolution = audit_item_to_resolution(row, item)
            print(f"[fill {index}/{len(targets)}] row={row.row:3d} word={row.word!r} status={resolution.status}")
            out_path = STAGING_WORDS_DIR / resolution.output_filename
            if resolution.status == "ok" and resolution.duden_audio_url:
                try:
                    size, sha256, content_type, etag = await download_audio(
                        session,
                        resolution.duden_audio_url,
                        out_path,
                        throttle=throttle,
                    )
                    resolution = Resolution(
                        **{
                            **asdict(resolution),
                            "size": size,
                            "sha256": sha256,
                            "content_type": content_type,
                            "etag": etag,
                        }
                    )
                except TechnicalError as exc:
                    resolution = Resolution(
                        **{
                            **asdict(resolution),
                            "status": "technical_error",
                            "reason": str(exc),
                            "match_method": "download-technical-error",
                        }
                    )
                except Exception as exc:
                    resolution = Resolution(
                        **{
                            **asdict(resolution),
                            "status": "invalid",
                            "reason": f"download failed: {exc}",
                        }
                    )
            elif out_path.exists():
                out_path.unlink()
            update_existing_manifest_row(manifest_rows, resolution)
            write_manifest(STAGING_MANIFEST_PATH, manifest_rows)
            if resolution.status == "technical_error":
                cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SECONDS)
                atomic_write_text(
                    STAGING_META_PATH,
                    json.dumps(
                        manifest_meta(rows, current_matrix_inventory(), "fill-missing", cooldown_until=cooldown_until.isoformat()),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                print(f"cooldown_until={cooldown_until.isoformat()}")
                print(f"technical_error_row={row.row}")
                return EXIT_TECHNICAL_ERROR

    for filename, sha256 in original_ok_hashes.items():
        if sha256 and hash_file(STAGING_WORDS_DIR / filename) != sha256:
            print(f"blocked_hash_changed={filename}")
            return EXIT_TECHNICAL_ERROR

    status_counts = manifest_status_counts(manifest_rows)
    blocked_statuses = {
        status: count
        for status, count in status_counts.items()
        if status in {"pending", "technical_error", "invalid"}
    }
    if blocked_statuses:
        print(f"blocked_statuses={blocked_statuses}")
        return EXIT_TECHNICAL_ERROR

    checkpoint_dir = checkpoint_current_duden_state()
    if checkpoint_dir:
        print(f"duden_checkpoint={checkpoint_dir}")
    atomic_write_text(
        STAGING_META_PATH,
        json.dumps(manifest_meta(rows, current_matrix_inventory(), "fill-missing", cooldown_until=None), ensure_ascii=False, indent=2),
    )
    finalize_staging_to_live()
    print(f"fill_counts={status_counts}")
    return EXIT_SUCCESS if not status_counts.get("technical_error") and not status_counts.get("invalid") else EXIT_TECHNICAL_ERROR


def parse_args(argv: list[str]) -> argparse.Namespace:
    level_match = re.search(r"Goethe_([A-Z]\d)", SOURCE_PATH.stem)
    level = level_match.group(1) if level_match else "A1"
    parser = argparse.ArgumentParser(description=f"Download Duden audio for Goethe {level}.")
    parser.add_argument("mode", choices=["preflight", "pilot", "full", "resume", "audit-missing", "fill-missing", "repair-manifest"])
    parser.add_argument(
        "--confirm-usage",
        action="store_true",
        help="Confirm you have checked Duden licensing and robots rules before downloading.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report a manifest repair without writing it.")
    parser.add_argument("--confirmation", help="Required confirmation token for manifest repair.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    rows = parse_markdown_wordlist(SOURCE_PATH)
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"expected {EXPECTED_ROWS} source rows, got {len(rows)}")
    if len({filename_for_row(row) for row in rows}) != len(rows):
        raise RuntimeError("duplicate output filename detected")
    if args.mode == "repair-manifest":
        print(json.dumps(repair_live_duden_manifest(rows, dry_run=args.dry_run, confirmation=args.confirmation), ensure_ascii=False, indent=2))
        return 0
    if args.mode == "preflight":
        inventory = current_matrix_inventory()
        print(f"source_rows={len(rows)}")
        print(f"source_sha256={hash_file(SOURCE_PATH)}")
        print(f"live_words_files={inventory['live_words']['file_count']}")
        print(f"live_manifest_hash={inventory['manifest_hash']}")
        print(f"live_meta_hash={inventory['meta_hash']}")
        atomic_write_text(
            STAGING_META_PATH,
            json.dumps(manifest_meta(rows, inventory, "preflight"), ensure_ascii=False, indent=2),
        )
        write_manifest(STAGING_MANIFEST_PATH, build_initial_manifest(rows))
        return 0
    if args.mode == "audit-missing":
        return asyncio.run(process_audit_missing(rows, confirm_usage=args.confirm_usage))
    if args.mode == "fill-missing":
        return asyncio.run(process_fill_missing(rows, confirm_usage=args.confirm_usage))
    return asyncio.run(process_rows(rows, mode=args.mode, confirm_usage=args.confirm_usage))


if __name__ == "__main__":
    raise SystemExit(main())
