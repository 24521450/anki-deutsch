from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_template_policy as policy  # noqa: E402
import goethe_review_policy as review_policy  # noqa: E402


def fields(**values: str) -> dict[str, str]:
    result = {
        "SourceID": "TEST",
        "Lemma": "arbeiten",
        "MeaningEN": "to work",
        "CEFR": "A1",
        "POS": "v.",
        "Gender": "",
        "AcceptedAnswersDE": "arbeiten",
        "AcceptedArticlesDE": "",
        "Example1EN": "I work.",
    }
    result.update(values)
    return result


def test_review_policy_has_expected_reviewed_sets() -> None:
    loaded = policy.load_policy()
    assert len(loaded["answers"]) == 58
    assert len(loaded["production"]) == 181
    assert sum(rule["enabled"] == policy.DISABLED for rule in loaded["production"].values()) == 10
    assert loaded["answers"]["A1-84886454468"] == "der Arzt|die Ärztin"
    assert loaded["answers"]["A1-84887177212"] == "ein halb|halb"
    assert loaded["answers"]["A2-WG-0023"] == "der Angestellte|die Angestellte|Angestellter"
    assert loaded["answers"]["A2-0572"] == "die Kunst|die Kunsterziehung"
    assert loaded["production"]["B1-MAIN-0139"] == {"enabled": "1", "hint": "D/CH"}
    # Brieftasche stays active by default; its feminine Gender cue already
    # separates it from the masculine/neuter wallet variants.
    assert "B1-MAIN-0442" not in loaded["production"]
    assert loaded["production"]["B1-MAIN-1299"]["enabled"] == policy.DISABLED
    assert loaded["production"]["B1-WG-0015"] == {"enabled": "", "hint": ""}
    assert loaded["production"]["B1-WG-0161"] == {"enabled": "", "hint": ""}
    assert loaded["answers"]["B1-MAIN-0252"] == "der Bancomat|der Bankomat"


def test_derivation_splits_complete_alternatives_and_preserves_bound_forms() -> None:
    assert policy.split_full_answers("") == []
    assert policy.split_full_answers("der Arzt|die Ärztin") == ["der Arzt", "die Ärztin"]
    assert policy.derive_full_answers(fields()) == ["arbeiten"]
    assert policy.derive_full_answers(fields(
        Lemma="Bahn", AcceptedAnswersDE="Bahn", AcceptedArticlesDE="die"
    )) == ["die Bahn"]
    assert policy.derive_full_answers(fields(
        Lemma="Samstag/Sonnabend", AcceptedAnswersDE="Samstag/Sonnabend", Article="der"
    )) == ["der Samstag", "der Sonnabend"]
    assert policy.split_full_answers("Samstag/Sonnabend") == ["Samstag", "Sonnabend"]
    assert policy.split_full_answers("getrennt / geschieden") == ["getrennt", "geschieden"]
    assert policy.split_full_answers("Speise-/-speise") == ["Speise-/-speise"]
    assert policy.split_full_answers("fünf Minuten vor/nach eins") == [
        "fünf Minuten vor/nach eins"
    ]
    assert policy.derive_full_answers(fields(
        Lemma="Eltern", AcceptedAnswersDE="Eltern (Pl.)", AcceptedArticlesDE="die"
    )) == ["die Eltern"]
    # Capitalised ``Das`` starts a phrase here, not an article prefix.
    assert policy.derive_full_answers(fields(
        Lemma="Das macht nichts", AcceptedAnswersDE="Das macht nichts"
    )) == ["Das macht nichts"]

    with pytest.raises(policy.PolicyError, match="explicit override"):
        policy.derive_full_answers(fields(
            Lemma="Arzt", AcceptedAnswersDE="Arzt|Ärztin", AcceptedArticlesDE="der|die"
        ))


