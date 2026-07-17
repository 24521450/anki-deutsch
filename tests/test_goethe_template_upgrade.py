from __future__ import annotations

import copy
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_template_upgrade as upgrade  # noqa: E402


def old_fields() -> list[str]:
    return [name for name in upgrade.gw.FIELDS if name not in upgrade.NEW_FIELDS]


def fake_model(name: str = "old") -> dict[str, object]:
    return {
        "fields": old_fields(),
        "templates": {
            "German → English": {"Front": f"{name}-front", "Back": f"{name}-back"},
            "English → German": {"Front": f"{name}-front-2", "Back": f"{name}-back-2"},
        },
        "styling": f"{name}-css",
    }


def schedule(card_id: int, note_id: int, ord_value: int, *, queue: int = 0) -> dict[str, object]:
    return {
        "cardId": card_id,
        "note": note_id,
        "ord": ord_value,
        "deckName": "Goethe Institute::A1 Wordlist",
        "factor": 2500,
        "interval": 3,
        "type": 2,
        "queue": queue,
        "due": 123,
        "reps": 4,
        "lapses": 0,
        "left": 0,
        "flags": 0,
    }


def inventory_fixture() -> dict[str, object]:
    legacy = {
        "Lemma": "Haus",
        "MeaningEN": "house",
        "CEFR": "A1",
        "POS": "n.",
        "Article": "das",
        "Gender": "neuter",
        "AcceptedAnswersDE": "Haus",
        "AcceptedArticlesDE": "das",
        "Example1DE": "Das Haus ist groÃŸ.",
        "Example1EN": "The house is big.",
        "SourceID": "S1",
    }
    legacy.update({name: "" for name in old_fields() if name not in legacy})
    second = dict(legacy)
    second.update({
        "Lemma": "gehen",
        "MeaningEN": "go",
        "POS": "v.",
        "Article": "",
        "Gender": "",
        "AcceptedAnswersDE": "gehen",
        "AcceptedArticlesDE": "",
        "Example1DE": "Wir gehen.",
        "Example1EN": "We go.",
        "SourceID": "S2",
    })
    notes = {
        "1": {"model": upgrade.MODEL, "fields": legacy, "tags": ["source", "A1"], "cards": [101, 102], "source_id": "S1"},
        "2": {"model": upgrade.MODEL, "fields": second, "tags": ["source", "A1"], "cards": [201, 202], "source_id": "S2"},
    }
    cards = {}
    for note_id, pair in ((1, (101, 102)), (2, (201, 202))):
        cards[str(pair[0])] = {"schedule": schedule(pair[0], note_id, 0), "suspended": False, "note": note_id, "ord": 0, "deckName": "Goethe Institute::A1 Wordlist"}
        cards[str(pair[1])] = {"schedule": schedule(pair[1], note_id, 1), "suspended": False, "note": note_id, "ord": 1, "deckName": "Goethe Institute::A1 Wordlist"}
    reviews = {str(card_id): [] for card_id in cards}
    return {
        "created_utc": "2026-01-01T00:00:00+00:00",
        "model": fake_model(),
        "notes": notes,
        "cards": cards,
        "reviews": reviews,
        "reviews_sha256": upgrade.canonical_hash(reviews),
        "deck_counts": {"Goethe Institute::A1 Wordlist": {"notes": 2, "cards": 4}},
    }


def fake_review_modules(monkeypatch: pytest.MonkeyPatch, *, disabled: set[str] | None = None) -> None:
    disabled = disabled or set()

    def apply_policy(records):
        for item in records.values():
            fields = item["fields"]
            if fields["SourceID"] in disabled:
                fields["AcceptedFullAnswersDE"] = ""
                fields["ProductionEnabled"] = ""
                fields["ProductionHint"] = ""
            else:
                fields["AcceptedFullAnswersDE"] = f"{fields['Lemma']}!"
                fields["ProductionEnabled"] = "1"
                fields["ProductionHint"] = ""
        return {"enabled": len(records) - len(disabled), "disabled": len(disabled)}

    policy = SimpleNamespace(apply_policy=apply_policy)
    highlights = SimpleNamespace(build_spans=lambda fields: "[]")
    monkeypatch.setattr(upgrade, "_load_policy_module", lambda: policy)
    monkeypatch.setattr(upgrade, "_load_highlight_module", lambda: highlights)
    monkeypatch.setattr(upgrade, "source_templates", lambda: fake_model("target")["templates"])
    monkeypatch.setattr(upgrade, "target_css", lambda: "target-css")


