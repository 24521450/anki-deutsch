"""Repair the B1 Markdown extraction with coordinate-verified PDF evidence.

The reference CSV is used only to recover entry/example boundaries.  Every
changed example must also be found as an ordered token span in the official
PDF, otherwise the command fails without writing the Markdown source.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdfplumber

import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
SOURCE = gw.SOURCE_B1
PDF = ROOT / "sources" / "goethe" / "Goethe-Zertifikat_B1_Wortliste.pdf"
STATE = ROOT / "tools" / ".b1_source_audit"
DEFAULT_REFERENCE = STATE / "wejn.csv"
REPORT = STATE / "source_cleanup_report.json"

POS_OVERRIDES = {
    "beschränken": ("v.", ""),
    "dabei": ("adv.", ""),
    "derselbe": ("det., pron.", ""),
    "drin": ("adv.", ""),
    "Halt": ("n.", "m."),
    "Mal": ("n.", "n."),
    "präsentieren": ("v.", ""),
    "weg/weg-": ("adv.", ""),
}


class CleanupError(RuntimeError):
    pass


def clean(value: str) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", value).strip())


def normalized_source(value: str) -> str:
    value = value.replace("\n", "; ").replace("→", ";")
    return clean(value).casefold()


def dehyphenate(value: str) -> str:
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    return clean(value)


def reference_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0][0] != "Goethe Zertifikat B1 Wortliste":
        raise CleanupError("unexpected B1 reference CSV")
    if any(len(row) != 2 for row in rows[1:]):
        raise CleanupError("reference CSV must have exactly two columns")
    return rows[1:]


def split_reference_examples(value: str) -> list[str]:
    result = []
    for line in value.splitlines():
        line = re.sub(r"^\s*\d+\.\s*", "", line).strip()
        if line:
            result.append(clean(line))
    return result or ([clean(value)] if clean(value) else [])


def exact_word_candidates(word: str, rows: list[list[str]]) -> list[tuple[int, list[str]]]:
    pattern = re.compile(r"(?<![\wÄÖÜäöüß-])" + re.escape(word) + r"(?![\wÄÖÜäöüß-])")
    return [(index, row) for index, row in enumerate(rows) if pattern.search(row[0])]


def primary_lemma_contains(word: str, head: str) -> bool:
    block = head.split("→", 1)[0]
    lines = block.splitlines()
    # The first line carries the lemma.  A later line is another lemma only
    # when it starts with a noun article (typically the feminine counterpart),
    # not when it merely contains a separated verb prefix in its paradigm.
    lemma_lines = lines[:1] + [
        line for line in lines[1:]
        if re.match(r"^(?:der|die|das)\s+", clean(line), re.IGNORECASE)
    ]
    for line in lemma_lines:
        lemma = clean(line.split(",", 1)[0])
        lemma = re.sub(r"^(?:(?:der|die|das)(?:/(?:der|die|das))*\s+)", "", lemma)
        lemma = re.sub(r"^sich\s+", "", lemma)
        lemma = re.sub(r"\s+\([A-Z](?:,\s*[A-Z])*\)$", "", lemma)
        if lemma == word or word in lemma.split("/"):
            return True
    return False


def choose_reference(source_row: dict[str, Any], rows: list[list[str]]) -> tuple[int, list[str], float]:
    candidates = exact_word_candidates(source_row["word"], rows) or list(enumerate(rows))
    raw = normalized_source(source_row["note"].removeprefix("source: "))
    expected_index = source_row["row"] * len(rows) / 2969

    def score(item: tuple[int, list[str]]) -> tuple[int, float, float]:
        index, row = item
        # A word mentioned only after an arrow is a cross-reference, not this
        # entry.  Prefer the candidate whose primary headword block contains
        # the source word before considering textual similarity.
        primary_match = primary_lemma_contains(source_row["word"], row[0])
        similarity = difflib.SequenceMatcher(
            None, raw, normalized_source(row[0]), autojunk=False,
        ).ratio()
        return int(primary_match), similarity, -abs(index - expected_index)

    index, row = max(candidates, key=score)
    similarity = score((index, row))[1]
    if similarity < 0.30:
        raise CleanupError(
            f"weak reference match for row {source_row['row']} {source_row['word']!r}: "
            f"{similarity:.3f} -> {row[0]!r}"
        )
    return index, row, similarity


def choose_references(source_row: dict[str, Any], rows: list[list[str]]) -> list[tuple[int, list[str], float]]:
    """Return every primary homograph represented by one consolidated row."""
    primary = []
    raw = normalized_source(source_row["note"].removeprefix("source: "))
    for index, row in exact_word_candidates(source_row["word"], rows):
        if not primary_lemma_contains(source_row["word"], row[0]):
            continue
        similarity = difflib.SequenceMatcher(
            None, raw, normalized_source(row[0]), autojunk=False,
        ).ratio()
        primary.append((index, row, similarity))
    if primary:
        return primary
    return [choose_reference(source_row, rows)]


TOKEN_RE = re.compile(r"[^\W_]+(?:-[^\W_]+)*-?", re.UNICODE)


def sentence_tokens(value: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(value)]


def pdf_lines() -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    pages: list[dict[str, Any]] = []
    first_token_pages: dict[str, list[int]] = defaultdict(list)
    with pdfplumber.open(PDF) as document:
        for page_number in range(16, 103):
            words = document.pages[page_number - 1].extract_words(
                use_text_flow=False, keep_blank_chars=False,
            )
            # Each alphabetical page has two independent example columns.  Do
            # not interleave words at the same y-coordinate across columns.
            columns = (
                [item for item in words if 130 <= float(item["x0"]) < 295],
                [item for item in words if float(item["x0"]) >= 410],
            )
            for column, items in enumerate(columns):
                # Cross-reference arrows use a slightly different font and
                # sit about 1.4 pt above the sentence baseline.  Cluster such
                # near-equal y positions into one visual line before sorting
                # left-to-right.
                visual_lines: list[list[dict[str, Any]]] = []
                for item in sorted(items, key=lambda value: (float(value["top"]), float(value["x0"]))):
                    if not visual_lines or abs(float(item["top"]) - float(visual_lines[-1][0]["top"])) > 3.0:
                        visual_lines.append([item])
                    else:
                        visual_lines[-1].append(item)
                ordered = [
                    item
                    for line in visual_lines
                    for item in sorted(line, key=lambda value: float(value["x0"]))
                ]
                tokens: list[dict[str, Any]] = []
                for item in ordered:
                    values = sentence_tokens(str(item["text"]))
                    for value in values:
                        tokens.append({"value": value, **item})
                pages.append({"page": page_number, "column": column, "tokens": tokens})
                for value in {item["value"] for item in tokens}:
                    first_token_pages[value].append(len(pages) - 1)
    return pages, first_token_pages


def compact_pdf_values(tokens: list[dict[str, Any]]) -> tuple[list[str], list[tuple[int, int]]]:
    values: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(tokens):
        value = tokens[index]["value"]
        if value.endswith("-") and index + 1 < len(tokens):
            value = value[:-1] + tokens[index + 1]["value"]
            values.append(value)
            spans.append((index, index + 1))
            index += 2
        else:
            values.append(value)
            spans.append((index, index))
            index += 1
    return values, spans


def locate_sentence(
    sentence: str, pages: list[dict[str, Any]], first_token_pages: dict[str, list[int]],
) -> dict[str, Any]:
    wanted = sentence_tokens(sentence)
    if not wanted:
        raise CleanupError(f"empty sentence tokens: {sentence!r}")
    # Hyphens are layout-ambiguous in this PDF: the same glyph represents
    # both optional compounds such as ``(Schlag-)Rahm`` and line wrapping.
    # Coordinates and every letter still have to match exactly.
    target = "".join(wanted).replace("-", "")
    # Some words in the official PDF have no encoded spaces even though they
    # are visually separated (for example ``Ichholenoch...``).  Search exact
    # character sequences across adjacent PDF tokens as a second, still
    # fail-closed, representation.  We deliberately do not use fuzzy text
    # matching for evidence.
    likely_pages = first_token_pages.get(wanted[0], [])
    page_indexes = likely_pages or range(len(pages))
    for page_index in page_indexes:
        page = pages[page_index]
        values, spans = compact_pdf_values(page["tokens"])
        for start in range(len(values)):
            joined = ""
            for end in range(start, len(values)):
                joined += values[end].replace("-", "")
                if len(joined) > len(target):
                    break
                if joined != target:
                    continue
                first = page["tokens"][spans[start][0]]
                last = page["tokens"][spans[end][1]]
                return {
                    "page": page["page"],
                    "start": {"x0": first["x0"], "top": first["top"]},
                    "end": {"x1": last["x1"], "bottom": last["bottom"]},
                }
    raise CleanupError(f"sentence not found in coordinate PDF text: {sentence!r}")


def table_rows(path: Path) -> tuple[list[str], dict[int, int]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    mapping: dict[int, int] = {}
    row = 0
    for index, line in enumerate(lines):
        if line.startswith("| **"):
            row += 1
            mapping[row] = index
    if row != 2969:
        raise CleanupError(f"expected 2969 B1 rows, got {row}")
    return lines, mapping


def render_row(line: str, examples: list[str], pos: str, gender: str) -> str:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) != 6:
        raise CleanupError(f"unexpected source row: {line}")
    cells[1] = pos
    cells[2] = gender
    cells[4] = "<br>".join(examples)
    return "| " + " | ".join(cells) + " |"


def build(reference: Path) -> tuple[list[str], dict[str, Any]]:
    refs = reference_rows(reference)
    source_rows = gw.parse_markdown(SOURCE)
    lines, line_by_row = table_rows(SOURCE)
    pages, first_token_pages = pdf_lines()
    changes = []

    for row in source_rows:
        matches = choose_references(row, refs)
        desired = []
        for _, ref, _ in matches:
            for sentence in split_reference_examples(ref[1]):
                if sentence not in desired:
                    desired.append(sentence)
        similarity = max(item[2] for item in matches)
        current = [dehyphenate(value) for value in row["examples"]]
        pos, gender = POS_OVERRIDES.get(row["word"], (row["pos"], row["gender"]))
        if current == desired and (pos, gender) == (row["pos"], row["gender"]):
            continue
        evidence = [locate_sentence(sentence, pages, first_token_pages) for sentence in desired if not sentence.startswith("(siehe ")]
        changes.append({
            "row": row["row"], "word": row["word"],
            "reference_head": " || ".join(item[1][0] for item in matches),
            "reference_similarity": round(similarity, 6), "old_examples": row["examples"],
            "new_examples": desired, "old_pos": row["pos"], "new_pos": pos,
            "old_gender": row["gender"], "new_gender": gender, "pdf_evidence": evidence,
        })
        lines[line_by_row[row["row"]]] = render_row(
            lines[line_by_row[row["row"]]], desired, pos, gender,
        )

    # Pure line-wrap repairs may not differ from the clean reference comparison.
    for row in gw.parse_markdown(SOURCE):
        if not any(re.search(r"\b[^\W\d_]+-\s+[^\W\d_]+\b", value) for value in row["examples"]):
            continue
        if any(item["row"] == row["row"] for item in changes):
            continue
        desired = [dehyphenate(value) for value in row["examples"]]
        evidence = [locate_sentence(sentence, pages, first_token_pages) for sentence in desired]
        changes.append({
            "row": row["row"], "word": row["word"], "reference_head": None,
            "reference_similarity": None, "old_examples": row["examples"],
            "new_examples": desired, "old_pos": row["pos"], "new_pos": row["pos"],
            "old_gender": row["gender"], "new_gender": row["gender"], "pdf_evidence": evidence,
        })
        lines[line_by_row[row["row"]]] = render_row(
            lines[line_by_row[row["row"]]], desired, row["pos"], row["gender"],
        )

    report = {"schema_version": 1, "source_rows": len(source_rows), "changes": sorted(changes, key=lambda item: item["row"])}
    return lines, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-csv", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    lines, report = build(args.reference_csv)
    STATE.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.apply:
        payload = "\n".join(lines) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=SOURCE.parent, delete=False) as handle:
            handle.write(payload)
            temp = Path(handle.name)
        temp.replace(SOURCE)
    print(json.dumps({"rows": report["source_rows"], "changes": len(report["changes"]), "applied": args.apply, "report": str(REPORT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
