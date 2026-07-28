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


def test_select_local_duden_does_not_treat_accepted_answer_as_spoken_form():
    exact = item("A2", 623, word="meistens", sha="exact", pos="adv.", gender="")
    accepted_answer = item("B1", 1596, word="meist", sha="wrong", pos="adv.", gender="")
    target = fields(
        Lemma="meistens",
        POS="adv.",
        Gender="",
        AcceptedAnswersDE="meistens|meist",
        SourceRefs="A2-MAIN-0634|B1-MAIN-1596",
        CEFR="A2",
    )
    by_ref = {("B1", 1596): accepted_answer}
    assert gwa.select_local_duden(
        target,
        by_ref,
        {"meistens": [exact], "meist": [accepted_answer]},
    ) is exact


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


def test_canonical_spoken_identity_strips_bound_markers_and_dedupes_atoms():
    assert gwa.canonical_spoken_identity("eigen-") == "eigen"
    assert gwa.canonical_spoken_identity("weg/weg-") == "weg"
    assert gwa.canonical_spoken_identity("her/her-/-her") == "her"


@pytest.mark.parametrize(
    ("lemma", "spoken"),
    [
        ("ander-", "ander"),
        ("all-", "all"),
        ("erst-", "erst"),
        ("eigen-", "eigen"),
        ("einig-", "einig"),
        ("geehrt-", "geehrt"),
        ("manch-", "manch"),
        ("Geburts-", "Geburts"),
        ("beid-", "beid"),
        ("besonder-", "besonder"),
        ("link-", "link"),
        ("recht-", "recht"),
        ("selb-", "selb"),
        ("un-", "un"),
    ],
)
def test_confirmed_bound_audio_regressions_use_the_physical_stem(
    lemma: str,
    spoken: str,
) -> None:
    assert gwa.bound_spoken_identity(lemma) == spoken


def test_spoken_text_uses_physical_bound_lemma_instead_of_stale_override():
    assert gwa.spoken_text(
        fields(Lemma="eigen-", SourceRefs="A2-0266|B1-MAIN-0599"),
        "eigenes",
        {"A2-0266": "eigenes"},
    ) == "eigen"
    assert gwa.spoken_text(
        fields(Lemma="ander-", SourceRefs="A1-84886454424"),
        "anderen",
        {"ander-": "anderen"},
    ) == "ander"


def test_select_local_duden_rejects_expanded_word_for_bound_stem():
    exact = item("B1", 599, word="eigen-", sha="exact", pos="adj.", gender="")
    expanded = item("A2", 266, word="eigenes", sha="wrong", pos="adj.", gender="")
    target = fields(
        Lemma="eigen-",
        POS="adj.",
        Gender="",
        AcceptedAnswersDE="eigen-",
        SourceRefs="A2-MAIN-0266|B1-MAIN-0599",
        CEFR="A2",
    )
    assert gwa.select_local_duden(
        target,
        {("A2", 266): expanded, ("B1", 599): exact},
        {"eigen-": [exact], "eigenes": [expanded]},
    ) is exact
    assert gwa.select_local_duden(
        fields(
            Lemma="Geburts-",
            POS="adj.",
            Gender="",
            AcceptedAnswersDE="Geburts-",
            SourceRefs="A2-MAIN-0374",
            CEFR="A2",
        ),
        {
            ("A2", 374): item(
                "A2", 374, word="Geburtsort", sha="wrong", pos="adj.", gender=""
            )
        },
        {},
    ) is None


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
    manifest = {"notes": {"7": {
        "lemma_identity": "Bahnhof",
        "spoken_text": "Bahnhof",
        "assignment": {
            "lemma_identity": "Bahnhof",
            "spoken_text": "Bahnhof",
            "media_name": "new.mp3",
        },
    }}}
    gwa.verify_baseline(records, snapshot, manifest)


def test_assignment_identity_rejects_audio_after_lemma_change():
    item = {
        "lemma_identity": "eigenes",
        "spoken_text": "eigenes",
        "assignment": {
            "lemma_identity": "eigenes",
            "spoken_text": "eigenes",
            "media_name": "old.mp3",
        },
    }
    with pytest.raises(gwa.WordAudioError, match="lemma identity mismatch"):
        gwa.validate_assignment_identity(
            fields(Lemma="eigen-", SourceRefs="A2-0266|B1-MAIN-0599"),
            item,
        )


