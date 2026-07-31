from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_reviewed_number_spoken_forms_omit_optional_ein():
    overrides = gwa.load_overrides()
    assert gwa.spoken_text(
        fields(
            Lemma="(ein)hundert",
            SourceRefs="A1-WG-0029|A2-WG-0208|B1-WG-0270",
        ),
        "(ein)hundert",
        overrides,
    ) == "hundert"
    assert gwa.spoken_text(
        fields(
            Lemma="(ein)tausend",
            SourceRefs="A1-WG-0032|A2-WG-0211|B1-WG-0273",
        ),
        "(ein)tausend",
        overrides,
    ) == "tausend"


def test_reviewed_fit_sein_spoken_form_omits_auxiliary():
    assert gwa.spoken_text(
        fields(
            Lemma="fit sein",
            SourceRefs="A2-0332|A2-MAIN-0328",
            POS="v.",
            Gender="",
        ),
        "fit sein",
        gwa.load_overrides(),
    ) == "fit"


def test_reviewed_sein_policy_only_matches_explicit_copula_overrides():
    overrides = gwa.load_overrides()
    assert gwa.reviewed_spoken_override(
        fields(Lemma="fit sein", SourceRefs="A2-0332"),
        "fit",
        overrides,
    )
    assert not gwa.reviewed_spoken_override(
        fields(Lemma="zu sein", SourceRefs="A1-84886455089"),
        "zu sein",
        overrides,
    )
    assert gwa.spoken_text(
        fields(Lemma="sein", SourceRefs="A1-84886455083"),
        "sein",
        overrides,
    ) == "sein"


def test_reviewed_spoken_override_clears_source_pos_for_duden_lookup():
    manifest = {
        "notes": {
            "1": {
                "request_key": "k",
                    "note_id": 1,
                "spoken_text": "fit",
                "pos": "v.",
                "gender": "",
                "note_ids": [],
                "duden_pages": [],
                "spoken_override_reviewed": True,
            }
        }
    }
    groups = gwa.request_groups(manifest)
    assert groups["k"]["spoken_override_reviewed"] is True
    assert groups["k"]["pos"] == ""
    assert groups["k"]["gender"] == ""


@pytest.mark.parametrize(
    ("source_id", "source_refs", "lemma"),
    [
        ("A1-84887177192", "A1-84887177192|A1-WG-0033|B1-WG-0274", "Million"),
        ("A1-84887177193", "A1-84887177193|A1-WG-0034|B1-WG-0275", "Milliarde"),
    ],
)
def test_canonical_large_number_source_wins_merged_quantity_spoken_form(
    source_id, source_refs, lemma,
):
    overrides = gwa.load_overrides()
    note = fields(Lemma=lemma, SourceID=source_id, SourceRefs=source_refs)
    assert gwa.spoken_text(note, f"eine {lemma}", overrides) == lemma


