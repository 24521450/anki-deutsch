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
        "SourceNoteRaw": "",
        "SourceID": "",
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


def test_mixed_adjective_adverb_headword_highlights_inflected_example():
    value = fields(
        Lemma="absolut",
        AcceptedAnswersDE="absolut",
        POS="adj., adv.",
        SourceID="B1-MAIN-0030",
        Example1DE="Was Sie da sagen, ist absolut falsch.",
        Example2DE="Ich habe absolutes Vertrauen zu dir.",
    )

    assert json.loads(highlights.build_target_spans(value)) == [
        [[22, 29]], [[9, 18]],
    ]


@pytest.mark.parametrize(
    ("lemma", "example", "expected"),
    [
        ("weder … noch", "Für Urlaub haben wir weder Zeit noch Geld.", ["weder", "noch"]),
        ("entweder ... oder", "Nur einer kann gewinnen, entweder du oder ich.", ["entweder", "oder"]),
        ("je … desto …", "Je länger ich Deutsch lerne, desto besser kann ich es verstehen.", ["Je", "desto"]),
        ("sowohl … als auch", "Sowohl Sie als auch Ihre Frau müssen unterschreiben.", ["Sowohl", "als auch"]),
        ("um … zu", "Um gesund zu bleiben, musst du Sport machen.", ["Um", "zu"]),
    ],
)
def test_discontinuous_headwords_highlight_each_ordered_component(
    lemma: str, example: str, expected: list[str],
) -> None:
    value = fields(Lemma=lemma, AcceptedAnswersDE=lemma, POS="phrase")
    found = highlights.match_ranges(example, highlights.candidate_terms(value), value["POS"])
    assert surfaces(example, found) == expected


def test_discontinuous_headword_requires_the_complete_ordered_expression() -> None:
    value = fields(Lemma="weder … noch", AcceptedAnswersDE="weder … noch", POS="phrase")
    candidates = highlights.candidate_terms(value)

    assert highlights.match_ranges("Weder heute.", candidates, value["POS"]) == []
    assert highlights.match_ranges("Noch heute, weder morgen.", candidates, value["POS"]) == []


@pytest.mark.parametrize(
    ("lemma", "example", "expected"),
    [
        ("-weise", "War der Test schwierig? – Teilweise.", ["weise"]),
        ("-weise", "Das ist möglicherweise nicht so einfach.", ["weise"]),
        ("un-", "Der Verkäufer war sehr unfreundlich.", ["un"]),
        ("Feier-", "Am Montag ist Feiertag.", ["Feier"]),
        ("dein-", "Ist das dein Auto?", ["dein"]),
        ("Nord-/Ostsee", "Wir fahren an die Nord/Ostsee.", ["Nord", "Ostsee"]),
        ("(Kredit)-Karte", "Kann ich mit Karte zahlen?", ["Karte"]),
        ("Speise-/-speise", "Als Vorspeise nehme ich keine Nachspeise.", ["speise", "speise"]),
        ("früher/früher-", "Früher nehmen wir den früheren Zug.", ["Früher", "früher"]),
        ("gern(e)", "Ich gehe gerne und helfe gern.", ["gerne", "gern"]),
    ],
)
def test_dictionary_notation_maps_to_visible_headword_morphemes(
    lemma: str, example: str, expected: list[str],
) -> None:
    value = fields(Lemma=lemma, AcceptedAnswersDE=lemma, POS="adv.")
    found = highlights.match_ranges(example, highlights.candidate_terms(value), value["POS"])
    assert surfaces(example, found) == expected


def test_bound_headword_respects_the_declared_word_edge() -> None:
    prefix = fields(Lemma="un-", AcceptedAnswersDE="un-", POS="prefix")
    suffix = fields(Lemma="-weise", AcceptedAnswersDE="-weise", POS="suffix")

    assert highlights.match_ranges("Die Runde ist bunt.", highlights.candidate_terms(prefix), prefix["POS"]) == []
    assert highlights.match_ranges("Der Weise spricht.", highlights.candidate_terms(suffix), suffix["POS"]) == []


@pytest.mark.parametrize(
    ("lemma", "pos", "example", "expected"),
    [
        ("Kollege", "n.", "Wie heißt die neue Kollegin?", ["Kollegin"]),
        ("Trainerin", "n.", "Ich finde unseren Trainer nett.", ["Trainer"]),
        ("Job", "n.", "Ich suche einen Ferienjob.", ["job"]),
        ("Baum", "n.", "Wir haben zwei Apfelbäume.", ["bäume"]),
        ("dunkel", "adj.", "Sie hat dunkle Haare.", ["dunkle"]),
        ("regional", "adv.", "Wir nehmen eine Regionalbahn.", ["Regional"]),
        ("jung", "adj.", "Mein Bruder ist jünger.", ["jünger"]),
        ("sagen", "v.", "Was haben Sie gesagt?", ["gesagt"]),
        ("ärgern", "v.", "Das hat mich geärgert.", ["geärgert"]),
    ],
)
def test_common_morphology_and_compounds_keep_the_headword_visible(
    lemma: str, pos: str, example: str, expected: list[str],
) -> None:
    value = fields(Lemma=lemma, AcceptedAnswersDE=lemma, POS=pos)
    found = highlights.match_ranges(example, highlights.candidate_terms(value), pos)
    assert surfaces(example, found) == expected


