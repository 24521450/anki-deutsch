from __future__ import annotations

import copy
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_english_audit as audit  # noqa: E402


def test_manifest_covers_every_current_note_with_a_decision_and_sources():
    manifest = audit.load_json(audit.MANIFEST)
    audit.validate_manifest(manifest)
    assert manifest["schema_version"] == 3
    assert manifest["counts"]["notes"] == 1530
    assert manifest["counts"]["a1"] == 818
    assert manifest["counts"]["a2"] == 712
    assert manifest["counts"]["keep"] + manifest["counts"]["revise"] == 1530
    assert manifest["counts"]["ambiguous_prompt_groups"] == 0
    assert sum(not entry["desired_examples"] for entry in manifest["entries"].values()) == 66
    assert all(entry["review_status"] == "reviewed" for entry in manifest["entries"].values())
    assert all(entry["evidence"] for entry in manifest["entries"].values())
    assert all(
        not entry["difficult"] or len({item["url"].split("/")[2] for item in entry["evidence"]}) >= 2
        for entry in manifest["entries"].values()
    )


def test_audited_glosses_have_no_dictionary_placeholders_or_spaced_slashes():
    manifest = audit.load_json(audit.MANIFEST)
    meanings = [entry["desired_meaning_en"] for entry in manifest["entries"].values()]
    assert not any("sth." in value or "so." in value for value in meanings)
    assert not any(" / " in value for value in meanings)


def test_every_retained_example_was_reaudited_with_explicit_origin():
    entries = audit.load_json(audit.MANIFEST)["entries"].values()
    examples = [example for entry in entries for example in entry["desired_examples"]]
    assert len(examples) == 2008
    assert sum(example["origin"] == "review-authored" for example in examples) == 139
    assert not any(" a author" in example["en"] for example in examples)
    assert not any(" a artist" in example["en"] for example in examples)
    assert not any(" a actor" in example["en"] for example in examples)
    assert not any(re.search(r"\btires?\b", example["en"], re.I) for example in examples)


def test_schimpfen_uses_contextual_cambridge_senses():
    entry = audit.load_json(audit.MANIFEST)["entries"]["A2-0851"]
    assert entry["desired_meaning_en"] == "to moan/complain (about); to tell someone off"
    assert entry["desired_examples"][0]["en"] == "Why are you complaining so loudly? — I'm annoyed about my car."
    assert entry["evidence"][0]["url"] == "https://dictionary.cambridge.org/dictionary/german-english/schimpfen"


def test_herzlich_has_a_general_learner_gloss_not_the_context_bound_heartfelt():
    entry = audit.load_json(audit.MANIFEST)["entries"]["A1-84886454810"]
    assert entry["desired_meaning_en"] == "warm; sincere"
    assert entry["desired_examples"] == [{
        "de": "Herzlichen Glückwunsch!",
        "en": "Congratulations!",
        "origin": "goethe",
    }]
    assert len(entry["evidence"]) >= 2


def test_desired_fields_is_idempotent_and_preserves_audio():
    manifest = audit.load_json(audit.MANIFEST)
    entry = manifest["entries"]["A2-0851"]
    fields = {name: "" for name in audit.gw.FIELDS}
    fields.update({
        "SourceID": "A2-0851",
        "Lemma": "schimpfen",
        "MeaningEN": entry["expected_meaning_en"],
        "Example1DE": entry["expected_examples"][0]["de"],
        "Example1EN": entry["expected_examples"][0]["en"],
        "Example1Audio": "[sound:test.mp3]",
        "Example2DE": entry["expected_examples"][1]["de"],
        "Example2EN": entry["expected_examples"][1]["en"],
    })
    desired = audit.desired_fields(fields, entry)
    assert desired["Example1Audio"] == "[sound:test.mp3]"
    assert audit.desired_fields(copy.deepcopy(desired), entry) == desired
    downstream_audio = copy.deepcopy(desired)
    downstream_audio["Example1Audio"] = "[sound:edge.mp3]"
    assert audit.audit_projection(downstream_audio) == audit.audit_projection(desired)


def test_uncovered_record_is_marked_for_review_not_verified():
    fields = {name: "" for name in audit.gw.FIELDS}
    fields.update({"SourceID": "NEW", "LegacyGUID": "new", "MeaningEN": "new"})
    records = {"new": {"fields": fields, "examples": [], "tags": [audit.OLD_VERIFIED_TAG]}}
    audit.apply_manifest_to_records(records, audit.load_json(audit.MANIFEST), strict=False)
    assert audit.REVIEW_TAG in records["new"]["tags"]
    assert audit.AUDITED_TAG not in records["new"]["tags"]
