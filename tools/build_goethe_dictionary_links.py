"""Build exact-entry dictionary links for every live Goethe Werkstatt note.

Duden direct-entry evidence and successful audio manifests take priority.
Unmatched headwords use a direct Wiktionary article only after the MediaWiki API
confirms that the article exists. Search pages are never emitted.

    python tools/build_goethe_dictionary_links.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import goethe_template_upgrade as guard


ROOT = guard.ROOT
OUT = ROOT / "review" / "goethe_dictionary_links.json"
AUDIT = ROOT / "review" / "goethe_english_audit_v5.jsonl"
API = "https://de.wiktionary.org/w/api.php"
USER_AGENT = "GoetheWerkstatt/1.0 (dictionary-link audit; local repository)"
LEVELS = ("a1", "a2", "b1")
MANUAL_DUDEN_BY_SOURCE = {
    "A1-84886454855": "https://www.duden.de/rechtschreibung/kaufen",
    "A1-84886455088": "https://www.duden.de/rechtschreibung/weg_fort_abwesend_entfernt",
    "A1-84886455089": "https://www.duden.de/rechtschreibung/zu_Adverb",
    "A1-84886455200": "https://www.duden.de/rechtschreibung/fuer_welch",
    "A1-84887177201": "https://www.duden.de/rechtschreibung/Uhr",
    "A1-84887177208": "https://www.duden.de/rechtschreibung/Uhr",
    "A1-84887177209": "https://www.duden.de/rechtschreibung/Uhr",
    "A1-84887177211": "https://www.duden.de/rechtschreibung/halb_zur_Haelfte_teilweise",
    "A1-84887177214": "https://www.duden.de/rechtschreibung/neunzehnhundert",
    "A1-84887177215": "https://www.duden.de/rechtschreibung/Zahlwort",
    "A1-84887177225": "https://www.duden.de/rechtschreibung/Wochenende",
    "A2-0250": "https://www.duden.de/rechtschreibung/einverstanden",
    "A2-0293": "https://www.duden.de/rechtschreibung/erlaubt",
    "A2-0315": "https://www.duden.de/rechtschreibung/fertig",
    "A2-0332": "https://www.duden.de/rechtschreibung/fit",
    "A2-0420": "https://www.duden.de/rechtschreibung/gueltig",
    "A2-1045": "https://www.duden.de/rechtschreibung/unterwegs",
    "A2-1050": "https://www.duden.de/rechtschreibung/verabreden",
    "A2-MAIN-0088": "https://www.duden.de/rechtschreibung/gern",
    "A2-MAIN-0089": "https://www.duden.de/rechtschreibung/am_besten",
    "A2-MAIN-0374": "https://www.duden.de/rechtschreibung/Geburt",
    "B1-MAIN-1503": "https://www.duden.de/rechtschreibung/Leiter_Anfuehrer",
    "B1-MAIN-1542": "https://www.duden.de/rechtschreibung/los_Aufforderung_locker",
    "B1-MAIN-2744-IMAGINE": "https://www.duden.de/rechtschreibung/vorstellen",
    "B1-MAIN-2777": "https://www.duden.de/rechtschreibung/fuer_welch",
    "B1-WG-0123": "https://www.duden.de/rechtschreibung/AHS",
    "B1-WG-0124": "https://www.duden.de/rechtschreibung/BHS",
    "B1-WG-0207": "https://www.duden.de/rechtschreibung/Confoederatio_Helvetica",
    "B1-WG-0212": "https://www.duden.de/rechtschreibung/Staenderat",
    "B1-WG-0296": "https://www.duden.de/rechtschreibung/Zahlwort",
    "B1-WG-0335": "https://www.duden.de/rechtschreibung/Viertel_Stadtteil",
    "A1-84887177245": "https://www.duden.de/rechtschreibung/Fruehling",
    "B1-MAIN-2038": "https://www.duden.de/rechtschreibung/runterwerfen",
    "B1-MAIN-2913": "https://www.duden.de/rechtschreibung/Znueni",
    "B1-WG-0129": "https://www.duden.de/rechtschreibung/Primarschule",
    "B1-WG-0130": "https://www.duden.de/rechtschreibung/Sekundarstufe",
    "B1-WG-0131": "https://www.duden.de/rechtschreibung/Sekundarstufe",
}


class LinkBuildError(RuntimeError):
    """Raised when the direct-link map cannot be built safely."""


def direct_duden_url(value: str) -> bool:
    parsed = urlsplit(str(value or ""))
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "www.duden.de"
        and parsed.path.startswith("/rechtschreibung/")
        and bool(parsed.path.removeprefix("/rechtschreibung/").strip("/"))
        and not parsed.query
        and not parsed.fragment
    )


def direct_wiktionary_url(value: str) -> bool:
    parsed = urlsplit(str(value or ""))
    title = parsed.path.removeprefix("/wiki/")
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "de.wiktionary.org"
        and parsed.path.startswith("/wiki/")
        and bool(title.strip("/"))
        and not title.casefold().startswith(("spezial:", "wiktionary:"))
        and not parsed.query
        and not parsed.fragment
    )


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFC", str(value or "")).strip()
    value = re.sub(r"^(?:der|die|das)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value)
    return value.casefold()


def _pos_norm(value: str) -> str:
    return re.sub(r"[^a-z]+", "", str(value or "").casefold())


def _lexical_aliases(value: str) -> list[str]:
    raw = unicodedata.normalize("NFC", str(value or "")).strip()
    raw = re.sub(r"^(?:der|die|das)\s+", "", raw, flags=re.I)
    values = [raw]
    for part in re.split(r"\s*(?:/|;|\|)\s*", raw):
        if part and part not in values:
            values.append(part)
    expanded: list[str] = []
    for item in values:
        candidate = item.strip()
        candidate = re.sub(r"^\(?\s*sich(?:\s+etw\.)?\s*\)?\s+", "", candidate, flags=re.I)
        candidate = re.sub(r"^jemanden\s+", "", candidate, flags=re.I)
        candidate = re.sub(r"\s+usw\.?$", "", candidate, flags=re.I)
        if candidate.startswith("(") and ")" in candidate:
            optional, rest = candidate[1:].split(")", 1)
            candidate = (optional + rest).strip()
        candidate = re.sub(r"\s+\([^)]*\)$", "", candidate).strip()
        candidate = re.sub(r"^\d+\s+", "", candidate)
        if candidate and candidate not in expanded:
            expanded.append(candidate)
        if candidate.endswith("-") and len(candidate) > 1:
            expanded.append(candidate[:-1])
        if re.fullmatch(r"[a-zA-ZÀ-ɏ]+-", candidate):
            expanded.append(candidate[:-1])
            expanded.append(candidate[:-1] + "er")
        if "(" in item and ")" in item:
            expanded.append(re.sub(r"[()]", "", item))
        compact = candidate.replace("-", "")
        if compact and compact != candidate and len(compact) > 3:
            expanded.append(compact)
    result: list[str] = []
    for item in expanded:
        item = re.sub(r"\s+", " ", item).strip(" \t,;|")
        if item and item not in result:
            result.append(item)
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise LinkBuildError(f"cannot read JSONL dictionary evidence: {path}") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LinkBuildError(f"cannot read dictionary evidence: {path}") from exc
    if not isinstance(value, dict):
        raise LinkBuildError(f"dictionary evidence is not an object: {path}")
    return value


def _live_identity_index(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for note_id, item in inventory["notes"].items():
        fields = item["fields"]
        identities = {str(fields.get("SourceID", "")).strip()}
        identities.update(
            ref.strip() for ref in str(fields.get("SourceRefs", "")).split("|") if ref.strip()
        )
        for identity in identities - {""}:
            prior = result.get(identity)
            if prior is not None and str(prior["note_id"]) != str(note_id):
                raise LinkBuildError(f"ambiguous live SourceID or SourceRef: {identity}")
            result[identity] = {**item, "note_id": str(note_id)}
    return result


def _manifest_evidence(
    inventory: dict[str, Any],
    add_candidate: Any,
) -> None:
    by_word_pos: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_word: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note_id, item in inventory["notes"].items():
        fields = item["fields"]
        word = _norm(str(fields.get("Lemma", "")))
        if not word:
            continue
        record = {
            "note_id": str(note_id),
            "source_id": str(fields.get("SourceID", "")),
            "pos": _pos_norm(str(fields.get("POS", ""))),
        }
        for alias in _lexical_aliases(str(fields.get("Lemma", ""))):
            alias_key = _norm(alias)
            if not alias_key:
                continue
            if alias_key == word:
                by_word_pos[(alias_key, record["pos"])].append(record)
                by_word[alias_key].append(record)
            else:
                by_word_pos[(alias_key, record["pos"])].append({**record, "alias": True})
                by_word[alias_key].append({**record, "alias": True})

    for level in LEVELS:
        manifest_path = ROOT / "audio" / level / "words_manifest.jsonl"
        if not manifest_path.exists():
            continue
        override_path = ROOT / "review" / f"duden_{level}_overrides.json"
        if level == "a1":
            override_path = ROOT / "review" / "duden_overrides.json"
        overrides = _load_json(override_path).get("rows", {}) if override_path.exists() else {}
        for row in _read_jsonl(manifest_path):
            word = _norm(str(row.get("word", "")))
            if not word:
                continue
            override = overrides.get(str(row.get("row", "")), {})
            override_url = str(override.get("duden_page_url", ""))
            manifest_url = str(row.get("duden_page_url", ""))
            if direct_duden_url(override_url) and override.get("status") == "ok":
                url, score = override_url, 88
            elif direct_duden_url(manifest_url) and row.get("status") in {"ok", "reused"}:
                url, score = manifest_url, 78
            else:
                continue

            pos = _pos_norm(str(row.get("pos", "")))
            matches = by_word_pos.get((word, pos), []) if pos else []
            if not matches:
                matches = by_word.get(word, [])
                score -= 8
            for match in matches:
                alias_penalty = 14 if match.get("alias") else 0
                add_candidate(match["source_id"], url, score - alias_penalty, f"{level}_duden_manifest")


def _wordgroup_sources(identity_index: dict[str, dict[str, Any]], add_candidate: Any) -> None:
    for level in ("A1", "A2", "B1"):
        path = ROOT / "sources" / "goethe" / f"Goethe_{level}_Wortgruppen.md"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            cells = [part.strip() for part in line.strip().strip("|").split("|")]
            if not cells or not re.fullmatch(r"[AB]\d-WG-\d{4}", cells[0]):
                continue
            source_id = cells[0]
            for raw in re.findall(r"https://www\.duden\.de/[^\s<>|]+", line):
                url = raw.rstrip(".,);]")
                if direct_duden_url(url) and source_id in identity_index:
                    item = identity_index[source_id]
                    add_candidate(str(item["source_id"]), url, 94, "goethe_wordgroup_source")


def _wiki_candidates(lemma: str) -> list[str]:
    value = unicodedata.normalize("NFC", str(lemma or "")).strip()
    values = _lexical_aliases(value)
    values.extend(["okay", "OK"] if value.casefold() == "o.k." else [])
    values.extend(["Eurocheque-Karte", "Kreditkarte"] if "Kredit" in value else [])
    values.extend(["Kilometer pro Stunde"] if "km/h" in value.casefold() else [])
    values.extend(["Schweizer Franken", "Franken"] if "sfr" in value.casefold() else [])
    values.extend(["Rappen"] if re.search(r"\bRp\.", value) else [])
    values.extend(["Chatroom", "Chat"] if "chat(room)" in value.casefold() else [])
    result: list[str] = []
    for candidate in values:
        candidate = re.sub(r"\s+", " ", candidate).strip(" \t,;|")
        if not candidate or candidate in result or len(candidate) > 100:
            continue
        # Wiktionary has no exact lexical article for multi-answer scaffolds;
        # query each intact spelling instead of linking to a search page.
        result.append(candidate)
    return result


def _wiki_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).replace("_", " ")).casefold()


def _query_wiktionary(titles: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    unique = list(dict.fromkeys(title for title in titles if title.strip()))
    for offset in range(0, len(unique), 50):
        batch = unique[offset:offset + 50]
        query = urllib.parse.urlencode({
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
            "prop": "info",
            "titles": "|".join(batch),
        })
        request = urllib.request.Request(
            f"{API}?{query}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        data: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
        if data is None:
            raise LinkBuildError(f"Wiktionary API request failed: {last_error}")
        query_data = data.get("query", {})
        transformations: dict[str, str] = {}
        for kind in ("normalized", "redirects"):
            for item in query_data.get(kind, []):
                transformations[_wiki_key(str(item.get("from", "")))] = _wiki_key(
                    str(item.get("to", ""))
                )
        pages: dict[str, str] = {}
        for page in query_data.get("pages", []):
            if page.get("missing") is not None:
                continue
            title = str(page.get("title", ""))
            if title:
                pages[_wiki_key(title)] = title
        for title in batch:
            key = _wiki_key(title)
            seen: set[str] = set()
            while key in transformations and key not in seen:
                seen.add(key)
                key = transformations[key]
            if key in pages:
                resolved[title] = pages[key]
        if offset + 50 < len(unique):
            time.sleep(0.15)
    return resolved


def _wiki_url(title: str) -> str:
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe="_()'-,.~")
    return f"https://de.wiktionary.org/wiki/{encoded}"


def build_map() -> dict[str, Any]:
    guard.require_version()
    inventory = guard.live_inventory()
    identity_index = _live_identity_index(inventory)
    live_by_id = {
        str(item["fields"].get("SourceID", "")): (str(note_id), item)
        for note_id, item in inventory["notes"].items()
    }
    candidates: dict[str, list[tuple[int, str, str]]] = defaultdict(list)

    def add_candidate(source_id: str, url: str, score: int, source: str) -> None:
        if not source_id or not direct_duden_url(url):
            return
        candidates[source_id].append((score, url, source))

    for row in _read_jsonl(AUDIT):
        urls = [
            str(evidence.get("url", ""))
            for evidence in row.get("evidence", [])
            if evidence.get("provider") == "Duden"
            and direct_duden_url(str(evidence.get("url", "")))
        ]
        if not urls:
            continue
        row_lemma = _norm(str(row.get("lemma", "")))
        identities = [str(row.get("source_id", "")), *map(str, row.get("source_refs", []))]
        for identity in dict.fromkeys(identities):
            live = identity_index.get(identity)
            if live is None or _norm(str(live["fields"].get("Lemma", ""))) != row_lemma:
                continue
            source_id = str(live["fields"].get("SourceID", ""))
            exact_source = identity == source_id and str(row.get("source_id", "")) == source_id
            for url in urls:
                add_candidate(source_id, url, 110 if exact_source else 98, "v5_duden_evidence")

    _manifest_evidence(inventory, add_candidate)
    _wordgroup_sources(identity_index, add_candidate)

    # The user supplied this exact Duden entry; keep it as the canonical card link.
    dose = live_by_id.get("B1-MAIN-0554")
    if dose is not None:
        add_candidate("B1-MAIN-0554", "https://www.duden.de/rechtschreibung/Dose", 200, "user_verified_dose_entry")
    for source_id, url in MANUAL_DUDEN_BY_SOURCE.items():
        if source_id in live_by_id:
            add_candidate(source_id, url, 200, "verified_direct_duden_entry")

    selected: dict[str, tuple[str, str]] = {}
    ambiguous: list[str] = []
    for source_id, pair in live_by_id.items():
        note_id, item = pair
        values = candidates.get(source_id, [])
        if not values:
            continue
        top_score = max(value[0] for value in values)
        top_urls = sorted({value[1] for value in values if value[0] == top_score})
        if len(top_urls) != 1:
            ambiguous.append(source_id)
            continue
        selected[source_id] = ("Duden", top_urls[0])

    unresolved_ids = [source_id for source_id in live_by_id if source_id not in selected]
    wiki_candidates_by_source: dict[str, list[str]] = {}
    all_titles: list[str] = []
    for source_id in unresolved_ids:
        _, item = live_by_id[source_id]
        titles = _wiki_candidates(str(item["fields"].get("Lemma", "")))
        wiki_candidates_by_source[source_id] = titles
        all_titles.extend(titles)
    wiki_pages = _query_wiktionary(all_titles) if all_titles else {}
    unresolved: list[dict[str, str]] = []
    for source_id in unresolved_ids:
        _, item = live_by_id[source_id]
        url = ""
        for title in wiki_candidates_by_source[source_id]:
            canonical = wiki_pages.get(title)
            if canonical:
                candidate_url = _wiki_url(canonical)
                if direct_wiktionary_url(candidate_url):
                    url = candidate_url
                    break
        if url:
            selected[source_id] = ("Wiktionary", url)
        else:
            fields = item["fields"]
            unresolved.append({
                "source_id": source_id,
                "lemma": str(fields.get("Lemma", "")),
                "cefr": str(fields.get("CEFR", "")),
            })

    unique_urls = sorted(set(selected.values()), key=lambda value: (value[0], value[1]))
    url_indexes = {value: index for index, value in enumerate(unique_urls)}
    source_to_url = {
        source_id: url_indexes[value]
        for source_id, value in sorted(selected.items())
    }
    counts = {
        "notes": len(live_by_id),
        "duden": sum(provider == "Duden" for provider, _ in selected.values()),
        "wiktionary": sum(provider == "Wiktionary" for provider, _ in selected.values()),
        "unresolved": len(unresolved),
        "ambiguous_duden_candidates": len(ambiguous),
    }
    return {
        "schema_version": 1,
        "description": "Exact Duden entries, with verified direct Wiktionary article fallbacks.",
        "urls": [{"provider": provider, "url": url} for provider, url in unique_urls],
        "source_to_url": source_to_url,
        "unresolved": sorted(unresolved, key=lambda item: item["source_id"]),
        "counts": counts,
    }


def validate_map(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1:
        raise LinkBuildError("unsupported dictionary-link map schema")
    urls = data.get("urls")
    lookup = data.get("source_to_url")
    if not isinstance(urls, list) or not isinstance(lookup, dict):
        raise LinkBuildError("dictionary-link map is missing urls or source lookup")
    for entry in urls:
        provider, url = str(entry.get("provider", "")), str(entry.get("url", ""))
        if provider == "Duden" and not direct_duden_url(url):
            raise LinkBuildError(f"non-direct Duden link in map: {url}")
        if provider == "Wiktionary" and not direct_wiktionary_url(url):
            raise LinkBuildError(f"non-direct Wiktionary link in map: {url}")
        if provider not in {"Duden", "Wiktionary"}:
            raise LinkBuildError(f"unsupported dictionary provider: {provider}")
    for source_id, index in lookup.items():
        if not isinstance(source_id, str) or not isinstance(index, int) or not 0 <= index < len(urls):
            raise LinkBuildError(f"invalid dictionary-link reference for {source_id}")
    for item in data.get("unresolved", []):
        if item.get("source_id") in lookup:
            raise LinkBuildError(f"unresolved note also has a link: {item.get('source_id')}")


def main() -> int:
    try:
        data = build_map()
        validate_map(data)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except LinkBuildError as exc:
        print(f"ERROR: {exc}")
        return 1
    counts = data["counts"]
    print(json.dumps({"status": "PASS", "output": str(OUT), **counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
