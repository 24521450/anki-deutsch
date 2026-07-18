from __future__ import annotations

import copy
import json
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
    assert manifest["counts"]["notes"] == 1531
    assert manifest["counts"]["a1"] == 818
    assert manifest["counts"]["a2"] == 713
    assert manifest["counts"]["keep"] + manifest["counts"]["revise"] == 1531
    assert manifest["counts"]["ambiguous_prompt_groups"] == 0
    assert sum(not entry["desired_examples"] for entry in manifest["entries"].values()) == 57
    assert all(entry["review_status"] == "reviewed" for entry in manifest["entries"].values())
    assert all(entry["evidence"] for entry in manifest["entries"].values())
    assert all(
        not entry["difficult"] or len({item["url"].split("/")[2] for item in entry["evidence"]}) >= 2
        for entry in manifest["entries"].values()
    )


def test_neujahr_and_silvester_have_distinct_reviewed_senses():
    entries = audit.load_json(audit.MANIFEST)["entries"]
    silvester = entries["A2-WG-0130"]
    neujahr = entries["A2-WG-0130-NEUJAHR"]

    assert silvester["lemma"] == "Silvester"
    assert silvester["note_id_guard"] == 1783863836345
    assert silvester["desired_meaning_en"] == "New Year's Eve"
    assert neujahr["lemma"] == "Neujahr"
    assert neujahr["note_id_guard"] == 1784375306336
    assert neujahr["desired_meaning_en"] == "New Year's Day"
    assert neujahr["desired_examples"] == [{
        "de": "Neujahr fällt in diesem Jahr auf einen Mittwoch.",
        "en": "New Year's Day falls on a Wednesday this year.",
        "origin": "review-authored",
    }]


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


def test_audited_glosses_have_no_dictionary_placeholders_or_spaced_slashes():
    manifest = audit.load_json(audit.MANIFEST)
    meanings = [entry["desired_meaning_en"] for entry in manifest["entries"].values()]
    assert not any("sth." in value or "so." in value for value in meanings)
    assert not any(" / " in value for value in meanings)


def test_every_retained_example_was_reaudited_with_explicit_origin():
    entries = audit.load_json(audit.MANIFEST)["entries"].values()
    examples = [example for entry in entries for example in entry["desired_examples"]]
    assert len(examples) == 2035
    assert sum(example["origin"] == "review-authored" for example in examples) == 149
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


def test_achtung_is_one_contextual_example_and_turkei_uses_standard_english():
    entries = audit.load_json(audit.MANIFEST)["entries"]
    assert entries["A1-MAIN-0008"]["desired_examples"] == [{
        "de": "Achtung! Das dürfen Sie nicht tun.",
        "en": "Watch out! You're not allowed to do that.",
        "origin": "goethe",
    }]
    assert entries["A1-WG-0107"]["desired_meaning_en"] == "Turkey"
    assert entries["A1-WG-0107"]["desired_examples"] == [{
        "de": "Sie kommt aus der Türkei.",
        "en": "She comes from Turkey.",
        "origin": "review-authored",
    }]
    translations = json.loads((ROOT / "review" / "goethe_completion_translations.json").read_text(encoding="utf-8"))
    assert translations["Türkei"] == "Turkey"


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


def test_pair_state_ignores_untranslated_duplicate_of_a_merged_dialogue():
    entry = audit.load_json(audit.MANIFEST)["entries"]["A1-84886454639"]
    current = copy.deepcopy(entry["expected_examples"])
    current.append({
        "de": "Hin und zurück?<br>– Nein, bitte nur einfach.",
        "en": "",
        "origin": "goethe",
    })

    assert audit.pair_state(current, entry) == "expected"


def test_audit_tracks_reviewed_canonical_merge_glosses_and_lieblings_example():
    entries = audit.load_json(audit.MANIFEST)["entries"]
    assert {
        source_id: entries[source_id]["desired_meaning_en"]
        for source_id in (
            "A1-84886454612", "A1-84886454914",
            "A1-84886455036", "A2-MAIN-0202",
        )
    } == {
        "A1-84886454612": "disco; discotheque",
        "A1-84886454914": "dear; kind",
        "A1-84886455036": "reception; front desk",
        "A2-MAIN-0202": "with you; present or included; while doing so",
    }
    assert entries["A1-84886454917"]["desired_examples"][-1] == {
        "de": "Meine Lieblingsfarbe ist Blau.",
        "en": "My favourite colour is blue.",
        "origin": "goethe",
    }

    retained_merge_examples = {
        "A1-84886454531": "Eine Bekannte von mir wohnt in Köln.",
        "A1-84886454612": "Wir gehen heute Abend in die Disko(thek).",
        "A1-84886454788": "Zum Mittagessen gibt es Hühnchen mit Reis.",
        "A1-84886454914": "Liebe Frau Meier!",
        "A1-84886454963": "Bis nächstes Mal!",
        "A1-84886455036": "Geben Sie bitte den Schlüssel an der Rezeption ab.",
        "A1-84886455149": "Tschüs, bis morgen!",
        "A1-84886455204": "Ich will dir nicht wehtun.",
        "A1-84886455228": "Herzlich willkommen in Köln.",
        "A2-0647": "Seid ihr am Wochenende zu Hause? – Ja, meistens.",
        "A2-0759": "Lass uns eine Pizza bestellen!",
        "A2-MAIN-0202": "Was hast du dir dabei gedacht?",
    }
    for source_id, german in retained_merge_examples.items():
        assert german in {item["de"] for item in entries[source_id]["desired_examples"]}


def test_uncovered_record_is_marked_for_review_not_verified():
    fields = {name: "" for name in audit.gw.FIELDS}
    fields.update({"SourceID": "NEW", "LegacyGUID": "new", "MeaningEN": "new"})
    records = {"new": {"fields": fields, "examples": [], "tags": [audit.OLD_VERIFIED_TAG]}}
    audit.apply_manifest_to_records(records, audit.load_json(audit.MANIFEST), strict=False)
    assert audit.REVIEW_TAG in records["new"]["tags"]
    assert audit.AUDITED_TAG not in records["new"]["tags"]


def test_strict_coverage_accepts_merged_alias_source_refs_without_applying_alias_content():
    full_manifest = audit.load_json(audit.MANIFEST)
    canonical = copy.deepcopy(full_manifest["entries"]["A1-84886454963"])
    alias = copy.deepcopy(full_manifest["entries"]["A2-0679"])
    fields = {name: "" for name in audit.gw.FIELDS}
    fields.update({
        "SourceID": canonical["source_id"],
        "SourceRefs": f"{canonical['source_id']}|{alias['source_id']}",
        "MeaningEN": canonical["expected_meaning_en"],
    })
    audit.goethe_examples.render_fields(fields, canonical["expected_examples"])
    records = {"merged": {"fields": fields, "examples": [], "tags": []}}

    audit.apply_manifest_to_records(
        records,
        {"entries": {canonical["source_id"]: canonical, alias["source_id"]: alias}},
        strict=True,
    )

    assert records["merged"]["fields"]["MeaningEN"] == canonical["desired_meaning_en"]
