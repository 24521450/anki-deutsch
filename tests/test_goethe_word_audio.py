from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_word_audio as gwa  # noqa: E402
import goethe_b1_media as b1_media  # noqa: E402


def fields(**values):
    base = {
        "Lemma": "Bahnhof", "POS": "n.", "Gender": "m.", "AcceptedAnswersDE": "Bahnhof",
        "SourceRefs": "A1-MAIN-0080|A2-MAIN-0099", "CEFR": "A1",
    }
    base.update(values)
    return base


def item(level, row, word="Bahnhof", sha="same", pos="n.", gender="m."):
    return {"level": level, "row": row, "word": word, "pos": pos, "gender": gender, "status": "ok", "sha256": sha}


def test_select_local_duden_prefers_a1_for_same_lexeme():
    a1, a2, b1 = item("A1", 80), item("A2", 99), item("B1", 120)
    by_ref = {("A1", 80): a1, ("A2", 99): a2, ("B1", 120): b1}
    index = {"Bahnhof": [b1, a2, a1]}
    assert gwa.select_local_duden(fields(), by_ref, index) is a1


def test_select_local_duden_supports_b1_main_refs_and_three_level_precedence():
    a2, b1 = item("A2", 99), item("B1", 120)
    target = fields(SourceRefs="B1-MAIN-0120|A2-MAIN-0099", CEFR="B1")
    assert gwa.select_local_duden(target, {("A2", 99): a2, ("B1", 120): b1}, {}) is a2


def test_select_local_duden_rejects_pos_conflict():
    noun = item("A1", 80)
    assert gwa.select_local_duden(fields(POS="v."), {("A1", 80): noun}, {"Bahnhof": [noun]}) is None


def test_select_local_duden_does_not_casefold_homographs():
    lower = item("A1", 1, word="essen")
    assert gwa.select_local_duden(fields(Lemma="Essen", AcceptedAnswersDE="Essen"), {}, {"essen": [lower]}) is None


def test_select_local_duden_does_not_use_bare_article_for_ordinal():
    article = item("A1", 155, word="der", sha="article")
    ordinal = fields(Lemma="dritte", POS="", Gender="", AcceptedAnswersDE="dritte|der/die dritte")
    assert gwa.select_local_duden(ordinal, {}, {"der": [article], "dritte": []}) is None


def test_select_local_duden_keeps_true_article_lemma():
    article = item("A1", 155, word="der", pos="det.", sha="article")
    assert gwa.select_local_duden(fields(Lemma="der", POS="det.", Gender="", AcceptedAnswersDE="der"), {}, {"der": [article]}) is article


def test_spoken_text_requires_override_for_notation():
    with pytest.raises(gwa.WordAudioError, match="missing spoken-text override"):
        gwa.spoken_text(fields(Lemma="d. h."), "d. h.", {})
    assert gwa.spoken_text(fields(Lemma="d. h."), "d. h.", {"d. h.": "das heißt"}) == "das heißt"


def test_edge_audio_id_is_deterministic_and_case_sensitive():
    assert gwa.edge_audio_id("Bahnhof") == gwa.edge_audio_id("Bahnhof")
    assert gwa.edge_audio_id("Bahnhof") != gwa.edge_audio_id("bahnhof")


def test_console_text_escapes_unicode_that_windows_cp1252_cannot_encode():
    assert gwa.console_text("one third: ⅓", "cp1252") == "one third: \\u2153"


def test_b1_spoken_overrides_cover_reviewed_notation_by_source_id():
    overrides = gwa.load_overrides()
    assert gwa.spoken_text(
        fields(
            Lemma="1 dkg oder dag (= 10 g)", CEFR="B1",
            SourceRefs="B1-WG-0253",
        ),
        "1 dkg oder dag (= 10 g)",
        overrides,
    ) == "ein Dekagramm oder zehn Gramm"
    assert overrides["B1-MAIN-1742"] == "Nordsee, Ostsee"
    assert overrides["B1-WG-0161"] == "hell, dunkel"