def test_gemini_audit_classifies_exact_ambiguous_and_valid_fallbacks():
    manifest = {"notes": {
        "1": {
            "note_id": 1, "level": "A1", "lemma": "klar", "pos": "adj.",
            "spoken_text": "klar", "request_key": "exact",
            "old_word_audio": f"[sound:_goethe_word_gemini_{'a' * 64}.mp3]",
            "assignment": {
                "source": "duden_extra", "sha256": "b" * 64,
                "semantic_qa": {"status": "exact", "transcript": "klar"},
            },
        },
        "2": {
            "note_id": 2, "level": "A2", "lemma": "mehrdeutig", "pos": "adj.",
            "spoken_text": "mehrdeutig", "request_key": "ambiguous",
            "old_word_audio": f"[sound:_goethe_word_gemini_{'c' * 64}.mp3]",
            "assignment": {"source": "gemini", "sha256": "c" * 64},
        },
        "3": {
            "note_id": 3, "level": "B1", "lemma": "ohne Quelle", "pos": "phrase",
            "spoken_text": "ohne Quelle", "request_key": "missing",
            "old_word_audio": f"[sound:_goethe_word_gemini_{'d' * 64}.mp3]",
            "assignment": {"source": "gemini", "sha256": "d" * 64},
        },
    }, "live_audio_audit": {
        "semantic_mismatches": [], "semantic_candidates": [],
        "unknown_provenance": [],
    }}
    duden = {"items": {
        "exact": {"status": "ok"},
        "ambiguous": {"status": "ambiguous", "reason": "two pronunciations"},
        "missing": {"status": "unresolved", "match_method": "sitemap-not-found"},
    }}
    commons = {"items": {
        "exact": {"status": "ambiguous", "reason": "lower-priority duplicate"},
        "ambiguous": {"status": "unresolved"},
        "missing": {"status": "unresolved"},
    }}
    wiktionary = {"items": {
        "ambiguous": {"status": "unresolved"},
        "missing": {"status": "unresolved"},
    }}

    report = gwa.build_gemini_audit_report(
        manifest, duden, commons, wiktionary,
    )

    assert [item["note_id"] for item in report["wrong_certain"]] == [1]
    assert [item["note_id"] for item in report["needs_review"]] == [2]
    assert [item["note_id"] for item in report["valid_fallback"]] == [3]
    assert report["counts"] == {
        "wrong_certain": 1, "needs_review": 1, "valid_fallback": 1,
    }


def test_gemini_audit_scope_selects_only_live_gemini_notes():
    manifest = {"notes": {
        "1": {"note_id": 1, "old_word_audio": "[sound:_goethe_word_gemini_a.mp3]"},
        "2": {"note_id": 2, "old_word_audio": "[sound:_goethe_word_duden_b.mp3]"},
        "3": {"note_id": 3, "old_word_audio": "[sound:_goethe_word_gemini_c.mp3]"},
    }}

    assert gwa.selected_ids(manifest, "gemini-audit") == [1, 3]

    manifest["gemini_audit"] = {
        "wrong_certain": [{"note_id": 3}],
        "needs_review": [{"note_id": 1}],
        "valid_fallback": [],
    }
    assert gwa.selected_ids(manifest, "gemini-audit") == [3]
    assert gwa.gemini_baseline_ids(manifest) == [1, 3]


def test_gemini_audit_requires_exact_asr_for_human_replacement():
    manifest = {"notes": {"7": {
        "note_id": 7, "level": "B1", "lemma": "absolut", "pos": "adj., adv.",
        "spoken_text": "absolut", "request_key": "k",
        "old_word_audio": f"[sound:_goethe_word_gemini_{'a' * 64}.mp3]",
        "assignment": {
            "source": "duden_extra", "sha256": "b" * 64,
            "semantic_qa": {"status": "mismatch", "transcript": "Absolvent"},
        },
    }}, "live_audio_audit": {
        "semantic_mismatches": [], "semantic_candidates": [],
        "unknown_provenance": [],
    }}
    unavailable = {"items": {"k": {"status": "unresolved"}}}

    report = gwa.build_gemini_audit_report(
        manifest, {"items": {"k": {"status": "ok"}}},
        unavailable, unavailable,
    )

    assert report["wrong_certain"] == []
    assert [item["note_id"] for item in report["needs_review"]] == [7]


def test_gemini_audit_change_guard_uses_only_wrong_certain():
    exact = {
        "note_id": 1,
        "old_word_audio": f"[sound:_goethe_word_gemini_{'a' * 64}.mp3]",
        "assignment": {
            "source": "commons", "media_name": f"_goethe_word_commons_{'b' * 64}.mp3",
            "semantic_qa": {"status": "exact"},
        },
    }
    review = {
        "note_id": 2,
        "old_word_audio": f"[sound:_goethe_word_gemini_{'c' * 64}.mp3]",
        "assignment": {
            "source": "commons", "media_name": f"_goethe_word_commons_{'d' * 64}.mp3",
            "semantic_qa": {"status": "error"},
        },
    }
    manifest = {
        "prepared_scope": "gemini-audit",
        "prepared_note_ids": [1, 2],
        "notes": {"1": exact, "2": review},
    }
    gwa.validate_change_set(manifest)
    manifest["gemini_audit"] = {
        "wrong_certain": [{"note_id": 1}],
        "needs_review": [{"note_id": 2}],
        "valid_fallback": [],
    }
    gwa.validate_change_set(manifest)


