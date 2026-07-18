from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "design" / "GoetheWerkstatt"


def read(name: str) -> str:
    return (DESIGN / name).read_text(encoding="utf-8")


def test_direction_metadata_is_hidden_and_answer_hierarchy_is_minimal() -> None:
    front_de = read("front_german.html")
    back_de = read("back_german.html")
    front_en = read("front_english.html")
    back_en = read("back_english.html")

    assert 'data-direction="de-en"' in front_de
    assert 'data-direction="de-en"' in back_de
    assert 'data-direction="en-de"' in front_en
    assert 'data-direction="en-de"' in back_en

    combined = "\n".join((front_de, back_de, front_en, back_en))
    assert 'class="gw-direction"' not in combined
    for visible_label in (">DE → EN<", ">EN → DE<", ">German headword<", ">German answer<", ">English cue<"):
        assert visible_label not in combined

    # Both backs keep the German headword primary and the English meaning secondary.
    assert back_de.index('class="gw-german-answer"') < back_de.index('class="gw-meaning-secondary"')
    assert back_en.index('class="gw-german-answer"') < back_en.index('class="gw-meaning-secondary"')


def test_back_templates_keep_the_headword_in_a_stable_answer_stage() -> None:
    for name in ("back_german.html", "back_english.html"):
        template = read(name)
        assert 'class="gw-answer-stage"' in template
        assert template.index('class="gw-answer-stage"') < template.index('class="gw-answer-hero"')

    css = read("styling.css")
    assert ".gw-answer-stage" in css
    answer_stage_rule = css.split(".gw-answer-stage {", 1)[1].split("}", 1)[0]
    assert "min-height" not in answer_stage_rule
    assert "text-align: center" in answer_stage_rule
    assert "justify-content: center" in css
    assert ".gw-headword { max-width: 100%; margin: 0; color: var(--gw-strong); font-size: clamp(36px, 9vw, 56px);" in css
    assert ".gw-meaning, .gw-german-answer, .gw-production-meaning { margin: 0; color: var(--gw-strong); font-size: clamp(28px, 7vw, 40px);" in css
    assert ".gw-german-answer { font-size: clamp(36px, 9vw, 56px);" in css
    assert ".gw-meaning-secondary { margin: 10px 0 0; color: var(--gw-muted); font-size: clamp(20px, 4.5vw, 24px);" in css
    assert "@keyframes gw-anchor-reveal" in css
    assert "translateY(72px)" not in css
    assert "prefers-reduced-motion: reduce" in css


def test_morphology_labels_and_language_attributes_are_explicit() -> None:
    for name in ("back_german.html", "back_english.html"):
        template = read(name)
        assert "Noun forms" in template
        assert "Verb forms" in template
        assert "Plural: {{NounFormsRaw}}" not in template
        assert 'class="gw-grammar-label" lang="en"' in template
        assert 'class="gw-grammar-value" lang="de"' in template
        assert 'class="gw-example-main gw-example-de" lang="de"' in template
        assert 'class="gw-example-sub" lang="en"' in template
        assert 'class="gw-usage" lang="en"' in template
        assert 'class="gw-regional" lang="de"' in template

    assert 'class="gw-headword" lang="de"' in read("front_german.html")
    front_en = read("front_english.html")
    assert 'class="gw-production-meaning" lang="en"' in front_en
    assert 'class="gw-production-gender" lang="de"' in front_en
    assert 'class="gw-production-hint" lang="en"' in front_en


def test_production_prompt_has_accessible_enabled_and_disabled_branches() -> None:
    template = read("front_english.html")
    assert "{{#ProductionEnabled}}" in template
    assert "{{^ProductionEnabled}}" in template
    assert "gw-production-disabled" in template
    assert 'class="gw-typebox-label"' not in template
    assert 'input.setAttribute("aria-label", "German answer")' in template
    assert 'input.setAttribute("lang", "de")' in template


def test_result_state_hooks_and_hidden_contract_fields_exist() -> None:
    template = read("back_english.html")
    assert 'data-result-state="pending"' in template
    assert 'id="gw-accepted-full-answers"' in template
    assert 'id="gw-example-target-spans"' in template
    script = template.split("<script>", 1)[-1]
    assert 'gw-result--" + state' in script
    assert 'gw-result-" + state' in script
    assert 'gw-result-title' in script
    css = read("styling.css")
    for state in ("correct", "partial", "incorrect"):
        assert f"gw-result--{state}" in css
        assert f"gw-result-{state}" in css


