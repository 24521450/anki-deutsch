from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_noun_policy as policy  # noqa: E402
import validate_goethe_wortgruppen as validator  # noqa: E402


EXPECTED_EXCEPTIONS = {
    "A1-WG-0105": ("Deutschland", "https://www.duden.de/rechtschreibung/Deutschland"),
    "A1-WG-0106": ("Europa", "https://www.duden.de/rechtschreibung/Europa_Kontinent"),
    "A1-WG-0108": ("Finnland", "https://www.duden.de/rechtschreibung/Finnland"),
    "A1-WG-0109": ("Mexiko", "https://www.duden.de/rechtschreibung/Mexiko_Staat"),
    "A2-WG-0088": ("Österreich", "https://www.duden.de/rechtschreibung/Oesterreich"),
    "A2-WG-0090": ("Luxemburg", "https://www.duden.de/rechtschreibung/Luxemburg_Staat"),
}


def validate(**overrides: str | list[str] | None) -> bool:
    values = {
        "source_id": "TEST-WG-0001",
        "lemma": "Bahn",
        "pos": "n.",
        "article": "die",
        "gender": "f.",
        "dictionary_sources": [],
    }
    values.update(overrides)
    return policy.validate_noun_article(**values)  # type: ignore[arg-type]


def test_exception_registry_is_exact_and_duden_backed() -> None:
    assert {
        source_id: (item.lemma, item.duden_url)
        for source_id, item in policy.ARTICLELESS_NOUN_EXCEPTIONS.items()
    } == EXPECTED_EXCEPTIONS

    for source_id, (lemma, url) in EXPECTED_EXCEPTIONS.items():
        assert validate(
            source_id=source_id, lemma=lemma, article="", gender="n.",
            dictionary_sources=[url],
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"article": ""}, "not a reviewed exception"),
        ({"gender": ""}, "missing Gender"),
        ({"article": "der", "gender": "f."}, "Article/Gender mismatch"),
        ({"article": "der/die", "gender": "m./n."}, "Article/Gender mismatch"),
        ({"article": "den", "gender": "m."}, "Article/Gender mismatch"),
    ],
)
def test_noun_article_and_gender_fail_closed(
    overrides: dict[str, str], message: str,
) -> None:
    with pytest.raises(policy.NounPolicyError, match=message):
        validate(**overrides)


def test_plural_and_multiple_articles_are_validated() -> None:
    assert not validate(article="die", gender="pl.")
    assert not validate(article="der/das", gender="m./n.")


def test_derived_manifest_may_keep_a_primary_article_for_regional_gender_variants() -> None:
    assert not validate(article="der", gender="m./n.", require_complete_mapping=False)
    with pytest.raises(policy.NounPolicyError, match="Article/Gender mismatch"):
        validate(article="die", gender="m./n.", require_complete_mapping=False)


def test_exception_requires_exact_identity_gender_and_evidence() -> None:
    source_id = "A1-WG-0105"
    url = EXPECTED_EXCEPTIONS[source_id][1]
    with pytest.raises(policy.NounPolicyError, match="identity mismatch"):
        validate(source_id=source_id, lemma="Deutschlands", article="", gender="n.", dictionary_sources=[url])
    with pytest.raises(policy.NounPolicyError, match="stale Gender"):
        validate(source_id=source_id, lemma="Deutschland", article="", gender="m.", dictionary_sources=[url])
    with pytest.raises(policy.NounPolicyError, match="missing Duden evidence"):
        validate(source_id=source_id, lemma="Deutschland", article="", gender="n.", dictionary_sources=[])


def test_usage_note_cannot_bypass_source_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("Goethe_A1.md", "Goethe_A1_Wortgruppen.md", "A1_SD1_Wortliste_02.pdf"):
        shutil.copy2(validator.SOURCE_DIR / name, tmp_path / name)
    inventory = tmp_path / "Goethe_A1_Wortgruppen.md"
    lines = inventory.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("| A1-WG-0093 |"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        cells[9] = ""
        cells[13] = "Normally used without an article in this Goethe context."
        lines[index] = "| " + " | ".join(cells) + " |"
        break
    else:
        raise AssertionError("A1-WG-0093 fixture row not found")
    inventory.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(validator, "SOURCE_DIR", tmp_path)

    with pytest.raises(ValueError, match="not a reviewed exception"):
        validator.validate_level("A1")
