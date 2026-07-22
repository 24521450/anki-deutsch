from __future__ import annotations

import copy
import json
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_target_highlight_refresh as refresh  # noqa: E402
import goethe_target_highlights as highlights  # noqa: E402


def test_checked_in_review_manifest_has_the_locked_v2_contract() -> None:
    manifest = refresh.load_manifest()
    assert len(manifest["repairs"]) == 141
    assert sum(
        before != after
        for item in manifest["repairs"]
        for before, after in zip(item["before"], item["after"])
    ) == 166
    assert manifest["expected_added_ranges"] == 195
    assert manifest["expected_removed_ranges"] == 8


def field_values(note: dict) -> dict[str, str]:
    return {name: str(item["value"]) for name, item in note["fields"].items()}


def known_pre_refresh_templates() -> dict:
    templates = copy.deepcopy(refresh.source_templates())
    start = "</main>\n<script>\n"
    end = "\n</script>\n<script>\n"
    for template in templates.values():
        back = template["Back"]
        prefix, remainder = back.split(start, 1)
        _, suffix = remainder.split(end, 1)
        template["Back"] = prefix + start + "// prior highlighter" + end + suffix
    return templates


def reviewed_fixture() -> tuple[list[dict], dict]:
    notes: list[dict] = []
    repairs: list[dict] = []
    for index in range(40):
        note_id = 1_000 + index
        lemma = f"Haus{index}"
        example_count = 2 if index < 4 else 1
        fields = {
            "SourceID": f"A1-TEST-{index:04d}",
            "Lemma": lemma,
            "POS": "n.",
            "AcceptedAnswersDE": lemma,
            "NounFormsRaw": "",
            "VerbFormsRaw": "",
            "MoreExamplesHTML": "",
        }
        for example_index in range(1, 5):
            fields[f"Example{example_index}DE"] = (
                f"Das {lemma} bleibt." if example_index <= example_count else ""
            )
            fields[f"Example{example_index}EN"] = ""
            fields[f"Example{example_index}Audio"] = ""
        before = [[] for _ in range(example_count)]
        fields[refresh.TARGET_FIELD] = json.dumps(before, separators=(",", ":"))
        cards = [10_000 + index * 2, 10_001 + index * 2]
        note = {
            "noteId": note_id,
            "modelName": refresh.MODEL,
            "tags": ["goethe", "A1"],
            "cards": cards,
            "fields": {
                name: {"value": value, "order": order}
                for order, (name, value) in enumerate(fields.items())
            },
        }
        after = json.loads(highlights.build_spans(field_values(note)))
        notes.append(note)
        repairs.append({
            "source_id": fields["SourceID"],
            "note_id": note_id,
            "card_ids": cards,
            "lemma": lemma,
            "before": before,
            "after": after,
        })
    return notes, {
        "schema_version": 1,
        "expected_changed_notes": 40,
        "expected_changed_examples": 44,
        "expected_added_ranges": 44,
        "expected_removed_ranges": 0,
        "repairs": repairs,
    }


