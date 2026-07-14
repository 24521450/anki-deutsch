"""Parse and render Goethe Werkstatt example fields without losing audio."""
from __future__ import annotations

import html
import re
from typing import Any


ARTICLE_RE = re.compile(
    r'<article class="gw-example"><div class="gw-example-main gw-example-de">(.*?)</div>'
    r'<div class="gw-example-sub">(.*?)</div>'
    r'(?:<div class="gw-example-audio">(.*?)</div>)?</article>',
    re.S,
)


def parse_overflow(value: str) -> list[dict[str, str]]:
    return [
        {"de": html.unescape(german), "en": html.unescape(english), "audio": audio or ""}
        for german, english, audio in ARTICLE_RE.findall(value or "")
    ]


def render_overflow(examples: list[dict[str, Any]]) -> str:
    result = []
    for example in examples:
        audio = str(example.get("audio") or "")
        audio_html = f'<div class="gw-example-audio">{audio}</div>' if audio else ""
        result.append(
            '<article class="gw-example"><div class="gw-example-main gw-example-de">'
            + html.escape(str(example.get("de") or ""))
            + '</div><div class="gw-example-sub">'
            + html.escape(str(example.get("en") or ""))
            + f"</div>{audio_html}</article>"
        )
    return "".join(result)


def parse_fields(fields: dict[str, str]) -> list[dict[str, str]]:
    examples = []
    for index in range(1, 5):
        german = fields.get(f"Example{index}DE", "")
        if german:
            examples.append({
                "de": german,
                "en": fields.get(f"Example{index}EN", ""),
                "audio": fields.get(f"Example{index}Audio", ""),
            })
    examples.extend(parse_overflow(fields.get("MoreExamplesHTML", "")))
    return examples


def render_fields(fields: dict[str, str], examples: list[dict[str, Any]]) -> None:
    for index in range(1, 5):
        example = examples[index - 1] if len(examples) >= index else {"de": "", "en": "", "audio": ""}
        fields[f"Example{index}DE"] = str(example.get("de") or "")
        fields[f"Example{index}EN"] = str(example.get("en") or "")
        fields[f"Example{index}Audio"] = str(example.get("audio") or "")
    fields["MoreExamplesHTML"] = render_overflow(examples[4:])