def test_human_assignment_semantics_are_strict_asr_verified(tmp_path):
    exact = tmp_path / "exact.mp3"
    mismatch = tmp_path / "mismatch.mp3"
    exact.write_bytes(b"ID3" + b"x" * 200)
    mismatch.write_bytes(b"ID3" + b"y" * 200)
    manifest = {"notes": {
        "1": {
            "note_id": 1, "spoken_text": "klar",
            "assignment": {"source": "duden_extra", "sha256": "a" * 64, "path": str(exact)},
        },
        "2": {
            "note_id": 2, "spoken_text": "absolut",
            "assignment": {"source": "commons", "sha256": "b" * 64, "path": str(mismatch)},
        },
    }}

    async def transcribe(path):
        return "Klar." if path == exact else "Absolvent"

    cache = asyncio.run(gwa.verify_human_assignment_semantics(
        manifest, [1, 2], cache={}, transcribe=transcribe,
    ))

    assert manifest["notes"]["1"]["assignment"]["semantic_qa"]["status"] == "exact"
    assert manifest["notes"]["2"]["assignment"]["semantic_qa"]["status"] == "mismatch"
    assert set(cache) == {"a" * 64, "b" * 64}


def test_human_assignment_semantics_checkpoints_timeout_errors(tmp_path):
    audio = tmp_path / "slow.mp3"
    audio.write_bytes(b"ID3" + b"x" * 200)
    manifest = {"notes": {"1": {
        "note_id": 1, "spoken_text": "langsam",
        "assignment": {
            "source": "commons", "sha256": "c" * 64, "path": str(audio),
        },
    }}}
    checkpoints = []

    async def transcribe(path):
        await asyncio.sleep(0.05)
        return "langsam"

    cache = asyncio.run(gwa.verify_human_assignment_semantics(
        manifest, [1], cache={}, transcribe=transcribe,
        timeout_seconds=0.001,
        checkpoint=lambda value: checkpoints.append(dict(value)),
    ))
    assert manifest["notes"]["1"]["assignment"]["semantic_qa"]["status"] == "error"
    assert cache["c" * 64]["status"] == "error"
    assert len(checkpoints) == 1

    calls = 0

    async def must_not_retry(path):
        nonlocal calls
        calls += 1
        return "langsam"

    asyncio.run(gwa.verify_human_assignment_semantics(
        manifest, [1], cache=cache, transcribe=must_not_retry,
        timeout_seconds=0.001,
    ))
    assert calls == 0


def test_reviewed_duden_pages_are_source_scoped_and_protected_audio_wins():
    policy = gwa.load_duden_page_overrides()
    august = fields(
        Lemma="August",
        SourceRefs="A1-WG-0083|A2-WG-0142|B1-WG-0316",
    )
    pages = gwa.duden_page_specs(august, "August", policy)
    assert pages == [{
        "source_ref": "A1-WG-0083",
        "expected_lemma": "August",
        "spoken_text": "August",
        "url": "https://www.duden.de/rechtschreibung/August_Monat",
        "headword": "August",
        "reviewed": True,
    }]

    protected = {"A1-WG-0083": {
        "provider": "commons",
        "expected_lemma": "August",
        "spoken_text": "August",
        "sha256": "a" * 64,
        "title": "File:De-August.ogg",
        "original_sha1": "b" * 40,
        "reason": "reviewed",
    }}
    assert gwa.protected_audio_for(august, protected) is not None


