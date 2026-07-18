from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


def test_reviewed_measure_merges_delete_exact_b1_notes_and_route_collision():
    policy = gc.load_redundancy_policy()
    groups = policy["reviewed_note_merges"]
    expected = {
        1784075689145: 1584887177249,
        1784075689238: 1584887177250,
        1784075689331: 1584887177251,
        1784075689425: 1584887177252,
        1784075689517: 1584887177253,
        1784075689611: 1584887177254,
        1784075689705: 1584887177255,
        1784075689797: 1584887177256,
        1784075689892: 1584887177257,
        1784075689985: 1584887177258,
        1784075690077: 1584887177259,
        1784075690172: 1584887177260,
    }
    assert {item["duplicate"]: item["survivor"] for item in groups} == expected
    merges = policy["merge_wortgruppen"]
    assert {
        item["duplicate_source_ref"]: merges[item["duplicate_source_ref"]]["target_source_ref"]
        for item in groups
    } == {
        item["duplicate_source_ref"]: item["survivor_source_ref"] for item in groups
    }

    records = {}
    for item in groups:
        survivor = gc.new_record(item["survivor_source_ref"], "survivor", "A1")
        survivor.update({"note_id": item["survivor"], "is_new": False, "cards": []})
        records[str(item["survivor"])] = survivor
        duplicate = gc.new_record(item["duplicate_source_ref"], "duplicate", "B1")
        duplicate.update({"note_id": item["duplicate"], "is_new": False, "cards": []})
        if item["duplicate_source_ref"] == "B1-WG-0243":
            duplicate["source_refs"].append("B1-WG-0255")
        records[str(item["duplicate"])] = duplicate
    eins = gc.new_record("A1-WG-0001", "eins", "A1")
    eins.update({"note_id": 1584887177160, "is_new": False, "cards": []})
    records["1584887177160"] = eins

    deletions = gc.apply_reviewed_note_merges(records, [], groups)
    assert {item["note_id"]: item["survivor"] for item in deletions} == expected
    assert not ({str(note_id) for note_id in expected} & set(records))
    assert gc.apply_reviewed_note_merges(records, deletions, groups) == deletions

    by_source_ref = {
        ref: key for key, record in records.items() for ref in record["source_refs"]
    }
    assert gc.configured_wortgruppe_key(
        "B1-WG-0243", merges["B1-WG-0243"], by_source_ref,
    ) == "1584887177251"
    assert gc.configured_wortgruppe_key(
        "B1-WG-0255", merges["B1-WG-0255"], by_source_ref,
    ) == "1584887177160"


def test_reviewed_note_merge_fails_closed_on_wrong_identity():
    survivor = gc.new_record("A1-WG-WRONG", "wrong", "A1")
    survivor.update({"note_id": 1, "is_new": False, "cards": []})
    duplicate = gc.new_record("B1-WG-0241", "1 m", "B1")
    duplicate.update({"note_id": 2, "is_new": False, "cards": []})
    with pytest.raises(gc.CompletionError, match="reviewed merge survivor identity mismatch"):
        gc.apply_reviewed_note_merges({"1": survivor, "2": duplicate}, [], [{
            "survivor": 1,
            "survivor_source_ref": "A1-WG-0093",
            "duplicate": 2,
            "duplicate_source_ref": "B1-WG-0241",
        }])


def test_reindex_record_discards_stale_lemma_after_canonicalisation():
    record = gc.new_record("A1-WG-0102", "ein Gramm", "A1")
    records = {"1": record}
    index = gc.variant_index(records)
    record["fields"]["Lemma"] = "Gramm"
    record["fields"]["AcceptedAnswersDE"] = "Gramm"
    gc.reindex_record(index, "1", record)
    assert gc.find_record(records, index, "Gramm") == "1"
    assert gc.find_record(records, index, "ein Gramm") is None


def test_b1_speed_unit_keeps_full_lemma_and_reviewed_translation():
    row = next(
        item for item in gc.parse_wortgruppen(gc.WG_FILES["B1"])
        if item["id"] == "B1-WG-0254"
    )
    assert (gc.wg_lemma(row), gc.wg_answers(row), row["variants"]) == (
        "1 km/h", ["1 km/h"], "",
    )
    overrides = json.loads(gc.B1_ENGLISH_OVERRIDES.read_text(encoding="utf-8"))
    assert overrides["B1-WG-0254"]["meaning_en"] == "one kilometre per hour"


def test_wortgruppe_gender_variants_become_accepted_answers():
    row = {"entry": "Prüfer, - / Prüferin, -nen", "detail": "", "match": ""}
    assert gc.wg_answers(row) == ["Prüfer", "Prüferin"]


def test_enriched_wortgruppe_uses_canonical_grammar(tmp_path):
    path = tmp_path / "wg.md"
    path.write_text(
        "| A2-WG-0001 | die Schweiz |  | A2 | 1 |  | Land | Schweiz | n. | die | f. |  |  | Normally used with its article. | https://www.duden.de/rechtschreibung/Schweiz |\n",
        encoding="utf-8",
    )
    row = gc.parse_wortgruppen(path)[0]
    assert gc.wg_lemma(row) == "Schweiz"
    assert gc.wg_answers(row) == ["Schweiz"]


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


