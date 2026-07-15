"""Append deterministic grammar enrichment to the B1 Wortgruppen inventory.

The seven PDF-transcription columns are never changed.  Grammar is derived
only where the printed entry gives enough structural evidence; the Duden URL
is a review link, not evidence used to silently override the Goethe source.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import urllib.parse
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources" / "goethe" / "Goethe_B1_Wortgruppen.md"
STATE = ROOT / "tools" / ".b1_source_audit"
REPORT = STATE / "wortgruppen_enrichment_report.json"
EXTRA_HEADERS = (
    "Canonical Lemma", "POS", "Article", "Gender", "Noun Forms",
    "Accepted Variants", "Grammar Note", "Dictionary Sources",
)
ARTICLES = {"der": "m.", "die": "f.", "das": "n."}
ADJECTIVE_SECTIONS = {"1.7 Farben"}


class EnrichmentError(RuntimeError):
    pass


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def cells(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


def strip_region(value: str) -> str:
    return clean(re.sub(r"\s*\((?:D|A|CH)(?:\s*,\s*(?:D|A|CH))*\)\s*", " ", value))


def noun_data(head: str) -> tuple[str, str, str, list[str]] | None:
    first = head.split(",", 1)[0].strip()
    found = re.findall(r"\b(der|die|das)\b", head)
    if not found:
        return None
    article_values = list(dict.fromkeys(found))
    article = "/".join(article_values)
    genders = list(dict.fromkeys(ARTICLES[item] for item in article_values))
    gender = "pl." if "(Pl.)" in first else "/".join(genders)
    value = re.sub(r"^(?:(?:der|die|das)(?:\s*\([^)]*\))?\s*(?:/\s*)?)+", "", first).strip()
    value = strip_region(value)
    # Gender annotations sometimes occur before the actual abbreviation.
    value = re.sub(r"^(?:\([^)]*\)\s*/?\s*)+", "", value).strip()
    variants = []
    # The first comma-free head may list coordinated abbreviations or lexical
    # variants. Keep them as accepted spellings while choosing the first.
    for part in re.split(r"\s*,\s*|/", value):
        part = clean(re.sub(r"^(?:der|die|das)\s+", "", strip_region(part)))
        if part and not re.fullmatch(r"(?:D|A|CH)", part):
            variants.append(part)
    canonical = variants[0] if variants else value
    canonical = clean(re.sub(r"\s*\(Pl\.\)\s*$", "", canonical)).strip("() ")
    variants.extend(
        clean(strip_region(match))
        for match in re.findall(r"/(?:der|die|das)\s+([^,]+)", head)
    )
    if not canonical:
        raise EnrichmentError(f"cannot derive noun lemma from {head!r}")
    return canonical, article, gender, [
        value for value in dict.fromkeys(variants[1:]) if value and value != canonical
    ]


def base_lemma(entry: str) -> str:
    return strip_region(entry.split(",", 1)[0]).strip()


def classify(entry: str, section: str) -> tuple[str, str, str, str, str, str]:
    noun = noun_data(entry)
    if noun:
        canonical, article, gender, variants = noun
        noun_forms = clean(entry.split(",", 1)[1]) if "," in entry else ""
        note = ""
        if "/" in article:
            note = f"Article variants printed by Goethe: {article}."
        return canonical, "n.", article, gender, noun_forms, "<br>".join(variants), note

    lemma = base_lemma(entry)
    if not lemma:
        raise EnrichmentError(f"cannot derive lemma from {entry!r}")
    variants = [clean(item) for item in lemma.split("/") if clean(item)]
    canonical = variants[0]
    accepted = "<br>".join(variants[1:])
    remainder = clean(entry.split(",", 1)[1]) if "," in entry else ""
    verb_shape = bool(remainder) and bool(re.match(r"^(?:sich\s+)?[a-zäöüß-]+(?:en|eln|ern|n)$", canonical))
    if verb_shape:
        return canonical.removeprefix("sich "), "v.", "", "", "", accepted, f"Conjugation: {remainder}"
    if section in ADJECTIVE_SECTIONS:
        return canonical, "adj.", "", "", "", accepted, ""
    return canonical, "phrase", "", "", "", accepted, ""


def enrich_row(row: list[str], section: str) -> list[str]:
    if len(row) not in {7, 15}:
        raise EnrichmentError(f"expected 7 or 15 cells, got {len(row)}: {row!r}")
    source = row[:7]
    entry, detail, match = source[1], source[2], source[5]
    if match:
        return source + [""] * 8
    canonical, pos, article, gender, noun_forms, variants, grammar = classify(entry, section)
    if detail:
        grammar = clean("; ".join(item for item in (grammar, f"Expansion/context: {detail}") if item))
    url = "https://www.duden.de/suchen/dudenonline/" + urllib.parse.quote(canonical, safe="")
    return source + [canonical, pos, article, gender, noun_forms, variants, grammar, url]


def build() -> tuple[list[str], dict[str, object]]:
    output = []
    section = ""
    counts: Counter[str] = Counter()
    enriched = 0
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        if line.startswith("| ID |"):
            original = cells(line)
            output.append("| " + " | ".join(original[:7] + list(EXTRA_HEADERS)) + " |")
            continue
        if line.startswith("|---"):
            count = len(cells(line))
            output.append("|" + "|".join(["---"] * (15 if count in {7, 15} else count)) + "|")
            continue
        if not line.startswith("| B1-WG-"):
            output.append(line)
            continue
        row = enrich_row(cells(line), section)
        if row[7]:
            enriched += 1
            counts[row[8]] += 1
        output.append("| " + " | ".join(row) + " |")
    if sum(counts.values()) != enriched:
        raise EnrichmentError("enrichment accounting mismatch")
    report = {"rows": 355, "enriched": enriched, "matched_main": 355 - enriched, "pos": dict(sorted(counts.items()))}
    return output, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    lines, report = build()
    STATE.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.apply:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=SOURCE.parent, delete=False) as handle:
            handle.write("\n".join(lines) + "\n")
            temp = Path(handle.name)
        temp.replace(SOURCE)
    print(json.dumps({**report, "applied": args.apply}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
