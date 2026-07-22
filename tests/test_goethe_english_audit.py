from __future__ import annotations

import copy
import json
import sys
from functools import lru_cache
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_english_audit as audit  # noqa: E402


@lru_cache(maxsize=1)
def manifest() -> dict:
    return audit.load_json(audit.MANIFEST)


def test_v4_catalog_covers_the_fully_reviewed_canonical_a1_b1_corpus():
    catalog = manifest()
    audit.validate_scaffold(catalog)

    assert catalog["schema_version"] == 4
    assert catalog["counts"] == {
        "notes": audit.goethe_scope.EXPECTED_NOTES,
        "reviewed": audit.goethe_scope.EXPECTED_NOTES,
        "unreviewed": 0,
        "keep": 1572,
        "revise": 1921,
        "pending": 0,
        "meaning_updates": 1584,
        "example_updates": 1058,
        "no_examples": audit.goethe_scope.EXPECTED_EMPTY_NOTES,
        "b1_no_examples": audit.goethe_scope.EXPECTED_EMPTY_NOTES_BY_LEVEL["B1"],
        "ambiguous_prompt_groups": 0,
        "a1": audit.goethe_scope.EXPECTED_NOTES_BY_LEVEL["A1"],
        "a2": audit.goethe_scope.EXPECTED_NOTES_BY_LEVEL["A2"],
        "b1": audit.goethe_scope.EXPECTED_NOTES_BY_LEVEL["B1"],
    }


def test_full_validation_accepts_the_complete_reviewed_catalog():
    assert audit.audit_blockers(manifest()) == {}
    audit.validate_manifest(manifest())


def test_every_row_has_one_stable_canonical_identity_without_note_id_guards():
    entries = list(manifest()["entries"].values())
    expected = audit.goethe_scope.EXPECTED_NOTES
    assert len({entry["source_id"] for entry in entries}) == expected
    assert len({entry["stable_guid"] for entry in entries}) == expected
    assert all(entry["source_id"] in entry["source_refs"] for entry in entries)
    assert all("note_id_guard" not in entry for entry in entries)


def test_a1_a2_v3_rows_are_collapsed_into_current_canonical_notes():
    entries = manifest()["entries"]
    lower = [entry for entry in entries.values() if entry["cefr"] in {"A1", "A2"}]
    collapsed = [entry for entry in lower if len(entry["collapsed_v3_source_ids"]) > 1]

    assert len(lower) == 1525
    assert all(entry["review_status"] == "reviewed" for entry in lower)
    assert all(entry["evidence"] for entry in lower)
    assert len(collapsed) == 5
    assert sum(len(entry["collapsed_v3_source_ids"]) - 1 for entry in collapsed) == 6
    assert entries["A1-84886454531"]["collapsed_v3_source_ids"] == [
        "A1-84886454531", "A2-0102", "A2-0103",
    ]


def test_all_b1_batches_are_reviewed_and_legacy_material_remains_only_a_hint():
    b1 = [entry for entry in manifest()["entries"].values() if entry["cefr"] == "B1"]

    assert len(b1) == audit.goethe_scope.EXPECTED_NOTES_BY_LEVEL["B1"]
    assert all(entry["decision"] in {"KEEP", "REVISE"} for entry in b1)
    assert all(entry["review_status"] == "reviewed" for entry in b1)
    assert all(entry["evidence"] for entry in b1)
    hinted = [entry for entry in b1 if "legacy_hints" in entry]
    assert hinted
    assert all(
        entry["legacy_hints"]["classification"] == "hint_only_not_review_evidence"
        for entry in hinted
    )