def test_every_current_b1_unsafe_spoken_form_has_a_source_override():
    overrides = gwa.load_overrides()
    rows = [
        json.loads(line)
        for line in (ROOT / "review" / "goethe_english_audit_v4.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    unsafe = [row["source_id"] for row in rows if row["cefr"] == "B1" and gwa.UNSAFE_SPOKEN_RE.search(row["lemma"])]
    assert unsafe
    assert set(unsafe) <= set(overrides)


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


def test_wiktionary_audio_candidates_use_german_section_and_prefer_germany():
    parsed = {"revid": 123, "text": {"*": """
        <div class='mw-heading'><h2 id='German'>German</h2></div>
        <h3 id='Pronunciation'>Pronunciation</h3>
        <ul>
          <li>Audio:<audio data-mwtitle='De-dritte.ogg'></audio></li>
          <li>Audio (Germany):<audio data-mwtitle='De-dritte2.ogg'></audio></li>
        </ul>
        <div class='mw-heading'><h2 id='Italian'>Italian</h2></div>
        <audio data-mwtitle='De-italian.ogg'></audio>
    """}}
    candidates = gwa.wiktionary_audio_candidates(parsed, "dritte")
    assert [item["title"] for item in candidates] == ["File:De-dritte2.ogg", "File:De-dritte.ogg"]


def test_wiktionary_audio_candidates_require_german_section():
    parsed = {"text": {"*": "<div class='mw-heading'><h2 id='Italian'>Italian</h2></div><audio data-mwtitle='De-test.ogg'></audio>"}}
    assert gwa.wiktionary_audio_candidates(parsed, "test") == []


def test_duden_manifest_validator_requires_strict_duden_schema(monkeypatch):
    monkeypatch.setitem(gwa.scope.DUDEN_ROWS, "B1", 1)
    row = {
        "row": 1, "word": "Bahnhof", "pos": "n.", "gender": "m.",
        "output_filename": "0001_bahnhof.mp3", "source": "duden", "status": "unresolved",
    }
    gwa.validate_duden_rows("B1", [row])
    with pytest.raises(gwa.WordAudioError, match="incompatible schema"):
        gwa.validate_duden_rows("B1", [{key: value for key, value in row.items() if key != "source"}])


def test_word_manifest_rejects_pre_b1_schema():
    with pytest.raises(gwa.WordAudioError, match="schema is stale"):
        gwa.validate_manifest({"schema_version": 2})


def test_duden_negative_cache_is_versioned_and_refreshable():
    current = {"status": "unresolved", "resolver_version": gwa.DUDEN_RESOLVER_VERSION}
    stale = {"status": "unresolved", "resolver_version": gwa.DUDEN_RESOLVER_VERSION - 1}
    positive = {"status": "ok", "resolver_version": 1}
    assert gwa.reuse_duden_cache(current, refresh_negative=False)
    assert not gwa.reuse_duden_cache(current, refresh_negative=True)
    assert not gwa.reuse_duden_cache(stale, refresh_negative=False)
    assert gwa.reuse_duden_cache(positive, refresh_negative=True)


def test_provider_pin_for_keller_uses_stable_source_id(tmp_path, monkeypatch):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "spoken_text": {},
        "provider_pins": {
            "A2-0521": {
                "provider": "wiktionary",
                "expected_lemma": "Keller",
                "title": "File:De-Keller.ogg",
                "sha256": "f" * 64,
                "reason": "intentional",
            }
        },
    }), encoding="utf-8")
    monkeypatch.setattr(gwa, "OVERRIDES_PATH", path)
    pins = gwa.load_provider_pins()
    assert gwa.provider_pin_for({"SourceID": "A2-0521", "Lemma": "Keller"}, pins)["provider"] == "wiktionary"
    with pytest.raises(gwa.WordAudioError, match="lemma mismatch"):
        gwa.provider_pin_for({"SourceID": "A2-0521", "Lemma": "Kellner"}, pins)


def test_change_set_guard_allows_only_duden_upgrades_and_provider_pins():
    manifest = {"notes": {
        "1": {"note_id": 1, "old_word_audio": "[sound:_goethe_word_commons_old.mp3]", "assignment": {"source": "duden_extra", "media_name": "_goethe_word_duden_new.mp3"}},
        "2": {"note_id": 2, "old_word_audio": "[sound:_goethe_word_duden_old.mp3]", "provider_pin": {"provider": "wiktionary"}, "assignment": {"source": "wiktionary", "media_name": "_goethe_word_wiktionary_new.mp3"}},
    }}
    gwa.validate_change_set(manifest)
    manifest["notes"]["1"]["assignment"] = {"source": "edge", "media_name": "_goethe_word_edge_new.mp3"}
    with pytest.raises(gwa.WordAudioError, match="unapproved audio transition"):
        gwa.validate_change_set(manifest)


def test_word_pilot_covers_all_levels():
    notes = {}
    note_id = 1
    for level in gwa.scope.LEVELS:
        for source in ("duden_local", "edge", "commons", "wiktionary"):
            notes[str(note_id)] = {
                "note_id": note_id, "level": level, "old_word_audio": "",
                "assignment": {"source": source, "media_name": f"{source}-{note_id}.mp3"},
            }
            note_id += 1
    selected = set(gwa.pilot_ids({"notes": notes}))
    assert {notes[str(note_id)]["level"] for note_id in selected} == set(gwa.scope.LEVELS)


def test_word_pilot_prioritizes_alle_and_provider_pins():
    manifest = {"notes": {
        "1": {"note_id": 1, "level": "A2", "lemma": "alle", "old_word_audio": "[sound:old.mp3]", "assignment": {"source": "duden_extra", "media_name": "alle.mp3"}},
        "2": {"note_id": 2, "level": "A2", "lemma": "Keller", "old_word_audio": "[sound:old.mp3]", "provider_pin": {"provider": "wiktionary"}, "assignment": {"source": "wiktionary", "media_name": "keller.mp3"}},
        "3": {"note_id": 3, "level": "A1", "lemma": "ab", "old_word_audio": "[sound:old.mp3]", "assignment": {"source": "duden_extra", "media_name": "ab.mp3"}},
        "4": {"note_id": 4, "level": "B1", "lemma": "Ziel", "old_word_audio": "[sound:old.mp3]", "assignment": {"source": "duden_extra", "media_name": "ziel.mp3"}},
    }}
    selected = gwa.pilot_ids(manifest)
    assert {1, 2} <= set(selected)


def test_b1_media_shim_fails_fast_with_exactly_two_workflows(capsys):
    assert b1_media.main([]) == 2
    assert capsys.readouterr().err.splitlines() == [b1_media.WORD_WORKFLOW, b1_media.EXAMPLE_WORKFLOW]
