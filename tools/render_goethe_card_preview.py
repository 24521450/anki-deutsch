"""Build the self-contained Goethe Werkstatt card preview from live templates.

The preview deliberately renders empty audio fields.  Audio markup and controllers
are still loaded from the design templates, so the visual preview cannot silently
drift from the Anki model while avoiding a browser-dependent audio implementation.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any

import goethe_werkstatt_migrate as gw


OUTPUT = gw.ROOT / "docs" / "goethe_card_preview.html"
TOKEN_RE = re.compile(r"{{([#^/!]?)([^{}]+)}}")


def _span(sentence: str, target: str) -> list[list[int]]:
    start = sentence.index(target)
    return [[start, start + len(target)]]


def _sample(
    source_id: str,
    lemma: str,
    meaning: str,
    examples: list[tuple[str, str, str]],
    *,
    cefr: str = "A1",
    pos: str = "n.",
    article: str = "der",
    gender: str = "m.",
    noun_forms: str = "-¨e",
    verb_forms: str = "",
    hint: str = "Include the article when you produce the German answer.",
    production_enabled: bool = True,
    target_terms: list[str] | None = None,
) -> dict[str, str]:
    fields = {name: "" for name in gw.FIELDS}
    fields.update({
        "SourceID": source_id,
        "Lemma": lemma,
        "MeaningEN": meaning,
        "CEFR": cefr,
        "POS": pos,
        "Article": article,
        "Gender": gender,
        "NounFormsRaw": noun_forms,
        "VerbFormsRaw": verb_forms,
        "FormOrVariantNote": "",
        "RegionalVariants": "",
        "AcceptedAnswersDE": lemma,
        "AcceptedFullAnswersDE": f"{article} {lemma}" if article else lemma,
        "AcceptedArticlesDE": article,
        "ProductionEnabled": "1" if production_enabled else "",
        "ProductionHint": hint,
        "WordAudio": "",
        "MoreExamplesHTML": "",
    })
    spans: list[list[list[int]]] = []
    for index, (de, en, target) in enumerate(examples, start=1):
        fields[f"Example{index}DE"] = de
        fields[f"Example{index}EN"] = en
        fields[f"Example{index}Audio"] = ""
        terms = target_terms if target_terms is not None and index == 1 else [target]
        spans.append([item for term in terms for item in _span(de, term)])
    fields["ExampleTargetSpansJSON"] = json.dumps(spans, ensure_ascii=False, separators=(",", ":"))
    return fields


def samples() -> dict[str, dict[str, str]]:
    return {
        "noun": _sample(
            "preview-noun",
            "Anschluss",
            "connection",
            [(
                "In Mannheim haben Sie Anschluss nach Saarbrücken.",
                "In Mannheim you have a connection to Saarbrücken.",
                "Anschluss",
            )],
        ),
        "verb": _sample(
            "preview-verb",
            "anbieten",
            "to offer",
            [
                ("Er hat mir eine Stelle als Verkäuferin angeboten.", "He offered me a job as a saleswoman.", "angeboten"),
                ("Darf ich Ihnen ein Stück Kuchen anbieten?", "May I offer you a piece of cake?", "anbieten"),
            ],
            cefr="A2",
            pos="v.",
            article="",
            gender="",
            noun_forms="",
            verb_forms="bietet an, hat angeboten",
            hint="Watch the separable particle in the present tense.",
        ),
        "disabled": _sample(
            "preview-disabled",
            "Brötchen",
            "bread roll",
            [("Ich hole schnell ein paar Brötchen zum Frühstück.", "I will quickly get a few bread rolls for breakfast.", "Brötchen")],
            cefr="B1",
            pos="n.",
            article="das",
            gender="n.",
            noun_forms="-",
            hint="Production is waiting for a reviewed cue.",
            production_enabled=False,
        ),
    }


def parse_template(source: str) -> list[tuple[Any, ...]]:
    root: list[tuple[Any, ...]] = []
    stack: list[tuple[str, list[tuple[Any, ...]]]] = [("", root)]
    cursor = 0
    for match in TOKEN_RE.finditer(source):
        if match.start() > cursor:
            stack[-1][1].append(("text", source[cursor:match.start()]))
        marker, raw_name = match.groups()
        name = raw_name.strip()
        if marker in {"#", "^"}:
            children: list[tuple[Any, ...]] = []
            stack[-1][1].append(("section", marker, name, children))
            stack.append((name, children))
        elif marker == "/":
            if len(stack) == 1 or stack[-1][0] != name:
                raise ValueError(f"unbalanced template section: {name}")
            stack.pop()
        elif marker == "!":
            pass
        else:
            stack[-1][1].append(("variable", name))
        cursor = match.end()
    if cursor < len(source):
        stack[-1][1].append(("text", source[cursor:]))
    if len(stack) != 1:
        raise ValueError(f"unclosed template section: {stack[-1][0]}")
    return root


def render_nodes(nodes: list[tuple[Any, ...]], fields: dict[str, str]) -> str:
    output: list[str] = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            output.append(node[1])
            continue
        if kind == "variable":
            name = node[1]
            if name.startswith("type:"):
                output.append('<input id="typeans" type="text" autocomplete="off">')
            else:
                output.append(str(fields.get(name, "")))
            continue
        _, marker, name, children = node
        present = bool(str(fields.get(name, "")))
        if (marker == "#" and present) or (marker == "^" and not present):
            output.append(render_nodes(children, fields))
    return "".join(output)


def _js_json(value: Any) -> str:
    # A literal </script> in a JSON string would terminate the host script.
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def card_document(template: str, css: str, fields: dict[str, str], theme: str) -> str:
    source = fields.get("SourceID", "preview")
    answer = fields.get("AcceptedFullAnswersDE") or fields.get("Lemma", "")
    storage_key = f"goethe-werkstatt:{source}:production"
    shim = "<script>globalThis.pycmd=function(){};try{HTMLElement.prototype.focus=function(){};}catch(error){}globalThis.goetheWerkstattAnswers={};globalThis.goetheWerkstattAnswers[%s]=%s;try{sessionStorage.setItem(%s,%s);}catch(error){}</script>" % (
        _js_json(storage_key),
        _js_json(answer),
        _js_json(storage_key),
        _js_json(answer),
    )
    body_class = "card nightMode" if theme == "night" else "card"
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<style>{css}\nhtml,body{{min-height:100%;}}</style></head><body class=\"{body_class}\">"
        f"{shim}{render_nodes(parse_template(template), fields)}</body></html>"
    )


HOST_CSS = r"""
:root { color-scheme: light; --page: #f2f0ea; --card: #fffdf8; --border: #c8d0cb; --text: #304040; --muted: #68756f; --accent: #167866; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; padding: 28px 16px 54px; background: var(--page); color: var(--text); font-family: -apple-system, "Segoe UI", sans-serif; }
.page { width: min(1180px, 100%); margin: auto; }
.masthead { display: flex; justify-content: space-between; gap: 24px; align-items: end; margin-bottom: 20px; }
.eyebrow, .preview-label, button, th { font: 750 10px "Cascadia Mono", ui-monospace, monospace; letter-spacing: .11em; text-transform: uppercase; }
.eyebrow { margin: 0 0 8px; color: var(--accent); }
h1 { margin: 0; font-size: clamp(30px, 5vw, 52px); letter-spacing: -.04em; }
.intro { max-width: 700px; margin: 11px 0 0; color: var(--muted); line-height: 1.55; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }
button { padding: 9px 12px; border: 1px solid var(--border); border-radius: 7px; background: var(--card); color: var(--muted); cursor: pointer; }
button[aria-pressed="true"], button:hover { border-color: var(--accent); color: var(--accent); }
.preview-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.preview-label { display: flex; justify-content: space-between; margin: 0 2px 7px; color: var(--muted); }
.preview-label span:last-child { color: var(--accent); }
iframe { display: block; width: 100%; height: 560px; border: 1px solid var(--border); border-radius: 12px; background: transparent; }
.field-panel { margin-top: 26px; }
.field-panel h2 { margin: 0 0 8px; font-size: 22px; }
.field-panel p { margin: 0 0 12px; color: var(--muted); font-size: 13px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; background: var(--card); }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 8px 11px; border-bottom: 1px solid #e2e5df; vertical-align: top; }
tr:last-child th, tr:last-child td { border-bottom: 0; }
th { color: var(--muted); font-size: 9px; }
tbody th { width: 190px; color: var(--accent); text-transform: none; letter-spacing: 0; }
td { font-size: 12px; line-height: 1.45; }
.empty { color: #89948e; font-style: italic; }
@media (max-width: 760px) { body { padding: 20px 10px 40px; } .masthead { display: block; } .preview-grid { grid-template-columns: 1fr; } iframe { height: 590px; } }
"""


def build_preview() -> str:
    model_templates = gw.templates()
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    model_samples = samples()
    cards = {
        "front-de": ("German → English", "Front"),
        "back-de": ("German → English", "Back"),
        "front-en": ("English → German", "Front"),
        "back-en": ("English → German", "Back"),
    }
    variants: dict[str, dict[str, dict[str, str]]] = {}
    for sample_name, fields in model_samples.items():
        variants[sample_name] = {"day": {}, "night": {}}
        for card_id, (direction, side) in cards.items():
            template = model_templates[direction][side]
            for theme in ("day", "night"):
                variants[sample_name][theme][card_id] = card_document(template, css, fields, theme)
    field_values = {
        name: {sample_name: fields.get(name, "") for sample_name, fields in model_samples.items()}
        for name in gw.FIELDS
    }
    labels = {
        "front-de": ("Front", "Recognition"),
        "back-de": ("Back", "Recognition"),
        "front-en": ("Front", "Production"),
        "back-en": ("Back", "Production"),
    }
    variant_json = _js_json(variants)
    fields_json = _js_json(field_values)
    sample_options = "".join(
        f'<button type="button" data-sample="{html.escape(name)}" aria-pressed="{str(index == 0).lower()}">{html.escape(name)}</button>'
        for index, name in enumerate(model_samples)
    )
    cards_html = "".join(
        f'<div><p class="preview-label"><span>{html.escape(left)}</span><span>{html.escape(right)}</span></p>'
        f'<iframe id="{card_id}" title="{html.escape(right)} {html.escape(left)}" sandbox="allow-scripts"></iframe></div>'
        for card_id, (left, right) in labels.items()
    )
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Goethe Werkstatt — generated card preview</title><style>{HOST_CSS}</style></head>
<body><main class="page">
  <header class="masthead"><div><p class="eyebrow">Generated from Goethe Werkstatt templates</p><h1>Goethe Werkstatt</h1><p class="intro">This preview is rebuilt from the live Anki templates, CSS and field contract. Audio fields are intentionally empty.</p></div></header>
  <nav class="controls" aria-label="Preview controls"><span class="eyebrow">Sample</span>{sample_options}<span class="eyebrow">Theme</span><button type="button" data-theme="day" aria-pressed="true">Day</button><button type="button" data-theme="night" aria-pressed="false">Night</button></nav>
  <section class="preview-grid" aria-label="Card preview">{cards_html}</section>
  <section class="field-panel"><h2>Field inspector</h2><p>Field names come from the live Goethe Werkstatt model contract.</p><div class="table-wrap"><table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody id="field-rows"></tbody></table></div></section>
</main><script>
const variants = {variant_json};
const fieldValues = {fields_json};
const labels = { _js_json(labels) };
let sample = { _js_json(next(iter(model_samples))) };
let theme = "day";
function escapeCell(value) {{
  return String(value).replace(/[&<>"]/g, function (char) {{
    if (char === "&") return "&amp;";
    if (char === "<") return "&lt;";
    if (char === ">") return "&gt;";
    return "&quot;";
  }});
}}
function renderCards() {{
  Object.keys(labels).forEach(function (id) {{ document.getElementById(id).srcdoc = variants[sample][theme][id]; }});
  document.querySelectorAll("button[data-sample]").forEach(function (button) {{ button.setAttribute("aria-pressed", String(button.dataset.sample === sample)); }});
  document.querySelectorAll("button[data-theme]").forEach(function (button) {{ button.setAttribute("aria-pressed", String(button.dataset.theme === theme)); }});
  document.getElementById("field-rows").innerHTML = Object.keys(fieldValues).map(function (field) {{
    const value = fieldValues[field][sample] || "";
    return '<tr><th>' + escapeCell(field) + '</th><td class="' + (value ? '' : 'empty') + '">' + escapeCell(value || 'empty') + '</td></tr>';
  }}).join("");
}}
document.querySelectorAll("button[data-sample]").forEach(function (button) {{ button.addEventListener("click", function () {{ sample = button.dataset.sample; renderCards(); }}); }});
document.querySelectorAll("button[data-theme]").forEach(function (button) {{ button.addEventListener("click", function () {{ theme = button.dataset.theme; renderCards(); }}); }});
renderCards();
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the checked-in preview is stale")
    args = parser.parse_args(argv)
    rendered = build_preview()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"stale preview: {args.output}")
            return 1
        print(f"preview current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote preview: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