def test_reported_audio_regressions_are_source_scoped_and_pinned():
    pages = gwa.load_duden_page_overrides()
    assert pages["B1-MAIN-0030"] == {
        "expected_lemma": "absolut",
        "spoken_text": "absolut",
        "url": "https://www.duden.de/rechtschreibung/absolut_Adjektiv",
        "headword": "absolut",
    }
    assert pages["B1-MAIN-0489"]["url"] == (
        "https://www.duden.de/rechtschreibung/dagegen"
    )
    assert pages["A2-0212"] == {
        "expected_lemma": "dagegen sein",
        "spoken_text": "dagegen",
        "url": "https://www.duden.de/rechtschreibung/dagegen",
        "headword": "dagegen",
    }

    protected = gwa.load_protected_audio()
    assert protected["A2-0293"]["title"] == "File:De-erlaubt.ogg"
    assert protected["A2-0293"]["spoken_text"] == "erlaubt"
    assert protected["A2-1050"]["title"] == "File:De-verabredet.ogg"
    assert protected["A2-1050"]["spoken_text"] == "verabredet"

    approved = gwa.load_approved_audio()
    required = {
        "A1-84887177192", "A1-84887177193",
        "A2-0212", "B1-MAIN-0489", "A2-0293", "A2-1050",
        "B1-MAIN-0030",
    }
    assert required <= set(approved)
    assert approved["B1-MAIN-0030"]["sha256"] == (
        "01160db9bdf952e0ab76dbf9428bc36a4796364fd76fe1fe237c1b9f2a38b434"
    )
    assert approved["A2-0293"]["source_revision"].startswith("sha1:")
    assert approved["A1-84887177192"]["provider"] == "duden"
    assert approved["A1-84887177193"]["semantic_transcript"] == "milliarde"


def test_approved_audio_hash_drift_fails_closed():
    item = {
        "assignment": {"source": "commons", "sha256": "b" * 64},
        "approved_audio": {
            "source_id": "A2-test", "provider": "commons", "sha256": "a" * 64,
            "spoken_text": "Test", "semantic_model": "model",
            "semantic_transcript": "Test",
        },
    }
    with pytest.raises(gwa.WordAudioError, match="approved audio drift"):
        gwa.enforce_approved_assignment(item)


def test_word_audio_batch_update_retries_partial_ankiconnect_success(monkeypatch):
    state = {1: "old-1", 2: "old-2"}

    def anki(action, **params):
        if action == "multi":
            first = params["actions"][0]["params"]["note"]
            state[first["id"]] = first["fields"]["WordAudio"]
            return [None for _ in params["actions"]]
        if action == "notesInfo":
            return [
                {"noteId": note_id, "fields": {"WordAudio": {"value": state[note_id]}}}
                for note_id in params["notes"]
            ]
        if action == "updateNoteFields":
            note = params["note"]
            state[note["id"]] = note["fields"]["WordAudio"]
            return None
        raise AssertionError(action)

    monkeypatch.setattr(gwa.gw, "anki", anki)
    gwa.update_word_audio([1, 2], {1: "new-1", 2: "new-2"})
    assert state == {1: "new-1", 2: "new-2"}


def test_direct_wortgruppen_duden_page_is_discovered_without_partial_match():
    pages = gwa.load_wortgruppen_duden_pages()
    geografie = fields(
        Lemma="Geografie",
        SourceRefs="A2-WG-0104|B1-WG-0137",
    )
    specs = gwa.duden_page_specs(
        geografie, "Geografie", {}, source_pages=pages
    )
    assert [item["url"] for item in specs] == [
        "https://www.duden.de/rechtschreibung/Geografie"
    ]

    phrase = fields(
        Lemma="ein Meter fünfzehn",
        SourceRefs="A1-WG-0095",
    )
    specs = gwa.duden_page_specs(
        phrase, "ein Meter fünfzehn", {}, source_pages=pages
    )
    assert specs[0]["reviewed"] is False
    assert specs[0]["url"] == "https://www.duden.de/rechtschreibung/Meter"


