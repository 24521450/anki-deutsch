"""Audit and migrate explicit Goethe example boundaries.

``<br data-example-boundary>`` separates examples.  A plain ``<br>`` is
reserved for a line break inside one example, currently a dash-led dialogue
reply.  The tracked merge decisions are verified against ordered PDF tokens.
"""
from __future__ import annotations

import argparse
import functools
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pdfplumber

import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
DECISIONS = ROOT / "review" / "goethe_example_boundary_decisions.json"
SOURCE_PATHS = {"A1": gw.SOURCE_A1, "A2": gw.SOURCE_A2, "B1": gw.SOURCE_B1}
PDF_CONFIG = {
    "A1": (ROOT / "sources" / "goethe" / "A1_SD1_Wortliste_02.pdf", ((225, 570),)),
    "A2": (
        ROOT / "sources" / "goethe" / "Goethe-Zertifikat_A2_Wortliste.pdf",
        ((100, 292), (370, 562)),
    ),
    "B1": (
        ROOT / "sources" / "goethe" / "Goethe-Zertifikat_B1_Wortliste.pdf",
        ((135, 292), (415, 572)),
    ),
}
PLAIN_BR_RE = re.compile(r"<br\s*/?>", re.I)
DASH_REPLY_RE = re.compile(r"^[–—-]\s*")


class BoundaryError(RuntimeError):
    pass


def normalized_tokens(value: str) -> list[str]:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    return [
        unicodedata.normalize("NFC", token)
        .casefold()
        .replace("–", "-")
        .replace("—", "-")
        .replace("’", "'")
        for token in re.findall(r"\S+", value)
    ]


def load_decisions() -> dict[str, Any]:
    data = json.loads(DECISIONS.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("merges"), dict):
        raise BoundaryError("unsupported example-boundary decision schema")
    return data


def source_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def example_cell(line: str) -> str:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    if len(cells) != 6:
        raise BoundaryError(f"unexpected Markdown row: {line}")
    return cells[4]


def replace_example_cell(line: str, value: str) -> str:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    cells[4] = value
    return "| " + " | ".join(cells) + " |"


def migrate_cell(ref: str, value: str, merge_indexes: set[int]) -> str:
    if gw.EXAMPLE_BOUNDARY in value:
        return value
    parts = [part.strip() for part in PLAIN_BR_RE.split(value)]
    if len(parts) == 1:
        return value
    result = parts[0]
    for index, part in enumerate(parts[1:]):
        if not part:
            raise BoundaryError(f"empty example fragment: {ref}")
        if index in merge_indexes:
            separator = " "
        elif DASH_REPLY_RE.match(part):
            separator = "<br>"
        else:
            separator = gw.EXAMPLE_BOUNDARY
        result += separator + part
    return result


def migrate_sources() -> None:
    decisions = load_decisions()
    merge_indexes = {
        ref: set(map(int, item["legacy_boundary_indexes"]))
        for ref, item in decisions["merges"].items()
    }
    for level, path in SOURCE_PATHS.items():
        output: list[str] = []
        row_number = 0
        for line in source_lines(path):
            if not line.startswith("| **"):
                output.append(line)
                continue
            row_number += 1
            ref = f"{level}-MAIN-{row_number:04d}"
            output.append(
                replace_example_cell(
                    line,
                    migrate_cell(ref, example_cell(line), merge_indexes.get(ref, set())),
                )
            )
        path.write_text("\n".join(output) + "\n", encoding="utf-8")


def pdf_occurrences(level: str, sentence: str) -> list[dict[str, Any]]:
    pdf_path, columns = PDF_CONFIG[level]
    target = normalized_tokens(sentence)
    matches: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as document:
        for page_number, page in enumerate(document.pages, start=1):
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            for column_index, (left, right) in enumerate(columns):
                stream = [word for word in words if left <= word["x0"] < right]
                stream.sort(key=lambda word: (round(word["top"], 1), word["x0"]))
                tokens = normalized_tokens(" ".join(word["text"] for word in stream))
                for index in range(len(tokens) - len(target) + 1):
                    if tokens[index:index + len(target)] == target:
                        start = stream[index]
                        end = stream[index + len(target) - 1]
                        matches.append({
                            "page": page_number,
                            "column": column_index,
                            "start_top": round(start["top"], 3),
                            "end_top": round(end["top"], 3),
                        })
    return matches


@functools.lru_cache(maxsize=1)
def check_source_grammar() -> dict[str, int]:
    marker_count = 0
    row_count = 0
    for level, path in SOURCE_PATHS.items():
        for line in source_lines(path):
            if not line.startswith("| **"):
                continue
            row_count += 1
            cell = example_cell(line)
            marker_count += cell.count(gw.EXAMPLE_BOUNDARY)
            for match in PLAIN_BR_RE.finditer(cell):
                tail = cell[match.end():].lstrip()
                if not DASH_REPLY_RE.match(tail):
                    raise BoundaryError(f"ambiguous plain <br> boundary: {level} {cell}")
    if not marker_count:
        raise BoundaryError("explicit example-boundary migration is missing")
    return {"rows": row_count, "explicit_boundaries": marker_count}


@functools.lru_cache(maxsize=1)
def check_sources() -> dict[str, Any]:
    grammar = check_source_grammar()
    decisions = load_decisions()
    rows_by_ref: dict[str, dict[str, Any]] = {}
    for level, path in SOURCE_PATHS.items():
        for row in gw.parse_markdown(path):
            ref = f"{level}-MAIN-{row['row']:04d}"
            rows_by_ref[ref] = row

    evidence: dict[str, Any] = {}
    for ref, decision in decisions["merges"].items():
        row = rows_by_ref.get(ref)
        if row is None:
            raise BoundaryError(f"reviewed merge source is missing: {ref}")
        expected = decision["example"]
        if expected not in row["examples"]:
            raise BoundaryError(f"reviewed merge drift: {ref}")
        matches = pdf_occurrences(ref.split("-", 1)[0], expected)
        if len(matches) != 1:
            raise BoundaryError(f"PDF evidence is not unique for {ref}: {len(matches)}")
        evidence[ref] = matches[0]
    return {
        **grammar,
        "reviewed_merges": len(evidence),
        "pdf_evidence": evidence,
        "unresolved": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("migrate", "check"))
    args = parser.parse_args()
    if args.command == "migrate":
        migrate_sources()
    report = check_sources()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (BoundaryError, gw.MigrationError, OSError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
