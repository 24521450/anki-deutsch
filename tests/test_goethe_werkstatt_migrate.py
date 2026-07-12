from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_werkstatt_migrate as gw  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bahnhof", "bahnhof"),
        ("BAHNHOF", "bahnhof"),
        ("für", "fuer"),
        ("grüßen", "gruessen"),
        ("Straße", "strasse"),
        ("  Das   macht nichts?! ", "das macht nichts"),
        ("war’s", "war's"),
    ],
)
def test_normalize_answer(raw, expected):
    assert gw.normalize_answer(raw) == expected


def test_answer_accepts_case_transliteration_and_optional_correct_article():
    assert gw.answer_is_correct("bahnhof", "Bahnhof", "", "der")
    assert gw.answer_is_correct("BAHNHOF", "Bahnhof", "", "der")
    assert gw.answer_is_correct("der bahnhof", "Bahnhof", "", "der")
    assert not gw.answer_is_correct("die bahnhof", "Bahnhof", "", "der")
    assert gw.answer_is_correct("fuer", "für")
    assert not gw.answer_is_correct("fur", "für")


def test_answer_accepts_reviewed_variants_but_not_blank_or_partial_phrase():
    accepted = "leidtun|leid tun"
    assert gw.answer_is_correct("leidtun", "leidtun", accepted)
    assert gw.answer_is_correct("leid tun", "leidtun", accepted)
    assert not gw.answer_is_correct("", "leidtun", accepted)
    assert not gw.answer_is_correct("macht nichts", "Das macht nichts", "Das macht nichts")
    assert gw.answer_is_correct("das macht nichts.", "Das macht nichts", "Das macht nichts")


def test_internal_hyphen_and_word_order_remain_strict():
    assert gw.answer_is_correct("E-Mail", "E-Mail")
    assert not gw.answer_is_correct("Email", "E-Mail")
    assert not gw.answer_is_correct("jeden auf Fall", "auf jeden Fall")


@pytest.mark.parametrize(
    ("raw", "lemma", "article", "articles", "forms"),
    [
        ("die Ansage, -n", "Ansage", "die", "die", "-n"),
        ("das Ausland", "Ausland", "das", "das", ""),
        ("der/die Bekannte, -n", "Bekannte", "der/die", "der|die", "-n"),
        ("das Wort, -ö, er/-e", "Wort", "das", "das", "-ö, er/-e"),
        ("heißen", "heißen", "", "", ""),
    ],
)
def test_parse_a1_lexeme(raw, lemma, article, articles, forms):
    parsed = gw.parse_a1_lexeme(raw)
    assert parsed == {
        "Lemma": lemma,
        "Article": article,
        "AcceptedArticlesDE": articles,
        "NounFormsRaw": forms,
    }


def test_manual_audio_converts_sound_tag_to_non_autoplay_html():
    assert gw.manual_audio("[sound:test.mp3]") == (
        '<audio class="gw-example-player" controls preload="none" src="test.mp3"></audio>'
    )
    assert gw.manual_audio("") == ""
    with pytest.raises(gw.MigrationError):
        gw.manual_audio("test.mp3")


def test_card_flip_uses_staged_motion_with_reduced_motion_fallback():
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    assert "@keyframes gw-answer-reveal" in css
    assert ".gw-back > :not(.gw-answer-hero)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_export_shape_and_counts_are_stable():
    a1, a2 = gw.parse_export()
    assert len(a1) == 925
    assert len(a2) == 656
    assert a1[0]["legacy_id"] == "84886454452"
    assert a2[0]["original_order"] == "1"


def test_pilot_is_fixed_and_balanced():
    assert len(gw.PILOT_A1) == len(set(gw.PILOT_A1)) == 10
    assert len(gw.PILOT_A2) == len(set(gw.PILOT_A2)) == 10
    assert not set(gw.PILOT_A1) & set(gw.PILOT_A2)


def test_templates_do_not_leak_german_or_audio_on_production_front():
    front = (gw.DESIGN / "front_english.html").read_text(encoding="utf-8")
    assert "{{type:Lemma}}" in front
    assert "{{MeaningEN}}" in front
    assert "{{#Example1EN}}" in front
    assert "{{Article}}" not in front
    assert "{{WordAudio}}" not in front
    assert "{{Example1DE}}" not in front


