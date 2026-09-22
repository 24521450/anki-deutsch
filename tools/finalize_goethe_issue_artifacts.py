"""Align the reviewed v5 audit catalog with the live reviewed merges.

The three historical Wortgruppen merges and the September ``zusammen`` merge
are already applied in Anki.  This small deterministic step removes the
retired source rows and records their provenance/examples on the survivors so
the audit and JSONL export continue to describe the exact live corpus.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "review" / "goethe_english_audit_v5.jsonl"
REMOVED = {
    "B1-WG-0127",
    "B1-WG-0134",
    "B1-WG-0202",
    "B1-WG-0209",
    "B1-MAIN-2947",
}

PREVIOUS_MERGES = {
    "B1-WG-0120": {
        "source_refs": ["B1-WG-0120", "B1-WG-0127", "B1-WG-0134"],
        "examples": [
            {
                "de": "\u00d6sterreich: Die Volkshochschule bietet viele Abendkurse an.",
                "en": "Austria: The adult education center offers many evening classes.",
                "origin": "review-authored",
            },
            {
                "de": "Schweiz: Die Volkshochschule bietet Kurse f\u00fcr Erwachsene an.",
                "en": "Switzerland: The adult education center offers courses for adults.",
                "origin": "review-authored",
            },
        ],
    },
    "B1-WG-0199": {
        "source_refs": ["B1-WG-0199", "B1-WG-0202"],
        "examples": [
            {
                "de": "\u00d6sterreich: Das Bundesland Tirol liegt im Westen.",
                "en": "Austria: The federal state of Tyrol is in the west.",
                "origin": "review-authored",
            },
        ],
    },
    "B1-WG-0203": {
        "source_refs": ["B1-WG-0203", "B1-WG-0209"],
        "examples": [
            {
                "de": "Schweiz: Der Nationalrat hat zweihundert Mitglieder.",
                "en": "Switzerland: The National Council has two hundred members.",
                "origin": "review-authored",
            },
        ],
    },
}

TOGETHER_EXAMPLES = [
    {
        "de": "Wir haben als Kinder oft zusammen gespielt.",
        "en": "We often played together as children.",
        "origin": "review-authored",
    },
    {
        "de": "Fu\u00df und Ball musst du zusammenschreiben \u2013 Fu\u00dfball!",
        "en": "You have to write Fu\u00df and Ball together \u2013 Fu\u00dfball!",
        "origin": "review-authored",
    },
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def append_examples(row: dict[str, Any], examples: list[dict[str, Any]], *, expected_translation: bool) -> None:
    key = "expected_examples" if expected_translation else "desired_examples"
    current = row.setdefault(key, [])
    known = {str(item.get("de", "")) for item in current}
    for example in examples:
        if example["de"] not in known:
            current.append(copy.deepcopy(example))
            known.add(example["de"])


def main() -> int:
    rows = read_jsonl(AUDIT)
    by_id = {str(row.get("source_id", "")): row for row in rows}
    together = by_id.get("A1-84886455260")
    if together is None:
        raise RuntimeError("missing A1 zusammen audit row")

    together["source_refs"] = list(dict.fromkeys([
        *together.get("source_refs", []), "B1-MAIN-2947",
    ]))
    append_examples(together, TOGETHER_EXAMPLES, expected_translation=False)
    # Keep the German source inventory unchanged while explicitly recording
    # the English reviewed translations for the merged examples.
    append_examples(
        together,
        [dict(item, en="") for item in TOGETHER_EXAMPLES],
        expected_translation=True,
    )

    for source_id, spec in PREVIOUS_MERGES.items():
        row = by_id.get(source_id)
        if row is None:
            raise RuntimeError(f"missing merged survivor audit row: {source_id}")
        row["source_refs"] = list(spec["source_refs"])
        append_examples(row, spec["examples"], expected_translation=True)
        append_examples(row, spec["examples"], expected_translation=False)

    final_rows = [row for row in rows if str(row.get("source_id", "")) not in REMOVED]
    if len(final_rows) != 3420:
        raise RuntimeError(f"expected 3420 final audit rows, got {len(final_rows)}")
    for row in final_rows:
        expected_de = [item.get("de") for item in row.get("expected_examples", [])]
        desired_de = [item.get("de") for item in row.get("desired_examples", [])]
        if expected_de != desired_de:
            raise RuntimeError(f"German example inventory drift: {row.get('source_id')}")
    write_jsonl(AUDIT, final_rows)
    print(json.dumps({"audit": str(AUDIT), "rows": len(final_rows), "removed": sorted(REMOVED)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