class AuditAnki:
    def __init__(self, notes: list[dict]) -> None:
        self.notes = notes
        self.calls: list[tuple[str, dict]] = []
        self.version = 6
        self.templates = known_pre_refresh_templates()
        self.styling = ".card { color: black; }"
        self.cards = []
        for note in notes:
            for ord_value, card_id in enumerate(note["cards"]):
                self.cards.append({
                    "cardId": card_id,
                    "note": note["noteId"],
                    "ord": ord_value,
                    "deckName": "Goethe Institute::A1 Wordlist",
                    "factor": 2500,
                    "interval": 4,
                    "type": 2,
                    "queue": 2,
                    "due": card_id + 7,
                    "reps": 5,
                    "lapses": 0,
                    "left": 0,
                    "flags": 0,
                })
        self.review_map = {str(card["cardId"]): [] for card in self.cards}

    def __call__(self, action: str, **params):
        self.calls.append((action, params))
        if action == "version":
            return self.version
        if action == "findNotes":
            return [note["noteId"] for note in self.notes]
        if action == "notesInfo":
            requested = set(params["notes"])
            return [note for note in self.notes if note["noteId"] in requested]
        if action == "modelFieldNames":
            return list(self.notes[0]["fields"])
        if action == "modelTemplates":
            return self.templates
        if action == "modelStyling":
            return {"css": self.styling}
        if action == "cardsInfo":
            requested = set(params["cards"])
            return [card for card in self.cards if card["cardId"] in requested]
        if action == "areSuspended":
            return [False for _ in params["cards"]]
        if action == "getReviewsOfCards":
            return {
                str(card_id): self.review_map[str(card_id)]
                for card_id in params["cards"]
            }
        if action == "exportPackage":
            path = Path(params["path"])
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("collection.anki2", b"SQLite format 3\x00fixture")
            return True
        if action == "multi":
            results = []
            by_id = {note["noteId"]: note for note in self.notes}
            for item in params["actions"]:
                assert item["action"] == "updateNoteFields"
                assert item["version"] == 6
                payload = item["params"]["note"]
                for name, value in payload["fields"].items():
                    by_id[payload["id"]]["fields"][name]["value"] = value
                results.append({"result": None, "error": None})
            return results
        if action == "updateModelTemplates":
            self.templates = copy.deepcopy(params["model"]["templates"])
            return None
        raise AssertionError(action)


def test_audit_writes_a_hashed_plan_for_the_exact_reviewed_delta(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    monkeypatch.setattr(refresh, "STATE", tmp_path / "state")
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)

    assert refresh.main(["audit"]) == 0

    envelope = json.loads((refresh.STATE / "plan.json").read_text(encoding="utf-8"))
    assert envelope["plan_sha256"] == refresh.canonical_hash(envelope["plan"])
    assert envelope["plan"]["changed_notes"] == 40
    assert envelope["plan"]["changed_examples"] == 44
    assert [item["source_id"] for item in envelope["plan"]["repairs"]] == sorted(
        item["source_id"] for item in manifest["repairs"]
    )
    assert fake.calls[0] == ("version", {})


def test_audit_fails_before_state_on_wrong_version_or_changed_set_drift(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setattr(refresh, "STATE", state)
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)

    wrong_version = AuditAnki(copy.deepcopy(notes))
    wrong_version.version = 5
    monkeypatch.setattr(refresh, "anki", wrong_version)
    assert refresh.main(["audit"]) == 1
    assert wrong_version.calls == [("version", {})]
    assert not (state / "plan.json").exists()

    drifted_notes = copy.deepcopy(notes)
    drifted_notes[0]["fields"][refresh.TARGET_FIELD]["value"] = json.dumps(
        manifest["repairs"][0]["after"], separators=(",", ":"),
    )
    drifted = AuditAnki(drifted_notes)
    monkeypatch.setattr(refresh, "anki", drifted)
    assert refresh.main(["audit"]) == 1
    assert not (state / "plan.json").exists()