def test_reviewed_highlight_audit_applies_only_missing_certain_ranges() -> None:
    value = fields(
        Lemma="Parfüm",
        POS="n.",
        SourceID="A2-0735",
        Example1DE="Ich suche ein Parfum als Geschenk für meine Frau.",
    )
    assert json.loads(highlights.build_spans(value)) == [[[14, 20]]]

    statuses = [case["status"] for case in highlights.highlight_policy()["cases"]]
    assert statuses.count("missing_certain") == 51
    assert statuses.count("valid_blank") == 3
    assert statuses.count("needs_review") == 21


def test_reviewed_highlight_audit_fails_closed_on_example_text_drift() -> None:
    value = fields(
        Lemma="Parfüm",
        POS="n.",
        SourceID="A2-0735",
        Example1DE="Ich kaufe ein Parfum als Geschenk.",
    )
    with pytest.raises(highlights.HighlightError, match="reviewed audit text drift"):
        highlights.build_spans(value)


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


def test_regular_imperative_is_supported_without_matching_mid_clause_noun():
    probieren = fields(
        Lemma="probieren",
        AcceptedAnswersDE="probieren",
        VerbFormsRaw="probiert, hat probiert",
        POS="v.",
        Example1DE="Die Tür geht schwer auf. Probier mal!",
    )
    planen = fields(
        Lemma="planen",
        AcceptedAnswersDE="planen",
        VerbFormsRaw="plant, hat geplant",
        POS="v.",
        Example1DE="Das ist ein guter Plan.",
    )

    assert json.loads(highlights.build_spans(probieren)) == [[[25, 32]]]
    assert json.loads(highlights.build_spans(planen)) == [[]]


@pytest.mark.parametrize(
    ("lemma", "raw", "source_raw", "sentence", "expected"),
    [
        ("antworten", "", "source: antworten, antwortet,", "Er antwortet sofort.", ["antwortet"]),
        ("wissen", "", "source: wissen, weiß,", "Weißt du das?", ["Weißt"]),
        ("vergessen", "vergisst, hat vergessen", "", "Vergiss das nicht!", ["Vergiss"]),
        ("zweifeln", "zweifelt, hat gezweifelt", "", "Ich zweifle daran.", ["zweifle"]),
        ("winken", "winkt, winkte, hat gewinkt", "", "Sie winkten uns zu.", ["winkten"]),
    ],
)
def test_verb_form_families_use_principal_forms(
    lemma: str, raw: str, source_raw: str, sentence: str, expected: list[str],
):
    value = fields(
        Lemma=lemma, AcceptedAnswersDE=lemma, VerbFormsRaw=raw,
        SourceNoteRaw=source_raw, POS="v.", Example1DE=sentence,
    )
    found = highlights.match_ranges(sentence, highlights.candidate_terms(value), value["POS"])
    assert surfaces(sentence, found) == expected


def test_separable_particle_is_clause_paired_and_not_a_preposition():
    value = fields(
        Lemma="aufpassen",
        AcceptedAnswersDE="aufpassen",
        VerbFormsRaw="passt auf, hat aufgepasst",
        POS="v.",
        Example1DE="Pass auf der Treppe auf, dass du nicht fällst!",
    )
    text = value["Example1DE"]
    found = highlights.match_ranges(text, highlights.candidate_terms(value), value["POS"])
    assert surfaces(text, found) == ["Pass", "auf"]
    assert found[-1] == (20, 23)


def test_reviewed_blank_pos_verb_is_supported_without_general_pos_inference():
    reviewed = fields(
        SourceID="A2-1202",
        Lemma="zurückkommen",
        AcceptedAnswersDE="zurückkommen",
        SourceNoteRaw='{"Wort_DE":"zurückkommen","Verbformen":""}',
        POS="",
        Example1DE="Wann kommst du zurück?",
    )
    unknown = fields(
        SourceID="UNKNOWN",
        Lemma="zurückkommen",
        AcceptedAnswersDE="zurückkommen",
        POS="",
        Example1DE="Wann kommst du zurück?",
    )

    assert json.loads(highlights.build_spans(reviewed)) == [[[5, 11], [15, 21]]]
    assert json.loads(highlights.build_spans(unknown)) == [[]]


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
