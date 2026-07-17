from __future__ import annotations

import json
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


@pytest.mark.parametrize(
    "answer",
    [
        "dabei sein",
        "dagegen sein",
        "dafür sein",
        "einverstanden sein",
        "erkältet sein",
        "erlaubt sein",
        "fertig sein",
        "fit sein",
        "gültig sein",
        "unterwegs sein",
        "verabredet sein",
        "verboten sein",
    ],
)
def test_answer_accepts_long_state_with_or_without_terminal_sein(answer):
    core = answer.removesuffix(" sein")
    assert gw.answer_is_correct(answer, answer, answer)
    assert gw.answer_is_correct(core, answer, answer)


def test_answer_accepts_transliteration_when_terminal_sein_is_omitted():
    assert gw.answer_is_correct("erkältet", "erkältet sein", "erkältet sein")
    assert gw.answer_is_correct("erkaeltet", "erkältet sein", "erkältet sein")
    assert not gw.answer_is_correct("", "erkältet sein", "erkältet sein")


@pytest.mark.parametrize("answer", ["an sein", "aus sein", "auf sein", "weg sein", "zu sein"])
def test_answer_keeps_short_particle_sein_phrases_strict(answer):
    core = answer.removesuffix(" sein")
    assert gw.answer_is_correct(answer, answer, answer)
    assert not gw.answer_is_correct(core, answer, answer)


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


def test_parse_markdown_keeps_dialogue_reply_in_the_same_example(tmp_path):
    source = tmp_path / "dialogue.md"
    source.write_text(
        "| **ander-** | det., pron. | | A1 | "
        "Willst du diese Jacke?<br>– Nein, ich möchte die andere.<br>Ein weiterer Satz. | |\n",
        encoding="utf-8",
    )
    assert gw.parse_markdown(source)[0]["examples"] == [
        "Willst du diese Jacke?<br>– Nein, ich möchte die andere.",
        "Ein weiterer Satz.",
    ]


def test_a1_achtung_is_one_pdf_faithful_example():
    row = next(item for item in gw.parse_markdown(gw.SOURCE_A1) if item["word"] == "Achtung")
    assert row["examples"] == ["Achtung! Das dürfen Sie nicht tun."]


def test_card_flip_uses_staged_motion_with_reduced_motion_fallback():
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    assert "@keyframes gw-anchor-reveal" in css
    assert ".gw-back > :not(.gw-answer-stage)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_noir_palette_keeps_semantic_levels_and_readable_metadata():
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    for color in ("#0f1416", "#171c1f", "#262b2d", "#3d4946", "#dfe3e6", "#bdc9c5", "#879390", "#71d8c5"):
        assert color in css
    assert '.gw-card[data-level="A2"] { --level: #e5b85b; }' in css
    assert ".gw-article-der { color: #7fa8ff; }" in css
    assert ".gw-article-die { color: #f08aa6; }" in css
    assert ".gw-article-das { color: #72cda5; }" in css


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


def test_headword_audio_is_hidden_and_bound_to_the_headword():
    for name in ("front_german.html", "back_german.html", "back_english.html"):
        template = (gw.DESIGN / name).read_text(encoding="utf-8")
        assert template.count("{{WordAudio}}") == 1
        assert '{{#WordAudio}}<span class="gw-word-audio" hidden>' in template
        assert "{{WordAudioController}}" in template
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    assert ".gw-word-audio { display: none !important; }" in css
    assert ".gw-word-playable:hover" in css
    rendered = gw.templates()
    assert "goetheWerkstattWordAudio" in rendered["German → English"]["Front"]
    for card in rendered.values():
        assert "goetheWerkstattWordAudio" in card["Back"]
        assert "{{WordAudioController}}" not in card["Back"]


def test_word_audio_controller_supports_click_enter_and_space_without_visible_button():
    script = (gw.DESIGN / "word_audio.js").read_text(encoding="utf-8")
    assert 'headword.addEventListener("click", replay)' in script
    assert 'event.key === "Enter"' in script
    assert 'event.key === " "' in script
    assert 'container.querySelector(".replay-button, .soundLink, a, button")' in script


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


def test_back_templates_inject_click_to_play_example_audio():
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    assert ".gw-example-audio { display: none; }" in css
    rendered = gw.templates()
    for card in rendered.values():
        back = card["Back"]
        assert "goetheWerkstattExampleAudio" in back
        assert "{{ExampleAudioController}}" not in back