def test_live_assignment_mismatches_separates_semantics_from_provider_drift():
    records = {
        1: {"fields": fields(
            Lemma="eigen-",
            WordAudio=f"[sound:_goethe_word_edge_{'a' * 64}.mp3]",
        )},
        2: {"fields": fields(
            Lemma="Bahnhof",
            WordAudio=f"[sound:_goethe_word_commons_{'b' * 64}.mp3]",
        )},
    }
    manifest = {"notes": {
        "1": {
            "note_id": 1,
            "lemma_identity": "eigen-",
            "spoken_text": "eigen",
            "assignment": {
                "sha256": "c" * 64,
                "spoken_text": "eigen",
                "media_name": "eigen.mp3",
            },
        },
        "2": {
            "note_id": 2,
            "lemma_identity": "Bahnhof",
            "spoken_text": "Bahnhof",
            "assignment": {
                "sha256": "d" * 64,
                "spoken_text": "Bahnhof",
                "media_name": "bahnhof.mp3",
            },
        },
    }}
    provenance = {
        "a" * 64: {"spoken_texts": ["eigenes"], "providers": ["edge"]},
        "b" * 64: {"spoken_texts": ["Bahnhof"], "providers": ["commons"]},
    }
    report = gwa.live_assignment_mismatches(records, manifest, provenance)
    assert [item["note_id"] for item in report["semantic_mismatches"]] == [1]
    assert [item["note_id"] for item in report["provider_drift"]] == [2]


def test_live_assignment_mismatches_accepts_only_reviewed_spoken_equivalence():
    records = {
        1: {"fields": fields(
            SourceID="B1-MAIN-2060",
            Lemma="Sauce",
            WordAudio=f"[sound:_goethe_word_edge_{'a' * 64}.mp3]",
        )},
    }
    manifest = {"notes": {
        "1": {
            "spoken_text": "Sauce",
            "assignment": {"sha256": "b" * 64, "spoken_text": "Sauce"},
        },
    }}
    provenance = {
        "a" * 64: {"spoken_texts": ["Soße"], "providers": ["edge"]},
    }
    equivalences = {
        "B1-MAIN-2060": {
            "expected_lemma": "Sauce",
            "spoken_texts": ["Soße"],
            "reason": "reviewed regional variant",
        },
    }
    report = gwa.live_assignment_mismatches(
        records, manifest, provenance, equivalences
    )
    assert [item["note_id"] for item in report["reviewed_equivalences"]] == [1]
    assert report["semantic_candidates"] == []


def test_live_assignment_mismatches_rejects_stale_equivalence_lemma():
    records = {
        1: {"fields": fields(
            SourceID="B1-MAIN-2060",
            Lemma="Suppe",
            WordAudio=f"[sound:_goethe_word_edge_{'a' * 64}.mp3]",
        )},
    }
    manifest = {"notes": {
        "1": {
            "spoken_text": "Suppe",
            "assignment": {"sha256": "b" * 64, "spoken_text": "Suppe"},
        },
    }}
    provenance = {
        "a" * 64: {"spoken_texts": ["Soße"], "providers": ["edge"]},
    }
    equivalences = {
        "B1-MAIN-2060": {
            "expected_lemma": "Sauce",
            "spoken_texts": ["Soße"],
            "reason": "reviewed regional variant",
        },
    }
    with pytest.raises(gwa.WordAudioError, match="spoken equivalence lemma mismatch"):
        gwa.live_assignment_mismatches(records, manifest, provenance, equivalences)


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


def test_level_duden_rows_fall_back_to_latest_valid_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setitem(gwa.scope.DUDEN_ROWS, "A1", 1)
    root = tmp_path / "a1"
    root.mkdir()
    (root / "words_manifest.jsonl").write_text(
        json.dumps({"row": 1, "word": "Bahnhof"}) + "\n",
        encoding="utf-8",
    )
    checkpoint = root / "duden_checkpoints" / "duden_20260704T082543Z"
    checkpoint.mkdir(parents=True)
    valid = {
        "row": 1,
        "word": "Bahnhof",
        "pos": "n.",
        "gender": "m.",
        "output_filename": "0001_bahnhof.mp3",
        "source": "duden",
        "status": "unresolved",
    }
    (checkpoint / "words_manifest.jsonl").write_text(json.dumps(valid) + "\n", encoding="utf-8")
    assert gwa.load_level_duden_rows("A1", root) == [valid]


def test_word_manifest_rejects_pre_b1_schema():
    with pytest.raises(gwa.WordAudioError, match="schema is stale"):
        gwa.validate_manifest({"schema_version": 2})


