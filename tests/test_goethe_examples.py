from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_examples as examples  # noqa: E402


def test_merge_dialogue_replies_keeps_prompt_and_reply_in_one_example():
    rows = [
        {"de": "WiIlst du diese Jacke?", "en": "Do you want this jacket?", "audio": "old-1"},
        {"de": "– Nein, ich möchte die andere.", "en": "– No, I want the other one.", "audio": "old-2"},
        {"de": "Ein weiterer Satz.", "en": "Another sentence.", "audio": "old-3"},
    ]
    assert examples.merge_dialogue_replies(rows) == [
        {
            "de": "Willst du diese Jacke?<br>– Nein, ich möchte die andere.",
            "en": "Do you want this jacket?<br>– No, I want the other one.",
            "audio": "",
        },
        {"de": "Ein weiterer Satz.", "en": "Another sentence.", "audio": "old-3"},
    ]


def test_overflow_round_trip_preserves_optional_audio():
    rows = [
        {"de": "Grüße & mehr", "en": "greetings & more", "audio": ""},
        {"de": "Noch ein Satz.", "en": "Another sentence.", "audio": '<audio src="edge.mp3"></audio>'},
    ]
    rendered = examples.render_overflow(rows)
    assert "Grüße &amp; mehr" in rendered
    assert '<div class="gw-example-audio"><audio src="edge.mp3"></audio></div>' in rendered
    assert examples.parse_overflow(rendered) == rows


def test_legacy_overflow_without_audio_still_parses():
    raw = (
        '<article class="gw-example"><div class="gw-example-main gw-example-de">Hallo.</div>'
        '<div class="gw-example-sub">Hello.</div></article>'
    )
    assert examples.parse_overflow(raw) == [{"de": "Hallo.", "en": "Hello.", "audio": ""}]


def test_render_fields_populates_fixed_and_overflow_audio():
    fields: dict[str, str] = {}
    rows = [
        {"de": f"Satz {index}", "en": f"Sentence {index}", "audio": f"audio-{index}"}
        for index in range(1, 6)
    ]
    examples.render_fields(fields, rows)
    assert fields["Example4Audio"] == "audio-4"
    assert examples.parse_overflow(fields["MoreExamplesHTML"])[0]["audio"] == "audio-5"
    assert examples.parse_fields(fields) == rows