def test_example_audio_controller_replays_and_switches_sentences():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the card JavaScript regression test")
    script = (gw.DESIGN / "example_audio.js").read_text(encoding="utf-8")
    harness = r'''
function classes() {
  const values = new Set();
  return { add: (v) => values.add(v), remove: (v) => values.delete(v), contains: (v) => values.has(v) };
}
function sentence(text) {
  const listeners = {};
  return {
    textContent: text, classList: classes(), attributes: {}, listeners,
    setAttribute: function (key, value) { this.attributes[key] = value; },
    addEventListener: function (name, callback) { listeners[name] = callback; }
  };
}
function audio() {
  const listeners = {};
  return {
    currentTime: 9, paused: true, playCount: 0, pauseCount: 0, listeners,
    addEventListener: function (name, callback) { listeners[name] = callback; },
    pause: function () { this.pauseCount += 1; this.paused = true; if (listeners.pause) listeners.pause(); },
    play: function () { this.playCount += 1; this.paused = false; if (listeners.play) listeners.play(); return { catch: function () {} }; },
    emit: function (name) { if (listeners[name]) listeners[name](); }
  };
}
function article(s, a) {
  return { querySelector: (selector) => selector === ".gw-example-main" ? s : (selector === ".gw-example-audio audio" ? a : null) };
}
const firstSentence = sentence("Er kommt allein.");
const secondSentence = sentence("Sie lernt Deutsch.");
const silentSentence = sentence("Kein Audio.");
const firstAudio = audio();
const secondAudio = audio();
const articles = [article(firstSentence, firstAudio), article(secondSentence, secondAudio), article(silentSentence, null)];
globalThis.document = { querySelectorAll: (selector) => selector === ".gw-example" ? articles : [] };
''' + script + r'''
if (globalThis.goetheWerkstattExampleAudio.count !== 2) throw new Error("wrong playable count");
if (!firstSentence.classList.contains("gw-example-playable")) throw new Error("sentence not playable");
if (silentSentence.classList.contains("gw-example-playable")) throw new Error("silent sentence is playable");
if (firstSentence.attributes.role !== "button" || firstSentence.attributes.tabindex !== "0") throw new Error("missing accessibility attributes");
function event(key) {
  return { key, prevented: false, stopped: false, preventDefault: function () { this.prevented = true; }, stopPropagation: function () { this.stopped = true; } };
}
let click = event();
firstSentence.listeners.click(click);
if (!click.prevented || !click.stopped || firstAudio.playCount !== 1 || firstAudio.currentTime !== 0) throw new Error("click did not play from start");
if (!firstSentence.classList.contains("gw-example-playing")) throw new Error("missing playing state");
firstAudio.currentTime = 4;
firstSentence.listeners.click(event());
if (firstAudio.playCount !== 2 || firstAudio.currentTime !== 0) throw new Error("second click did not replay");
secondSentence.listeners.click(event());
if (!firstAudio.paused || firstAudio.currentTime !== 0 || secondAudio.playCount !== 1) throw new Error("switch did not stop previous audio");
secondAudio.emit("ended");
if (secondSentence.classList.contains("gw-example-playing")) throw new Error("ended state remained active");
const key = event(" ");
secondSentence.listeners.keydown(key);
if (!key.prevented || !key.stopped || secondAudio.playCount !== 2) throw new Error("space did not play safely");
'''
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)


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


