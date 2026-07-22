from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_target_highlights as highlights  # noqa: E402


def fields(**values: str) -> dict[str, str]:
    result = {
        "Lemma": "",
        "AcceptedAnswersDE": "",
        "NounFormsRaw": "",
        "VerbFormsRaw": "",
        "POS": "",
        **{f"Example{index}DE": "" for index in range(1, 5)},
        "MoreExamplesHTML": "",
    }
    result.update(values)
    return result


def surfaces(text: str, ranges: list[tuple[int, int]]) -> list[str]:
    return [text[start:end] for start, end in ranges]


def test_candidates_cover_inflections_and_separable_verbs():
    noun = fields(Lemma="Apfel", NounFormsRaw="-Ä", POS="n.")
    assert "Äpfel" in highlights.candidate_terms(noun)

    verb = fields(Lemma="schaffen", VerbFormsRaw="schafft, hat geschafft", POS="v.")
    text = "Kannst du helfen? Ich schaffe das nicht allein."
    found = highlights.match_ranges(text, highlights.candidate_terms(verb), verb["POS"])
    assert surfaces(text, found) == ["schaffe"]

    separable = fields(Lemma="abfahren", VerbFormsRaw="fährt ab, ist abgefahren", POS="v.")
    text = "Wir fahren um zwölf Uhr ab."
    found = highlights.match_ranges(text, highlights.candidate_terms(separable), separable["POS"])
    assert surfaces(text, found) == ["fahren", "ab"]


def test_noun_suffixes_apply_to_accepted_spelling_variants():
    noun = fields(
        Lemma="Bancomat",
        AcceptedAnswersDE="Bancomat|Bankomat",
        NounFormsRaw="-en",
        POS="n.",
    )
    text = "Ich hole Geld vom Bankomaten."
    found = highlights.match_ranges(text, highlights.candidate_terms(noun), noun["POS"])
    assert surfaces(text, found) == ["Bankomaten"]


def test_short_words_and_irregular_sein_use_boundaries():
    short = fields(Lemma="an", POS="prep.")
    text = "Kann ich an der Ampel halten?"
    found = highlights.match_ranges(text, highlights.candidate_terms(short), short["POS"])
    assert surfaces(text, found) == ["an"]

    sein = fields(Lemma="sein", VerbFormsRaw="ist, war, ist gewesen", POS="v.")
    text = "Er ist bei seinem Bruder."
    found = highlights.match_ranges(text, highlights.candidate_terms(sein), sein["POS"])
    assert surfaces(text, found) == ["ist"]


def test_visible_text_preserves_text_content_without_flattening_markup():
    assert highlights.visible_text("Grüße&nbsp;<br><strong>aus Köln</strong>") == "Grüße\u00a0aus Köln"


def test_build_target_spans_uses_utf16_offsets_and_example_order():
    value = fields(
        Lemma="Deutsch",
        AcceptedAnswersDE="Deutsch",
        POS="n.",
        Example1DE="🙂 Ich lerne Deutsch.",
        Example2DE="Deutsch macht Spaß.",
        MoreExamplesHTML=(
            '<article class="gw-example"><div class="gw-example-main gw-example-de">'
            "Wir sprechen Deutsch.</div><div class=\"gw-example-sub\">We speak German.</div></article>"
        ),
    )
    encoded = highlights.build_target_spans(value)
    spans = json.loads(encoded)
    assert spans == [[[13, 20]], [[0, 7]], [[13, 20]]]
    assert highlights.parse_target_spans(encoded, [
        "🙂 Ich lerne Deutsch.", "Deutsch macht Spaß.", "Wir sprechen Deutsch.",
    ]) == [[(13, 20)], [(0, 7)], [(13, 20)]]


@pytest.mark.parametrize("lemma", ["sich eintragen", "(sich) eintragen"])
def test_build_target_spans_highlights_bare_infinitive_for_reflexive_lemma(lemma: str):
    value = fields(
        Lemma=lemma,
        AcceptedAnswersDE=lemma,
        VerbFormsRaw="trägt ein, hat eingetragen",
        POS="v.",
        Example1DE="Sie müssen Ihren Namen und Ihre Adresse eintragen.",
        Example2DE="Tragen Sie sich bitte in diese Liste ein!",
    )

    assert json.loads(highlights.build_target_spans(value)) == [
        [[40, 49]],
        [[0, 6], [37, 40]],
    ]


def test_build_target_spans_highlights_bounded_separable_imperative_stem():
    value = fields(
        Lemma="umdrehen",
        AcceptedAnswersDE="umdrehen",
        VerbFormsRaw="dreht um, drehte um, hat umgedreht",
        POS="v.",
        Example1DE="Dreh dich mal um. Da hinten liegt das Buch doch.",
        Example2DE="Dreh das Blatt um; die Lösung steht auf der Rückseite.",
    )

    assert json.loads(highlights.build_target_spans(value)) == [
        [[0, 4], [14, 16]],
        [[0, 4], [15, 17]],
    ]


def test_imperative_stem_requires_separable_evidence_and_word_boundaries():
    non_separable = fields(
        Lemma="planen",
        AcceptedAnswersDE="planen",
        VerbFormsRaw="plant, hat geplant",
        POS="v.",
        Example1DE="Das ist ein guter Plan.",
    )
    separable = fields(
        Lemma="umdrehen",
        AcceptedAnswersDE="umdrehen",
        VerbFormsRaw="dreht um, hat umgedreht",
        POS="v.",
        Example1DE="Das Drehbuch liegt da; dreh es um.",
    )

    assert json.loads(highlights.build_spans(non_separable)) == [[]]
    assert json.loads(highlights.build_spans(separable)) == [
        [[23, 27], [31, 33]],
    ]


def test_build_spans_derives_umlaut_plural_from_goethe_marker():
    value = fields(
        Lemma="Blatt",
        AcceptedAnswersDE="Blatt",
        NounFormsRaw="¨-er",
        POS="n.",
        Example1DE="Haben Sie ein Blatt Papier für mich?",
        Example2DE="Die Bäume haben schon gelbe Blätter.",
    )

    assert json.loads(highlights.build_spans(value)) == [
        [[14, 19]],
        [[28, 35]],
    ]


def test_umlaut_marker_forms_do_not_match_lowercase_verbs():
    value = fields(
        Lemma="Wunsch",
        AcceptedAnswersDE="Wunsch",
        NounFormsRaw='"-e',
        POS="n.",
        Example1DE="Wünsche können wahr werden.",
        Example2DE="Ich wünsche Ihnen alles Gute!",
        Example3DE="Sie wünschen?",
    )

    assert json.loads(highlights.build_spans(value)) == [
        [[0, 7]],
        [],
        [],
    ]


@pytest.mark.parametrize("marker", ["¨-", '"-', '"'])
def test_build_spans_derives_umlaut_form_from_marker_without_suffix(marker: str):
    value = fields(
        Lemma="Mangel",
        AcceptedAnswersDE="Mangel",
        NounFormsRaw=marker,
        POS="n.",
        Example1DE="Mehrere Mängel wurden gefunden.",
    )

    assert json.loads(highlights.build_spans(value)) == [[[8, 14]]]


@pytest.mark.parametrize(
    "value,texts",
    [
        ("not json", ["Deutsch"]),
        ("[]", ["Deutsch"]),
        ("[[[0,99]]]", ["Deutsch"]),
        ("[[[3,4],[2,5]]]", ["Deutsch"]),
    ],
)
def test_parse_target_spans_fails_closed(value: str, texts: list[str]):
    with pytest.raises(highlights.HighlightError):
        highlights.parse_target_spans(value, texts)