def test_backup_exports_scheduling_and_snapshots_the_exact_guarded_surface(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    state = tmp_path / "state"
    monkeypatch.setattr(refresh, "STATE", state)
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)

    assert refresh.main(["audit"]) == 0
    assert refresh.main(["backup"]) == 0

    snapshot = json.loads((state / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["plan_sha256"] == json.loads(
        (state / "plan.json").read_text(encoding="utf-8")
    )["plan_sha256"]
    assert len(snapshot["inventory"]["target_notes"]) == 40
    assert len(snapshot["inventory"]["cards"]) == 80
    assert len(snapshot["inventory"]["reviews"]) == 80
    assert snapshot["inventory"]["model"]["templates"] == fake.templates
    assert snapshot["inventory"]["model"]["styling"] == fake.styling
    export = next(params for action, params in fake.calls if action == "exportPackage")
    assert export["deck"] == refresh.PARENT_DECK
    assert export["includeSched"] is True
    assert Path(snapshot["backup"]).is_file()


def test_backup_accepts_ankiconnect_timeout_only_after_apkg_validation(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    class TimedOutExportAnki(AuditAnki):
        def __call__(self, action: str, **params):
            result = super().__call__(action, **params)
            if action == "exportPackage":
                raise refresh.RefreshError("AnkiConnect exportPackage failed: timed out")
            return result

    fake = TimedOutExportAnki(notes)
    monkeypatch.setattr(refresh, "STATE", tmp_path / "state")
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)
    assert refresh.main(["audit"]) == 0

    assert refresh.main(["backup"]) == 0
    snapshot = json.loads((refresh.STATE / "snapshot.json").read_text(encoding="utf-8"))
    assert Path(snapshot["backup"]).is_file()


def test_backup_rejects_model_cards_outside_the_exported_deck_tree(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    fake.cards[0]["deckName"] = "Unrelated Deck"
    monkeypatch.setattr(refresh, "STATE", tmp_path / "state")
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)
    assert refresh.main(["audit"]) == 0

    assert refresh.main(["backup"]) == 1
    assert not any(action == "exportPackage" for action, _ in fake.calls)


def test_backup_rejects_unrelated_live_template_drift_before_export(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    first = next(iter(fake.templates.values()))
    first["Front"] += "<!-- manual drift -->"
    monkeypatch.setattr(refresh, "STATE", tmp_path / "state")
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)
    assert refresh.main(["audit"]) == 0

    assert refresh.main(["backup"]) == 1
    assert not any(action == "exportPackage" for action, _ in fake.calls)


def test_apply_is_confirmation_guarded_and_writes_only_spans_and_templates(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    state = tmp_path / "state"
    monkeypatch.setattr(refresh, "STATE", state)
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)
    before_fields = {
        note["noteId"]: copy.deepcopy(field_values(note)) for note in notes
    }

    assert refresh.main(["audit"]) == 0
    assert refresh.main(["backup"]) == 0
    calls_before_rejection = len(fake.calls)
    assert refresh.main(["apply", "--confirmation", "wrong"]) == 1
    assert len(fake.calls) == calls_before_rejection
    assert refresh.main([
        "apply", "--confirmation", refresh.APPLY_CONFIRMATION,
    ]) == 0

    after_by_id = {note["noteId"]: field_values(note) for note in notes}
    reviewed = {item["note_id"]: item for item in manifest["repairs"]}
    for note_id, before in before_fields.items():
        expected = dict(before)
        expected[refresh.TARGET_FIELD] = json.dumps(
            reviewed[note_id]["after"], ensure_ascii=False, separators=(",", ":"),
        )
        assert after_by_id[note_id] == expected
    writes = [call for call in fake.calls if call[0] in {
        "multi", "updateModelTemplates", "updateModelStyling", "modelFieldAdd",
        "addTags", "removeTags", "changeDeck",
    }]
    assert [action for action, _ in writes] == ["multi", "updateModelTemplates"]
    actions = writes[0][1]["actions"]
    assert len(actions) == 40
    assert all(
        set(item["params"]["note"]["fields"]) == {refresh.TARGET_FIELD}
        for item in actions
    )
    assert fake.templates == refresh.source_templates()
    assert fake.styling == ".card { color: black; }"
    result = json.loads((state / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "applied"


def test_apply_rejects_live_preimage_drift_and_a_corrupt_backup_before_writes(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    state = tmp_path / "state"
    monkeypatch.setattr(refresh, "STATE", state)
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)
    assert refresh.main(["audit"]) == 0
    assert refresh.main(["backup"]) == 0

    original_lemma = fake.notes[0]["fields"]["Lemma"]["value"]
    fake.notes[0]["fields"]["Lemma"]["value"] = "changed after backup"
    writes_before = len([
        action for action, _ in fake.calls
        if action in {"multi", "updateModelTemplates"}
    ])
    assert refresh.main([
        "apply", "--confirmation", refresh.APPLY_CONFIRMATION,
    ]) == 1
    assert len([
        action for action, _ in fake.calls
        if action in {"multi", "updateModelTemplates"}
    ]) == writes_before

    fake.notes[0]["fields"]["Lemma"]["value"] = original_lemma
    snapshot = json.loads((state / "snapshot.json").read_text(encoding="utf-8"))
    Path(snapshot["backup"]).write_bytes(b"not an apkg")
    assert refresh.main([
        "apply", "--confirmation", refresh.APPLY_CONFIRMATION,
    ]) == 1
    assert len([
        action for action, _ in fake.calls
        if action in {"multi", "updateModelTemplates"}
    ]) == writes_before


def test_rollback_rejects_a_tampered_snapshot_inventory_before_writes(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    state = tmp_path / "state"
    monkeypatch.setattr(refresh, "STATE", state)
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)
    assert refresh.main(["audit"]) == 0
    assert refresh.main(["backup"]) == 0
    assert refresh.main([
        "apply", "--confirmation", refresh.APPLY_CONFIRMATION,
    ]) == 0
    snapshot_path = state / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    first_template = next(iter(snapshot["inventory"]["model"]["templates"].values()))
    prefix, _, suffix = refresh.split_highlighter(first_template["Back"], "fixture")
    first_template["Back"] = prefix + "// tampered snapshot" + suffix
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    writes_before = len([
        action for action, _ in fake.calls
        if action in {"multi", "updateModelTemplates"}
    ])

    assert refresh.main([
        "rollback", "--confirmation", refresh.ROLLBACK_CONFIRMATION,
    ]) == 1
    assert len([
        action for action, _ in fake.calls
        if action in {"multi", "updateModelTemplates"}
    ]) == writes_before


def test_verify_passes_exact_applied_state_and_rejects_untouched_field_drift(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    state = tmp_path / "state"
    monkeypatch.setattr(refresh, "STATE", state)
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)

    assert refresh.main(["audit"]) == 0
    assert refresh.main(["backup"]) == 0
    assert refresh.main([
        "apply", "--confirmation", refresh.APPLY_CONFIRMATION,
    ]) == 0
    assert refresh.main(["verify"]) == 0

    fake.notes[0]["fields"]["Lemma"]["value"] = "manual drift"
    assert refresh.main(["verify"]) == 1
    fake.notes[0]["fields"]["Lemma"]["value"] = manifest["repairs"][0]["lemma"]
    fake.cards[0]["due"] += 1
    assert refresh.main(["verify"]) == 1
    fake.cards[0]["due"] -= 1
    fake.review_map[str(fake.cards[0]["cardId"])] = [{"id": 123, "ease": 3}]
    assert refresh.main(["verify"]) == 1


def test_rollback_is_confirmation_guarded_and_restores_exact_prior_values(
    monkeypatch, tmp_path: Path,
) -> None:
    notes, manifest = reviewed_fixture()
    manifest_path = tmp_path / "review.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = AuditAnki(notes)
    state = tmp_path / "state"
    monkeypatch.setattr(refresh, "STATE", state)
    monkeypatch.setattr(refresh, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(refresh, "anki", fake)
    prior_fields = {
        note["noteId"]: copy.deepcopy(field_values(note)) for note in notes
    }
    prior_templates = copy.deepcopy(fake.templates)

    assert refresh.main(["audit"]) == 0
    assert refresh.main(["backup"]) == 0
    assert refresh.main([
        "apply", "--confirmation", refresh.APPLY_CONFIRMATION,
    ]) == 0
    calls_before_rejection = len(fake.calls)
    assert refresh.main(["rollback", "--confirmation", "wrong"]) == 1
    assert len(fake.calls) == calls_before_rejection
    assert refresh.main([
        "rollback", "--confirmation", refresh.ROLLBACK_CONFIRMATION,
    ]) == 0

    assert {
        note["noteId"]: field_values(note) for note in notes
    } == prior_fields
    assert fake.templates == prior_templates
    result = json.loads((state / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "rolled_back"