def test_protected_manifest_requires_only_protected_assignments(monkeypatch):
    monkeypatch.setattr(gwa.scope, "EXPECTED_NOTES", 3)
    monkeypatch.setattr(gwa.scope, "EXPECTED_CARDS", 6)
    monkeypatch.setattr(gwa.scope, "EXPECTED_NOTES_BY_LEVEL", {"A1": 1, "A2": 1, "B1": 1})
    monkeypatch.setattr(gwa, "expected_level_counts", lambda: {
        "A1": {"notes": 1, "cards": 2},
        "A2": {"notes": 1, "cards": 2},
        "B1": {"notes": 1, "cards": 2},
    })
    manifest = {
        "schema_version": gwa.MANIFEST_SCHEMA_VERSION,
        "levels": list(gwa.scope.LEVELS),
        "duden_rows": gwa.scope.DUDEN_ROWS,
        "duden_statuses": sorted(gwa.DUDEN_STABLE_STATUSES),
        "edge_config": gwa.EDGE_CONFIG,
        "commons_config": gwa.COMMONS_CONFIG,
        "wiktionary_config": gwa.WIKTIONARY_CONFIG,
        "source_order": ["duden_local", "duden_extra", "commons", "wiktionary", "edge"],
        "duden_level_order": list(gwa.scope.LEVELS),
        "note_count": 3,
        "card_count": 6,
        "level_counts": gwa.expected_level_counts(),
        "prepared_utc": "now",
        "prepared_scope": "protected",
        "notes": {
            "1": {"note_id": 1, "level": "A1", "protected_audio": {"provider": "commons"}, "assignment": {"media_name": "x.mp3"}},
            "2": {"note_id": 2, "level": "A2"},
            "3": {"note_id": 3, "level": "B1"},
        },
    }
    gwa.validate_manifest(manifest, require_prepared=True)
    del manifest["notes"]["1"]["assignment"]
    with pytest.raises(gwa.WordAudioError, match="unassigned protected notes"):
        gwa.validate_manifest(manifest, require_prepared=True)


def test_duden_negative_cache_is_versioned_and_refreshable():
    current = {"status": "unresolved", "resolver_version": gwa.DUDEN_RESOLVER_VERSION}
    stale = {"status": "unresolved", "resolver_version": gwa.DUDEN_RESOLVER_VERSION - 1}
    positive = {"status": "ok", "resolver_version": 1}
    assert gwa.reuse_duden_cache(current, refresh_negative=False)
    assert not gwa.reuse_duden_cache(current, refresh_negative=True)
    assert not gwa.reuse_duden_cache(stale, refresh_negative=False)
    assert gwa.reuse_duden_cache(positive, refresh_negative=True)


def test_protected_audio_follows_source_refs_after_merge(tmp_path, monkeypatch):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({
        "schema_version": 3,
        "spoken_text": {},
        "protected_audio": {
            "A2-0521": {
                "provider": "commons",
                "expected_lemma": "Keller",
                "spoken_text": "Keller",
                "title": "File:De-Keller.ogg",
                "original_sha1": "a" * 40,
                "sha256": "f" * 64,
                "reason": "intentional",
            }
        },
    }), encoding="utf-8")
    monkeypatch.setattr(gwa, "OVERRIDES_PATH", path)
    protected = gwa.load_protected_audio()
    match = gwa.protected_audio_for(
        {"SourceID": "A1-0001", "SourceRefs": "A1-0001|A2-0521", "Lemma": "Keller"},
        protected,
    )
    assert match["provider"] == "commons"
    with pytest.raises(gwa.WordAudioError, match="lemma mismatch"):
        gwa.protected_audio_for(
            {"SourceID": "A1-0001", "SourceRefs": "A1-0001|A2-0521", "Lemma": "Kellner"},
            protected,
        )


