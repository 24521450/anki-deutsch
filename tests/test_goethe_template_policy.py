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
    assert len(loaded["answers"]) == 46
    assert len(loaded["production"]) >= 25
    assert sum(rule["enabled"] == policy.DISABLED for rule in loaded["production"].values()) == 7
    assert loaded["answers"]["A1-84886454468"] == "der Arzt|die Ärztin"
    assert loaded["answers"]["A2-WG-0023"] == "der Angestellte|die Angestellte"
    assert loaded["answers"]["A2-0572"] == "die Kunst|die Kunsterziehung"
    assert loaded["production"]["B1-MAIN-0139"] == {"enabled": "1", "hint": "D/CH"}
    # Brieftasche stays active by default; its feminine Gender cue already
    # separates it from the masculine/neuter wallet variants.
    assert "B1-MAIN-0442" not in loaded["production"]
    assert loaded["production"]["B1-MAIN-1299"]["enabled"] == policy.DISABLED
    assert loaded["production"]["B1-WG-0015"] == {"enabled": "", "hint": ""}
    assert loaded["answers"]["B1-MAIN-0252"] == "der Bancomat|der Bankomat"


def test_derivation_is_conservative_and_does_not_split_lexical_slashes() -> None:
    assert policy.split_full_answers("") == []
    assert policy.split_full_answers("der Arzt|die Ärztin") == ["der Arzt", "die Ärztin"]
    assert policy.derive_full_answers(fields()) == ["arbeiten"]
    assert policy.derive_full_answers(fields(
        Lemma="Bahn", AcceptedAnswersDE="Bahn", AcceptedArticlesDE="die"
    )) == ["die Bahn"]
    assert policy.derive_full_answers(fields(
        Lemma="Samstag/Sonnabend", AcceptedAnswersDE="Samstag/Sonnabend", Article="der"
    )) == ["der Samstag/Sonnabend"]
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


def test_explicit_overrides_keep_full_forms_and_weak_nouns() -> None:
    assert policy.derive_full_answers(
        fields(Lemma="Arzt"), override="der Arzt|die Ärztin"
    ) == ["der Arzt", "die Ärztin"]
    assert policy.derive_full_answers(
        fields(Lemma="Angestellter"),
        override="der Angestellte|die Angestellte",
    ) == ["der Angestellte", "die Angestellte"]
    assert policy.derive_full_answers(
        fields(Lemma="Kunst"), override="die Kunst|die Kunsterziehung"
    ) == ["die Kunst", "die Kunsterziehung"]


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