def test_b1_review_batches_are_bounded_and_preserve_no_example_exceptions():
    b1 = [entry for entry in manifest()["entries"].values() if entry["cefr"] == "B1"]
    batches = {entry["audit_batch"] for entry in b1}

    assert batches == {f"B1-{index:02d}" for index in range(1, 9)}
    assert max(sum(entry["audit_batch"] == batch for entry in b1) for batch in batches) <= 250
    assert sum(not entry["desired_examples"] for entry in b1) == (
        audit.goethe_scope.EXPECTED_EMPTY_NOTES_BY_LEVEL["B1"]
    )


def test_b1_wortgruppe_contrast_has_canonical_grammar_and_gloss():
    entry = manifest()["entries"]["B1-WG-0161"]
    assert entry["lemma"] == "hell-, dunkel-"
    assert entry["pos"] == "adj."
    assert entry["desired_meaning_en"] == "light-; dark- (colour prefixes)"


def test_known_v3_reviewed_senses_survive_the_canonical_migration():
    entries = manifest()["entries"]
    assert entries["A2-WG-0130"]["desired_meaning_en"] == "New Year's Eve"
    assert entries["A2-WG-0130-NEUJAHR"]["desired_meaning_en"] == "New Year's Day"
    assert entries["A2-0851"]["desired_meaning_en"] == (
        "to moan/complain (about); to tell someone off"
    )
    assert entries["A1-84886454810"]["desired_meaning_en"] == "warm; sincere"
    assert len(entries["A1-84886454810"]["evidence"]) >= 2


def test_confirmed_a2_translation_repairs_are_canonical():
    entries = manifest()["entries"]

    abitur = entries["A2-WG-0092"]
    assert abitur["expected_meaning_en"] == "Abitur; school-leaving qualification"
    assert abitur["desired_meaning_en"] == (
        "German school-leaving examination; university entrance qualification"
    )
    assert abitur["desired_examples"] == [{
        "de": "Nach dem Abitur möchte sie studieren.",
        "en": (
            "After taking her school-leaving examination, she would like to go "
            "to university."
        ),
        "origin": "review-authored",
    }]

    mailbox = entries["A2-0615"]
    assert mailbox["expected_meaning_en"] == "mailbox"
    assert mailbox["desired_meaning_en"] == "voicemail"
    assert mailbox["decision"] == "REVISE"
    assert mailbox["difficult"] is True
    assert {item["provider"] for item in mailbox["evidence"]} >= {"Cambridge", "Duden"}

    museum = entries["A2-0661"]
    assert museum["desired_examples"][0]["en"] == (
        "There is a new exhibition at the art museum."
    )
    assert museum["decision"] == "REVISE"

    consultation = entries["A2-0919"]
    assert consultation["desired_examples"][0]["en"] == (
        "Dr Weiß has office hours from 9:00 am to 12:30 pm."
    )
    assert consultation["decision"] == "REVISE"

    jeans = entries["A2-0520"]
    assert jeans["expected_meaning_en"] == "Jeans"
    assert jeans["desired_meaning_en"] == "jeans"
    assert jeans["decision"] == "REVISE"

    assert entries["A2-WG-0003"]["desired_meaning_en"] == "ICE"


def test_reviewed_lieblings_example_and_all_current_german_examples_are_retained():
    entries = manifest()["entries"]
    assert sum(len(entry["desired_examples"]) for entry in entries.values()) == 4318
    assert sum(
        len(entry["desired_examples"])
        for entry in entries.values() if entry["cefr"] == "A1"
    ) == 995
    assert entries["A1-84886454917"]["desired_examples"][-1] == {
        "de": "Meine Lieblingsfarbe ist Blau.",
        "en": "My favourite colour is blue.",
        "origin": "goethe",
    }


def test_identity_check_accepts_reviewed_canonical_inflection_overrides_only():
    assert audit.identity_matches_reviewed_lemma({
        "Lemma": "ander-", "AcceptedAnswersDE": "ander|anderen",
    }, "anderen")
    assert audit.identity_matches_reviewed_lemma({
        "Lemma": "anziehen", "AcceptedAnswersDE": "sich anziehen",
    }, "(sich) anziehen")
    assert not audit.identity_matches_reviewed_lemma({
        "Lemma": "Bahn", "AcceptedAnswersDE": "Bahn",
    }, "Bus")