def test_protected_audio_conflict_after_merge_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "overrides.json"
    entry = {
        "provider": "commons",
        "expected_lemma": "Keller",
        "spoken_text": "Keller",
        "title": "File:De-Keller.ogg",
        "original_sha1": "a" * 40,
        "sha256": "f" * 64,
        "reason": "intentional",
    }
    path.write_text(json.dumps({
        "schema_version": 3,
        "spoken_text": {},
        "protected_audio": {
            "A1-0001": entry,
            "A2-0521": {**entry, "title": "File:De-Keller2.ogg", "sha256": "e" * 64},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(gwa, "OVERRIDES_PATH", path)
    with pytest.raises(gwa.WordAudioError, match="conflicting protected audio"):
        gwa.protected_audio_for(
            {"SourceID": "A1-0001", "SourceRefs": "A1-0001|A2-0521", "Lemma": "Keller"},
            gwa.load_protected_audio(),
        )


def test_reviewed_protected_audio_registry_contains_requested_recordings():
    protected = gwa.load_protected_audio()
    expected = {
        "A1-MAIN-0016": ("anbieten", "File:De-anbieten.ogg"),
        "A1-84886454592": ("Dame", "File:De-Dame.ogg"),
        "A1-84886454915": ("lieben", "File:De-lieben2.ogg"),
        "A2-0610": ("Magen", "File:De-Magen.ogg"),
        "A2-0647": ("meistens", "File:De-meistens.ogg"),
        "A2-1107": ("warm", "File:De-warm.ogg"),
    }
    assert {
        source_ref: (protected[source_ref]["expected_lemma"], protected[source_ref]["title"])
        for source_ref in expected
    } == expected


def test_protected_commons_revision_and_derivative_are_hash_locked():
    protected = {
        "title": "File:De-meistens.ogg",
        "original_sha1": "a" * 40,
        "sha256": "b" * 64,
    }
    gwa.validate_protected_commons(
        protected,
        {"title": protected["title"], "original_sha1": "a" * 40, "sha256": "b" * 64},
    )
    with pytest.raises(gwa.WordAudioError, match="source revision changed"):
        gwa.validate_protected_commons(
            protected,
            {"title": protected["title"], "original_sha1": "c" * 40, "sha256": "b" * 64},
        )
    with pytest.raises(gwa.WordAudioError, match="MP3 changed"):
        gwa.validate_protected_commons(
            protected,
            {"title": protected["title"], "original_sha1": "a" * 40, "sha256": "d" * 64},
        )


def test_local_protected_entry_hashes_current_anki_mp3():
    payload = b"ID3" + b"\0" * 32
    entry = gwa.local_protected_entry(
        {"Lemma": "Bahnhof", "SourceID": "A1-MAIN-0080"},
        payload,
        reason="user-reviewed pronunciation",
    )
    assert entry == {
        "provider": "local",
        "expected_lemma": "Bahnhof",
        "spoken_text": "Bahnhof",
        "sha256": gwa.hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "reason": "user-reviewed pronunciation",
    }


def test_change_set_guard_allows_only_duden_upgrades_and_protected_audio():
    manifest = {"notes": {
        "1": {"note_id": 1, "old_word_audio": "[sound:_goethe_word_commons_old.mp3]", "assignment": {"source": "duden_extra", "media_name": "_goethe_word_duden_new.mp3"}},
        "2": {"note_id": 2, "old_word_audio": "[sound:_goethe_word_duden_old.mp3]", "protected_audio": {"provider": "commons"}, "assignment": {"source": "commons", "media_name": "_goethe_word_commons_new.mp3"}},
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


def test_word_pilot_prioritizes_alle_and_protected_audio():
    manifest = {"notes": {
        "1": {"note_id": 1, "level": "A2", "lemma": "alle", "old_word_audio": "[sound:old.mp3]", "assignment": {"source": "duden_extra", "media_name": "alle.mp3"}},
        "2": {"note_id": 2, "level": "A2", "lemma": "Keller", "old_word_audio": "[sound:old.mp3]", "protected_audio": {"provider": "commons"}, "assignment": {"source": "commons", "media_name": "keller.mp3"}},
        "3": {"note_id": 3, "level": "A1", "lemma": "ab", "old_word_audio": "[sound:old.mp3]", "assignment": {"source": "duden_extra", "media_name": "ab.mp3"}},
        "4": {"note_id": 4, "level": "B1", "lemma": "Ziel", "old_word_audio": "[sound:old.mp3]", "assignment": {"source": "duden_extra", "media_name": "ziel.mp3"}},
    }}
    selected = gwa.pilot_ids(manifest)
    assert {1, 2} <= set(selected)


def test_protected_scope_selects_only_protected_audio():
    manifest = {"notes": {
        "1": {"note_id": 1, "protected_audio": {"provider": "commons"}},
        "2": {"note_id": 2},
    }}
    assert gwa.selected_ids(manifest, "protected") == [1]


def test_exact_note_filter_is_repeatable_and_fails_closed():
    manifest = {"notes": {
        "1": {"note_id": 1},
        "2": {"note_id": 2},
        "3": {"note_id": 3},
    }}
    assert gwa.selected_ids(manifest, "full", [3, 1, 3]) == [1, 3]
    with pytest.raises(gwa.WordAudioError, match="not present"):
        gwa.selected_ids(manifest, "full", [4])


def test_appended_review_cards_accepts_only_history_extensions():
    before = {"1": [{"id": 10}], "2": []}
    after = {"1": [{"id": 10}, {"id": 11}], "2": []}
    assert gwa.appended_review_cards(before, after) == {"1"}
    with pytest.raises(gwa.WordAudioError, match="review history changed"):
        gwa.appended_review_cards(before, {"1": [{"id": 99}], "2": []})


def test_b1_media_shim_fails_fast_with_exactly_two_workflows(capsys):
    assert b1_media.main([]) == 2
    assert capsys.readouterr().err.splitlines() == [b1_media.WORD_WORKFLOW, b1_media.EXAMPLE_WORKFLOW]