def test_half_card_accepts_both_source_alternatives() -> None:
    record = fields(
        SourceID="A1-84887177212",
        Lemma="ein halb; halb",
        AcceptedAnswersDE="ein halb; halb|ein halb",
        MeaningEN="half",
    )

    policy.apply_policy([{"fields": record}], strict=False)

    assert record["AcceptedFullAnswersDE"] == "ein halb|halb"


def test_explicit_overrides_keep_full_forms_and_weak_nouns() -> None:
    assert policy.derive_full_answers(
        fields(Lemma="Arzt"), override="der Arzt|die Ärztin"
    ) == ["der Arzt", "die Ärztin"]
    assert policy.derive_full_answers(
        fields(Lemma="Angestellter"),
        override="der Angestellte|die Angestellte|Angestellter",
    ) == ["der Angestellte", "die Angestellte", "Angestellter"]
    assert policy.derive_full_answers(
        fields(Lemma="Kunst"), override="die Kunst|die Kunsterziehung"
    ) == ["die Kunst", "die Kunsterziehung"]


def test_reviewed_reflexive_resolver_is_the_canonical_full_answer_source() -> None:
    source = fields(
        SourceID="A1-84886454459",
        SourceRefs="A1-84886454459|A1-MAIN-0038",
        Lemma="anziehen",
        AcceptedAnswersDE="sich anziehen",
        AcceptedFullAnswersDE="sich anziehen|s anziehen",
    )
    resolved = review_policy.resolve_reviewed_fields(source)

    assert resolved["Lemma"] == "(sich) anziehen"
    assert resolved["AcceptedAnswersDE"] == "anziehen|sich anziehen"
    assert resolved["AcceptedFullAnswersDE"] == "anziehen|sich anziehen|s anziehen"
    assert source["Lemma"] == "anziehen"


def test_reviewed_legacy_answer_cleanup_has_no_annotations_or_composites() -> None:
    expected = {
        "A1-84886454650": ("Eltern|Eltern (Pl.)", "Eltern"),
        "A1-84886454749": ("Geschwister|Geschwister (Pl.)", "Geschwister"),
        "A1-84886454773": ("Großeltern|Großeltern (Pl.)", "Großeltern"),
        "A1-84887177245": ("Frühling/das Frühjahr|Frühling|Frühjahr", "Frühling|Frühjahr"),
        "A2-0572": ("Kunst|Kunst(erziehung)", "Kunst|Kunsterziehung"),
        "A2-WG-0023": ("Angestellter|Angestellte", "Angestellte"),
        "A2-WG-0025": ("Auszubildender|Auszubildende", "Auszubildende"),
        "A2-WG-0173": ("Arbeitstag/Werktag|Arbeitstag|Werktag", "Arbeitstag|Werktag"),
        "B1-WG-0061": ("Ketchup|Ketschup|Ketchup/Ketschup", "Ketchup|Ketschup"),
    }
    loaded = review_policy.load_policy()
    for source_id, (before, after) in expected.items():
        record = fields(SourceID=source_id, AcceptedAnswersDE=before)
        assert review_policy.apply_fields(record, loaded) is True
        assert record["AcceptedAnswersDE"] == after


def test_lexical_cleanup_does_not_treat_existing_full_answer_as_review_override() -> None:
    source = fields(
        SourceID="A2-WG-0023",
        Lemma="Angestellter",
        AcceptedAnswersDE="Angestellter|Angestellte",
        AcceptedFullAnswersDE="stale reviewed value",
    )

    assert review_policy.reviewed_full_answer_override(source) is None


def test_apply_policy_uses_reflexive_full_answers_without_mutating_legacy_fields() -> None:
    active = fields(
        SourceID="A1-84886454459",
        SourceRefs="A1-84886454459|A1-MAIN-0038",
        Lemma="anziehen",
        AcceptedAnswersDE="sich anziehen",
        AcceptedFullAnswersDE="sich anziehen|s anziehen",
        MeaningEN="to put on",
        Example1EN="I put on my coat.",
    )
    legacy = {
        key: value for key, value in active.items()
        if key not in {"AcceptedFullAnswersDE", "ProductionEnabled", "ProductionHint", "ExampleTargetSpansJSON"}
    }

    policy.apply_policy([{"fields": active}], strict=False)

    assert active["AcceptedFullAnswersDE"] == "anziehen|sich anziehen|s anziehen"
    for key, value in legacy.items():
        assert active[key] == value