def test_find_entry_uses_stable_guid_when_source_id_changes():
    entry = manifest()["entries"]["A2-0851"]
    fields = {
        "SourceID": "RENAMED",
        "LegacyGUID": entry["stable_guid"],
    }
    assert audit.find_entry(fields, manifest()) is entry


def test_identity_equivalent_accepts_historical_alias_and_ref_reordering():
    catalog = manifest()
    entry = catalog["entries"]["A1-84886454470"]
    fields = {
        "SourceID": "A1-84886454472",
        "SourceRefs": "|".join(reversed(entry["source_refs"])),
        "LegacyGUID": entry["stable_guid"],
        "Lemma": entry["lemma"],
        "AcceptedAnswersDE": entry["lemma"],
        "CEFR": entry["cefr"],
    }
    assert audit.identity_equivalent(fields, entry, catalog)


def test_identity_equivalent_accepts_only_html_escaped_guid_representation():
    catalog = manifest()
    entry = catalog["entries"]["A2-0192"]
    fields = {
        "SourceID": entry["source_id"],
        "SourceRefs": "|".join(entry["source_refs"]),
        "LegacyGUID": entry["stable_guid"].replace(">", "&gt;"),
        "Lemma": entry["lemma"],
        "AcceptedAnswersDE": entry["lemma"],
        "CEFR": entry["cefr"],
    }

    assert audit.identity_equivalent(fields, entry, catalog)
    fields["LegacyGUID"] = "not-the-reviewed-guid"
    assert not audit.identity_equivalent(fields, entry, catalog)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong_guid"])
def test_identity_equivalent_rejects_provenance_or_guid_drift(mutation):
    catalog = manifest()
    entry = catalog["entries"]["A1-84886454470"]
    refs = list(entry["source_refs"])
    if mutation == "missing":
        refs = refs[:-1]
    elif mutation == "extra":
        refs.append("A1-NOT-A-REAL-REF")
    elif mutation == "duplicate":
        refs.append(refs[-1])
    fields = {
        "SourceID": entry["source_id"],
        "SourceRefs": "|".join(refs),
        "LegacyGUID": (
            catalog["entries"]["A1-84886454531"]["stable_guid"]
            if mutation == "wrong_guid" else entry["stable_guid"]
        ),
        "Lemma": entry["lemma"],
        "AcceptedAnswersDE": entry["lemma"],
        "CEFR": entry["cefr"],
    }
    assert not audit.identity_equivalent(fields, entry, catalog)


def test_desired_fields_canonicalises_alias_identity():
    catalog = manifest()
    entry = catalog["entries"]["A1-84886454470"]
    fields = {name: "" for name in audit.gw.FIELDS}
    fields.update({
        "SourceID": "A1-84886454472",
        "SourceRefs": "|".join(reversed(entry["source_refs"])),
        "LegacyGUID": entry["stable_guid"],
        "Lemma": entry["lemma"],
        "MeaningEN": entry["expected_meaning_en"],
        "CEFR": entry["cefr"],
    })
    audit.goethe_examples.render_fields(fields, entry["expected_examples"])
    desired = audit.desired_fields(fields, entry)
    assert desired["SourceID"] == entry["source_id"]
    assert desired["SourceRefs"] == "|".join(entry["source_refs"])


def test_desired_fields_is_idempotent_and_preserves_audio_by_unchanged_german():
    entry = manifest()["entries"]["A2-0851"]
    fields = {name: "" for name in audit.gw.FIELDS}
    fields.update({
        "SourceID": entry["source_id"],
        "LegacyGUID": entry["stable_guid"],
        "Lemma": entry["lemma"],
        "CEFR": entry["cefr"],
        "MeaningEN": entry["expected_meaning_en"],
    })
    audit.goethe_examples.render_fields(fields, entry["expected_examples"])
    fields["Example1Audio"] = "[sound:test.mp3]"

    desired = audit.desired_fields(fields, entry)
    assert desired["Example1Audio"] == "[sound:test.mp3]"
    assert audit.desired_fields(copy.deepcopy(desired), entry) == desired