def test_direct_duden_resolver_uses_reviewed_page_and_exact_variant(
    monkeypatch, tmp_path
):
    pages = {
        "https://www.duden.de/rechtschreibung/August_Monat":
            gwa.duden.DudenPage(
                "https://www.duden.de/rechtschreibung/August_Monat",
                "August",
                "m",
                "Substantiv, maskulin",
                ("noun",),
                ({"audio_url": "https://cdn.test/august.mp3", "file_id": "august"},),
                (),
            ),
        "https://www.duden.de/rechtschreibung/Geografie":
            gwa.duden.DudenPage(
                "https://www.duden.de/rechtschreibung/Geografie",
                "Geografie, Geographie",
                "f",
                "Substantiv, feminin",
                ("noun",),
                ({"audio_url": "https://cdn.test/geografie.mp3", "file_id": "geo"},),
                (),
            ),
    }

    async def fetch(session, url, throttle=None):
        return 200, url, {}

    async def download(session, url, target, throttle=None):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ID3" + b"x" * 128)
        return target.stat().st_size, "a" * 64, "audio/mpeg", None

    monkeypatch.setattr(gwa, "DUDEN_EXTRA_DIR", tmp_path)
    monkeypatch.setattr(gwa.duden, "fetch_page", fetch)
    monkeypatch.setattr(
        gwa.duden, "parse_duden_page", lambda value, requested_url=None: pages[value]
    )
    monkeypatch.setattr(gwa.duden, "download_audio", download)

    august = asyncio.run(gwa.resolve_direct_duden_page(
        None,
        "august-key",
        {
            "spoken_text": "August",
            "pos": "n.",
            "gender": "m.",
            "duden_pages": [{
                "url": "https://www.duden.de/rechtschreibung/August_Monat",
                "headword": "August",
                "reviewed": True,
            }],
        },
        throttle=gwa.duden.RequestThrottle(),
    ))
    assert august["status"] == "ok"
    assert august["match_method"] == "reviewed-canonical-page"

    geografie = asyncio.run(gwa.resolve_direct_duden_page(
        None,
        "geo-key",
        {
            "spoken_text": "Geografie",
            "pos": "n.",
            "gender": "f.",
            "duden_pages": [{
                "url": "https://www.duden.de/rechtschreibung/Geografie",
                "headword": "",
                "reviewed": False,
            }],
        },
        throttle=gwa.duden.RequestThrottle(),
    ))
    assert geografie["status"] == "ok"
    assert geografie["match_method"] == "wortgruppen-direct-page"


def test_direct_duden_resolver_rejects_partial_phrase_page(monkeypatch):
    page = gwa.duden.DudenPage(
        "https://www.duden.de/rechtschreibung/Meter",
        "Meter",
        "m",
        "Substantiv, maskulin",
        ("noun",),
        ({"audio_url": "https://cdn.test/meter.mp3", "file_id": "meter"},),
        (),
    )

    async def fetch(session, url, throttle=None):
        return 200, "page", {}

    monkeypatch.setattr(gwa.duden, "fetch_page", fetch)
    monkeypatch.setattr(
        gwa.duden, "parse_duden_page", lambda *args, **kwargs: page
    )
    result = asyncio.run(gwa.resolve_direct_duden_page(
        None,
        "meter-key",
        {
            "spoken_text": "ein Meter fünfzehn",
            "pos": "phrase",
            "gender": "",
            "duden_pages": [{
                "url": page.canonical_url,
                "headword": "",
                "reviewed": False,
            }],
        },
        throttle=gwa.duden.RequestThrottle(),
    ))
    assert result is None


def test_direct_duden_resolver_records_unreviewed_transport_failure(monkeypatch):
    async def fetch(*args, **kwargs):
        raise TimeoutError("page timed out")

    monkeypatch.setattr(gwa.duden, "fetch_page", fetch)
    result = asyncio.run(gwa.resolve_direct_duden_page(
        None,
        "timeout-key",
        {
            "spoken_text": "Test",
            "pos": "n.",
            "gender": "n.",
            "duden_pages": [{
                "url": "https://www.duden.de/rechtschreibung/Test",
                "headword": "",
                "reviewed": False,
            }],
        },
        throttle=gwa.duden.RequestThrottle(),
    ))
    assert result["status"] == "technical_error"
    assert result["match_method"] == "direct-page-technical-error"
    assert "timed out" in result["reason"]


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


