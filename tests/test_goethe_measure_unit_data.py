from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_english_audit as english_audit  # noqa: E402
import goethe_review_policy as review_policy  # noqa: E402
import validate_goethe_wortgruppen as wortgruppen  # noqa: E402


NOUN_ROWS = {
    "A1-WG-0093": ("Meter", "der", "m.", "-"),
    "A1-WG-0094": ("Zentimeter", "der", "m.", "-"),
    "A1-WG-0096": ("Kilometer", "der", "m.", "-"),
    "A1-WG-0097": ("Quadratmeter", "der", "m.", "-"),
    "A1-WG-0100": ("Prozent", "das", "n.", "-e"),
    "A1-WG-0101": ("Liter", "der/das", "m./n.", "-"),
    "A1-WG-0102": ("Gramm", "das", "n.", "-e"),
    "A1-WG-0103": ("Pfund", "das", "n.", "-e"),
    "A1-WG-0104": ("Kilogramm", "das", "n.", "-/-e"),
}

AUDIT_ROWS = {
    "A1-84887177249": ("Meter", "metre", "one metre"),
    "A1-84887177250": ("Zentimeter", "centimetre", "one centimetre"),
    "A1-84887177252": ("Kilometer", "kilometre", "two hundred kilometres"),
    "A1-84887177253": ("Quadratmeter", "square metre", "one square metre"),
    "A1-84887177256": ("Prozent", "per cent", "one per cent"),
    "A1-84887177257": ("Liter", "litre", "one litre"),
    "A1-84887177258": ("Gramm", "gram", "one gram"),
    "A1-84887177259": ("Pfund", "pound", "one pound"),
    "A1-84887177260": ("Kilogramm", "kilogram", "one kilo(gram)"),
}


def test_a1_measure_rows_separate_noun_headwords_from_source_phrases() -> None:
    _, rows = wortgruppen.parse_inventory(
        wortgruppen.SOURCE_DIR / "Goethe_A1_Wortgruppen.md", "A1",
    )
    by_id = {row["ID"]: row for row in rows}

    for source_id, expected in NOUN_ROWS.items():
        row = by_id[source_id]
        assert (
            row["Canonical Lemma"], row["Article"], row["Gender"], row["Noun Forms"],
        ) == expected
        assert row["POS"] == "n."
        assert row["Dictionary Sources"].startswith("https://www.duden.de/rechtschreibung/")

    for source_id in ("A1-WG-0095", "A1-WG-0098", "A1-WG-0099"):
        row = by_id[source_id]
        assert row["Canonical Lemma"] == row["Entry"]
        assert row["POS"] == "phrase"
        assert not any(row[field] for field in ("Article", "Gender", "Noun Forms"))

    assert by_id["A1-WG-0104"]["Accepted Variants"] == "Kilo"


def test_measure_audit_accepts_old_live_content_and_targets_lexical_content() -> None:
    entries = english_audit.load_json(english_audit.MANIFEST)["entries"]

    for source_id, (lemma, meaning, old_meaning) in AUDIT_ROWS.items():
        entry = entries[source_id]
        assert (entry["lemma"], entry["pos"], entry["decision"]) == (lemma, "n.", "REVISE")
        assert entry["desired_meaning_en"] == meaning
        # v4 canonicalises the expected field to the reviewed lexical gloss;
        # the pre-v4 live wording remains available as migration provenance.
        assert entry["expected_meaning_en"] == meaning
        assert entry["previous_meaning_en"] == old_meaning
        assert entry["previous_examples"] == []
        assert entry["expected_examples"] == entry["desired_examples"]
        assert entry["desired_examples"][0]["origin"] == "goethe"
        assert {item["provider"] for item in entry["evidence"]} == {"Cambridge", "Duden"}
        assert all("/suchen/" not in item["url"] for item in entry["evidence"])

    for source_id in ("A1-84887177251", "A1-84887177254", "A1-84887177255"):
        entry = entries[source_id]
        assert (entry["pos"], entry["decision"]) == ("phrase", "KEEP")
        assert all("/suchen/" not in item["url"] for item in entry["evidence"])


def test_measure_overrides_explain_pfund_and_keep_both_kilogramm_answers() -> None:
    policy = review_policy.load_policy()
    pfund = {"SourceID": "A1-84887177259", "MeaningEN": "one pound"}
    kilogramm = {"SourceID": "A1-84887177260"}

    assert review_policy.apply_fields(pfund, policy)
    assert pfund == {
        "SourceID": "A1-84887177259",
        "MeaningEN": "pound",
        "UsageNoteEN": "In German weight usage, one Pfund equals 500 grams.",
    }
    assert review_policy.apply_fields(kilogramm, policy)
    assert kilogramm["AcceptedAnswersDE"] == "Kilogramm|Kilo"
    assert kilogramm["AcceptedFullAnswersDE"] == "das Kilogramm|das Kilo"


def test_b1_colour_contrast_override_removes_html_and_disables_ambiguous_production() -> None:
    policy = review_policy.load_policy()
    fields = {
        "SourceID": "B1-WG-0161",
        "Lemma": "hell<br>dunkel",
        "POS": "",
    }
    assert review_policy.apply_fields(fields, policy)
    assert fields["Lemma"] == "hell-, dunkel-"
    assert fields["POS"] == "adj."
    assert fields["AcceptedAnswersDE"] == "hell-, dunkel-"
    assert fields["AcceptedFullAnswersDE"] == "hell-, dunkel-"


def test_exported_measure_cards_use_bare_nouns_and_human_audio() -> None:
    rows = {
        row["source_id"]: row
        for line in (ROOT / "data" / "build" / "anki_notes.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
        if (row := json.loads(line))["source_id"] in AUDIT_ROWS
    }

    for source_id, (lemma, meaning, _old_meaning) in AUDIT_ROWS.items():
        row = rows[source_id]
        assert (row["lemma"], row["meaning_en"], row["pos"]) == (lemma, meaning, "n.")
        assert row["word_audio"].startswith((
            "[sound:_goethe_word_duden_",
            "[sound:_goethe_word_commons_",
        ))
        assert not row["lemma"].startswith("ein ")
        assert any(ref.startswith("B1-WG-") for ref in row["source_refs"])

    assert rows["A1-84887177257"]["article"] == "der/das"
    assert rows["A1-84887177259"]["usage_note_en"].endswith("500 grams.")