def test_configured_schema_is_append_only(monkeypatch: pytest.MonkeyPatch):
    legacy = old_fields()
    monkeypatch.setattr(upgrade.gw, "FIELDS", legacy)
    assert upgrade.configured_field_sets() == (legacy, legacy + list(upgrade.NEW_FIELDS))

    inserted = legacy[:3] + [upgrade.NEW_FIELDS[0]] + legacy[3:]
    monkeypatch.setattr(upgrade.gw, "FIELDS", inserted)
    with pytest.raises(upgrade.UpgradeError, match="inserted/reordered"):
        upgrade.configured_field_sets()

    monkeypatch.setattr(upgrade.gw, "FIELDS", legacy + [legacy[0]])
    with pytest.raises(upgrade.UpgradeError, match="duplicate"):
        upgrade.configured_field_sets()


def test_model_snapshot_normalizes_ankiconnect_styling_shape(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(upgrade, "model_fields", lambda: old_fields())

    def fake_anki(action: str, **params):
        if action == "modelStyling":
            return {"css": ".card { color: red; }"}
        if action == "modelTemplates":
            return {"Card 1": {"Front": "front", "Back": "back"}}
        raise AssertionError(action)

    monkeypatch.setattr(upgrade, "anki", fake_anki)
    snapshot = upgrade.model_snapshot()
    assert snapshot["styling"] == ".card { color: red; }"


def test_valid_apkg_requires_collection_and_integrity(tmp_path: Path):
    valid = tmp_path / "valid.apkg"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("collection.anki2", b"sqlite")
    assert upgrade.valid_apkg(valid)

    missing_collection = tmp_path / "missing.apkg"
    with zipfile.ZipFile(missing_collection, "w") as archive:
        archive.writestr("media", b"{}")
    assert not upgrade.valid_apkg(missing_collection)
    assert not upgrade.valid_apkg(tmp_path / "does-not-exist.apkg")


def test_export_backup_uses_scheduling_and_hashes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_anki(action: str, **params):
        calls.append((action, params))
        path = Path(params["path"])
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("collection.anki21", b"sqlite")
        return True

    monkeypatch.setattr(upgrade, "STATE", tmp_path)
    monkeypatch.setattr(upgrade, "anki", fake_anki)
    path = upgrade.export_backup()
    assert path.parent == tmp_path
    assert upgrade.valid_apkg(path)
    assert calls == [("exportPackage", {
        "deck": upgrade.PARENT_DECK,
        "path": path.resolve().as_posix(),
        "includeSched": True,
    })]
    assert upgrade.hash_file(path)


def test_build_plan_only_writes_four_new_fields(monkeypatch: pytest.MonkeyPatch):
    inventory = inventory_fixture()
    fake_review_modules(monkeypatch, disabled={"S2"})
    plan = upgrade.build_plan(inventory)
    assert plan["old_fields"] == old_fields()
    assert plan["target_fields"] == old_fields() + list(upgrade.NEW_FIELDS)
    assert set(plan["updates"]) == {"1", "2"}
    assert set(plan["updates"]["1"]) == set(upgrade.NEW_FIELDS)
    assert plan["updates"]["1"]["ProductionEnabled"] == "1"
    assert plan["updates"]["2"]["ProductionEnabled"] == ""
    assert plan["updates"]["2"]["AcceptedFullAnswersDE"] == ""
    assert plan["disabled_card_ids"] == [202]
    assert inventory["notes"]["1"]["fields"]["MeaningEN"] == "house"


def test_build_plan_rejects_policy_legacy_mutation(monkeypatch: pytest.MonkeyPatch):
    inventory = inventory_fixture()

    def bad_policy(records):
        records["1"]["fields"]["MeaningEN"] = "mutated"
        return {}

    monkeypatch.setattr(upgrade, "_load_policy_module", lambda: SimpleNamespace(apply_policy=bad_policy))
    monkeypatch.setattr(upgrade, "_load_highlight_module", lambda: SimpleNamespace(build_spans=lambda fields: "[]"))
    with pytest.raises(upgrade.UpgradeError, match="legacy field"):
        upgrade.build_plan(inventory)


def test_build_plan_rejects_invalid_target_span(monkeypatch: pytest.MonkeyPatch):
    inventory = inventory_fixture()
    fake_review_modules(monkeypatch)
    monkeypatch.setattr(upgrade, "_load_highlight_module", lambda: SimpleNamespace(build_spans=lambda fields: "not-json"))
    with pytest.raises(upgrade.UpgradeError, match="target-span"):
        upgrade.build_plan(inventory)


def test_update_new_fields_payload_contains_no_legacy_values(monkeypatch: pytest.MonkeyPatch):
    inventory = inventory_fixture()
    plan = {
        "updates": {"1": {name: f"new-{name}" for name in upgrade.NEW_FIELDS}},
        "new_fields": list(upgrade.NEW_FIELDS),
    }
    calls: list[dict[str, object]] = []

    def fake_anki(action: str, **params):
        assert action == "multi"
        calls.append(params)
        return [{} for _ in params["actions"]]

    monkeypatch.setattr(upgrade, "anki", fake_anki)
    assert upgrade.update_new_fields(plan, inventory) == 1
    fields = calls[0]["actions"][0]["params"]["note"]["fields"]
    assert set(fields) == set(upgrade.NEW_FIELDS)


def test_compare_baseline_detects_tags_reviews_and_suspension_changes():
    snapshot = inventory_fixture()
    snapshot["old_fields"] = old_fields()
    current = copy.deepcopy(snapshot)
    current["notes"]["1"]["tags"] = ["changed"]
    with pytest.raises(upgrade.UpgradeError, match="tags"):
        upgrade.compare_baseline(snapshot, current)

    current = copy.deepcopy(snapshot)
    current["reviews"]["101"] = [{"id": 1}]
    current["reviews_sha256"] = upgrade.canonical_hash(current["reviews"])
    with pytest.raises(upgrade.UpgradeError, match="review"):
        upgrade.compare_baseline(snapshot, current)

    current = copy.deepcopy(snapshot)
    current["cards"]["101"]["suspended"] = True
    with pytest.raises(upgrade.UpgradeError, match="suspension"):
        upgrade.compare_baseline(snapshot, current)


def test_verify_suspension_allows_only_new_reverse_queue():
    snapshot = inventory_fixture()
    current = copy.deepcopy(snapshot)
    current["cards"]["202"]["suspended"] = True
    current["cards"]["202"]["schedule"]["queue"] = -1
    plan = {"disabled_card_ids": [202]}
    upgrade.verify_suspension(plan, snapshot, current, [202])

    current["cards"]["101"]["schedule"]["queue"] = 1
    with pytest.raises(upgrade.UpgradeError, match="scheduling"):
        upgrade.verify_suspension(plan, snapshot, current, [202])


def test_rollback_candidates_refuse_manual_non_target_suspension():
    snapshot = inventory_fixture()
    current = copy.deepcopy(snapshot)
    current["cards"]["202"]["suspended"] = True
    current["cards"]["202"]["schedule"]["queue"] = -1
    plan = {"disabled_card_ids": [202]}
    assert upgrade.rollback_suspension_candidates(snapshot, current, plan, {"newly_suspended": [202]}) == {"202"}

    current["cards"]["101"]["suspended"] = True
    with pytest.raises(upgrade.UpgradeError, match="unexpected suspension"):
        upgrade.rollback_suspension_candidates(snapshot, current, plan, {"newly_suspended": [202]})

    snapshot["cards"]["202"]["suspended"] = True
    with pytest.raises(upgrade.UpgradeError, match="pre-suspended"):
        upgrade.rollback_suspension_candidates(snapshot, current, plan, {"newly_suspended": [202]})


def test_rollback_fails_closed_when_suspension_delta_has_no_result_journal():
    snapshot = inventory_fixture()
    current = copy.deepcopy(snapshot)
    plan = {"disabled_card_ids": [202]}
    assert upgrade.rollback_suspension_candidates(snapshot, current, plan, None) == set()

    current["cards"]["202"]["suspended"] = True
    current["cards"]["202"]["schedule"]["queue"] = -1
    with pytest.raises(upgrade.UpgradeError, match="result is missing"):
        upgrade.rollback_suspension_candidates(snapshot, current, plan, None)


def test_disabled_placeholder_requires_visible_non_type_prompt():
    plan = {"disabled_card_ids": [202]}
    cards = [{"cardId": 202, "question": '<div class="gw-production-disabled">Production disabled</div>', "answer": "answer"}]
    upgrade.disabled_placeholder_check(plan, cards)

    with pytest.raises(upgrade.UpgradeError, match="blank"):
        upgrade.disabled_placeholder_check(plan, [{"cardId": 202, "question": "<div></div>", "answer": "answer"}])
    with pytest.raises(upgrade.UpgradeError, match="type box"):
        upgrade.disabled_placeholder_check(plan, [{"cardId": 202, "question": '<div id="typeans">Production disabled</div>', "answer": "answer"}])


def test_restore_note_fields_is_limited_to_additive_names(monkeypatch: pytest.MonkeyPatch):
    snapshot = inventory_fixture()
    current = copy.deepcopy(snapshot)
    current["model"]["fields"] = old_fields() + list(upgrade.NEW_FIELDS)
    current["notes"]["1"]["fields"].update({name: "changed" for name in upgrade.NEW_FIELDS})
    plan = {"new_fields": list(upgrade.NEW_FIELDS)}
    captured: list[dict[str, object]] = []

    def fake_anki(action: str, **params):
        assert action == "multi"
        captured.append(params)
        return [{} for _ in params["actions"]]

    monkeypatch.setattr(upgrade, "anki", fake_anki)
    assert upgrade.restore_note_fields(snapshot, plan, current) == 1
    fields = captured[0]["actions"][0]["params"]["note"]["fields"]
    assert set(fields) == set(upgrade.NEW_FIELDS)


def test_remove_upgrade_fields_is_reverse_order(monkeypatch: pytest.MonkeyPatch):
    current = old_fields() + list(upgrade.NEW_FIELDS)
    calls: list[str] = []

    def fake_model_fields():
        return list(current)

    def fake_anki(action: str, **params):
        assert action == "modelFieldRemove"
        name = params["fieldName"]
        calls.append(name)
        current.remove(name)
        return None

    monkeypatch.setattr(upgrade, "model_fields", fake_model_fields)
    monkeypatch.setattr(upgrade, "anki", fake_anki)
    upgrade.remove_upgrade_fields({"old_fields": old_fields(), "new_fields": list(upgrade.NEW_FIELDS)})
    assert calls == list(reversed(upgrade.NEW_FIELDS))
    assert current == old_fields()


def test_cli_rejects_missing_confirmation_without_contacting_anki(monkeypatch: pytest.MonkeyPatch):
    called = []
    monkeypatch.setattr(upgrade, "anki", lambda *args, **kwargs: called.append((args, kwargs)))
    assert upgrade.main(["apply", "--confirmation", "wrong"]) == 1
    assert upgrade.main(["rollback", "--confirmation", "wrong"]) == 1
    assert called == []
