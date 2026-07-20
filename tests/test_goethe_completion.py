from __future__ import annotations

import json
import copy
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_completion as gc  # noqa: E402


def noun_fields(**overrides):
    fields = {name: "" for name in gc.gw.FIELDS}
    fields.update({
        "SourceID": "A2-WG-0111",
        "Lemma": "Religion",
        "POS": "n.",
        "Article": "die",
        "Gender": "f.",
        "AcceptedArticlesDE": "die",
        "AcceptedFullAnswersDE": "die Religion",
        "ProductionEnabled": "1",
    })
    fields.update(overrides)
    return fields


def complete_card(note_id, card_id, ord, deck):
    return {
        "cardId": card_id, "note": note_id, "ord": ord, "deckName": deck,
        "factor": 0, "interval": 0, "type": 0, "queue": 0, "due": card_id,
        "reps": 0, "lapses": 0, "left": 0, "flags": 0, "mod": 1,
    }


def live_record(note_id=1, level="A1"):
    record = gc.new_record(f"{level}-MAIN-{note_id:04d}", "testen", level, "v.")
    record.update({
        "note_id": note_id,
        "is_new": False,
        "model": gc.MODEL,
        "cards": [
            complete_card(note_id, note_id * 10, 0, gc.LEVEL_DECK[level]),
            complete_card(note_id, note_id * 10 + 1, 1, gc.LEVEL_DECK[level]),
        ],
    })
    record["fields"]["MeaningEN"] = "to test"
    record["fields"]["SourceRefs"] = record["fields"]["SourceID"]
    gc.render_examples(record)
    return record


def test_completion_noun_policy_requires_learner_facing_article_answers():
    gc.validate_noun_fields(noun_fields())
    with pytest.raises(gc.CompletionError, match="noun article policy failed"):
        gc.validate_noun_fields(noun_fields(Article=""))
    with pytest.raises(gc.CompletionError, match="AcceptedFullAnswersDE missing article"):
        gc.validate_noun_fields(noun_fields(AcceptedFullAnswersDE="Religion"))

    gc.validate_noun_fields(noun_fields(
        SourceID="A1-WG-0105", Lemma="Deutschland", Article="",
        Gender="n.", AcceptedArticlesDE="", AcceptedFullAnswersDE="Deutschland",
    ))
    gc.validate_noun_fields(noun_fields(
        SourceID="B1-MAIN-1299", Lemma="Karotte", Article="die",
        Gender="f.", AcceptedArticlesDE="die", AcceptedFullAnswersDE="",
        ProductionEnabled="",
    ))


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
    b1 = gc.parse_wortgruppen(gc.WG_FILES["B1"])
    assert len(a1) == 121
    assert len(a2) == 225
    assert len(b1) == 355
    assert a1[0]["id"] == "A1-WG-0001"
    assert a2[-1]["id"] == "A2-WG-0225"
    assert b1[-1]["id"] == "B1-WG-0355"
    assert a1[0]["category"] == "Zahlen"


def test_wortgruppen_canonical_lemmas_are_complete_and_keep_b1_regional_variants():
    rows = [
        row
        for level in gc.LEVELS
        for row in gc.parse_wortgruppen(gc.WG_FILES[level])
    ]
    assert all(
        row["canonical"].count("(") == row["canonical"].count(")")
        for row in rows
    )
    by_id = {row["id"]: row for row in rows}
    assert gc.wg_lemma(by_id["B1-WG-0033"]) == "Chat(room)"
    assert gc.wg_lemma(by_id["B1-WG-0123"]).endswith("(AHS)")
    assert gc.wg_lemma(by_id["B1-WG-0124"]).endswith("(BHS)")
    assert gc.wg_answers(by_id["B1-WG-0309"]) == ["Januar", "Jänner"]
    assert gc.wg_answers(by_id["B1-WG-0310"]) == ["Februar", "Feber"]


def test_numeric_wortgruppe_uses_spoken_german_as_lemma():
    row = {"entry": "1", "detail": "eins", "match": ""}
    assert gc.wg_lemma(row) == "eins"


