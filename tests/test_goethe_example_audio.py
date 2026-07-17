from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_example_audio as audio  # noqa: E402
import goethe_examples  # noqa: E402


def test_spoken_text_normalizes_only_tts_punctuation():
    assert audio.spoken_text("  –  Woher kommen Sie? / Aus Frankreich. ") == "Woher kommen Sie? — Aus Frankreich."
    assert audio.spoken_text("Das kostet 10 Euro (inklusive).") == "Das kostet 10 Euro (inklusive)."


def test_spoken_text_converts_html_dialogue_break_to_a_pause():
    assert audio.spoken_text(
        "Willst du diese Jacke?<br>– Nein, ich möchte die andere."
    ) == "Willst du diese Jacke? Nein, ich möchte die andere."


def test_voice_and_request_id_are_deterministic():
    text = "Guten Morgen."
    voice = audio.voice_for(text)
    assert voice in audio.EDGE_CONFIG["voices"]
    assert audio.voice_for(text) == voice
    assert audio.request_id(text, voice) == audio.request_id(text, voice)


def test_expected_audio_fields_covers_overflow():
    fields: dict[str, str] = {}
    rows = [{"de": f"Satz {i}", "en": f"Sentence {i}", "audio": "old"} for i in range(1, 6)]
    goethe_examples.render_fields(fields, rows)
    occurrences = []
    unique = {}
    for index, row in enumerate(rows, 1):
        key = f"id-{index}"
        occurrences.append({"index": index, "de": row["de"], "en": row["en"], "audio_id": key})
        unique[key] = {"media_name": f"edge-{index}.mp3"}
    manifest = {"notes": {"1": {"occurrences": occurrences}}, "unique": unique}
    rendered = audio.expected_audio_fields(1, manifest, fields)
    assert "edge-4.mp3" in rendered["Example4Audio"]
    assert goethe_examples.parse_overflow(rendered["MoreExamplesHTML"])[0]["audio"].endswith('edge-5.mp3"></audio>')


def test_media_name_is_content_hash_scoped():
    item = {"sha256": "a" * 64}
    assert f"_goethe_example_edge_{item['sha256']}.mp3" == "_goethe_example_edge_" + "a" * 64 + ".mp3"
