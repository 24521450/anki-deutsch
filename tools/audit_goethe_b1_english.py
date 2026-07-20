"""Generate legacy FreeDict triage hints for the manual Goethe B1 English audit.

FreeDict similarity is deliberately not review evidence.  This helper can rank
candidate glosses, but its output must remain ``hint_only`` and cannot make a
v4 row reviewed.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools" / ".goethe_completion" / "manifest.json"
DEFAULT_TEI = ROOT / "tools" / ".b1_source_audit" / "deu-eng" / "deu-eng.tei"
OUTPUT = ROOT / "review" / "goethe_b1_english_audit.jsonl"
SUMMARY = ROOT / "tools" / ".b1_source_audit" / "english_audit_summary.json"
NS = "{http://www.tei-c.org/ns/1.0}"


class AuditError(RuntimeError):
    pass


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def key(value: str) -> str:
    value = unicodedata.normalize("NFC", clean(value)).casefold()
    value = re.sub(r"^(?:der|die|das|sich)\s+", "", value)
    return value


def comparison(value: str) -> str:
    value = key(value)
    value = re.sub(r"^(?:to|a|an|the)\s+", "", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def dictionary_index(path: Path, wanted: set[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for _, entry in ET.iterparse(path, events=("end",)):
        if entry.tag != NS + "entry":
            continue
        orths = [clean("".join(node.itertext())) for node in entry.findall(f"./{NS}form/{NS}orth")]
        matched = {key(value) for value in orths} & wanted
        if matched:
            translations = []
            for sense in entry.findall(f"./{NS}sense"):
                for citation in sense.findall(f"./{NS}cit[@type='trans']"):
                    quote = citation.find(f"./{NS}quote")
                    if quote is not None:
                        value = clean("".join(quote.itertext()))
                        if value and len(value) <= 120 and value not in translations:
                            translations.append(value)
            for lemma in matched:
                for value in translations:
                    if value not in found[lemma]:
                        found[lemma].append(value)
        entry.clear()
    return found


def similarity(left: str, right: str) -> float:
    left, right = comparison(left), comparison(right)
    if not left or not right:
        return 0.0
    if left == right or left in right or right in left:
        return 1.0
    left_words, right_words = set(left.split()), set(right.split())
    token_score = len(left_words & right_words) / max(1, min(len(left_words), len(right_words)))
    sequence = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
    return max(token_score, sequence)


def b1_records(manifest: dict) -> list[dict]:
    return [
        record for record in manifest["records"].values()
        if record["fields"].get("CEFR") == "B1"
    ]


def audit(tei: Path) -> tuple[list[dict], dict]:
    """Return triage classifications, never evidence-backed review decisions."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    records = b1_records(manifest)
    new_levels = Counter(
        record["fields"].get("CEFR", "")
        for record in manifest["records"].values() if record["is_new"]
    )
    if set(new_levels) - {"B1"}:
        raise AuditError(f"unexpected new lower-level records: {dict(new_levels)}")
    wanted = {key(record["fields"]["Lemma"]) for record in records}
    index = dictionary_index(tei, wanted)
    entries = []
    statuses: Counter[str] = Counter()
    example_flags: Counter[str] = Counter()
    for record in sorted(records, key=lambda item: item["fields"]["SourceID"]):
        fields = record["fields"]
        meaning = clean(fields.get("MeaningEN", ""))
        candidates = index.get(key(fields["Lemma"]), [])
        ranked = sorted(
            ((round(similarity(meaning, candidate), 6), candidate) for candidate in candidates),
            key=lambda item: (-item[0], len(item[1]), item[1].casefold()),
        )
        best_score = ranked[0][0] if ranked else 0.0
        if not meaning:
            status = "missing_translation"
        elif best_score >= 0.62:
            status = "dictionary_confirmed"
        elif comparison(meaning) == comparison(fields["Lemma"]):
            status = "unchanged_german"
        elif candidates:
            status = "dictionary_available_unaligned"
        else:
            status = "no_dictionary_entry"
        statuses[status] += 1
        examples = []
        for example in record["examples"]:
            flags = []
            if not clean(example.get("en", "")):
                flags.append("missing")
            elif comparison(example["de"]) == comparison(example["en"]):
                flags.append("unchanged_german")
            if re.search(r"\b(?:ich|du|der|die|das|nicht|und|ist|sind|habe|haben)\b", example.get("en", ""), re.I):
                flags.append("german_token")
            for flag in flags:
                example_flags[flag] += 1
            examples.append({"de": example["de"], "en": example.get("en", ""), "flags": flags})
        entries.append({
            "source_id": fields["SourceID"], "source_refs": record["source_refs"],
            "lemma": fields["Lemma"], "pos": fields.get("POS", ""),
            "meaning_en": meaning, "status": status,
            "best_dictionary_score": best_score,
            "dictionary_candidates": [value for _, value in ranked[:8]],
            "review_status": "hint_only",
            "is_review_evidence": False,
            "source_url": "https://freedict.org/downloads/",
            "examples": examples,
        })
    summary = {
        "schema_version": 1, "records": len(entries), "statuses": dict(sorted(statuses.items())),
        "dictionary_coverage": sum(bool(entry["dictionary_candidates"]) for entry in entries),
        "example_flags": dict(sorted(example_flags.items())),
        "source": "FreeDict deu-eng 1.9-fd1",
        "source_role": "hint_only_not_review_evidence",
    }
    return entries, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tei", type=Path, default=DEFAULT_TEI)
    args = parser.parse_args()
    entries, summary = audit(args.tei)
    OUTPUT.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries), encoding="utf-8")
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
