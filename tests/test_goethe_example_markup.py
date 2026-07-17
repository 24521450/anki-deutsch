from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_examples as examples  # noqa: E402


def test_render_overflow_declares_each_example_language():
    rendered = examples.render_overflow(
        [{"de": "Grüße & mehr", "en": "greetings & more", "audio": "[sound:x.mp3]"}]
    )

    assert '<div class="gw-example-main gw-example-de" lang="de">' in rendered
    assert '<div class="gw-example-sub" lang="en">' in rendered
    assert examples.parse_overflow(rendered) == [
        {"de": "Grüße & mehr", "en": "greetings & more", "audio": "[sound:x.mp3]"}
    ]


def test_parse_overflow_remains_compatible_with_existing_markup():
    legacy = (
        '<article class="gw-example">'
        '<div class="gw-example-main gw-example-de">Guten Tag</div>'
        '<div class="gw-example-sub">Good afternoon</div>'
        "</article>"
    )

    assert examples.parse_overflow(legacy) == [
        {"de": "Guten Tag", "en": "Good afternoon", "audio": ""}
    ]
