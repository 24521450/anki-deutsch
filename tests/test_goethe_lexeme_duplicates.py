from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_lexeme_duplicates as audit  # noqa: E402


def item(note_id: int, lemma: str, meaning: str, pos: str, level: str = "A1", audio: str = "") -> dict:
    return {
        "note_id": note_id,
        "fields": {
            "Lemma": lemma, "MeaningEN": meaning, "POS": pos, "CEFR": level,
            "AcceptedAnswersDE": lemma, "WordAudio": audio,
        },
        "cards": [], "tags": [],
    }


def test_candidate_keys_cover_qualifier_spacing_and_combining_marker() -> None:
    assert "bekannte" in audit.candidate_keys("Bekannte (weiblich)")
    assert audit.orthographic_key("weh tun") == audit.orthographic_key("wehtun")
    assert audit.orthographic_key("lieb-") == audit.orthographic_key("lieb")


def test_bekannte_is_merge_proposed_with_lowest_level_survivor() -> None:
    group = [
        item(1, "Bekannte", "acquaintance", "n.", "A1"),
        item(2, "Bekannte (weiblich)", "female acquaintance", "n.", "A2"),
        item(3, "Bekannte (männlich)", "male acquaintance", "n.", "A2"),
    ]
    result = audit.classify_group(group)
    assert result["decision"] == "MERGE_PROPOSED"
    assert audit.choose_survivor(group)["note_id"] == 1


def test_spelling_variant_with_same_pos_and_meaning_is_merge_proposed() -> None:
    group = [
        item(1, "weh tun", "to hurt", "v.", "A1"),
        item(2, "wehtun", "to hurt", "v.", "A2"),
    ]
    assert audit.classify_group(group)["decision"] == "MERGE_PROPOSED"


def test_case_and_pos_homographs_are_kept_separate() -> None:
    group = [item(1, "Arm", "arm", "n."), item(2, "arm", "poor", "adj.", "A2")]
    assert audit.classify_group(group)["decision"] == "KEEP_SEPARATE_HOMOGRAPH"


def test_source_combining_form_with_different_meaning_is_kept_separate() -> None:
    group = [item(1, "groß", "tall; large", "adj."), item(2, "Groß-", "grand-", "adj.", "B1")]
    assert audit.classify_group(group)["decision"] == "KEEP_SEPARATE_HOMOGRAPH"


def test_case_distinguished_pronouns_with_different_meanings_are_kept_separate() -> None:
    group = [item(1, "sie", "she", "pron."), item(2, "Sie", "you (polite)", "pron.")]
    assert audit.classify_group(group)["decision"] == "KEEP_SEPARATE_HOMOGRAPH"


def test_noun_verb_conversion_is_kept_even_when_english_tokens_overlap() -> None:
    group = [item(1, "Vertrauen", "trust", "n."), item(2, "vertrauen", "to trust", "v.", "B1")]
    assert audit.classify_group(group)["decision"] == "KEEP_SEPARATE_HOMOGRAPH"


def test_overlapping_pos_labels_for_same_lexeme_can_merge() -> None:
    group = [item(1, "willkommen", "welcome", "interj."), item(2, "willkommen", "welcome", "adj., interj.", "B1")]
    assert audit.classify_group(group)["decision"] == "MERGE_PROPOSED"


def test_overlapping_pos_labels_with_distinct_senses_are_kept_separate() -> None:
    group = [item(1, "klar", "of course", "interj."), item(2, "klar", "clear", "adj., interj.", "B1")]
    assert audit.classify_group(group)["decision"] == "KEEP_SEPARATE_HOMOGRAPH"


def test_reviewed_live_group_can_override_bad_source_pos() -> None:
    group = [
        item(1783863833253, "dabei", "with you; there; among them", "v.", "A2"),
        item(1784075521894, "dabei", "while doing so; present or included; with one", "adv.", "B1"),
    ]
    assert audit.classify_group(group)["decision"] == "MERGE_PROPOSED"


def test_all_non_survivors_are_deleted_regardless_of_review_history() -> None:
    survivor = item(1, "Bekannte", "acquaintance", "n.")
    reviewed = item(2, "Bekannte (weiblich)", "female acquaintance", "n.", "A2")
    reviewed["cards"] = [{"reps": 2, "review_count": 2}]
    fresh = item(3, "Bekannte (männlich)", "male acquaintance", "n.", "A2")
    actions = audit.proposed_actions([survivor, reviewed, fresh], survivor)
    assert actions == {1: "SURVIVE", 2: "DELETE_AFTER_APPROVAL", 3: "DELETE_AFTER_APPROVAL"}


def test_derivational_gender_pair_is_out_of_scope_even_when_answers_overlap() -> None:
    female = item(1, "Hausfrau", "housewife", "n.")
    male = item(2, "Hausmann", "househusband", "n.")
    female["fields"]["AcceptedAnswersDE"] = "Hausfrau|Hausmann"
    male["fields"]["AcceptedAnswersDE"] = "Hausfrau|Hausmann"
    assert audit.candidate_groups([female, male]) == []


def test_article_in_lemma_is_a_same_lexeme_candidate() -> None:
    assert len(audit.candidate_groups([
        item(1, "die Bank", "bank", "n."), item(2, "Bank", "bank", "n."),
    ])) == 1


def test_explicit_reviewed_spelling_variants_are_candidates() -> None:
    records = [
        item(1784075508824, "Bancomat", "ATM", "n.", "B1"),
        item(1784075509014, "Bankomat", "ATM", "n.", "B1"),
    ]
    assert [[record["note_id"] for record in group] for group in audit.candidate_groups(records)] == [
        [1784075508824, 1784075509014],
    ]


