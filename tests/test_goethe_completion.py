from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_completion as gc  # noqa: E402


def test_lemma_identity_preserves_german_case_and_reflexive_variants():
    assert gc.lemma_key("der Arm") == "Arm"
    assert gc.lemma_key("arm") == "arm"
    assert gc.lemma_key("(sich) anmelden") == "anmelden"
    assert gc.lemma_key("sich anmelden") == "anmelden"
    assert "auf keinen Fall" in gc.source_variants("auf jeden/keinen Fall")
    assert "leid tun" in gc.source_variants("leidtun/leid tun")


def test_wortgruppen_parser_preserves_all_rows_and_categories():
    a1 = gc.parse_wortgruppen(gc.WG_FILES["A1"])
    a2 = gc.parse_wortgruppen(gc.WG_FILES["A2"])
    assert len(a1) == 121
    assert len(a2) == 225
    assert a1[0]["id"] == "A1-WG-0001"
    assert a2[-1]["id"] == "A2-WG-0225"
    assert a1[0]["category"] == "Zahlen"


def test_numeric_wortgruppe_uses_spoken_german_as_lemma():
    row = {"entry": "1", "detail": "eins", "match": ""}
    assert gc.wg_lemma(row) == "eins"


def test_wortgruppe_gender_variants_become_accepted_answers():
    row = {"entry": "Prüfer, - / Prüferin, -nen", "detail": "", "match": ""}
    assert gc.wg_answers(row) == ["Prüfer", "Prüferin"]


def test_more_examples_html_is_escaped_and_highlightable():
    record = gc.new_record("A1-MAIN-0001", "testen", "A1")
    record["fields"]["MeaningEN"] = "test"
    record["examples"] = [
        {"de": f"Satz {i}", "en": f"Sentence {i}", "audio": ""} for i in range(1, 6)
    ] + [{"de": "<script>x</script>", "en": "safe", "audio": ""}]
    gc.render_examples(record)
    overflow = record["fields"]["MoreExamplesHTML"]
    assert "gw-example-de" in overflow
    assert "&lt;script&gt;" in overflow
    assert "<script>" not in overflow


def test_variant_index_finds_reflexive_source_without_scanning_all_records():
    record = gc.new_record("A1-X", "sich anmelden", "A1", "v.")
    records = {"1": record}
    index = gc.variant_index(records)
    assert gc.find_record(records, index, "anmelden", "v.") == "1"


def test_existing_same_spelling_is_preferred_over_creating_a_third_sense():
    low = gc.new_record("A1-X", "Bank", "A1", "n.", "f.")
    high = gc.new_record("A1-Y", "Bank", "A1", "n.", "f.")
    low.update({"note_id": 1, "is_new": False, "cards": [{"reps": 2}]})
    high.update({"note_id": 2, "is_new": False, "cards": [{"reps": 9}]})
    records = {"1": low, "2": high}
    assert gc.find_record(records, gc.variant_index(records), "Bank", "n.", "f.") == "2"


def test_casefold_fallback_uses_pos_to_keep_arm_senses_separate():
    noun = gc.new_record("A1-X", "Arm", "A1", "n.", "m.")
    adjective = gc.new_record("A2-X", "arm", "A2", "adj.")
    records = {"1": noun, "2": adjective}
    index = gc.variant_index(records)
    assert gc.find_record(records, index, "Arm", "n.", "m.") == "1"
    assert gc.find_record(records, index, "arm", "adj.") == "2"


def test_exact_duplicate_merge_keeps_more_reviewed_note_and_lower_level():
    def item(note_id, level, reps):
        record = gc.new_record(f"{level}-X", "zum Beispiel", level)
        record.update({"note_id": note_id, "is_new": False, "cards": [{"reps": reps}], "deck": gc.LEVEL_DECK[level]})
        record["fields"].update({"MeaningEN": "for example", "POS": "phrase", "CEFR": level})
        return record
    records = {"1": item(1, "A1", 2), "2": item(2, "A2", 10)}
    deleted = gc.merge_exact_duplicates(records)
    assert list(records) == ["2"]
    assert records["2"]["fields"]["CEFR"] == "A1"
    assert deleted[0]["note_id"] == 1


def test_apply_cli_exposes_history_preserving_duplicate_mode():
    args = gc.build_parser().parse_args(["apply", "--confirmation", gc.CONFIRMATION, "--keep-duplicates", "--skip-new"])
    assert args.keep_duplicates is True
    assert args.skip_new is True


def test_redundancy_policy_skips_drills_and_maps_merge_targets():
    policy = gc.load_redundancy_policy()
    assert "A2-WG-0160" in policy["skip_wortgruppen"]
    assert "A2-WG-0114" in policy["skip_wortgruppen"]
    assert policy["merge_wortgruppen"]["A2-WG-0163"] == {
        "target": "Viertel vor/nach zwei", "as_example": True,
    }
