from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "design" / "GoetheWerkstatt"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_goethe_dictionary_links as dictionary_links  # noqa: E402
import goethe_werkstatt_migrate as gw  # noqa: E402


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


def test_dictionary_badge_uses_direct_source_keyed_entries_on_all_card_faces() -> None:
    link_map = json.loads((ROOT / "review" / "goethe_dictionary_links.json").read_text(encoding="utf-8"))
    dose_index = link_map["source_to_url"]["B1-MAIN-0554"]
    assert link_map["urls"][dose_index] == {
        "provider": "Duden",
        "url": "https://www.duden.de/rechtschreibung/Dose",
    }
    fruehling_index = link_map["source_to_url"]["A1-84887177245"]
    assert link_map["urls"][fruehling_index] == {
        "provider": "Duden",
        "url": "https://www.duden.de/rechtschreibung/Fruehling",
    }
    assert link_map["counts"]["notes"] == 3420
    assert link_map["counts"]["unresolved"] == 0
    assert link_map["counts"]["ambiguous_duden_candidates"] == 0
    for source_id, expected_url in dictionary_links.MANUAL_DUDEN_BY_SOURCE.items():
        index = link_map["source_to_url"].get(source_id)
        assert index is not None, source_id
        assert link_map["urls"][index] == {"provider": "Duden", "url": expected_url}
    for item in link_map["urls"]:
        if item["provider"] == "Duden":
            assert dictionary_links.direct_duden_url(item["url"])
        else:
            assert item["provider"] == "Wiktionary"
            assert dictionary_links.direct_wiktionary_url(item["url"])

    templates = gw.templates()
    faces = {
        "front_german.html": ("German \u2192 English", "Front"),
        "back_german.html": ("German \u2192 English", "Back"),
        "front_english.html": ("English \u2192 German", "Front"),
        "back_english.html": ("English \u2192 German", "Back"),
    }
    for filename, (direction, side) in faces.items():
        source = read(filename)
        rendered = templates[direction][side]
        assert '<a class="gw-brand gw-dictionary-link"' in source
        assert 'data-source-id="{{SourceID}}"' in source
        assert 'data-source-refs="{{SourceRefs}}"' in source
        assert 'target="_blank" rel="noopener noreferrer" hidden' in source
        assert "{{DictionaryLinkController}}" in source
        assert "{{DictionaryLinkController}}" not in rendered
        assert "globalThis.goetheWerkstattDictionaryLinks" in rendered
        assert "https://www.duden.de/rechtschreibung/Dose" in rendered


