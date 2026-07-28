from __future__ import annotations

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
        "Lemma": "Kuchen",
        "MeaningEN": "cake",
        "CEFR": "A1",
        "POS": "n.",
        "Gender": "m.",
        "AcceptedAnswersDE": "Kuchen",
        "AcceptedArticlesDE": "der",
        "AcceptedFullAnswersDE": "der Kuchen",
        "Example1EN": "I like cake.",
        "ProductionEnabled": "1",
        "ProductionHint": "",
    }
    result.update(values)
    return result


def test_global_audit_does_not_use_level_grammar_or_examples_as_semantic_cues() -> None:
    report = policy.audit_semantic_ambiguity(
        [
            fields(
                SourceID="A1-KUCHEN",
                Lemma="Kuchen",
                CEFR="A1",
                POS="n.",
                Gender="m.",
                AcceptedFullAnswersDE="der Kuchen",
                Example1EN="I want a piece of cake.",
            ),
            fields(
                SourceID="A2-TORTE",
                Lemma="Torte",
                CEFR="A2",
                POS="n.",
                Gender="f.",
                AcceptedFullAnswersDE="die Torte",
                Example1EN="She baked a cake.",
            ),
        ]
    )
    assert [item["source_ids"] for item in report["collisions"]] == [
        ["A1-KUCHEN", "A2-TORTE"]
    ]


def test_global_audit_accepts_distinct_reviewed_hints() -> None:
    report = policy.audit_semantic_ambiguity(
        [
            fields(
                SourceID="A1-KUCHEN",
                Lemma="Kuchen",
                AcceptedFullAnswersDE="der Kuchen",
                ProductionHint="general baked cake",
            ),
            fields(
                SourceID="A2-TORTE",
                Lemma="Torte",
                AcceptedFullAnswersDE="die Torte",
                ProductionHint="layered or cream-filled cake",
            ),
        ]
    )
    assert report["collisions"] == []
    assert report["hinted_groups"] == 1


def test_global_audit_exempts_identical_accepted_answer_sets() -> None:
    report = policy.audit_semantic_ambiguity(
        [
            fields(SourceID="A1", Lemma="Kuchen"),
            fields(SourceID="A2", Lemma="cake", CEFR="A2"),
        ]
    )
    assert report["collisions"] == []
    assert report["same_answer_exemptions"] == 1


@pytest.mark.parametrize(
    "hint",
    ["Kuchen", "the Kuchen entry", "die Torte item"],
)
def test_global_audit_rejects_hints_that_leak_a_german_answer(hint: str) -> None:
    report = policy.audit_semantic_ambiguity(
        [
            fields(
                SourceID="A1",
                Lemma="Kuchen",
                AcceptedFullAnswersDE="der Kuchen",
                ProductionHint=hint,
            ),
            fields(
                SourceID="A2",
                Lemma="Torte",
                CEFR="A2",
                Gender="f.",
                AcceptedFullAnswersDE="die Torte",
                ProductionHint="other cake",
            ),
        ]
    )
    assert report["collisions"]
    assert report["invalid_hints"]


def test_known_review_hints_are_loaded_without_changing_meanings() -> None:
    loaded = policy.load_policy()
    assert loaded["production"]["A1-84886454881"]["hint"] == "general baked cake"
    assert loaded["production"]["A2-0995"]["hint"] == "layered or cream-filled cake"
    assert loaded["production"]["A1-84886454717"]["hint"] == "guided; led by a guide"
    assert loaded["production"]["A2-0809"]["hint"] == "a walk or circuit around a place"


def test_known_cross_gloss_pairs_keep_explicit_distinguishing_hints() -> None:
    loaded = policy.load_policy()["production"]
    required = {
        "A1-84886454604": "coordinating conjunction",
        "A2-1150": "subordinating conjunction",
        "A1-MAIN-0026": "course, event or service",
        "A2-0246": "name or details",
        "A1-84886455258": "free adverb",
        "A2-MAIN-1168": "separable verb particle",
    }
    for source_id, phrase in required.items():
        assert loaded[source_id]["enabled"] == policy.ENABLED
        assert phrase in loaded[source_id]["hint"]