def test_day_night_tokens_viewport_sizing_and_touch_sized_labels() -> None:
    css = read("styling.css")
    for token in ("--gw-page", "--gw-card", "--gw-border", "--gw-strong", "--gw-muted", "--gw-dim"):
        assert token in css
    assert ".card.nightMode" in css
    assert ".card.night_mode" in css
    assert "60dvh" in css
    assert "min-height: 44px" in css
    assert "font-size: 11px" in css
    assert "min-height: 350px" not in css


def test_audio_markup_and_audio_css_hooks_remain_unchanged() -> None:
    # The approved UI work must not alter the existing audio interaction.
    css = read("styling.css")
    audio_rules = [
        '.gw-example-audio { display: none; }',
        '.gw-example-playable { margin: -4px -6px; padding: 4px 6px; border-radius: 6px; cursor: pointer; transition: background-color 150ms ease, color 150ms ease; }',
        '.gw-example-playable::after { content: "  🔊"; color: var(--level); font-size: .78em; opacity: 0; transition: opacity 150ms ease; }',
        '.gw-example-playable:hover { background: rgba(113, 216, 197, .08); }',
        '.gw-example-playable:hover::after, .gw-example-playable:focus-visible::after { opacity: .85; }',
        '.gw-example-playable:focus-visible { outline: 2px solid var(--level); outline-offset: 3px; }',
        '.gw-example-playing { color: var(--level); }',
        '.gw-word-audio { display: none !important; }',
        '.gw-word-playable { padding: .08em .14em; border-radius: 8px; cursor: pointer; transition: background-color 150ms ease, color 150ms ease; }',
        '.gw-word-playable::after { content: "  🔊"; color: var(--level); font-size: .38em; opacity: 0; transition: opacity 150ms ease; vertical-align: middle; }',
        '.gw-word-playable:hover { background: rgba(113, 216, 197, .08); }',
        '.gw-word-playable:hover::after, .gw-word-playable:focus-visible::after { opacity: .85; }',
        '.gw-word-playable:focus-visible { outline: 2px solid var(--level); outline-offset: 3px; }',
        '.gw-word-playing { color: var(--level); }',
    ]
    for rule in audio_rules:
        assert rule in css
    for name in ("front_german.html", "back_german.html", "back_english.html"):
        template = read(name)
        assert '{{#WordAudio}}<span class="gw-word-audio" hidden>{{WordAudio}}</span>{{/WordAudio}}' in template
        assert "{{WordAudioController}}" in template


def test_target_highlighter_has_precomputed_path_and_preserves_markup() -> None:
    script = read("target_highlighter.js")
    assert 'text("gw-example-target-spans")' in script
    assert "parsePrecomputed" in script
    assert "validateRanges" in script
    assert 'targetRaw === "" ? terms() : null' in script
    assert "createTreeWalker" in script
    assert "splitText" in script
    assert "replaceChildren" not in script
    assert 'setAttribute("lang", "de")' in script