def test_scaffold_rejects_any_change_to_german_example_text():
    catalog = copy.deepcopy(manifest())
    entry = catalog["entries"]["A2-0851"]
    entry["desired_examples"][0]["de"] += " changed"

    with pytest.raises(audit.AuditError, match="changed German example text"):
        audit.validate_scaffold(catalog)


def test_tag_transition_clears_all_legacy_english_and_review_tags():
    tags = [
        "keep-me", audit.OLD_VERIFIED_TAG, audit.OLD_AUDITED_TAG,
        audit.V3_AUDITED_TAG, audit.REVIEW_TAG,
    ]
    assert audit.desired_tags(tags) == ["goethe::quality::english_audited::v4::british", "keep-me"]


def test_uncovered_and_pending_records_are_marked_for_review_not_audited():
    fields = {name: "" for name in audit.gw.FIELDS}
    fields.update({"SourceID": "NEW", "LegacyGUID": "new", "MeaningEN": "new"})
    records = {"new": {"fields": fields, "examples": [], "tags": [audit.V3_AUDITED_TAG]}}
    audit.apply_manifest_to_records(records, manifest(), strict=False)
    assert records["new"]["tags"] == [audit.REVIEW_TAG]

    pending_source_id = "B1-SYNTHETIC-PENDING"
    pending_entry = {
        "source_id": pending_source_id,
        "stable_guid": f"goethe:{pending_source_id}",
        "review_status": "unreviewed",
        "decision": "PENDING",
    }
    pending_fields = {name: "" for name in audit.gw.FIELDS}
    pending_fields.update({
        "SourceID": pending_source_id,
        "LegacyGUID": pending_entry["stable_guid"],
        "MeaningEN": "pending",
    })
    records = {
        "pending": {
            "fields": pending_fields,
            "examples": [],
            "tags": [audit.AUDITED_TAG],
        },
    }
    audit.apply_manifest_to_records(
        records,
        {"entries": {pending_source_id: pending_entry}},
        strict=False,
    )
    assert records["pending"]["tags"] == [audit.REVIEW_TAG]


def test_review_validator_requires_two_domains_for_difficult_entries():
    entry = copy.deepcopy(manifest()["entries"]["A1-84886454810"])
    entry["evidence"] = [entry["evidence"][0]]
    entry["difficult"] = True
    assert "difficult_needs_two_domains" in audit._review_entry_errors(entry)


def test_review_validator_rejects_known_us_spellings():
    entry = copy.deepcopy(manifest()["entries"]["A2-0851"])
    entry["desired_meaning_en"] = "favorite color"
    assert "non_british_spelling" in audit._review_entry_errors(entry)


def test_evidence_validator_rejects_wrong_provider_host_and_missing_difficulty():
    entry = copy.deepcopy(manifest()["entries"]["A2-0851"])
    entry["evidence"][0]["url"] = "https://example.invalid/dictionary/german-english/test"
    entry.pop("difficult", None)
    errors = audit._review_entry_errors(entry)
    assert "invalid_evidence" in errors
    assert "difficult_not_explicit" in errors