def test_source_text_overrides_join_pdf_line_wrap_and_fix_display_typo():
    overrides = gc.load_source_text_overrides()["examples"]
    assert overrides["A1-MAIN-0363"][-1] == "Im Zug fahre ich immer 2. Klasse."
    assert overrides["A2-MAIN-0855"][-1] == "Ich finde den Film schrecklich. Er macht mir Angst."
    assert overrides["A2-MAIN-0617"] == ["Sag mal, wie gefällt dir mein neues Kleid?"]


def test_shared_da_source_routes_only_darauf_example_to_darauf_note():
    policy = gc.load_redundancy_policy()
    source_overrides = gc.load_source_text_overrides()["examples"]
    row = {
        "examples": [
            "Darauf fällt mir keine Antwort ein.",
            "Darüber spreche ich nicht gern.",
        ],
    }

    assert policy["source_targets"]["A2-MAIN-0201"] == 1497484860928
    assert "A2-MAIN-0201" not in source_overrides
    assert gc.main_source_examples(
        "A2-MAIN-0201", row, source_overrides,
        policy["main_source_example_overrides"],
    ) == ["Darauf fällt mir keine Antwort ein."]


def test_redundancy_policy_preserves_reviewed_content_twins_and_routes_phrases():
    policy = gc.load_redundancy_policy()
    assert 1584886454573 in policy["preserve_note_ids"]
    assert policy["source_targets"]["A1-MAIN-0658"] == 1584886455225


def test_redundancy_policy_routes_reviewed_main_source_aliases():
    policy = gc.load_redundancy_policy()
    aliases = policy["main_source_aliases"]
    assert len(aliases) == 15
    assert aliases["B1-MAIN-0255"] == "B1-MAIN-0252"
    assert aliases["B1-MAIN-0539"] == "A1-MAIN-0159"
    assert aliases["B1-MAIN-1855"] == "A2-MAIN-0734"


def test_configured_main_source_alias_fails_closed_until_target_exists():
    with pytest.raises(gc.CompletionError, match="main source alias target missing"):
        gc.configured_main_source_key(
            "B1-MAIN-0255",
            {"B1-MAIN-0255": "B1-MAIN-0252"},
            {},
            {},
            {},
        )
    assert gc.configured_main_source_key(
        "B1-MAIN-0255",
        {"B1-MAIN-0255": "B1-MAIN-0252"},
        {},
        {"1": {}},
        {"B1-MAIN-0252": "1"},
    ) == "1"


def test_completion_uses_same_level_example_whitelist():
    allowed = gc.goethe_source_examples.allowed_examples_by_level()
    examples = [
        {"de": "Hast du die Tür abgeschlossen?", "en": "lock", "audio": "a"},
        {"de": "Darf ich Ihnen ein Stück Kuchen anbieten?", "en": "offer", "audio": "b"},
    ]
    assert gc.goethe_source_examples.filter_examples("A2", examples, allowed) == [examples[0]]


def test_build_manifest_renders_filtered_examples_before_english_audit(monkeypatch):
    record = gc.new_record("A1-TEST", "Bekannte", "A1", "n.", "m./f.")
    record["fields"]["MeaningEN"] = "acquaintance"
    record["examples"] = [
        {"de": "Keep me.", "en": "Keep me.", "audio": "keep.mp3"},
        {"de": "Drop me.", "en": "Drop me.", "audio": "drop.mp3"},
    ]
    gc.goethe_examples.render_fields(record["fields"], record["examples"])
    assert record["fields"]["Example2DE"] == "Drop me."

    monkeypatch.setattr(gc, "load_live", lambda: ({"1": record}, {}))
    monkeypatch.setattr(gc, "load_redundancy_policy", lambda: {
        "skip_wortgruppen": [],
        "merge_wortgruppen": {},
        "preserve_note_ids": [],
        "source_targets": {},
        "main_source_aliases": {},
    })
    monkeypatch.setattr(gc, "load_source_text_overrides", lambda: {"examples": {}})
    monkeypatch.setattr(gc, "apply_headword_policy", lambda records, deletions: deletions)
    monkeypatch.setattr(gc.gw, "parse_markdown", lambda path: [])
    monkeypatch.setattr(gc, "parse_wortgruppen", lambda path: [])
    monkeypatch.setattr(
        gc.goethe_source_examples,
        "allowed_examples_by_level",
        lambda: {level: {} for level in gc.LEVELS},
    )
    monkeypatch.setattr(
        gc.goethe_source_examples,
        "filter_examples",
        lambda level, examples, allowed: examples[:1],
    )
    monkeypatch.setattr(gc.english_audit, "validate_manifest", lambda manifest: None)
    monkeypatch.setattr(gc.english_audit, "load_json", lambda path: {
        "entries": {
            "A1-TEST": {
                "source_id": "A1-TEST",
                "cefr": "A1",
                "desired_examples": [],
            },
        },
    })

    def assert_filtered_fields(records, manifest, *, strict):
        assert records["1"]["fields"]["Example1DE"] == "Keep me."
        assert records["1"]["fields"]["Example2DE"] == ""

    monkeypatch.setattr(gc.english_audit, "apply_manifest_to_records", assert_filtered_fields)
    monkeypatch.setattr(gc, "apply_translation_cache", lambda records: None)
    monkeypatch.setattr(gc, "apply_b1_english_overrides", lambda records: None)
    monkeypatch.setattr(gc, "apply_b1_data_overrides", lambda records: None)
    monkeypatch.setattr(gc, "finalize_template_fields", lambda records: None)

    gc.build_manifest()
