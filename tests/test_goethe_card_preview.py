from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_werkstatt_migrate as gw  # noqa: E402
import render_goethe_card_preview as preview  # noqa: E402


def test_checked_in_preview_is_generated_from_current_sources() -> None:
    output = ROOT / "docs" / "goethe_card_preview.html"
    assert output.read_text(encoding="utf-8") == preview.build_preview()


def test_preview_contains_both_directions_themes_and_current_field_contract() -> None:
    rendered = preview.build_preview()
    assert "Generated from Goethe Werkstatt templates" in rendered
    assert "data-direction" in rendered
    assert "de-en" in rendered
    assert "en-de" in rendered
    assert "nightMode" in rendered
    assert "Recognition" in rendered
    assert "Production" in rendered
    assert "[sound:" not in rendered
    assert "{{" not in rendered
    for field in gw.FIELDS:
        assert field in rendered


def test_preview_host_script_is_valid_javascript() -> None:
    node = shutil.which("node")
    if not node:
        return
    rendered = preview.build_preview()
    start = rendered.rfind("<script>") + len("<script>")
    end = rendered.rfind("</script>")
    script = rendered[start:end]
    result = subprocess.run([node, "--check", "-"], input=script.encode("utf-8"), capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


def test_preview_card_documents_have_no_unresolved_template_tokens() -> None:
    templates = gw.templates()
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    fields = preview.samples()["noun"]
    for direction, side in ((list(templates)[0], "Front"), (list(templates)[0], "Back"), (list(templates)[1], "Front"), (list(templates)[1], "Back")):
        document = preview.card_document(templates[direction][side], css, fields, "day")
        assert "{{" not in document
        assert "[sound:" not in document
        assert re.search(r'<main class="gw-card"', document)


def test_preview_includes_large_pronunciation_variant_fixture() -> None:
    fields = preview.samples()["variants"]
    assert fields["WordAudioVariantsHTML"].count('class="gw-word-variant"') == 2
    assert fields["WordAudioVariantsHTML"].count('class="gw-word-variant-separator"') == 1
    assert 'aria-hidden="true">/</span>' in fields["WordAudioVariantsHTML"]
    assert "der Frühling" in fields["WordAudioVariantsHTML"]
    assert "das Frühjahr" in fields["WordAudioVariantsHTML"]
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    assert ".gw-german-answer .gw-word-variant" in css
    assert ".gw-german-answer .gw-word-variants { display: flex; width: 100%; }" in css
    assert "font: inherit !important" in css
    document = preview.card_document(
        gw.templates()["German → English"]["Back"], css, fields, "night"
    )
    assert document.count('class="gw-word-variant"') == 2
    assert document.count('class="gw-word-variant-separator"') == 1
    assert "font: inherit !important" in document


def test_production_preview_passes_the_typed_answer_through_frontside() -> None:
    templates = gw.templates()
    css = (gw.DESIGN / "styling.css").read_text(encoding="utf-8")
    fields = preview.samples()["noun"]
    frontside = preview._preview_frontside(
        fields, templates["English \u2192 German"]["Front"]
    )
    document = preview.card_document(
        templates["English \u2192 German"]["Back"],
        css,
        {**fields, "FrontSide": frontside},
        "day",
    )

    assert 'id="gw-native-answer-source"' in document
    assert '<code id="typeans"><span class="typeBad">der Anschluss</span></code>' in document
    assert "sessionStorage.setItem" not in document