def test_reviewed_measure_merges_delete_exact_b1_notes_and_route_collision():
    policy = gc.load_redundancy_policy()
    groups = policy["reviewed_note_merges"]
    expected = {
        1784527085549: 1584886454651,
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


def test_b1_email_routes_into_existing_a1_note_instead_of_creating_a_split_child():
    policy = gc.load_redundancy_policy()
    assert not any(
        child.get("source_id") == "B1-WG-0066"
        for group in policy["reviewed_note_splits"]
        for child in group.get("children", [group.get("child", {})])
    )
    merge = next(
        group for group in policy["reviewed_note_merges"]
        if group["duplicate_source_ref"] == "B1-WG-0066"
    )
    assert merge == {
        "survivor": 1584886454651,
        "survivor_source_ref": "A1-84886454651",
        "duplicate": 1784527085549,
        "duplicate_source_ref": "B1-WG-0066",
    }
    production = gc.production_policy.load_policy()["answers"]
    assert "B1-WG-0066" not in production
    assert production["A1-84886454651"] == (
        "die E-Mail|das E-Mail|die Email|das Email"
    )
    correction = gc.review_policy.load_policy()["records"]["A1-84886454651"]["set"]
    assert correction["AcceptedAnswersDE"] == "E-Mail|Email"
    assert correction["AcceptedArticlesDE"] == "die|das"

    survivor = gc.new_record("A1-84886454651", "E-Mail", "A1", "n.", "f.")
    survivor.update({"note_id": merge["survivor"], "is_new": False, "cards": []})
    duplicate = gc.new_record("B1-WG-0066", "E-Mail", "B1", "n.", "f./n.")
    duplicate.update({"note_id": merge["duplicate"], "is_new": False, "cards": []})
    records = {str(merge["survivor"]): survivor, str(merge["duplicate"]): duplicate}

    deletions = gc.apply_reviewed_note_merges(records, [], [merge])
    assert [(item["note_id"], item["survivor"]) for item in deletions] == [
        (1784527085549, 1584886454651),
    ]
    index = gc.variant_index(records)
    assert gc.find_record(records, index, "E-Mail", "n.", "f./n.") == "1584886454651"


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


def reviewed_holiday_split() -> list[dict[str, object]]:
    return [{
        "source_ref": "A2-WG-0130",
        "survivor_note_id": 1,
        "expected_lemma": "Neujahr/Silvester",
        "expected_source_refs": ["A2-WG-0130", "B1-WG-0299", "B1-WG-0304"],
        "survivor": {
            "source_id": "A2-WG-0130",
            "source_refs": ["A2-WG-0130", "B1-WG-0304"],
            "field_overrides": {
                "Lemma": "Silvester",
                "Article": "das/der",
                "Gender": "n./m.",
                "AcceptedAnswersDE": "Silvester",
                "AcceptedArticlesDE": "das|der",
                "WordAudio": "silvester.mp3",
            },
        },
        "child": {
            "source_id": "A2-WG-0130-NEUJAHR",
            "coverage_ref": "A2-WG-0130",
            "source_refs": ["A2-WG-0130-NEUJAHR", "B1-WG-0299"],
            "cefr": "A2",
            "field_overrides": {
                "Lemma": "Neujahr",
                "Article": "das",
                "Gender": "n.",
                "AcceptedAnswersDE": "Neujahr",
                "AcceptedArticlesDE": "das",
                "WordAudio": "neujahr.mp3",
                "OriginalOrder": "A2-WG-0130",
                "SourceNoteRaw": "A2-WG-0130 (Neujahr)",
                "LegacyGUID": "goethe:A2-WG-0130-NEUJAHR",
            },
        },
    }]


def combined_holiday_record() -> dict[str, object]:
    record = gc.new_record("A2-WG-0130", "Neujahr/Silvester", "A2", "n.", "n.")
    record.update({"note_id": 1, "is_new": False, "cards": [{"reps": 3}]})
    record["source_refs"] = ["A2-WG-0130", "B1-WG-0299", "B1-WG-0304"]
    record["fields"]["MeaningEN"] = "New Year; New Year's Eve"
    record["examples"] = [{
        "de": "An Silvester feiern wir das neue Jahr.",
        "en": "We celebrate the new year on New Year's Eve.",
        "audio": "combined-example.mp3",
    }]
    gc.render_examples(record)
    return record


def test_reviewed_note_split_is_idempotent_before_and_after_apply():
    records = {"1": combined_holiday_record()}
    aliases = gc.apply_reviewed_note_splits(records, reviewed_holiday_split())

    assert aliases == {"A2-WG-0130-NEUJAHR": "A2-WG-0130"}
    assert records["1"]["fields"]["Lemma"] == "Silvester"
    assert records["1"]["source_refs"] == ["A2-WG-0130", "B1-WG-0304"]
    child_key = "new:A2-WG-0130-NEUJAHR"
    child = records[child_key]
    assert child["is_new"] is True
    assert child["note_id"] is None
    assert child["cards"] == []
    assert child["fields"]["SourceID"] == "A2-WG-0130-NEUJAHR"
    assert child["fields"]["LegacyGUID"] == "goethe:A2-WG-0130-NEUJAHR"
    assert child["fields"]["Lemma"] == "Neujahr"
    assert child["source_refs"] == ["A2-WG-0130-NEUJAHR", "B1-WG-0299"]
    assert child["fields"]["MeaningEN"] == ""
    assert child["examples"] == []
    assert child["fields"]["Example1Audio"] == ""

    assert gc.apply_reviewed_note_splits(records, reviewed_holiday_split()) == aliases
    assert set(records) == {"1", child_key}

    child = records.pop(child_key)
    child.update({"note_id": 2, "is_new": False, "cards": [{"reps": 0}]})
    records["2"] = child
    assert gc.apply_reviewed_note_splits(records, reviewed_holiday_split()) == aliases
    assert set(records) == {"1", "2"}


def test_reviewed_note_split_supports_multiple_children():
    policy = json.loads(gc.REDUNDANCY_POLICY.read_text(encoding="utf-8"))
    split = next(
        group
        for group in policy["reviewed_note_splits"]
        if group["source_ref"] == "A2-WG-0089"
    )
    note_id = split["survivor_note_id"]
    survivor = gc.new_record("A2-WG-0089", "Schweiz", "A2", "n.", "f.")
    survivor.update({"note_id": note_id, "is_new": False, "cards": [{"reps": 3}]})
    survivor["source_refs"] = list(split["expected_source_refs"])
    records = {str(note_id): survivor}

    aliases = gc.apply_reviewed_note_splits(records, [split])

    assert aliases == {
        "B1-WG-0130": "B1-WG-0130",
        "B1-WG-0131": "B1-WG-0131",
    }
    assert records[str(note_id)]["source_refs"] == ["A2-WG-0089", "B1-WG-0181"]
    assert {
        key: record["fields"]["Lemma"]
        for key, record in records.items()
        if key.startswith("new:")
    } == {
        "new:B1-WG-0130": "Schweiz: Sekundarstufe I",
        "new:B1-WG-0131": "Schweiz: Sekundarstufe II",
    }
    assert gc.apply_reviewed_note_splits(records, [split]) == aliases
    assert len(records) == 3


@pytest.mark.parametrize("bad_field", ["expected_lemma", "expected_source_refs"])
def test_reviewed_note_split_fails_closed_on_bad_combined_guard(bad_field):
    split = reviewed_holiday_split()
    if bad_field == "expected_lemma":
        split[0][bad_field] = "Silvester"
    else:
        split[0][bad_field] = ["A2-WG-0130", "B1-WG-0304"]
    with pytest.raises(gc.CompletionError, match="reviewed split combined identity mismatch"):
        gc.apply_reviewed_note_splits({"1": combined_holiday_record()}, split)


def test_reviewed_note_split_aliases_child_to_physical_source_coverage():
    survivor = combined_holiday_record()
    records = {"1": survivor}
    aliases = gc.apply_reviewed_note_splits(records, reviewed_holiday_split())
    for record in records.values():
        record["fields"]["MeaningEN"] = "reviewed"
        record["fields"]["SourceRefs"] = "|".join(record["source_refs"])
        record["fields"]["AcceptedFullAnswersDE"] = (
            "das Silvester|der Silvester"
            if record["fields"]["Lemma"] == "Silvester"
            else "das Neujahr"
        )
    manifest = {
        "records": records,
        "deletions": [],
        "source_counts": {"A2_WG": 3},
        "skipped_source_refs": [],
        "source_coverage_aliases": aliases,
    }
    assert gc.validate_manifest(manifest, strict_corpus=False)["source_refs"] == 3


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


def test_find_record_prefers_lowest_level_then_reps_and_note_id():
    def item(note_id, level, reps):
        record = gc.new_record(f"{level}-X", "Bank", level, "n.", "f.")
        record.update({"note_id": note_id, "is_new": False, "cards": [{"reps": reps}]})
        return record

    records = {
        "3": item(3, "B1", 100),
        "2": item(2, "A1", 4),
        "1": item(1, "A1", 4),
    }
    assert gc.find_record(records, gc.variant_index(records), "Bank", "n.", "f.") == "1"


def test_casefold_fallback_uses_pos_to_keep_arm_senses_separate():
    noun = gc.new_record("A1-X", "Arm", "A1", "n.", "m.")
    adjective = gc.new_record("A2-X", "arm", "A2", "adj.")
    records = {"1": noun, "2": adjective}
    index = gc.variant_index(records)
    assert gc.find_record(records, index, "Arm", "n.", "m.") == "1"
    assert gc.find_record(records, index, "arm", "adj.") == "2"


def test_exact_duplicate_merge_keeps_lowest_level_and_merges_useful_audio():
    def item(note_id, level, reps):
        record = gc.new_record(f"{level}-X", "zum Beispiel", level)
        record.update({"note_id": note_id, "is_new": False, "cards": [{"reps": reps}], "deck": gc.LEVEL_DECK[level]})
        record["fields"].update({"MeaningEN": "for example", "POS": "phrase", "CEFR": level})
        return record
    records = {"1": item(1, "A1", 2), "2": item(2, "A2", 10)}
    records["2"]["fields"]["WordAudio"] = "duplicate.mp3"
    records["2"]["examples"] = [{"de": "Ein Beispiel.", "en": "An example.", "audio": "example.mp3"}]
    records["1"]["examples"] = [{"de": "Ein Beispiel.", "en": "", "audio": ""}]
    deleted = gc.merge_exact_duplicates(records)
    assert list(records) == ["1"]
    assert records["1"]["fields"]["CEFR"] == "A1"
    assert records["1"]["fields"]["WordAudio"] == "duplicate.mp3"
    assert records["1"]["examples"] == [{
        "de": "Ein Beispiel.", "en": "An example.", "audio": "example.mp3",
    }]
    assert deleted[0]["note_id"] == 2


def test_apply_cli_has_direct_delete_policy_and_deprecated_b1_is_non_mutating(monkeypatch):
    args = gc.build_parser().parse_args(["apply", "--confirmation", gc.CONFIRMATION, "--skip-new"])
    assert not hasattr(args, "keep_duplicates")
    assert args.skip_new is True
    with pytest.raises(SystemExit):
        gc.build_parser().parse_args([
            "apply", "--confirmation", gc.CONFIRMATION, "--keep-duplicates",
        ])

    monkeypatch.setattr(gc.gw, "anki", lambda *args, **kwargs: pytest.fail("must not call Anki"))
    deprecated = gc.build_parser().parse_args(["apply-b1"])
    with pytest.raises(gc.CompletionError, match="deprecated and non-mutating"):
        deprecated.func(deprecated)


def test_apply_deletes_every_scheduled_duplicate(monkeypatch, tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "records": {},
        "deletions": [{"note_id": 7, "survivor": 3}],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(gc, "MANIFEST", manifest_path)
    monkeypatch.setattr(gc, "load_manifest_artifact", lambda: (manifest, "file", "payload"))
    monkeypatch.setattr(gc, "validate_manifest", lambda manifest: {"records": 0})
    monkeypatch.setattr(gc, "anki_multi", lambda actions: None)
    monkeypatch.setattr(gc, "export_apply_backup", lambda *args, **kwargs: {
        "backup": "backup.apkg", "backup_sha256": "hash",
    })
    monkeypatch.setattr(gc, "require_manifest_file_hash", lambda expected: None)
    monkeypatch.setattr(gc, "load_live", lambda: ({}, {}))
    monkeypatch.setattr(gc, "verify_apply_inventory", lambda *args: None)
    monkeypatch.setattr(gc, "verify_pre_delete_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(gc, "verify_post_apply_state", lambda *args, **kwargs: {"notes": 0, "cards": 0})
    monkeypatch.setattr(gc, "build_live_preimage", lambda live: {"schema_version": 1, "notes": {}})
    monkeypatch.setattr(gc, "RESULT", tmp_path / "result.json")
    calls = []

    def anki(action, **params):
        calls.append((action, params))
        if action == "version":
            return 6
        if action == "modelFieldNames":
            return gc.gw.FIELDS
        if action == "deleteNotes":
            return None
        raise AssertionError(action)

    monkeypatch.setattr(gc.gw, "anki", anki)
    args = gc.build_parser().parse_args([
        "apply", "--confirmation", gc.CONFIRMATION, "--skip-new",
    ])
    args.func(args)

    assert ("deleteNotes", {"notes": [7]}) in calls


def test_apply_inventory_includes_survivors_and_exact_deletion_cards():
    manifest = {
        "records": {
            "1": {
                "note_id": 1, "is_new": False,
                "cards": [{"cardId": 10}, {"cardId": 11}],
            },
            "new:X": {"note_id": None, "is_new": True, "cards": []},
        },
        "deletions": [{
            "note_id": 2, "survivor": 1,
            "cards": [{"cardId": 20}, {"cardId": 21}],
        }],
    }
    assert gc.expected_apply_inventory(manifest) == ({1, 2}, {10, 11, 20, 21})


def test_v2_preimage_rejects_field_tag_and_card_schedule_drift():
    record = live_record()
    manifest = {
        "version": gc.MANIFEST_VERSION,
        "records": {"1": record},
        "deletions": [],
        "live_preimage": gc.build_live_preimage({"1": record}),
    }
    for mutate in (
        lambda value: value["fields"].__setitem__("MeaningEN", "changed"),
        lambda value: value["tags"].append("unexpected"),
        lambda value: value["cards"][0].__setitem__("reps", 99),
        lambda value: value["cards"][0].__setitem__("note", 999),
    ):
        changed = copy.deepcopy(record)
        mutate(changed)
        with pytest.raises(gc.CompletionError, match="preimage|association"):
            gc.verify_apply_inventory(manifest, {"1": changed}, {})


def test_load_live_uses_notes_info_card_links_and_fails_on_linkage_drift(monkeypatch):
    record = live_record()

    def note_payload(note_id, record):
        return {
            "noteId": note_id,
            "modelName": gc.MODEL,
            "fields": {name: {"value": value} for name, value in record["fields"].items()},
            "tags": record["tags"],
            "cards": [card["cardId"] for card in record["cards"]],
        }

    calls = []

    def anki(action, **params):
        calls.append((action, params))
        if action == "version":
            return 6
        if action == "findNotes":
            return [1]
        if action == "notesInfo":
            return [note_payload(1, record)]
        if action == "cardsInfo":
            return record["cards"]
        raise AssertionError(action)

    monkeypatch.setattr(gc.gw, "anki", anki)
    loaded, by_note = gc.load_live()
    assert set(loaded) == {"1"}
    assert [action for action, _ in calls] == ["version", "findNotes", "notesInfo", "cardsInfo"]
    assert {card["cardId"] for card in by_note[1]} == {10, 11}

    def bad_notes(action, **params):
        if action == "version":
            return 6
        if action == "findNotes":
            return [1]
        if action == "notesInfo":
            payload = note_payload(1, record)
            payload["cards"] = [10, 999]
            return [payload]
        if action == "cardsInfo":
            return record["cards"]
        raise AssertionError(action)

    monkeypatch.setattr(gc.gw, "anki", bad_notes)
    with pytest.raises(gc.CompletionError, match="different card ID set"):
        gc.load_live()


def test_validate_manifest_does_not_mutate_rendered_fields():
    record = live_record()
    record["fields"]["ExampleTargetSpansJSON"] = ""
    manifest = {
        "records": {"1": record},
        "deletions": [],
        "source_counts": {"A1_MAIN": 1},
        "skipped_source_refs": [],
        "source_coverage_aliases": {},
    }
    before = copy.deepcopy(manifest)
    gc.validate_manifest(manifest, strict_corpus=False)
    assert manifest == before


def test_inspect_apkg_preimage_checks_sqlite_inventory_and_schedule(tmp_path):
    record = live_record()
    manifest = {
        "version": gc.MANIFEST_VERSION,
        "records": {"1": record},
        "deletions": [],
        "live_preimage": gc.build_live_preimage({"1": record}),
    }
    database_path = tmp_path / "collection.anki21"
    db = sqlite3.connect(database_path)
    db.executescript(
        """
        CREATE TABLE col (decks text);
        CREATE TABLE notes (id integer primary key);
        CREATE TABLE cards (
          id integer primary key, nid integer, did integer, ord integer,
          factor integer, ivl integer, type integer, queue integer, due integer,
          reps integer, lapses integer, left integer, flags integer, mod integer
        );
        """
    )
    db.execute("INSERT INTO col(decks) VALUES (?)", (json.dumps({"1": {"name": gc.LEVEL_DECK["A1"]}}),))
    db.execute("INSERT INTO notes(id) VALUES (1)")
    for card in record["cards"]:
        db.execute(
            "INSERT INTO cards VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card["cardId"], card["note"], card["ord"], card["factor"], card["interval"],
                card["type"], card["queue"], card["due"], card["reps"], card["lapses"],
                card["left"], card["flags"], card["mod"],
            ),
        )
    db.commit()
    db.close()
    apkg = tmp_path / "ok.apkg"
    with zipfile.ZipFile(apkg, "w") as archive:
        archive.write(database_path, "collection.anki21")
    gc.inspect_apkg_preimage(apkg, manifest)

    changed = copy.deepcopy(record)
    changed["cards"][0]["reps"] = 1
    bad_manifest = copy.deepcopy(manifest)
    bad_manifest["records"]["1"] = changed
    bad_manifest["live_preimage"] = gc.build_live_preimage({"1": changed})
    with pytest.raises(gc.CompletionError, match="scheduling differs"):
        gc.inspect_apkg_preimage(apkg, bad_manifest)


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
    record.update({
        "note_id": 1,
        "is_new": False,
        "model": gc.MODEL,
        "cards": [
            complete_card(1, 10, 0, gc.LEVEL_DECK["A1"]),
            complete_card(1, 11, 1, gc.LEVEL_DECK["A1"]),
        ],
    })
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
        "schema_version": 4,
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
    monkeypatch.setattr(
        gc, "apply_b1_english_overrides",
        lambda records: pytest.fail("legacy B1 English overrides must not be runtime authority"),
    )
    monkeypatch.setattr(gc, "apply_b1_data_overrides", lambda records: None)
    monkeypatch.setattr(gc, "finalize_template_fields", lambda records: None)

    gc.build_manifest()


def test_incomplete_v4_audit_is_reported_but_not_applied(monkeypatch, tmp_path):
    path = tmp_path / "goethe_english_audit_v4.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(gc.english_audit, "MANIFEST", path)
    monkeypatch.setattr(gc.english_audit, "load_json", lambda path: {
        "schema_version": 4,
        "entries": {
            "B1-X": {"source_id": "B1-X", "review_status": "pending"},
        },
    })
    monkeypatch.setattr(
        gc.english_audit,
        "validate_manifest",
        lambda manifest: (_ for _ in ()).throw(gc.english_audit.AuditError("B1 audit pending")),
    )

    audit, state = gc.english_audit_for_build()

    assert audit is None
    assert state["ready"] is False
    assert state["uncovered"] == gc.scope.EXPECTED_NOTES
    assert state["error"] == "B1 audit pending"


def test_v4_audit_is_applied_atomically_as_final_authority(monkeypatch):
    record = gc.new_record("B1-X", "hell", "B1", "adj.")
    record["fields"]["MeaningEN"] = "legacy"
    records = {"1": record}
    manifest = {"entries": {"B1-X": {}}}
    state = {"ready": False, "error": "", "uncovered": 0}

    def apply(reviewed, audit_manifest, *, strict):
        assert strict is True
        reviewed["1"]["fields"]["MeaningEN"] = "light-coloured"

    monkeypatch.setattr(gc.english_audit, "apply_manifest_to_records", apply)
    result = gc.apply_final_english_audit(records, manifest, state)

    assert result["ready"] is True
    assert records["1"]["fields"]["MeaningEN"] == "light-coloured"


def test_strict_manifest_validation_blocks_incomplete_v4(monkeypatch):
    record = gc.new_record("A1-MAIN-0001", "testen", "A1", "v.")
    record.update({
        "note_id": 1,
        "is_new": False,
        "model": gc.MODEL,
        "cards": [
            complete_card(1, 10, 0, gc.LEVEL_DECK["A1"]),
            complete_card(1, 11, 1, gc.LEVEL_DECK["A1"]),
        ],
    })
    record["fields"]["MeaningEN"] = "to test"
    gc.render_examples(record)
    manifest = {
        "version": gc.MANIFEST_VERSION,
        "records": {"1": record},
        "deletions": [],
        "live_preimage": gc.build_live_preimage({"1": record}),
        "source_counts": {"A1_MAIN": 1},
        "skipped_source_refs": [],
        "source_coverage_aliases": {},
        "ambiguous": [],
        "english_audit": {
            "schema_version": 4,
            "entries": 1,
            "uncovered": 1,
            "ready": False,
            "error": "B1 audit pending",
        },
    }
    monkeypatch.setattr(gc.scope, "EXPECTED_NOTES", 1)
    monkeypatch.setattr(gc.scope, "EXPECTED_NOTES_BY_LEVEL", {
        "A1": 1, "A2": 0, "B1": 0,
    })

    with pytest.raises(gc.CompletionError, match="English audit v4 is not ready"):
        gc.validate_manifest(manifest)

    manifest["english_audit"].update({"uncovered": 0, "ready": True})
    assert gc.validate_manifest(manifest)["records"] == 1
