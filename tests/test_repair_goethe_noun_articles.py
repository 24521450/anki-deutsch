from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import repair_goethe_noun_articles as repair  # noqa: E402


EXPECTED_TARGET_SOURCE_IDS = {
    "A2-WG-0099", "A2-WG-0100", "A2-WG-0101", "A2-WG-0102",
    "A2-WG-0103", "A2-WG-0104", "A2-WG-0107", "A2-WG-0108",
    "A2-WG-0110", "A2-WG-0111", "A2-WG-0112", "A2-WG-0128",
    "A2-WG-0129", "A2-WG-0130",
}


def fields(**overrides: str) -> dict[str, str]:
    result = {name: "" for name in repair.gw.FIELDS}
    result.update({
        "SourceID": "TEST", "Lemma": "Test", "CEFR": "A2", "POS": "n.",
        "Article": "die", "Gender": "f.", "AcceptedAnswersDE": "Test",
        "AcceptedArticlesDE": "die", "AcceptedFullAnswersDE": "die Test",
        "ProductionEnabled": "1", "MeaningEN": "test", "SourceRefs": "TEST",
    })
    result.update(overrides)
    return result


def test_target_scope_is_the_exact_fourteen_reviewed_articleless_notes():
    assert len(repair.TARGETS) == 14
    assert {source_id for source_id, _ in repair.TARGETS.values()} == EXPECTED_TARGET_SOURCE_IDS
    assert repair.TARGETS[repair.SILVESTER_NOTE_ID] == ("A2-WG-0130", "Neujahr/Silvester")
    assert set(repair.EXACT_DUDEN_AUDIO) == {"Neujahr", "Silvester"}


def test_live_baseline_fails_closed_on_article_or_identity_drift(monkeypatch):
    notes = {}
    cards = {}
    for index, (note_id, (source_id, lemma)) in enumerate(repair.TARGETS.items()):
        card_ids = [10000 + index * 2, 10001 + index * 2]
        notes[note_id] = {
            "fields": fields(
                SourceID=source_id, Lemma=lemma, Article="",
                WordAudio="[sound:_goethe_word_edge_old.mp3]" if note_id == repair.SILVESTER_NOTE_ID else "",
            ),
            "tags": [], "card_ids": card_ids,
        }
        cards.update({card_id: {"reps": 0} for card_id in card_ids})
    state = {"notes": notes, "cards": cards}
    monkeypatch.setattr(repair, "EXPECTED_BASELINE", {"notes": len(notes), "cards": len(cards)})

    repair.validate_live_baseline(state)
    drift = copy.deepcopy(state)
    drift["notes"][1783863835819]["fields"]["Article"] = "die"
    with pytest.raises(repair.RepairError, match="identity drift"):
        repair.validate_live_baseline(drift)


def completion_manifest() -> dict:
    records = {}
    for note_id, (source_id, lemma) in repair.TARGETS.items():
        desired = fields(SourceID=source_id, Lemma=lemma, SourceRefs=source_id)
        if note_id == repair.SILVESTER_NOTE_ID:
            desired.update({
                "Lemma": "Silvester", "Article": "das/der", "Gender": "n./m.",
                "AcceptedAnswersDE": "Silvester", "AcceptedArticlesDE": "das|der",
                "AcceptedFullAnswersDE": "das Silvester|der Silvester",
            })
        records[str(note_id)] = {
            "note_id": note_id, "is_new": False, "fields": desired,
            "tags": ["goethe::level::a2"], "deck": repair.gw.A2_DECK,
        }
    child = fields(
        SourceID=repair.NEW_SOURCE_ID, SourceRefs=f"{repair.NEW_SOURCE_ID}|B1-WG-0299",
        Lemma="Neujahr", Article="das", Gender="n.", AcceptedAnswersDE="Neujahr",
        AcceptedArticlesDE="das", AcceptedFullAnswersDE="das Neujahr",
        MeaningEN="New Year's Day",
        Example1DE="Neujahr fällt in diesem Jahr auf einen Mittwoch.",
        Example1EN="New Year's Day falls on a Wednesday this year.",
    )
    records[f"new:{repair.NEW_SOURCE_ID}"] = {
        "note_id": None, "is_new": True, "fields": child,
        "tags": ["goethe::level::a2"], "deck": repair.gw.A2_DECK,
    }
    return {"records": records, "deletions": []}


def test_completion_selection_accepts_only_one_reviewed_child(monkeypatch):
    monkeypatch.setattr(repair.completion, "validate_manifest", lambda manifest: {})
    desired, child = repair.desired_from_completion(completion_manifest())

    assert set(desired) == set(repair.TARGETS)
    assert desired[repair.SILVESTER_NOTE_ID]["AcceptedFullAnswersDE"] == "das Silvester|der Silvester"
    assert child["fields"]["AcceptedFullAnswersDE"] == "das Neujahr"
    assert child["fields"]["Example1DE"].startswith("Neujahr fällt")

    bad = completion_manifest()
    bad["deletions"] = [{"note_id": 1}]
    with pytest.raises(repair.RepairError, match="deletes notes"):
        repair.desired_from_completion(bad)

    bad = completion_manifest()
    bad["records"]["new:extra"] = copy.deepcopy(bad["records"][f"new:{repair.NEW_SOURCE_ID}"])
    bad["records"]["new:extra"]["fields"]["SourceID"] = "EXTRA"
    with pytest.raises(repair.RepairError, match="exactly the reviewed Neujahr child"):
        repair.desired_from_completion(bad)
