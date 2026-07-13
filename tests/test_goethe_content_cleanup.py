from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_content_cleanup as cleanup  # noqa: E402


def empty_fields():
    return {name: "" for name in cleanup.gw.FIELDS}


def test_compiled_manifest_has_expected_reviewed_sections():
    manifest = cleanup.json.loads(cleanup.MANIFEST.read_text(encoding="utf-8"))
    assert manifest["counts"] == {
        "field_updates": 610, "meaning_rows": 117, "example_rows": 237,
        "usage_rows": 16, "trimmed_form_notes": 235,
    }


def test_britishise_is_conservative_and_normalizes_can_not():
    assert cleanup.britishise("My favorite movie is on vacation.") == "My favourite film is on holiday."
    assert cleanup.britishise("I can not use this computer program.") == "I cannot use this computer program."
    assert cleanup.britishise("I practice in the fall.") == "I practise in the autumn."


def test_render_examples_keeps_overflow_in_more_examples_html_once():
    fields = empty_fields()
    examples = [{"de": f"Satz {i}.", "en": f"Sentence {i}.", "audio": ""} for i in range(1, 7)]
    cleanup.render_examples(fields, examples)
    assert fields["Example4DE"] == "Satz 4."
    assert fields["MoreExamplesHTML"].count('<article class="gw-example">') == 2
    assert cleanup.examples_from_fields(fields) == examples


def test_deletion_map_is_exact_and_results_in_expected_inventory():
    assert cleanup.DELETION_MAP == {
        1584886454471: 1584886454470,
        1584886454757: 1584886454756,
        1584886454972: 1584886454971,
        1584886455083: 1584886455084,
        1584886455254: 1584886455253,
    }
    assert cleanup.BASE_NOTES - len(cleanup.DELETION_MAP) == cleanup.FINAL_NOTES
    assert cleanup.BASE_CARDS - 2 * len(cleanup.DELETION_MAP) == cleanup.FINAL_CARDS