def test_b1_evidence_requires_bilingual_support_and_duden_for_difficult_rows():
    entry = copy.deepcopy(manifest()["entries"]["B1-MAIN-0002"])
    entry.update({"decision": "KEEP", "review_status": "reviewed", "difficult": False})
    entry["evidence"] = [{
        "provider": "Duden",
        "url": "https://www.duden.de/rechtschreibung/abbiegen",
        "supports": "Confirms the German verb sense and part of speech.",
    }]
    assert "missing_bilingual_evidence" in audit._review_entry_errors(entry)

    entry["difficult"] = True
    entry["evidence"] = [
        {
            "provider": "Cambridge",
            "url": "https://dictionary.cambridge.org/dictionary/german-english/abbiegen",
            "supports": "Confirms the bilingual sense.",
        },
        {
            "provider": "Collins",
            "url": "https://www.collinsdictionary.com/dictionary/german-english/abbiegen",
            "supports": "Independent bilingual sense check.",
        },
    ]
    assert "difficult_needs_duden" in audit._review_entry_errors(entry)


def test_batch_report_is_a_strict_row_and_collision_gate():
    rows = copy.deepcopy(list(manifest()["entries"].values()))
    evidence = [{
        "provider": "Cambridge",
        "url": "https://dictionary.cambridge.org/dictionary/german-english/abbiegen",
        "supports": "Direct bilingual entry used by this synthetic validation fixture.",
    }]
    for index, entry in enumerate(rows):
        if entry.get("audit_batch") != "B1-01":
            continue
        meaning = (
            f"to review sense {index}"
            if str(entry.get("pos", "")).casefold().startswith("v.")
            else f"reviewed sense {index}"
        )
        entry.update({
            "desired_meaning_en": meaning,
            "decision": "REVISE",
            "review_status": "reviewed",
            "difficult": False,
            "reason": "Synthetic row-specific review fixture.",
            "evidence": evidence,
        })
        for example_index, example in enumerate(entry["desired_examples"]):
            example["en"] = f"Reviewed example {index}-{example_index}."
    catalog = audit.manifest_from_rows(rows)

    report = audit.batch_report(catalog, "B1-01")

    assert report["rows"] == 250
    assert report["examples"] == 320
    assert report["reviewed"] == 250
    assert report["blockers"] == {}
    assert report["internal_collision_groups"] == []
    assert report["cross_batch_collision_groups"] == []


def test_scaffold_refuses_to_overwrite_reviewed_b1_without_force(tmp_path):
    output = tmp_path / "catalog.jsonl"
    output.write_text(json.dumps({
        "source_id": "B1-X", "cefr": "B1", "review_status": "reviewed",
    }) + "\n", encoding="utf-8")

    with pytest.raises(audit.AuditError, match="refusing to overwrite reviewed B1 rows"):
        audit.guard_scaffold_overwrite(output, force=False)
    audit.guard_scaffold_overwrite(output, force=True)


def test_british_validator_allows_tire_as_a_verb_but_rejects_us_tyre_noun():
    entry = copy.deepcopy(manifest()["entries"]["A2-0851"])
    entry["pos"] = "v."
    entry["desired_meaning_en"] = "to tire"
    entry["desired_examples"] = [{
        "de": "Die Arbeit strengt mich an.",
        "en": "The work tires me.",
        "origin": "review-authored",
    }]
    assert "non_british_spelling" not in audit._review_entry_errors(entry)

    entry["desired_examples"][0]["en"] = "Check the front tires."
    assert "non_british_spelling" in audit._review_entry_errors(entry)


def test_reviewed_b1_rows_reject_additional_us_learner_vocabulary():
    entry = copy.deepcopy(manifest()["entries"]["B1-MAIN-0002"])
    entry.update({
        "decision": "REVISE",
        "review_status": "reviewed",
        "difficult": False,
        "evidence": [{
            "provider": "Cambridge",
            "url": "https://dictionary.cambridge.org/dictionary/german-english/abbiegen",
            "supports": "Confirms the bilingual verb sense.",
        }],
    })
    entry["desired_examples"][0]["en"] = "My neighbor took the elevator."
    assert "non_british_spelling" in audit._review_entry_errors(entry)

    entry["desired_meaning_en"] = "wallet; billfold"
    entry["desired_examples"][0]["en"] = "The wallet is empty."
    assert "non_british_spelling" in audit._review_entry_errors(entry)

    entry["desired_meaning_en"] = "car park"
    entry["desired_examples"][0]["en"] = "The parking lot is full."
    assert "non_british_spelling" in audit._review_entry_errors(entry)