def test_english_back_runtime_grades_optional_sein_and_articles():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the typed-answer JavaScript regression test")
    back = (gw.DESIGN / "back_english.html").read_text(encoding="utf-8")
    checker = back.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = r'''
const vm = require("vm");
const checker = CHECKER;
function grade(raw, lemma, acceptedAnswers, acceptedArticles, article) {
  const values = {
    "gw-source-id": "test", "gw-lemma": lemma,
    "gw-accepted-answers": acceptedAnswers, "gw-accepted-articles": acceptedArticles,
    "gw-accepted-full-answers": article ? article + " " + lemma : lemma,
    "gw-production-enabled": "1", "gw-article": article || ""
  };
  const classes = new Set();
  const result = {
    textContent: "", dataset: {},
    classList: {
      add: (...values) => values.forEach((value) => classes.add(value)),
      remove: (...values) => values.forEach((value) => classes.delete(value))
    },
    appendChild: function () {}
  };
  const context = {
    sessionStorage: { getItem: () => raw },
    document: {
      getElementById: (id) => id === "gw-result" ? result : ({ textContent: values[id] || "" }),
      createElement: () => ({ textContent: "" })
    }
  };
  context.globalThis = context;
  vm.runInNewContext(checker, context);
  return classes.has("gw-correct");
}
[
  "dabei sein", "dagegen sein", "dafür sein", "einverstanden sein",
  "erkältet sein", "erlaubt sein", "fertig sein", "fit sein", "gültig sein",
  "unterwegs sein", "verabredet sein", "verboten sein"
].forEach(function (answer) {
  const core = answer.replace(/ sein$/, "");
      if (grade(core, answer, answer, "", "")) throw new Error("bare state marked exact: " + answer);
      if (!grade(answer, answer, answer, "", "")) throw new Error("full state rejected: " + answer);
});
    if (grade("erkaeltet", "erkältet sein", "erkältet sein", "", "")) throw new Error("transliterated state marked exact");
["an sein", "aus sein", "auf sein", "weg sein", "zu sein"].forEach(function (answer) {
  if (grade(answer.replace(/ sein$/, ""), answer, answer, "", "")) throw new Error("short particle accepted: " + answer);
});
if (!grade("Bett", "Bett", "Bett", "das", "das")) throw new Error("bare noun rejected");
if (!grade("bett", "Bett", "Bett", "das", "das")) throw new Error("lowercase bare noun rejected");
if (!grade("das Bett", "Bett", "Bett", "das", "das")) throw new Error("correct article rejected");
if (!grade("das bett", "Bett", "Bett", "das", "das")) throw new Error("lowercase full noun rejected");
if (grade("die Bett", "Bett", "Bett", "das", "das")) throw new Error("wrong article accepted");
if (grade("", "erkältet sein", "erkältet sein", "", "")) throw new Error("blank accepted");
'''.replace("CHECKER", json.dumps(checker))
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)


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
let terms = configure({"gw-lemma": "schaffen", "gw-verb-forms": "schafft, hat geschafft", "gw-pos": "v."});
let source = "Kannst du mir bitte helfen? Ich schaffe das nicht alleine.";
let found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (found.join("|") !== "schaffe") throw new Error("missing regular first-person form: " + found.join("|"));
terms = configure({"gw-lemma": "Apfel", "gw-noun-forms": "-Ä", "gw-pos": "n."});
if (!terms.includes("Äpfel")) throw new Error("missing umlaut plural");
terms = configure({"gw-lemma": "anfangen", "gw-verb-forms": "fängt an, hat angefangen", "gw-pos": "v."});
source = "Hier fängt die Straße an.";
found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (!found.includes("fängt") || !found.includes("an")) throw new Error("missing separable verb parts");
terms = configure({"gw-lemma": "abfahren", "gw-verb-forms": "fährt ab, ist abgefahren", "gw-pos": "v."});
source = "Wir fahren um zwölf Uhr ab.";
found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (found.join("|") !== "fahren|ab") throw new Error("missing infinitive stem or particle: " + found.join("|"));
for (const [lemma, form, base, particle, sentence] of [
  ["ausgehen", "geht aus, ist ausgegangen", "gehen", "aus", "Gehen wir am Freitag zusammen aus?"],
  ["einziehen", "zieht ein, ist eingezogen", "ziehen", "ein", "Im Juni ziehen unsere neuen Nachbarn ein."],
  ["herstellen", "stellt her, hat hergestellt", "stellen", "her", "In unserer Firma stellen wir Möbel her."]
]) {
  terms = configure({"gw-lemma": lemma, "gw-verb-forms": form, "gw-pos": "v."});
  found = api.matchRanges(sentence, terms).map((range) => sentence.slice(range[0], range[1]));
  if (!found.some((value) => value.toLocaleLowerCase("de-DE") === base) || !found.includes(particle)) throw new Error("missing parts for " + lemma);
}
terms = configure({"gw-lemma": "Bekannte", "gw-noun-forms": "-n", "gw-pos": "n."});
source = "Ein Bekannter von mir heißt Klaus.";
found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (found.length !== 1 || found[0] !== "Bekannter") throw new Error("missing full nominalized inflection");
terms = configure({"gw-lemma": "Bekannte (männlich)", "gw-noun-forms": "-n", "gw-pos": "n."});
found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (found.length !== 1 || found[0] !== "Bekannter") throw new Error("gender qualifier blocked inflection");
terms = configure({"gw-lemma": "einverstanden sein", "gw-verb-forms": "ist einverstanden, war einverstanden", "gw-pos": "v."});
if (terms.includes("verstanden")) throw new Error("multi-word phrase treated as separable verb");
terms = configure({"gw-lemma": "an", "gw-pos": "prep."});
source = "Kann ich an der Ampel halten?";
found = api.matchRanges(source, terms).map((range) => source.slice(range[0], range[1]));
if (found.length !== 1 || found[0] !== "an") throw new Error("short target matched inside another word");
'''
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)


def test_field_contract_is_stable():
    assert gw.FIELDS[0] == "Lemma"
    assert gw.FIELDS[gw.FIELDS.index("LegacyGUID") + 1:] == [
        "AcceptedFullAnswersDE", "ProductionEnabled", "ProductionHint", "ExampleTargetSpansJSON",
    ]
    assert len(gw.FIELDS) == 36
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