def test_apply_policy_mutates_only_new_schema_fields_and_clears_disabled_answer() -> None:
    active = fields(
        SourceID="A1-84886454468", Lemma="Arzt", MeaningEN="doctor",
        AcceptedAnswersDE="Arzt", AcceptedArticlesDE="der|die", POS="n.",
        Gender="m./f.", Example1EN="The doctor is here.",
    )
    active["ExampleTargetSpansJSON"] = "keep-me"
    disabled = fields(
        SourceID="B1-MAIN-1299", Lemma="Karotte", MeaningEN="carrot",
        AcceptedAnswersDE="Karotte", AcceptedArticlesDE="die", POS="n.",
        Gender="f.", Example1EN="Rabbits eat carrots.",
    )
    report = policy.apply_policy([{"fields": active}, {"fields": disabled}], strict=False)
    assert report["enabled"] == 1
    assert report["disabled"] == 1
    assert active["AcceptedFullAnswersDE"] == "der Arzt|die Ärztin"
    assert active["ProductionEnabled"] == "1"
    assert active["ProductionHint"] == ""
    assert active["ExampleTargetSpansJSON"] == "keep-me"
    assert disabled["AcceptedFullAnswersDE"] == ""
    assert disabled["ProductionEnabled"] == ""
    assert disabled["ProductionHint"] == ""


def test_apply_policy_rejects_legacy_boolean_encoding() -> None:
    bad = fields(SourceID="A1-X")
    bad["ProductionEnabled"] = "0"
    with pytest.raises(policy.PolicyError, match="ProductionEnabled"):
        policy.apply_policy([bad], {"version": 1, "answers": {}, "production": {}}, strict=False)


def test_visible_cue_audit_uses_gender_hint_and_ignores_disabled_records() -> None:
    left = fields(SourceID="X", CEFR="B1", POS="n.", MeaningEN="same", Example1EN="same", Gender="")
    right = fields(SourceID="Y", CEFR="B1", POS="n.", MeaningEN="same", Example1EN="same", Gender="")
    report = policy.audit_visible_cues([left, right])
    assert len(report["collisions"]) == 1

    right["Gender"] = "f."
    assert policy.audit_visible_cues([left, right])["collisions"] == []
    right["Gender"] = ""
    right["ProductionHint"] = "A"
    assert policy.audit_visible_cues([left, right])["collisions"] == []
    right["ProductionEnabled"] = ""
    assert policy.audit_visible_cues([left, right])["collisions"] == []

    right["ProductionEnabled"] = "0"
    with pytest.raises(policy.PolicyError, match="ProductionEnabled"):
        policy.audit_visible_cues([left, right])


def test_apply_policy_fails_closed_on_remaining_collision() -> None:
    left = fields(SourceID="X", MeaningEN="same", Example1EN="same")
    right = fields(SourceID="Y", MeaningEN="same", Example1EN="same")
    empty_policy = {"version": 1, "answers": {}, "production": {}}
    with pytest.raises(policy.PolicyError, match="collisions"):
        policy.apply_policy([left, right], empty_policy, strict=False)
    assert "AcceptedFullAnswersDE" not in left
    assert "ProductionEnabled" not in right


def test_load_policy_rejects_zero_and_hint_on_disabled_record(tmp_path: Path) -> None:
    base = {"version": 1, "answers": {}, "production": {"X": {"enabled": "0", "hint": ""}}}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="enabled"):
        policy.load_policy(path)

    base["production"]["X"] = {"enabled": False, "hint": "D"}
    path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="cannot have a hint"):
        policy.load_policy(path)