def test_dictionary_link_controller_resolves_duden_and_keeps_missing_links_hidden() -> None:
    node = shutil.which("node")
    if not node:
        return
    controller = gw.dictionary_link_controller()
    harness = f"""
const vm = require('vm');
const code = {json.dumps(controller, ensure_ascii=False)};
function run(sourceId, refs) {{
  const link = {{
    href: '', hidden: true, textContent: 'DUDEN', attributes: {{
      'data-source-id': sourceId, 'data-source-refs': refs
    }},
    getAttribute(name) {{ return this.attributes[name] || ''; }},
    setAttribute(name, value) {{ this.attributes[name] = value; }}
  }};
  const context = {{ document: {{ querySelector: () => link }} }};
  vm.runInNewContext(code, context);
  return link;
}}
let link = run('B1-MAIN-0554', '');
if (link.hidden || link.textContent !== 'DUDEN') throw Error('Duden badge did not become visible');
if (link.href !== 'https://www.duden.de/rechtschreibung/Dose') throw Error('Dose did not use its direct Duden entry');
link = run('', 'B1-MAIN-0554');
if (link.hidden || link.href !== 'https://www.duden.de/rechtschreibung/Dose') throw Error('SourceRefs fallback failed');
link = run('unresolved-test-entry', '');
if (!link.hidden || link.href) throw Error('unmapped badge must stay hidden');
"""
    result = subprocess.run([node], input=harness, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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
    assert ".gw-answer-title .gw-german-answer { width: 100%; min-width: 0; }" in css
    assert ".gw-german-answer .gw-word-variants { display: flex; width: 100%; }" in css
    assert "border: 0; border-radius: 8px; padding: 0 .14em" in css
    assert ".gw-word-variant-separator { display: inline-flex; align-items: center; font: inherit; line-height: inherit; pointer-events: none; }" in css
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
    assert '<div class="gw-typebox" lang="de">{{type:Lemma}}</div>' in template


def test_production_prompt_uses_native_anki_typing_and_answer_side_bridge() -> None:
    template = read("front_english.html")
    back = read("back_english.html")
    assert "{{type:Lemma}}" in template
    assert '<input id="typeans"' not in template
    assert "sessionStorage" not in template
    assert 'id="gw-native-answer-source" hidden aria-hidden="true">{{FrontSide}}' in back
    assert 'document.getElementById("gw-native-answer-source")' in back

def test_result_state_hooks_and_hidden_contract_fields_exist() -> None:
    template = read("back_english.html")
    assert 'data-result-state="pending"' in template
    assert 'id="gw-accepted-full-answers"' in template
    assert 'id="gw-example-target-spans"' in template
    script = template.split("<script>", 1)[-1]
    assert 'gw-result--" + state' in script
    assert 'gw-result-" + state' in script
    assert 'gw-result-title' in script
    assert '"Expected answer: " + fullAnswers.join(" / ")' in script
    assert '"Canonical: " + fullAnswers.join(" / ")' not in script
    assert 'child.classList.contains("typeBad")' in script
    assert 'child.nodeName.toUpperCase() === "BR"' in script
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
    css = read("styling.css")
    assert 'text("gw-example-target-spans")' in script
    assert "parsePrecomputed" in script
    assert "validateRanges" in script
    assert 'targetRaw === "" ? terms() : null' in script
    assert "createTreeWalker" in script
    assert "splitText" in script
    assert "replaceChildren" not in script
    assert 'setAttribute("lang", "de")' in script
    assert "gw-target-fallback" not in script
    assert "gw-target-fallback" not in css


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


def test_native_answer_survives_a_fresh_mobile_back_context() -> None:
    node = shutil.which("node")
    if not node:
        return
    template = read("back_english.html")
    script = template.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = f"""
const vm = require('vm');
const grading = {json.dumps(script, ensure_ascii=False)};
const fields = {{
  'gw-production-enabled': '1', 'gw-source-id': 'test', 'gw-lemma': 'Fahrrad',
  'gw-accepted-answers': 'Fahrrad', 'gw-accepted-full-answers': 'das Fahrrad',
  'gw-accepted-articles': 'das', 'gw-article': 'das'
}};
function gradeNative(tokens) {{
  const result = {{ dataset: {{}}, children: [], classList: {{
    add: () => {{}}, remove: () => {{}}
  }}, appendChild: (child) => result.children.push(child) }};
  const typeans = {{ childNodes: tokens.map((token) => token.br
    ? {{ nodeName: 'BR', textContent: '' }}
    : {{ nodeName: 'SPAN', textContent: token.text, classList: {{ contains: (name) => name === token.className }} }}) }};
  const source = {{ querySelector: (selector) => selector === '#typeans' ? typeans : null }};
  const nodes = {{ 'gw-result': result, 'gw-native-answer-source': source }};
  Object.keys(fields).forEach((key) => {{ nodes[key] = {{ textContent: fields[key] }}; }});
  const context = {{
    document: {{
      getElementById: (id) => nodes[id] || null,
      createElement: () => ({{ className: '', textContent: '' }})
    }},
    sessionStorage: {{ getItem: () => null }},
    globalThis: {{}}
  }};
  vm.runInNewContext(grading, context);
  return result;
}}
let result = gradeNative([
  {{ className: 'typeBad', text: 'das ' }},
  {{ className: 'typeGood', text: 'Fahrrad' }},
  {{ br: true }}
]);
if (result.dataset.resultState !== 'correct') throw Error('native answer was lost without front-side storage');
result = gradeNative([
  {{ className: 'typeBad', text: 'die ' }},
  {{ className: 'typeGood', text: 'Katze' }},
  {{ className: 'typeMissed', text: '-' }},
  {{ br: true }},
  {{ className: 'typeGood', text: 'Fahrrad' }}
]);
if (result.dataset.resultState !== 'incorrect') throw Error('native wrong answer was not graded');
if (result.children[1].textContent !== 'Your answer: die Katze') throw Error('native typed text was not recovered from the first comparison row');
result = gradeNative([]);
if (result.dataset.resultState !== 'incorrect' || result.children[1].textContent !== 'Your answer: (no answer)') throw Error('empty native input was not recognized');
"""
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
for (const [canonical, typed] of [
  ['Fußball', 'fussball'], ['Äpfel', 'aepfel'], ['schön', 'schoen'],
  ['für', 'fuer'], ['grüßen', 'gruessen']
]) {{
  const fields = Object.assign({{}}, base, {{
    'gw-lemma': canonical, 'gw-accepted-answers': canonical,
    'gw-accepted-full-answers': canonical, 'gw-accepted-articles': '', 'gw-article': ''
  }});
  value = run(fields, typed);
  if (value.state !== 'correct' || value.title !== '✓ Correct' || value.classes.includes('gw-result--partial')) {{
    throw Error('German ASCII equivalent not green: ' + canonical + ' / ' + typed);
  }}
}}
for (const [canonical, typed] of [['für', 'fur'], ['grüßen', 'grussen']]) {{
  const fields = Object.assign({{}}, base, {{
    'gw-lemma': canonical, 'gw-accepted-answers': canonical,
    'gw-accepted-full-answers': canonical, 'gw-accepted-articles': '', 'gw-article': ''
  }});
  value = run(fields, typed);
  if (value.state !== 'incorrect') throw Error('missing transliteration letter accepted: ' + typed);
}}
const trailingHyphen = Object.assign({{}}, base, {{
  'gw-lemma': 'unser-', 'gw-accepted-answers': 'unser-',
  'gw-accepted-full-answers': 'unser-', 'gw-accepted-articles': '', 'gw-article': ''
}});
for (const answer of ['unser-', 'unser', 'unsere', 'unserer', 'unseres', 'unserem', 'unseren', 'unsers']) {{
  value = run(trailingHyphen, answer);
  if (value.state !== 'correct' || value.title !== '✓ Correct' || value.classes.includes('gw-result--partial')) {{
    throw Error('trailing-hyphen form not green: ' + answer);
  }}
}}
for (const answer of ['unserxyz', 'unser-foo']) {{
  value = run(trailingHyphen, answer);
  if (value.state !== 'incorrect') throw Error('arbitrary trailing-hyphen continuation accepted: ' + answer);
}}
const internalHyphen = Object.assign({{}}, base, {{
  'gw-lemma': 'E-Mail', 'gw-accepted-answers': 'E-Mail',
  'gw-accepted-full-answers': 'E-Mail', 'gw-accepted-articles': '', 'gw-article': ''
}});
value = run(internalHyphen, 'Email');
if (value.state !== 'incorrect') throw Error('internal hyphen was treated as optional');
const authoritativeTrailingHyphen = Object.assign({{}}, trailingHyphen, {{
  'gw-lemma': 'ander-', 'gw-accepted-answers': 'ander|anderen',
  'gw-accepted-full-answers': 'ander|anderen'
}});
for (const answer of ['ander', 'andere', 'anderen']) {{
  value = run(authoritativeTrailingHyphen, answer);
  if (value.state !== 'correct') throw Error('authoritative trailing-hyphen form not green: ' + answer);
}}
const umlautTrailingHyphen = Object.assign({{}}, trailingHyphen, {{
  'gw-lemma': 'nächst-', 'gw-accepted-answers': 'nächst-',
  'gw-accepted-full-answers': 'nächst-'
}});
value = run(umlautTrailingHyphen, 'naechsten');
if (value.state !== 'correct') throw Error('ASCII trailing-hyphen form not green');
for (const [canonical, correctAnswers, partialAnswers] of [
  ['erkältet sein', ['erkältet sein', 'erkältet s', 'erkaeltet sein', 'erkaeltet s'], ['erkältet', 'erkaeltet']],
  ['an sein', ['an sein', 'an s'], ['an']]
]) {{
  const fields = Object.assign({{}}, base, {{
    'gw-lemma': canonical, 'gw-accepted-answers': canonical,
    'gw-accepted-full-answers': canonical, 'gw-accepted-articles': '', 'gw-article': ''
  }});
  for (const answer of correctAnswers) {{
    value = run(fields, answer);
    if (value.state !== 'correct' || value.title !== '✓ Correct') throw Error('terminal sein answer not green: ' + answer);
  }}
  for (const answer of partialAnswers) {{
    value = run(fields, answer);
    if (value.state !== 'partial' || value.title !== '△ Check') throw Error('missing terminal sein not amber: ' + answer);
  }}
}}
const standaloneSein = Object.assign({{}}, base, {{ 'gw-lemma': 'sein', 'gw-accepted-answers': 'sein', 'gw-accepted-full-answers': 'sein', 'gw-accepted-articles': '', 'gw-article': '' }});
value = run(standaloneSein, 's');
if (value.state !== 'incorrect') throw Error('standalone sein incorrectly accepted as s');
value = run(Object.assign({{}}, standaloneSein, {{ 'gw-lemma': 'erkältet sein', 'gw-accepted-answers': 'erkältet sein', 'gw-accepted-full-answers': 'erkältet sein' }}), 'erkältet ss');
if (value.state !== 'incorrect') throw Error('invalid terminal sein abbreviation accepted');
const phrase = Object.assign({{}}, base, {{ 'gw-lemma': 'Das macht nichts.', 'gw-accepted-answers': 'Das macht nichts.', 'gw-accepted-full-answers': 'Das macht nichts.', 'gw-accepted-articles': '', 'gw-article': '' }});
value = run(phrase, 'der macht nichts');
if (value.state !== 'incorrect') throw Error('fixed phrase treated as article-bearing');
const disabled = Object.assign({{}}, base, {{ 'gw-production-enabled': '' }});
value = run(disabled, '', false);
if (value.state !== undefined) throw Error('disabled note rendered a grading result');
const reflexive = Object.assign({{}}, base, {{ 'gw-lemma': '(sich) duschen', 'gw-accepted-answers': 'duschen|sich duschen', 'gw-accepted-full-answers': 'duschen|sich duschen|s duschen', 'gw-accepted-articles': '', 'gw-article': '' }});
for (const answer of ['duschen', 'sich duschen', 's duschen']) {{
  value = run(reflexive, answer);
  if (value.state !== 'correct') throw Error('reflexive answer not green: ' + answer);
}}
for (const answer of ['(sich) duschen', 'mich duschen']) {{
  value = run(reflexive, answer);
  if (value.state !== 'incorrect') throw Error('invalid optional-reflexive answer accepted: ' + answer);
}}
const requiredReflexive = Object.assign({{}}, reflexive, {{ 'gw-lemma': 'sich anmelden', 'gw-accepted-answers': 'sich anmelden', 'gw-accepted-full-answers': 'sich anmelden|s anmelden' }});
value = run(requiredReflexive, 'anmelden');
if (value.state !== 'incorrect') {{
  throw Error('required reflexive was treated as optional');
}}
const weakNoun = Object.assign({{}}, base, {{
  'gw-lemma': 'Angestellter', 'gw-accepted-answers': 'Angestellter|Angestellte',
  'gw-accepted-full-answers': 'der Angestellte|die Angestellte|Angestellter',
  'gw-accepted-articles': 'der', 'gw-article': 'der'
}});
for (const answer of ['der Angestellte', 'die Angestellte', 'Angestellte', 'Angestellter']) {{
  value = run(weakNoun, answer);
  if (value.state !== 'correct') throw Error('reviewed weak-noun answer not green: ' + answer);
}}
const secondWeakNoun = Object.assign({{}}, base, {{
  'gw-lemma': 'Auszubildender', 'gw-accepted-answers': 'Auszubildende',
  'gw-accepted-full-answers': 'der Auszubildende|die Auszubildende|Auszubildender',
  'gw-accepted-articles': 'der', 'gw-article': 'der'
}});
for (const answer of ['der Auszubildende', 'die Auszubildende', 'Auszubildende', 'Auszubildender']) {{
  value = run(secondWeakNoun, answer);
  if (value.state !== 'correct') throw Error('reviewed weak-noun answer not green: ' + answer);
}}
const half = Object.assign({{}}, base, {{
  'gw-lemma': 'ein halb; halb', 'gw-accepted-answers': 'ein halb; halb|ein halb',
  'gw-accepted-full-answers': 'ein halb|halb', 'gw-accepted-articles': '', 'gw-article': ''
}});
for (const answer of ['ein halb', 'halb']) {{
  value = run(half, answer);
  if (value.state !== 'correct') throw Error('half alternative was not green: ' + answer);
}}
value = run(half, 'ein halb; halb');
if (value.state !== 'incorrect') throw Error('source composite was accepted as a full answer');
const lexicalSlash = Object.assign({{}}, base, {{
  'gw-lemma': 'Samstag/Sonnabend', 'gw-accepted-answers': 'Samstag/Sonnabend',
  'gw-accepted-full-answers': 'der Samstag/Sonnabend', 'gw-accepted-articles': 'der', 'gw-article': 'der'
}});
for (const answer of ['der Samstag', 'der Sonnabend', 'Samstag', 'Sonnabend']) {{
  value = run(lexicalSlash, answer);
  if (value.state !== 'correct') throw Error('slash alternative was not green: ' + answer);
}}
const boundSlash = Object.assign({{}}, base, {{
  'gw-lemma': 'Speise-/-speise', 'gw-accepted-answers': 'Speise-/-speise',
  'gw-accepted-full-answers': 'die Speise-/-speise', 'gw-accepted-articles': 'die', 'gw-article': 'die'
}});
for (const answer of ['die Speise-', 'die -speise']) {{
  value = run(boundSlash, answer);
  if (value.state === 'correct') throw Error('bound-form slash was split into an answer: ' + answer);
}}
const missingFullAnswers = Object.assign({{}}, base, {{ 'gw-accepted-full-answers': '' }});
value = run(missingFullAnswers, 'Fahrrad');
if (value.state !== undefined) throw Error('missing full-answer contract did not fail closed');
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
