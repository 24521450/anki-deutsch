from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "data" / "build" / "anki_notes.jsonl"


def records() -> list[dict]:
    return [
        json.loads(line)
        for line in EXPORT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def one_by_lemma(items: list[dict], lemma: str) -> dict:
    matches = [item for item in items if item["lemma"] == lemma]
    assert len(matches) == 1
    return matches[0]


def test_examples_and_variants_do_not_create_ghost_headwords() -> None:
    items = records()
    lemmas = {item["lemma"] for item in items}
    assert {"her kommen", "vorne", "Lieblingsfarbe"}.isdisjoint(lemmas)
    assert "vorne" in one_by_lemma(items, "vorn")["accepted_answers_de"]


def test_anmelden_keeps_only_verb_examples_and_kuendigen_keeps_source_sense() -> None:
    items = records()
    anmelden = one_by_lemma(items, "sich anmelden")
    anmeldung = one_by_lemma(items, "Anmeldung")
    noun_sentence = "Eine Anmeldung für diesen Kurs ist nicht mehr möglich."
    assert noun_sentence not in {example["de"] for example in anmelden["examples"]}
    assert noun_sentence in {example["de"] for example in anmeldung["examples"]}

    meaning = one_by_lemma(items, "kündigen")["meaning_en"].casefold()
    assert meaning == "to quit; to resign"
    assert "notice" not in meaning
    assert "notive" not in meaning


def test_free_and_bound_zurueck_are_distinct_without_generated_compounds() -> None:
    items = records()
    lemmas = [item["lemma"] for item in items]
    assert lemmas.count("zurück") == 1
    assert lemmas.count("zurück-") == 1
    assert {
        "zurückfahren",
        "zurückgeben",
        "zurückgehen",
        "zurückkommen",
        "zurücklaufen",
        "zurückzurückfahren",
    }.isdisjoint(lemmas)
    assert one_by_lemma(items, "zurück")["source_id"] != one_by_lemma(
        items, "zurück-"
    )["source_id"]