def test_gemini_audio_id_and_voice_are_deterministic_and_case_sensitive():
    assert gwa.gemini_audio_id("Bahnhof") == gwa.gemini_audio_id("Bahnhof")
    assert gwa.gemini_audio_id("Bahnhof") != gwa.gemini_audio_id("bahnhof")
    assert gwa.GEMINI_VOICES == ("Kore", "Charon")
    assert gwa.GEMINI_CONFIG["voices"] == ["Kore", "Charon"]
    assert gwa.gemini_voice_for("Bahnhof") in gwa.GEMINI_VOICES


def test_prepare_gemini_uses_deterministic_voice_and_verified_generator(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    calls = []
    monkeypatch.setattr(gwa, "GEMINI_DIR", tmp_path / "audio")
    monkeypatch.setattr(gwa, "GEMINI_INDEX", tmp_path / "gemini.json")
    monkeypatch.setattr(
        gwa,
        "validate_audio",
        lambda path, sha256=None, size=None: (size or 12, sha256 or "a" * 64),
    )

    async def fake_generate_verified_mp3(*, text, voice, purpose, target):
        calls.append((text, voice, purpose, target))
        return {
            "status": "ok",
            "path": str(target),
            "size": 12,
            "sha256": "a" * 64,
            "qa_status": "exact",
            "asr_transcript": text,
            "duration_seconds": 1.0,
            "voice": voice,
        }

    monkeypatch.setattr(
        gwa.gemini_tts, "generate_verified_mp3", fake_generate_verified_mp3
    )
    key = "request"
    groups = {key: {"spoken_text": "Bahnhof"}}
    unavailable = {"items": {key: {"status": "unresolved"}}}
    index = asyncio.run(
        gwa.prepare_gemini(groups, unavailable, unavailable, unavailable)
    )
    audio_id = gwa.gemini_audio_id("Bahnhof")

    assert calls == [(
        "Bahnhof",
        gwa.gemini_voice_for("Bahnhof"),
        "word",
        gwa.GEMINI_DIR / f"{audio_id}.mp3",
    )]
    assert index["items"][audio_id]["voice"] == gwa.gemini_voice_for("Bahnhof")


def test_prepare_gemini_needs_no_api_key_when_cache_is_complete(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setattr(gwa, "GEMINI_DIR", tmp_path / "audio")
    monkeypatch.setattr(gwa, "GEMINI_INDEX", tmp_path / "gemini.json")
    key = "request"
    index = asyncio.run(gwa.prepare_gemini(
        {key: {"spoken_text": "Bahnhof"}},
        {"items": {key: {"status": "ok"}}},
        {"items": {key: {"status": "unresolved"}}},
        {"items": {key: {"status": "unresolved"}}},
    ))
    assert index["items"] == {}


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
    state = {7: ""}

    def anki(action, **params):
        calls.append((action, params))
        if action == "multi":
            note = params["actions"][0]["params"]["note"]
            state[note["id"]] = note["fields"]["WordAudio"]
            return [{"result": None, "error": None}]
        if action == "notesInfo":
            return [{
                "noteId": note_id,
                "fields": {"WordAudio": {"value": state[note_id]}},
            } for note_id in params["notes"]]
        raise AssertionError(action)

    monkeypatch.setattr(gwa.gw, "anki", anki)
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


def test_assignment_identity_canonicalizes_bound_comma_alternatives():
    item = {
        "lemma_identity": "hell-, dunkel",
        "spoken_text": "hell-, dunkel",
        "assignment": {
            "lemma_identity": "hell-, dunkel",
            "spoken_text": "hell-, dunkel",
        },
    }

    gwa.validate_assignment_identity(
        fields(Lemma="hell-, dunkel"),
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


def test_live_assignment_mismatches_approves_tracked_spoken_override_repair():
    records = {
        1: {"fields": fields(
            SourceID="A2-0332",
            SourceRefs="A2-0332|A2-MAIN-0328",
            Lemma="fit sein",
            WordAudio=f"[sound:_goethe_word_gemini_{'a' * 64}.mp3]",
        )},
    }
    manifest = {"notes": {
        "1": {
            "spoken_text": "fit",
            "assignment": {"sha256": "b" * 64, "spoken_text": "fit"},
        },
    }}
    provenance = {
        "a" * 64: {"spoken_texts": ["fit sein"], "providers": ["gemini"]},
    }
    report = gwa.live_assignment_mismatches(records, manifest, provenance, {})
    assert [item["note_id"] for item in report["semantic_mismatches"]] == [1]
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
        "gemini_config": gwa.GEMINI_CONFIG,
        "commons_config": gwa.COMMONS_CONFIG,
        "wiktionary_config": gwa.WIKTIONARY_CONFIG,
        "source_order": ["duden_local", "duden_extra", "commons", "wiktionary", "gemini"],
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
    current = {
        "status": "unresolved",
        "resolver_version": gwa.DUDEN_RESOLVER_VERSION,
        "match_method": "sitemap-metadata-conflict",
    }
    definitive = {
        "status": "unresolved",
        "resolver_version": gwa.DUDEN_RESOLVER_VERSION - 1,
        "match_method": "sitemap-not-found",
    }
    stale = {"status": "unresolved", "resolver_version": gwa.DUDEN_RESOLVER_VERSION - 1}
    positive = {"status": "ok", "resolver_version": 1}
    assert gwa.reuse_duden_cache(current, refresh_negative=False)
    assert not gwa.reuse_duden_cache(current, refresh_negative=True)
    assert gwa.reuse_duden_cache(definitive, refresh_negative=True)
    assert not gwa.reuse_duden_cache(stale, refresh_negative=False)
    assert gwa.reuse_duden_cache(positive, refresh_negative=True)


def test_refresh_rechecks_cached_candidate_pages_without_recrawling_sitemap(
    monkeypatch, tmp_path
):
    index_path = tmp_path / "duden_extra.json"
    index_path.write_text(json.dumps({
        "schema_version": 2,
        "items": {
            "key": {
                "status": "unresolved",
                "resolver_version": gwa.DUDEN_RESOLVER_VERSION,
                "match_method": "sitemap-metadata-conflict",
                "candidate_pages": [{
                    "canonical_url":
                        "https://www.duden.de/rechtschreibung/Geografie"
                }],
            }
        },
    }), encoding="utf-8")
    monkeypatch.setattr(gwa, "DUDEN_EXTRA_INDEX", index_path)
    monkeypatch.setattr(gwa, "DUDEN_EXTRA_DIR", tmp_path / "audio")

    async def no_match(*args, **kwargs):
        return None

    async def no_sitemap(*args, **kwargs):
        raise AssertionError("sitemap crawl must not run")

    monkeypatch.setattr(gwa, "resolve_direct_duden_page", no_match)
    monkeypatch.setattr(
        gwa.duden, "build_lexeme_index_for_rows", no_sitemap
    )
    result = asyncio.run(gwa.prepare_duden({
        "key": {
            "request_key": "key",
            "spoken_text": "Geografie",
            "pos": "n.",
            "gender": "f.",
            "note_ids": [1],
            "skip_duden": False,
            "duden_pages": [],
        }
    }, refresh_negative=True))
    assert result["items"]["key"]["status"] == "unresolved"


def test_gemini_audit_duden_marks_sitemap_outage_for_review(
    monkeypatch, tmp_path,
):
    index_path = tmp_path / "duden_extra.json"
    monkeypatch.setattr(gwa, "DUDEN_EXTRA_INDEX", index_path)
    monkeypatch.setattr(gwa, "DUDEN_EXTRA_DIR", tmp_path / "audio")

    async def no_direct(*args, **kwargs):
        return None

    async def outage(*args, **kwargs):
        raise gwa.duden.TechnicalError("sitemap unavailable")

    monkeypatch.setattr(gwa, "resolve_direct_duden_page", no_direct)
    monkeypatch.setattr(gwa.duden, "build_lexeme_index_for_rows", outage)
    result = asyncio.run(gwa.prepare_duden({
        "key": {
            "request_key": "key", "spoken_text": "Test", "pos": "n.",
            "gender": "n.", "note_ids": [1], "skip_duden": False,
            "duden_pages": [],
        }
    }, refresh_negative=True, fail_on_technical_error=False))
    assert result["items"]["key"]["status"] == "technical_error"
    assert "sitemap unavailable" in result["items"]["key"]["reason"]


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
    manifest["notes"]["1"]["assignment"] = {"source": "gemini", "media_name": "_goethe_word_gemini_new.mp3"}
    with pytest.raises(gwa.WordAudioError, match="unapproved audio transition"):
        gwa.validate_change_set(manifest)


def test_change_set_guard_allows_historical_edge_migration_to_gemini():
    manifest = {"notes": {
        "1": {
            "note_id": 1,
            "old_word_audio": "[sound:_goethe_word_edge_old.mp3]",
            "assignment": {
                "source": "gemini",
                "media_name": "_goethe_word_gemini_new.mp3",
            },
        },
    }}
    gwa.validate_change_set(manifest)
    assert gwa.word_audio_provider(manifest["notes"]["1"]["old_word_audio"]) == "edge"
    assert gwa.word_audio_provider(
        "[sound:_goethe_word_gemini_new.mp3]"
    ) == "gemini"


def test_word_pilot_covers_all_levels():
    notes = {}
    note_id = 1
    for level in gwa.scope.LEVELS:
        for source in ("duden_local", "gemini", "commons", "wiktionary"):
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


def test_edge_scope_selects_and_persists_exact_historical_edge_notes():
    manifest = {"notes": {
        "1": {
            "note_id": 1,
            "old_word_audio": "[sound:_goethe_word_edge_old.mp3]",
        },
        "2": {
            "note_id": 2,
            "old_word_audio": "[sound:_goethe_word_gemini_new.mp3]",
        },
        "3": {"note_id": 3, "old_word_audio": ""},
    }}
    assert gwa.selected_ids(manifest, "edge") == [1]
    manifest.update({"prepared_scope": "edge", "prepared_note_ids": [1]})
    assert gwa.selected_ids(manifest, "edge") == [1]
    gwa.require_prepared_scope(manifest, "edge")
    with pytest.raises(gwa.WordAudioError, match="only for the audited Edge"):
        gwa.require_prepared_scope(manifest, "full")


def test_snapshot_baseline_rejects_word_audio_changed_after_prepare():
    original = fields(
        SourceID="A1-MAIN-0080",
        WordAudio="[sound:_goethe_word_edge_old.mp3]",
    )
    manifest = {"notes": {"1": {
        "source_signature": gwa.source_signature(original),
        "old_word_audio": original["WordAudio"],
    }}}
    records = {1: {"fields": dict(original)}}

    gwa.validate_prepared_live_baseline(manifest, records)
    records[1]["fields"]["WordAudio"] = "[sound:_goethe_word_duden_new.mp3]"

    with pytest.raises(gwa.WordAudioError, match="WordAudio changed"):
        gwa.validate_prepared_live_baseline(manifest, records)


def test_rollback_verifies_baseline_before_writing(monkeypatch):
    args = SimpleNamespace(
        confirmation=gwa.ROLLBACK_CONFIRMATION,
        scope="edge",
        note_id=None,
    )
    manifest = {"notes": {}}
    snapshot = {"notes": {}}
    records = {}
    writes = []
    monkeypatch.setattr(gwa, "load_ready", lambda: (manifest, snapshot))
    monkeypatch.setattr(gwa, "require_prepared_scope", lambda *args: None)
    monkeypatch.setattr(gwa, "live_records", lambda: records)
    monkeypatch.setattr(
        gwa,
        "verify_baseline",
        lambda *args: (_ for _ in ()).throw(gwa.WordAudioError("stale baseline")),
    )
    monkeypatch.setattr(gwa, "update_word_audio", lambda *args: writes.append(args))

    with pytest.raises(gwa.WordAudioError, match="stale baseline"):
        gwa.command_rollback(args)
    assert writes == []


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