def test_reviewed_b1_rows_enforce_decision_verb_and_gender_gloss_conventions():
    entry = copy.deepcopy(manifest()["entries"]["B1-MAIN-0002"])
    assert "decision_mismatch" not in audit._review_entry_errors(entry)
    assert "noncanonical_verb_gloss" not in audit._review_entry_errors(entry)

    entry["decision"] = "KEEP"
    assert "decision_mismatch" in audit._review_entry_errors(entry)

    entry["decision"] = "REVISE"
    entry["desired_meaning_en"] = "turn at a junction"
    assert "noncanonical_verb_gloss" in audit._review_entry_errors(entry)

    entry["pos"] = "n."
    entry["desired_meaning_en"] = "female guide"
    assert "noncanonical_gender_gloss" in audit._review_entry_errors(entry)

    entry["desired_meaning_en"] = "guide (female)"
    assert "noncanonical_gender_gloss" in audit._review_entry_errors(entry)

    entry["desired_meaning_en"] = "(female) guide"
    assert "noncanonical_gender_gloss" not in audit._review_entry_errors(entry)

    entry["desired_meaning_en"] = "guide service; (female) guide"
    assert "noncanonical_gender_gloss" not in audit._review_entry_errors(entry)


def test_verify_scope_requires_pilot_targets_and_preserves_other_notes(monkeypatch):
    entries = {
        source_id: copy.deepcopy(manifest()["entries"][source_id])
        for source_id in ("B1-MAIN-0002", "A2-0851")
    }
    catalog = {"entries": entries}

    def fields(entry):
        result = {name: "" for name in audit.gw.FIELDS}
        result.update({
            "SourceID": entry["source_id"],
            "SourceRefs": "|".join(entry["source_refs"]),
            "LegacyGUID": entry["stable_guid"],
            "Lemma": entry["lemma"],
            "AcceptedAnswersDE": entry["lemma"],
            "CEFR": entry["cefr"],
            "MeaningEN": entry["expected_meaning_en"],
        })
        audit.goethe_examples.render_fields(result, entry["expected_examples"])
        return result

    before = {source_id: fields(entry) for source_id, entry in entries.items()}
    snapshot = {"notes": {
        "1": {"fields": before["B1-MAIN-0002"], "tags": [audit.REVIEW_TAG]},
        "2": {"fields": before["A2-0851"], "tags": [audit.V3_AUDITED_TAG]},
    }}
    records = {
        1: {
            "fields": audit.desired_fields(before["B1-MAIN-0002"], entries["B1-MAIN-0002"]),
            "tags": audit.desired_tags([audit.REVIEW_TAG]),
        },
        2: {"fields": before["A2-0851"], "tags": [audit.V3_AUDITED_TAG]},
    }
    monkeypatch.setattr(audit, "verify_protected_collection", lambda *_: None)

    assert audit.verify_applied_scope(
        records, catalog, snapshot, {"B1-MAIN-0002"},
    ) == []
    assert audit.verify_applied_scope(
        records, catalog, snapshot, set(entries),
    ) == [2]


def test_cli_exposes_batch_gate_scaffold_guard_and_scoped_verify():
    parser = audit.build_parser()
    assert parser.parse_args(["check-batch", "--batch", "B1-01"]).batch == "B1-01"
    assert parser.parse_args(["verify"]).scope == "full"
    assert parser.parse_args(["verify", "--scope", "pilot"]).scope == "pilot"
    assert parser.parse_args([
        "scaffold", "--force-overwrite-reviewed",
    ]).force_overwrite_reviewed is True
