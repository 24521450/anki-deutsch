from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_goethe_wortgruppen as validator  # noqa: E402


def test_all_wortgruppen_sources_validate() -> None:
    results = validator.validate_all()
    assert [(item["level"], item["rows"]) for item in results] == [
        ("A1", 121),
        ("A2", 225),
        ("B1", 355),
    ]


def test_transcription_keeps_source_specific_unicode_and_variants() -> None:
    a1 = (validator.SOURCE_DIR / "Goethe_A1_Wortgruppen.md").read_text(encoding="utf-8")
    a2 = (validator.SOURCE_DIR / "Goethe_A2_Wortgruppen.md").read_text(encoding="utf-8")
    b1 = (validator.SOURCE_DIR / "Goethe_B1_Wortgruppen.md").read_text(encoding="utf-8")

    assert "1 m²" in a1 and "−1°" in a1
    assert "Österreicherin" in a2 and "zweitausendzwölf" in a2
    assert "Confoederatio Helvetica" in b1
    assert "⅓" in b1 and "dreiviertel drei (14:45)" in b1


def test_unknown_alphabetical_reference_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("Goethe_A1.md", "Goethe_A1_Wortgruppen.md", "A1_SD1_Wortliste_02.pdf"):
        shutil.copy2(validator.SOURCE_DIR / name, tmp_path / name)
    inventory = tmp_path / "Goethe_A1_Wortgruppen.md"
    text = inventory.read_text(encoding="utf-8")
    text = text.replace("| A1-WG-0001 | 1 | eins | A1 | 6 |  |", "| A1-WG-0001 | 1 | eins | A1 | 6 | kein-headword |")
    inventory.write_text(text, encoding="utf-8", newline="\n")
    monkeypatch.setattr(validator, "SOURCE_DIR", tmp_path)

    with pytest.raises(ValueError, match="unknown alphabetical headword"):
        validator.validate_level("A1")


def test_malformed_inventory_row_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text("| A1-WG-0001 | only two cells |\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected 7 cells"):
        validator.parse_inventory(path, "A1")
