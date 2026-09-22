"""Apply the reviewed content corrections for the September Goethe issue batch.

This is a deterministic review-artifact update only; live Anki mutations are
performed by the guarded refresh/merge commands after these before-images have
been checked.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "review" / "goethe_english_audit_v5.jsonl"
GLOSS = ROOT / "review" / "goethe_meaning_gloss_audit_v1.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def audit_updates(rows: list[dict[str, Any]]) -> None:
    by_id = {str(row.get("source_id")): row for row in rows}
    required = {"A1-84886454894", "A2-0048", "A2-0387", "A2-0812", "A1-84886455260"}
    missing = required - set(by_id)
    if missing:
        raise RuntimeError(f"missing English audit rows: {sorted(missing)}")

    long_row = by_id["A1-84886454894"]
    if long_row.get("desired_meaning_en") != "for a long time":
        raise RuntimeError("lange before-image is not the reviewed value")
    long_row["desired_meaning_en"] = "long; for a long time"
    long_row["decision"] = "REVISE"
    long_row["change_categories"] = ["meaning_clarification"]
    long_row["reason"] = "Clarify both the interrogative duration sense and the affirmative duration sense of lange."
    long_row["review_provenance"] = "goethe-issue-review-2026-09"

    training = by_id["A2-0048"]
    examples = training.get("desired_examples", [])
    if len(examples) != 1 or examples[0].get("en") != "I would like to become a nurse.":
        raise RuntimeError("Ausbildung before-image is not the reviewed value")
    examples[0]["en"] = "I would like to train as a nurse."
    training["decision"] = "REVISE"
    training["change_categories"] = ["translation_correction"]
    training["reason"] = "Translate eine Ausbildung zum Krankenpfleger machen as training for a profession."
    training["review_provenance"] = "goethe-issue-review-2026-09"

    opposite = by_id["A2-0387"]
    if opposite.get("desired_meaning_en") != "across; across from":
        raise RuntimeError("gegenüber before-image is not the reviewed value")
    opposite["desired_meaning_en"] = "opposite; across from"
    opposite["decision"] = "REVISE"
    opposite["change_categories"] = ["meaning_clarification"]
    opposite["reason"] = "Use the natural location gloss opposite; across from."
    opposite["review_provenance"] = "goethe-issue-review-2026-09"
    opposite["evidence"] = [
        {"provider": "Collins", "url": "https://www.collinsdictionary.com/us/dictionary/german-english/gegenuber", "supports": "opposite; across from"},
        {"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/gegenueber", "supports": "adverb describing a position opposite another place or person"},
    ]

    ride = by_id["A2-0812"]
    if ride.get("lemma") != "reiten (animal)" or ride.get("desired_meaning_en") != "to ride a horse":
        raise RuntimeError("reiten before-image is not the reviewed value")
    ride["previous_lemma"] = ride.get("lemma")
    ride["lemma"] = "reiten"
    ride["desired_meaning_en"] = "to ride (an animal)"
    ride["desired_examples"][0]["en"] = "Would you like to learn to ride?"
    ride["decision"] = "REVISE"
    ride["change_categories"] = ["headword_cleanup", "meaning_clarification", "translation_correction"]
    ride["reason"] = "Use the canonical German infinitive reiten and clarify that the verb means riding an animal."
    ride["review_provenance"] = "goethe-issue-review-2026-09"
    ride["evidence"] = [
        {"provider": "Collins", "url": "https://www.collinsdictionary.com/dictionary/german-english/reiten", "supports": "to ride"},
        {"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/reiten", "supports": "to ride an animal, especially a horse"},
    ]

    together = by_id["A1-84886455260"]
    if "B1-MAIN-2947" not in together.get("source_refs", []):
        together["source_refs"].append("B1-MAIN-2947")
    extra = [
        {"de": "Wir haben als Kinder oft zusammen gespielt.", "en": "We often played together as children.", "origin": "review-authored"},
        {"de": "Fuß und Ball musst du zusammenschreiben – Fußball!", "en": "You have to write Fuß and Ball together – Fußball!", "origin": "review-authored"},
    ]
    existing = {str(item.get("de", "")) for item in together.get("desired_examples", [])}
    together["desired_examples"].extend(item for item in extra if item["de"] not in existing)
    together["decision"] = "REVISE"
    together["change_categories"] = ["duplicate_merge", "example_provenance"]
    together["reason"] = "Merge the redundant B1 zusammen/zusammen- note into the A1 zusammen survivor and retain its useful examples."
    together["review_provenance"] = "goethe-issue-review-2026-09"


def gloss_updates(rows: list[dict[str, Any]]) -> None:
    by_id = {str(row.get("source_id")): row for row in rows}
    for source_id, current, desired, reason in (
        ("A1-84886454894", "for a long time", "long; for a long time", "Clarify the duration adverb's interrogative and affirmative uses."),
        ("A2-0387", "across; across from", "opposite; across from", "Use the natural location gloss opposite; across from."),
        ("A2-0812", "to ride a horse", "to ride (an animal)", "Clarify the general animal-riding sense."),
    ):
        row = by_id.get(source_id)
        if row is None:
            raise RuntimeError(f"missing gloss audit row: {source_id}")
        if row.get("current_meaning_en") != current:
            raise RuntimeError(f"gloss before-image drift: {source_id}")
        row["manifest_meaning_en"] = desired
        row["recommended_meaning_en"] = desired
        row["meaning_matches_manifest"] = False
        row["decision"] = "REVISE"
        row["classification"] = "REVIEWED_CORRECTION"
        row["reason"] = reason
        row["confidence"] = "high"
        row["review_basis"] = "goethe-issue-review-2026-09"


def main() -> int:
    audit = read_jsonl(AUDIT)
    audit_updates(audit)
    write_jsonl(AUDIT, audit)
    gloss = read_jsonl(GLOSS)
    gloss_updates(gloss)
    write_jsonl(GLOSS, gloss)
    print(f"updated {AUDIT.name} and {GLOSS.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