def test_production_front_submits_once_on_enter_without_a_custom_button():
    german = (gw.DESIGN / "front_german.html").read_text(encoding="utf-8")
    english = (gw.DESIGN / "front_english.html").read_text(encoding="utf-8")
    assert "gw-show-answer" not in german
    assert "gw-show-answer" not in english
    assert 'pycmd("ans")' in english
    assert 'event.key !== "Enter"' in english
    assert 'input.dataset.submitted === "true"' in english


def test_templates_keep_example_three_and_four_collapsed():
    for name in ("back_german.html", "back_english.html"):
        template = (gw.DESIGN / name).read_text(encoding="utf-8")
        assert "<details>" in template
        assert "{{#Example3" in template
        assert "{{#Example4" in template


def test_back_templates_put_german_first_and_inject_target_highlighter():
    rendered = gw.templates()
    for card in rendered.values():
        back = card["Back"]
        assert back.index("gw-german-answer") < back.index("gw-meaning-secondary")
        assert "Examples · German first" not in back
        assert "gw-example-main gw-example-de" in back
        assert "goetheWerkstattTargetHighlighter" in back
        assert "{{TargetHighlighter}}" not in back


def test_templates_omit_redundant_recall_prompt():
    front = (gw.DESIGN / "front_german.html").read_text(encoding="utf-8")
    assert "Recall the English meaning" not in front


def test_production_card_omits_redundant_instruction_labels():
    front = (gw.DESIGN / "front_english.html").read_text(encoding="utf-8")
    back = (gw.DESIGN / "back_english.html").read_text(encoding="utf-8")
    for label in ("English → German", "Press Enter once"):
        assert label not in front
    for label in ("Correct answer", "Press 1 for Again", "gw-again-hint"):
        assert label not in back


def test_target_highlighter_handles_inflections_separable_verbs_and_short_words():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the card JavaScript regression test")
    script = (gw.DESIGN / "target_highlighter.js").read_text(encoding="utf-8")
    harness = r'''
const fields = {};
globalThis.document = {
  getElementById: (id) => ({ textContent: fields[id] || "" }),
  querySelectorAll: () => []
};
''' + script + r'''
const api = globalThis.goetheWerkstattTargetHighlighter;
function configure(values) {
  Object.keys(fields).forEach((key) => delete fields[key]);
  Object.assign(fields, values);
  return api.terms();
}
let terms = configure({"gw-lemma": "Apfel", "gw-noun-forms": "-Ä", "gw-pos": "n."});
if (!terms.includes("Äpfel")) throw new Error("missing umlaut plural");
terms = configure({"gw-lemma": "anfangen", "gw-verb-forms": "fängt an, hat angefangen", "gw-pos": "v."});
let source = "Hier fängt die Straße an.";
let found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (!found.includes("fängt") || !found.includes("an")) throw new Error("missing separable verb parts");
terms = configure({"gw-lemma": "an", "gw-pos": "prep."});
source = "Kann ich an der Ampel halten?";
found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (found.length !== 1 || found[0] !== "an") throw new Error("short target matched inside another word");
'''
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)


def test_field_contract_is_stable():
    assert gw.FIELDS[0] == "Lemma"
    assert gw.FIELDS[-1] == "LegacyGUID"
    assert len(gw.FIELDS) == 32
    assert len(set(gw.FIELDS)) == len(gw.FIELDS)
    assert "MoreExamplesHTML" in gw.FIELDS
    assert "SourceRefs" in gw.FIELDS


def test_change_type_maps_preserve_both_card_ordinals():
    assert gw.TEMPLATE_MAP == {
        "German → English": "Card 1",
        "English → German": "Card 2",
    }
    assert gw.A1_FIELD_MAP["Lemma"] == "de_word"
    assert gw.A1_FIELD_MAP["SourceID"] == "Note ID"
    assert gw.A2_FIELD_MAP["Lemma"] == "Wort_DE"
    assert gw.A2_FIELD_MAP["Example4Audio"] == "Audio_S4"


def test_pilot_verification_scope_excludes_unrelated_reviews():
    assert set(gw.PILOT_IDS) == set(gw.PILOT_A1) | set(gw.PILOT_A2)
    assert len(gw.PILOT_IDS) == 20
