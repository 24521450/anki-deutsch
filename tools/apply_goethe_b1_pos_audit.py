"""Apply evidence-backed B1 POS corrections from the checked-in Duden audit."""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources" / "goethe" / "Goethe_B1.md"
AUDIT = ROOT / "review" / "duden_b1_overrides.json"
TYPE_MAP = {
    "Adjektiv": "adj.", "Adverb": "adv.", "Konjunktion": "conj.",
    "Präposition": "prep.", "Pronomen": "pron.",
    "schwaches Verb": "v.", "Substantiv, maskulin": "n.",
    "Substantiv, Neutrum": "n.",
}
EXPLICIT = {
    "derselbe": "det., pron.", "einschließlich": "prep.", "Halt": "n.",
    "Mal": "n.", "per": "prep.", "Prost": "interj.", "selten": "adj., adv.",
    "sondern": "conj.", "soviel": "conj.", "umso": "conj.",
    "während": "prep., conj.",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    audited = json.loads(AUDIT.read_text(encoding="utf-8"))["rows"]
    corrections = {}
    for row, item in audited.items():
        match = re.search(r"POS mismatch: expected .*?, got (.+)$", item.get("reason", ""))
        if match and match.group(1) in TYPE_MAP:
            corrections[int(row)] = TYPE_MAP[match.group(1)]
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    output = []
    changed = []
    row_number = 0
    for line in lines:
        if not line.startswith("| **"):
            output.append(line)
            continue
        row_number += 1
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        word = cells[0].removeprefix("**").removesuffix("**")
        target = EXPLICIT.get(word, corrections.get(row_number))
        if target and cells[1] != target:
            changed.append({"row": row_number, "word": word, "field": "POS", "old": cells[1], "new": target})
            cells[1] = target
        if cells[2] == "f." and re.search(r"\((?:nur\s+)?Pl\.(?:ural)?\)|\(Plural\)", cells[5], re.I):
            changed.append({"row": row_number, "word": word, "field": "Gender", "old": cells[2], "new": "pl."})
            cells[2] = "pl."
        if target or (changed and changed[-1]["row"] == row_number):
            line = "| " + " | ".join(cells) + " |"
        output.append(line)
    if row_number != 2969:
        raise RuntimeError(f"expected 2969 rows, got {row_number}")
    if args.apply:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=SOURCE.parent, delete=False) as handle:
            handle.write("\n".join(output) + "\n")
            temp = Path(handle.name)
        temp.replace(SOURCE)
    print(json.dumps({"rows": row_number, "changes": len(changed), "applied": args.apply, "items": changed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
