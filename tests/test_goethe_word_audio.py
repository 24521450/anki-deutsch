from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_word_audio as gwa  # noqa: E402


def fields(**values):
    base = {
        "Lemma": "Bahnhof", "POS": "n.", "Gender": "m.", "AcceptedAnswersDE": "Bahnhof",
        "SourceRefs": "A1-MAIN-0080|A2-MAIN-0099", "CEFR": "A1",
    }
    base.update(values)
    return base


def item(level, row, word="Bahnhof", sha="same"):
    return {"level": level, "row": row, "word": word, "pos": "n.", "gender": "m.", "status": "ok", "sha256": sha}


def test_select_local_duden_prefers_a1_for_same_lexeme():
    a1, a2 = item("A1", 80), item("A2", 99)
    by_ref = {("A1", 80): a1, ("A2", 99): a2}
    index = {"Bahnhof": [a2, a1]}
    assert gwa.select_local_duden(fields(), by_ref, index) is a1


def test_select_local_duden_rejects_pos_conflict():
    noun = item("A1", 80)
    assert gwa.select_local_duden(fields(POS="v."), {("A1", 80): noun}, {"Bahnhof": [noun]}) is None


def test_select_local_duden_does_not_casefold_homographs():
    lower = item("A1", 1, word="essen")
    assert gwa.select_local_duden(fields(Lemma="Essen", AcceptedAnswersDE="Essen"), {}, {"essen": [lower]}) is None


def test_spoken_text_requires_override_for_notation():
    with pytest.raises(gwa.WordAudioError, match="missing spoken-text override"):
        gwa.spoken_text(fields(Lemma="d. h."), "d. h.", {})
    assert gwa.spoken_text(fields(Lemma="d. h."), "d. h.", {"d. h.": "das heißt"}) == "das heißt"


def test_edge_audio_id_is_deterministic_and_case_sensitive():
    assert gwa.edge_audio_id("Bahnhof") == gwa.edge_audio_id("Bahnhof")
    assert gwa.edge_audio_id("Bahnhof") != gwa.edge_audio_id("bahnhof")


def test_update_word_audio_only_writes_word_audio(monkeypatch):
    calls = []
    monkeypatch.setattr(gwa.gw, "anki", lambda action, **params: calls.append((action, params)) or [{"result": None, "error": None}])
    gwa.update_word_audio([7], {7: "[sound:x.mp3]"})
    action = calls[0][1]["actions"][0]
    assert action["params"]["note"] == {"id": 7, "fields": {"WordAudio": "[sound:x.mp3]"}}


def test_verify_baseline_allows_already_applied_prepared_audio():
    before = fields(WordAudio="[sound:old.mp3]")
    records = {7: {"model": gwa.MODEL, "tags": ["A1"], "fields": fields(WordAudio="[sound:new.mp3]")}}
    snapshot = {"notes": {"7": {"model": gwa.MODEL, "tags": ["A1"], "fields": before}}}
    manifest = {"notes": {"7": {"assignment": {"media_name": "new.mp3"}}}}
    gwa.verify_baseline(records, snapshot, manifest)


def commons_page(*, title="File:De-Bahnhof.ogg", categories=None, license_name="CC BY-SA 4.0", artist="Speaker"):
    return {
        "pageid": 1,
        "title": title,
        "categories": [{"title": value} for value in (categories or ["Category:German pronunciation of nouns"])],
        "videoinfo": [{
            "mediatype": "AUDIO", "duration": 1.2, "mime": "application/ogg", "size": 12000,
            "url": "https://upload.example/original.ogg", "descriptionurl": "https://commons.example/file",
            "sha1": "a" * 40, "derivatives": [{"src": "https://upload.example/derived.mp3", "type": "audio/mpeg", "transcodekey": "mp3"}],
            "extmetadata": {
                "LicenseShortName": {"value": license_name},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                "Artist": {"value": artist},
                "ImageDescription": {"value": "German pronunciation of Bahnhof"},
                "AttributionRequired": {"value": "true"},
            },
        }],
    }


def test_commons_candidate_accepts_exact_standard_german_human_audio():
    candidate, reason = gwa.evaluate_commons_page(
        commons_page(), {"request_key": "k", "spoken_text": "Bahnhof", "pos": "n.", "gender": "m."}
    )
    assert reason == "accepted"
    assert candidate["license_short_name"] == "CC BY-SA 4.0"
    assert candidate["derivative_url"].endswith(".mp3")


@pytest.mark.parametrize("categories,description", [
    (["Category:Austrian German pronunciation"], "Austrian pronunciation"),
    (["Category:German pronunciation of nouns"], "AI-generated German pronunciation"),
])
def test_commons_candidate_rejects_dialect_and_ai(categories, description):
    page = commons_page(categories=categories)
    page["videoinfo"][0]["extmetadata"]["ImageDescription"]["value"] = description
    candidate, _ = gwa.evaluate_commons_page(page, {"request_key": "k", "spoken_text": "Bahnhof", "pos": "n."})
    assert candidate is None


def test_commons_candidate_rejects_pos_and_license_conflicts():
    candidate, reason = gwa.evaluate_commons_page(
        commons_page(categories=["Category:German pronunciation of verbs"]),
        {"request_key": "k", "spoken_text": "Bahnhof", "pos": "n."},
    )
    assert candidate is None and "POS" in reason
    candidate, reason = gwa.evaluate_commons_page(
        commons_page(license_name="GFDL"), {"request_key": "k", "spoken_text": "Bahnhof", "pos": "n."}
    )
    assert candidate is None and "license" in reason


def test_commons_media_name_and_title_are_deterministic():
    assert gwa.commons_title("StraÃŸe", "ogg") == "File:De-StraÃŸe.ogg"
    assert gwa.media_name("commons", "a" * 64) == f"_goethe_word_commons_{'a' * 64}.mp3"