def test_target_highlighter_range_api_rejects_invalid_ranges() -> None:
    node = shutil.which("node")
    if not node:
        return
    script = read("target_highlighter.js")
    harness = r'''
const fields = {};
globalThis.document = {
  getElementById: (id) => ({ textContent: fields[id] || "" }),
  querySelectorAll: () => []
};
''' + script + r'''
const api = globalThis.goetheWerkstattTargetHighlighter;
const good = api.validateRanges("Ich sehe Äpfel.", [[9, 14]]);
if (!good || good[0][0] !== 9 || good[0][1] !== 14) throw new Error("valid range rejected");
if (api.validateRanges("abc", [[0, 4]]) !== null) throw new Error("out-of-bounds range accepted");
if (api.validateRanges("abc", [[0, 2], [1, 3]]) !== null) throw new Error("overlap accepted");
if (api.parsePrecomputed("not json") !== null) throw new Error("invalid JSON accepted");
if (api.parsePrecomputed("[[[0,1]]]").length !== 1) throw new Error("precomputed JSON not parsed");
'''
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_production_grading_exposes_green_amber_red_and_skips_disabled_notes() -> None:
    node = shutil.which("node")
    if not node:
        return
    template = read("back_english.html")
    script = template.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = f"""
const vm = require('vm');
const grading = {json.dumps(script, ensure_ascii=False)};
function run(fields, raw, enabled = true) {{
  const classes = new Set();
  const result = {{ textContent: '', dataset: {{}}, children: [], classList: {{
    add: (...names) => names.forEach((name) => classes.add(name)),
    remove: (...names) => names.forEach((name) => classes.delete(name)),
  }}, appendChild: (child) => result.children.push(child) }};
  const nodes = {{ 'gw-result': enabled ? result : null }};
  Object.keys(fields).forEach((key) => {{ nodes[key] = {{ textContent: fields[key] }}; }});
  const context = {{
    document: {{
      getElementById: (id) => nodes[id] || null,
      createElement: () => ({{ className: '', textContent: '' }}),
    }},
    sessionStorage: {{ getItem: () => raw }},
    globalThis: {{}},
  }};
  vm.runInNewContext(grading, context);
  return {{ state: result.dataset.resultState, classes: Array.from(classes), title: result.children[0] && result.children[0].textContent }};
}}
const base = {{
  'gw-production-enabled': '1', 'gw-source-id': 'test', 'gw-lemma': 'Fahrrad',
  'gw-accepted-answers': 'Fahrrad', 'gw-accepted-full-answers': 'das Fahrrad',
  'gw-accepted-articles': 'das', 'gw-article': 'das'
}};
for (const answer of ['das Fahrrad', 'das fahrrad', 'Fahrrad', 'fahrrad']) {{
  const value = run(base, answer);
  if (value.state !== 'correct' || !value.classes.includes('gw-result--correct')) throw Error('accepted bicycle answer not green: ' + answer);
}}
let value = run(base, 'die Fahrrad');
if (value.state !== 'partial') throw Error('wrong article not amber');
value = run(base, 'die Katze');
if (value.state !== 'incorrect' || !value.classes.includes('gw-result--incorrect')) throw Error('wrong lexeme not red');
const state = Object.assign({{}}, base, {{ 'gw-lemma': 'erkältet sein', 'gw-accepted-answers': 'erkältet sein', 'gw-accepted-full-answers': 'erkältet sein', 'gw-accepted-articles': '', 'gw-article': '' }});
value = run(state, 'erkaeltet');
if (value.state !== 'partial') throw Error('optional sein/transliteration not amber');
const phrase = Object.assign({{}}, base, {{ 'gw-lemma': 'Das macht nichts.', 'gw-accepted-answers': 'Das macht nichts.', 'gw-accepted-full-answers': 'Das macht nichts.', 'gw-accepted-articles': '', 'gw-article': '' }});
value = run(phrase, 'der macht nichts');
if (value.state !== 'incorrect') throw Error('fixed phrase treated as article-bearing');
const disabled = Object.assign({{}}, base, {{ 'gw-production-enabled': '' }});
value = run(disabled, '', false);
if (value.state !== undefined) throw Error('disabled note rendered a grading result');
const reflexive = Object.assign({{}}, base, {{ 'gw-lemma': 'anziehen', 'gw-accepted-answers': 'sich anziehen', 'gw-accepted-full-answers': 'sich anziehen|s anziehen', 'gw-accepted-articles': '', 'gw-article': '' }});
for (const answer of ['sich anziehen', 's anziehen']) {{
  value = run(reflexive, answer);
  if (value.state !== 'correct') throw Error('reflexive answer not green: ' + answer);
}}
for (const answer of ['anziehen', '(sich) anziehen', 'mich anziehen']) {{
  value = run(reflexive, answer);
  if (value.state !== 'incorrect') throw Error('bare/parenthetical reflexive accepted: ' + answer);
}}
const ordinal = Object.assign({{}}, base, {{ 'gw-lemma': 'dritte', 'gw-accepted-answers': 'dritte', 'gw-accepted-full-answers': 'dritte', 'gw-accepted-articles': '', 'gw-article': '' }});
for (const answer of ['dritte']) {{
  value = run(ordinal, answer);
  if (value.state !== 'correct') throw Error('ordinal answer not green');
}}
for (const answer of ['der dritte', 'die dritte', 'das dritte']) {{
  value = run(ordinal, answer);
  if (value.state !== 'incorrect') throw Error('article ordinal accepted: ' + answer);
}}
"""
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