def test_pending_reviewed_merge_set_has_thirteen_groups_and_fourteen_deletions() -> None:
    assert len(audit.EXPECTED_MERGE_GROUPS) == 13
    assert sum(len(group) - 1 for group in audit.EXPECTED_MERGE_GROUPS) == 14
    expected_note_ids = {
        note_id for group in audit.EXPECTED_MERGE_GROUPS for note_id in group
    }
    assert set(audit.EXPECTED_CARD_IDS) == expected_note_ids


def test_explicit_meist_group_cannot_absorb_distinct_meist_combining_form() -> None:
    records = [
        item(1497484861364, "meistens", "mostly", "adv.", "A2"),
        item(1784075584927, "meist", "mostly", "adv.", "B1"),
        item(999, "meist-", "most", "det.", "B1"),
    ]
    groups = [[record["note_id"] for record in group] for group in audit.candidate_groups(records)]
    assert groups == [[1497484861364, 1784075584927]]


def test_merged_fields_keep_provenance_best_audio_and_unique_examples() -> None:
    survivor = item(1584886455204, "weh tun", "to hurt", "v.", "A1", "[sound:_goethe_word_edge_a.mp3]")
    duplicate = item(1497484861859, "wehtun", "to hurt", "v.", "A2", "[sound:_goethe_word_duden_b.mp3]")
    survivor["fields"].update({"SourceRefs": "A1-MAIN-1", "AcceptedAnswersDE": "weh tun"})
    duplicate["fields"].update({"SourceRefs": "A2-MAIN-2|B1-MAIN-3", "AcceptedAnswersDE": "wehtun"})
    survivor["examples"] = [{"de": "Wo tut es weh?", "en": "Where does it hurt?", "audio": ""}]
    duplicate["examples"] = [
        {"de": "Wo tut es weh?", "en": "Where does it hurt?", "audio": "audio-a"},
        {"de": "Mir tut der Rücken weh.", "en": "My back hurts.", "audio": "audio-b"},
    ]
    fields = audit.merged_fields([duplicate, survivor], survivor)
    assert fields["Lemma"] == "wehtun"
    assert fields["AcceptedAnswersDE"] == "wehtun|weh tun"
    assert fields["SourceRefs"] == "A1-MAIN-1|A2-MAIN-2|B1-MAIN-3"
    assert fields["WordAudio"] == "[sound:_goethe_word_duden_b.mp3]"
    assert fields["Example1Audio"] == "audio-a"
    assert fields["Example2DE"] == "Mir tut der Rücken weh."


def test_merged_bancomat_fields_enable_production_and_highlight_alias_inflection() -> None:
    survivor = item(
        1784075508824, "Bancomat", "ATM", "n.", "B1",
        "[sound:_goethe_word_edge_a.mp3]",
    )
    duplicate = item(
        1784075509014, "Bankomat", "ATM", "n.", "B1",
        "[sound:_goethe_word_commons_b.mp3]",
    )
    survivor["fields"].update({
        "SourceID": "B1-MAIN-0252", "SourceRefs": "B1-MAIN-0252",
        "Article": "der", "Gender": "m.", "AcceptedArticlesDE": "der",
        "NounFormsRaw": "-en", "ExampleTargetSpansJSON": "",
    })
    duplicate["fields"].update({
        "SourceID": "B1-MAIN-0255", "SourceRefs": "B1-MAIN-0255",
        "Article": "der", "Gender": "m.", "AcceptedArticlesDE": "der",
        "NounFormsRaw": "-en (A, CH);", "ExampleTargetSpansJSON": "",
    })
    survivor["examples"] = duplicate["examples"] = [{
        "de": "Ich hole Geld vom Bankomaten.", "en": "I get money from the ATM.", "audio": "audio",
    }]

    fields = audit.merged_fields([survivor, duplicate], survivor)

    assert fields["AcceptedAnswersDE"] == "Bancomat|Bankomat"
    assert fields["NounFormsRaw"] == "-en"
    assert fields["ProductionEnabled"] == "1"
    assert fields["AcceptedFullAnswersDE"] == "der Bancomat|der Bankomat"
    assert json.loads(fields["ExampleTargetSpansJSON"]) == [[[18, 28]]]


def test_export_backup_always_requests_a_fresh_scheduled_package(tmp_path, monkeypatch) -> None:
    previous = tmp_path / "Goethe_Institute_pre_lexeme_merge_previous.apkg"
    with zipfile.ZipFile(previous, "w") as archive:
        archive.writestr("collection.anki2", b"old")
    calls = []

    def fake_anki(action, **params):
        calls.append((action, params))
        path = Path(params["path"])
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("collection.anki2", b"fresh")
        return True

    monkeypatch.setattr(audit, "STATE", tmp_path)
    monkeypatch.setattr(audit.gw, "anki", fake_anki)

    backup = audit.export_backup()

    assert backup != previous
    assert calls == [("exportPackage", {
        "deck": audit.PARENT_DECK,
        "path": backup.resolve().as_posix(),
        "includeSched": True,
    })]


def test_inventory_signature_is_independent_of_card_return_order() -> None:
    record = item(1, "Test", "test", "n.")
    record["cards"] = [{"cardId": 2, "ord": 1}, {"cardId": 1, "ord": 0}]
    reversed_record = item(1, "Test", "test", "n.")
    reversed_record["cards"] = list(reversed(record["cards"]))
    assert audit.inventory_signature([record]) == audit.inventory_signature([reversed_record])
