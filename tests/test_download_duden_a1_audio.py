from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TEST_FILE = Path(__file__).resolve()
PROJECT_ROOT = TEST_FILE.parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
import download_duden_a1_audio as duden  # noqa: E402


def test_parse_markdown_wordlist_counts_and_keeps_words():
    rows = duden.parse_markdown_wordlist(duden.SOURCE_PATH)
    assert len(rows) == 685
    assert rows[3].word == "Abfahrt"
    assert rows[9].word == "all-"
    assert rows[33].word == "an sein"
    assert rows[380].word == "sich kümmern"
    assert rows[552].word == "Sie"


def test_normalize_word_for_file_handles_umlaut_and_punctuation():
    assert duden.normalize_word_for_file("Straße") == "strasze"
    assert duden.normalize_word_for_file("Frühstück") == "fruehstueck"
    assert duden.normalize_word_for_file("Pommes frites") == "pommes_frites"
    assert duden.normalize_word_for_file("all-") == "all"
    assert duden.normalize_word_for_file("sich kümmern") == "sich_kuemmern"


def test_parse_duden_page_extracts_canonical_headword_audio_and_disambiguation():
    html = """
    <html>
      <head>
        <link rel="canonical" href="https://www.duden.de/rechtschreibung/Abfahrt" />
      </head>
      <body>
        <h1>Ab\u00adfahrt, die</h1>
        <dl class="tuple">
          <dt class="tuple__key">Wortart: <a class="tuple__icon" href="/hilfe/wortart">ⓘ</a></dt>
          <dd class="tuple__val">Substantiv, feminin</dd>
        </dl>
        <dl class="tuple">
          <dt class="tuple__key">Aussprache: <a class="tuple__icon" href="/hilfe/aussprache">ⓘ</a></dt>
          <dd class="tuple__val">
            <dl class="pronunciation-guide">
              <dt class="pronunciation-guide__diction-type">Betonung</dt>
              <dd class="pronunciation-guide__diction">
                <div>
                  <button data-href="https://cdn.duden.de/_media_/audio/ID1_1.mp3"
                          data-file-id="ID1_1"
                          class="pronunciation-guide__sound">🔉</button>
                </div>
              </dd>
            </dl>
          </dd>
        </dl>
        <dl class="disambiguation">
          <dt class="disambiguation__title">Wort mit gleicher Schreibung</dt>
          <dd class="disambiguation__list">
            <a href="/rechtschreibung/Abfahrt_2" data-duden-ref-type="lexeme">Abfahrt (2)</a>
          </dd>
        </dl>
      </body>
    </html>
    """
    page = duden.parse_duden_page(html, requested_url="https://www.duden.de/rechtschreibung/Abfahrt")
    assert page.canonical_url == "https://www.duden.de/rechtschreibung/Abfahrt"
    assert page.headword == "Abfahrt"
    assert page.h1_gender == "f"
    assert "noun" in page.pos_labels
    assert page.audio_candidates[0]["audio_url"].endswith("ID1_1.mp3")
    assert page.audio_candidates[0]["file_id"] == "ID1_1"
    assert page.disambiguation_urls == ("https://www.duden.de/rechtschreibung/Abfahrt_2",)


def test_gender_and_pos_matching():
    assert duden.gender_matches("f.", "f")
    assert duden.gender_matches("", None)
    assert not duden.gender_matches("m.", "f")
    assert duden.pos_matches("n.", ("noun",))
    assert duden.pos_matches("det., pron.", ("pronoun",))
    assert not duden.pos_matches("v.", ("noun",))
    assert duden.pos_matches("part.", ("particle",))


def test_parse_lexeme_sitemap_page_extracts_rechtschreibung_links():
    html = """
    <html><body>
      <a href="/rechtschreibung/Pommes_frites">Pommes&nbsp;frites</a>
      <a href="/hilfe/rechtschreibung">ignore</a>
      <a href="/rechtschreibung/Pommes_frites">Pommes frites</a>
    </body></html>
    """
    candidates = duden.parse_lexeme_sitemap_page(html)
    assert candidates == [
        duden.LexemeCandidate(
            title="Pommes frites",
            url="https://www.duden.de/rechtschreibung/Pommes_frites",
        )
    ]


def test_audit_metadata_accepts_pluralwort_and_missing_wordart():
    pommes = duden.parse_duden_page(
        """
        <html><head><link rel="canonical" href="https://www.duden.de/rechtschreibung/Pommes_frites" /></head>
        <body>
          <h1>Pommes frites, die</h1>
          <dl><dt class="tuple__key">Wortart:</dt><dd class="tuple__val">Pluralwort</dd></dl>
        </body></html>
        """
    )
    pommes_row = duden.SourceRow(483, "Pommes frites", "n.", "pl.", "A1", "x", "")
    assert duden.page_metadata_matches_for_audit(pommes_row, pommes)[0]

    an_sein = duden.parse_duden_page(
        """
        <html><head><link rel="canonical" href="https://www.duden.de/rechtschreibung/an_sein" /></head>
        <body><h1>an sein</h1></body></html>
        """
    )
    an_sein_row = duden.SourceRow(34, "an sein", "v.", "", "A1", "x", "")
    assert duden.page_metadata_matches_for_audit(an_sein_row, an_sein)[0]


def test_audit_exact_sitemap_title_overrides_achtung_pos_conflict():
    page = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Achtung",
        headword="Achtung",
        h1_gender="f",
        wordart="Substantiv, feminin",
        pos_labels=("noun",),
        audio_candidates=(
            {
                "audio_url": "https://cdn.duden.de/_media_/audio/ID4109164_330610821.mp3",
                "file_id": "ID4109164_330610821",
                "label": "",
            },
        ),
        disambiguation_urls=(),
    )
    row = duden.SourceRow(8, "Achtung", "interj.", "", "A1", "x", "")
    ok, reason = duden.page_metadata_matches_for_audit(row, page, "Achtung")
    assert ok
    assert "metadata override" in reason
    decision = duden.choose_audit_audio(
        row,
        [(duden.LexemeCandidate("Achtung", page.canonical_url), page, duden.page_to_audit_candidate(duden.LexemeCandidate("Achtung", page.canonical_url), page, ok, reason))],
    )
    assert decision.status == "exact_audio_found"
    assert decision.selected_file_id == "ID4109164_330610821"
    assert duden.audit_decision_to_override(decision)["match_method"] == "exact-headword-metadata-override"


def test_gender_options_aliases_and_plural_identical_forms():
    assert duden.gender_matches("f.", "die oder das")
    assert duden.gender_matches("n.", "das oder der")
    assert duden.gender_matches("m.", "der oder das")

    alias_page = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/S_Bahn",
        headword="S-Bahn®",
        h1_gender="f",
        wordart="Substantiv, feminin",
        pos_labels=("noun",),
        audio_candidates=({"audio_url": "https://cdn.duden.de/_media_/audio/ID1.mp3", "file_id": "ID1", "label": ""},),
        disambiguation_urls=(),
    )
    alias_row = duden.SourceRow(517, "S-Bahn", "n.", "f.", "A1", "x", "")
    assert duden.page_metadata_matches_for_audit(alias_row, alias_page, "S-Bahn")[0]

    plural_page = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Moebel",
        headword="Möbel",
        h1_gender="n",
        wordart="Substantiv, Neutrum",
        pos_labels=("noun",),
        audio_candidates=({"audio_url": "https://cdn.duden.de/_media_/audio/ID2.mp3", "file_id": "ID2", "label": ""},),
        disambiguation_urls=(),
    )
    plural_row = duden.SourceRow(438, "Möbel", "n.", "pl.", "A1", "x", "")
    assert duden.page_metadata_matches_for_audit(plural_row, plural_page, "Möbel")[0]


def test_pos_equivalence_and_preferred_multi_page_selection():
    assert duden.pos_matches("det.", ("pronoun",))
    assert duden.pos_matches("det., pron.", ("determiner",))
    assert duden.pos_matches("interj.", ("particle",))
    assert duden.pos_matches("interj.", ("adjective",))

    row = duden.SourceRow(237, "Foto", "n.", "n.", "A1", "x", "")
    camera = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Foto_Fotoapparat",
        headword="Foto",
        h1_gender="m",
        wordart="Substantiv, maskulin",
        pos_labels=("noun",),
        audio_candidates=({"audio_url": "https://cdn.duden.de/_media_/audio/ID1.mp3", "file_id": "ID1", "label": ""},),
        disambiguation_urls=(),
    )
    photography = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Foto_Fotografie",
        headword="Foto",
        h1_gender="das oder die",
        wordart="Substantiv, Neutrum, oder Substantiv, feminin",
        pos_labels=("noun",),
        audio_candidates=({"audio_url": "https://cdn.duden.de/_media_/audio/ID2.mp3", "file_id": "ID2", "label": ""},),
        disambiguation_urls=(),
    )
    candidate_1 = duden.LexemeCandidate("Foto", camera.canonical_url)
    candidate_2 = duden.LexemeCandidate("Foto", photography.canonical_url)
    accepted = []
    for candidate, page in [(candidate_1, camera), (candidate_2, photography)]:
        ok, reason = duden.page_metadata_matches_for_audit(row, page, candidate.title)
        accepted.append((candidate, page, duden.page_to_audit_candidate(candidate, page, ok, reason)))
    decision = duden.choose_audit_audio(row, accepted)
    assert decision.status == "exact_audio_found"
    assert decision.selected_page_url == "https://www.duden.de/rechtschreibung/Foto_Fotografie"
    assert decision.selected_file_id == "ID2"


def test_matching_page_without_audio_blocks_conflicting_audio_page():
    row = duden.SourceRow(553, "Sie", "pron.", "", "A1", "x", "")
    pronoun = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Sie_Anrede",
        headword="Sie",
        h1_gender=None,
        wordart="Pronomen",
        pos_labels=("pronoun",),
        audio_candidates=(),
        disambiguation_urls=(),
    )
    noun = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Sie_Frau",
        headword="Sie",
        h1_gender="f",
        wordart="Substantiv, feminin",
        pos_labels=("noun",),
        audio_candidates=({"audio_url": "https://cdn.duden.de/_media_/audio/ID1.mp3", "file_id": "ID1", "label": ""},),
        disambiguation_urls=(),
    )
    accepted = []
    for page in (pronoun, noun):
        candidate = duden.LexemeCandidate("Sie", page.canonical_url)
        ok, reason = duden.page_metadata_matches_for_audit(row, page, candidate.title)
        accepted.append((candidate, page, duden.page_to_audit_candidate(candidate, page, ok, reason)))
    decision = duden.choose_audit_audio(row, accepted)
    assert decision.status == "exact_page_no_audio"


def test_audit_headword_matching_is_exact_case_and_rejects_lemma():
    assert duden.exact_audit_headword_matches("essen", "essen")
    assert not duden.exact_audit_headword_matches("Essen", "essen")
    assert not duden.exact_audit_headword_matches("Beamte", "Beamter")
    assert not duden.exact_audit_headword_matches("Papiere", "Papier")
    assert not duden.exact_audit_headword_matches("sich kümmern", "kümmern")


def test_choose_audit_audio_uses_first_pronunciation():
    row = duden.SourceRow(41, "Appetit", "n.", "m.", "A1", "x", "")
    page = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Appetit",
        headword="Appetit",
        h1_gender="m",
        wordart="Substantiv, maskulin",
        pos_labels=("noun",),
        audio_candidates=(
            {"audio_url": "https://cdn.duden.de/_media_/audio/ID1.mp3", "file_id": "ID1", "label": ""},
            {"audio_url": "https://cdn.duden.de/_media_/audio/ID2.mp3", "file_id": "ID2", "label": "auch"},
        ),
        disambiguation_urls=(),
    )
    candidate = duden.LexemeCandidate("Appetit", "https://www.duden.de/rechtschreibung/Appetit")
    audit_candidate = duden.page_to_audit_candidate(candidate, page, True, "metadata matched")
    decision = duden.choose_audit_audio(row, [(candidate, page, audit_candidate)])
    assert decision.status == "exact_audio_found"
    assert decision.selected_file_id == "ID1"


def test_ambiguous_audit_keeps_status_but_can_write_first_candidate_override():
    row = duden.SourceRow(145, "Dame", "n.", "f.", "A1", "x", "")
    page_1 = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Dame_Titel",
        headword="Dame",
        h1_gender="f",
        wordart="Substantiv, feminin",
        pos_labels=("noun",),
        audio_candidates=({"audio_url": "https://cdn.duden.de/_media_/audio/ID1.mp3", "file_id": "ID1", "label": ""},),
        disambiguation_urls=(),
    )
    page_2 = duden.DudenPage(
        canonical_url="https://www.duden.de/rechtschreibung/Dame_Frau",
        headword="Dame",
        h1_gender="f",
        wordart="Substantiv, feminin",
        pos_labels=("noun",),
        audio_candidates=({"audio_url": "https://cdn.duden.de/_media_/audio/ID2.mp3", "file_id": "ID2", "label": ""},),
        disambiguation_urls=(),
    )
    candidate_1 = duden.LexemeCandidate("Dame", page_1.canonical_url)
    candidate_2 = duden.LexemeCandidate("Dame", page_2.canonical_url)
    decision = duden.choose_audit_audio(
        row,
        [
            (candidate_1, page_1, duden.page_to_audit_candidate(candidate_1, page_1, True, "metadata matched")),
            (candidate_2, page_2, duden.page_to_audit_candidate(candidate_2, page_2, True, "metadata matched")),
        ],
    )
    assert decision.status == "ambiguous_page"
    assert decision.selected_file_id == "ID1"
    override = duden.audit_decision_to_override(decision)
    assert override["match_method"] == "audit-sitemap-ambiguous-first-override"
    assert duden.override_to_resolution(row, override).file_id == "ID1"


def test_parse_retry_after_numeric_and_http_date():
    assert duden.parse_retry_after("3") == 3.0
    assert duden.parse_retry_after("bad") is None


def test_should_retry_on_rate_limit_and_server_error():
    assert duden.should_retry(429)
    assert duden.should_retry(503)
    assert not duden.should_retry(404)


def test_validate_mp3_bytes_and_hash_validation(tmp_path: Path):
    mp3 = tmp_path / "ok.mp3"
    mp3.write_bytes(b"ID3" + b"\x00" * 32)
    assert duden.validate_mp3_bytes(mp3.read_bytes()) == "id3"
    assert duden.existing_file_is_valid(mp3, expected_sha256=hashlib.sha256(mp3.read_bytes()).hexdigest())
    assert not duden.existing_file_is_valid(mp3, expected_sha256="0" * 64)


def test_load_overrides_supports_rows_mapping(tmp_path: Path):
    path = tmp_path / "duden_overrides.json"
    path.write_text(
        json.dumps({"rows": {"553": {"duden_page_url": "https://example.test", "status": "ok"}}}),
        encoding="utf-8",
    )
    overrides = duden.load_overrides(path)
    assert overrides[553]["duden_page_url"] == "https://example.test"


def test_resolve_row_marks_ambiguous_when_multiple_audio(monkeypatch):
    html = """
    <html>
      <head>
        <link rel="canonical" href="https://www.duden.de/rechtschreibung/Abfahrt" />
      </head>
      <body>
        <h1>Abfahrt, die</h1>
        <dl class="tuple">
          <dt class="tuple__key">Wortart:</dt>
          <dd class="tuple__val">Substantiv, feminin</dd>
        </dl>
        <dl class="tuple">
          <dt class="tuple__key">Aussprache:</dt>
          <dd class="tuple__val">
            <button class="pronunciation-guide__sound" data-href="https://cdn.duden.de/_media_/audio/ID1.mp3" data-file-id="ID1">🔉</button>
            <button class="pronunciation-guide__sound" data-href="https://cdn.duden.de/_media_/audio/ID2.mp3" data-file-id="ID2">🔉</button>
          </dd>
        </dl>
      </body>
    </html>
    """

    async def fake_fetch_page(session, url):
        return 200, html, {}

    monkeypatch.setattr(duden, "fetch_page", fake_fetch_page)
    row = duden.SourceRow(4, "Abfahrt", "n.", "f.", "A1", "x", "")
    resolution, page = asyncio.run(duden.resolve_row(None, row, {}))
    assert resolution.status == "ambiguous"
    assert page is not None
    assert resolution.duden_page_url == "https://www.duden.de/rechtschreibung/Abfahrt"


def test_resolve_row_unresolved_when_page_missing(monkeypatch):
    async def fake_fetch_page(session, url):
        return 404, "", {}

    monkeypatch.setattr(duden, "fetch_page", fake_fetch_page)
    row = duden.SourceRow(553, "Sie", "pron.", "", "A1", "x", "")
    resolution, page = asyncio.run(duden.resolve_row(None, row, {}))
    assert resolution.status == "unresolved"
    assert page is None


def test_resolve_row_marks_technical_error_on_403(monkeypatch):
    async def fake_fetch_page(session, url):
        return 403, "", {}

    monkeypatch.setattr(duden, "fetch_page", fake_fetch_page)
    row = duden.SourceRow(4, "Abfahrt", "n.", "f.", "A1", "x", "")
    resolution, page = asyncio.run(duden.resolve_row(None, row, {}))
    assert resolution.status == "technical_error"
    assert "403" in resolution.reason
    assert page is None


def test_request_throttle_enforces_page_and_cdn_spacing():
    class FakeClock:
        def __init__(self):
            self.now = 100.0
            self.sleeps = []

        def time(self):
            return self.now

        async def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    clock = FakeClock()
    throttle = duden.RequestThrottle(now_fn=clock.time, sleep_fn=clock.sleep)

    async def run():
        await throttle.wait_for_page()
        await throttle.wait_for_page()
        await throttle.wait_for_cdn()
        await throttle.wait_for_cdn()

    asyncio.run(run())
    assert clock.sleeps == [2.0, 1.0]


def test_override_short_circuits_resolution(monkeypatch):
    async def fail_fetch_page(session, url):
        raise AssertionError("fetch should not run for override")

    monkeypatch.setattr(duden, "fetch_page", fail_fetch_page)
    row = duden.SourceRow(553, "Sie", "pron.", "", "A1", "x", "")
    resolution, page = asyncio.run(
        duden.resolve_row(
            None,
            row,
            {
                553: {
                    "duden_page_url": "https://www.duden.de/rechtschreibung/Sie_Anrede",
                    "status": "ok",
                    "reason": "manual",
                    "match_method": "manual-override",
                }
            },
        )
    )
    assert resolution.status == "ok"
    assert page is None
    assert resolution.duden_page_url.endswith("Sie_Anrede")


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], chunks: list[bytes]):
        self.status = status
        self.headers = headers
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self, errors="replace"):
        return "error body"

    @property
    def content(self):
        return self

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            yield chunk


class _FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, url, headers=None):
        return self.response


class _SequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, url, headers=None):
        return self.responses.pop(0)


def test_download_audio_rejects_bad_mime(tmp_path: Path):
    response = _FakeResponse(
        200,
        {"content-type": "text/html", "etag": '"abc"'},
        [b"ID3" + b"\x00" * 32],
    )
    session = _FakeSession(response)
    with pytest.raises(ValueError, match="unexpected content-type"):
        asyncio.run(duden.download_audio(session, "https://cdn.duden.de/_media_/audio/x.mp3", tmp_path / "x.mp3"))


def test_download_audio_retries_after_429(tmp_path: Path):
    session = _SequenceSession(
        [
            _FakeResponse(429, {"retry-after": "0", "content-type": "text/html"}, [b"rate limit"]),
            _FakeResponse(200, {"content-type": "audio/mpeg", "etag": '"abc"'}, [b"ID3", b"\x00" * 16]),
        ]
    )
    out = tmp_path / "x.mp3"
    size, sha256, content_type, etag = asyncio.run(
        duden.download_audio(session, "https://cdn.duden.de/_media_/audio/x.mp3", out)
    )
    assert out.exists()
    assert size > 0
    assert content_type == "audio/mpeg"
    assert etag == '"abc"'
    assert len(sha256) == 64


def test_download_audio_circuit_breaks_on_long_retry_after(tmp_path: Path):
    response = _FakeResponse(
        429,
        {"retry-after": "120", "content-type": "text/html"},
        [b"rate limit"],
    )
    session = _FakeSession(response)
    with pytest.raises(duden.TechnicalError, match="429 retry-after"):
        asyncio.run(duden.download_audio(session, "https://cdn.duden.de/_media_/audio/x.mp3", tmp_path / "x.mp3"))


def test_atomic_write_text_retries_on_permission_error(tmp_path: Path, monkeypatch):
    target = tmp_path / "manifest.jsonl"
    calls = {"count": 0}
    real_replace = duden.os.replace

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(duden.os, "replace", flaky_replace)
    duden.atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert calls["count"] >= 2


def test_download_audio_writes_valid_mp3(tmp_path: Path):
    response = _FakeResponse(
        200,
        {"content-type": "audio/mpeg", "etag": '"abc"'},
        [b"ID3", b"\x00" * 16],
    )
    session = _FakeSession(response)
    out = tmp_path / "x.mp3"
    size, sha256, content_type, etag = asyncio.run(
        duden.download_audio(session, "https://cdn.duden.de/_media_/audio/x.mp3", out)
    )
    assert out.exists()
    assert size > 0
    assert sha256 == hashlib.sha256(out.read_bytes()).hexdigest()
    assert content_type == "audio/mpeg"
    assert etag == '"abc"'


def test_backup_and_finalize_promotes_staging(tmp_path: Path, monkeypatch):
    live_words = tmp_path / "words"
    staging_words = tmp_path / "words_duden_staging"
    backup_root = tmp_path / "backup"
    live_words.mkdir()
    staging_words.mkdir()
    (live_words / "old.mp3").write_bytes(b"ID3" + b"\x00" * 8)
    (staging_words / "new.mp3").write_bytes(b"ID3" + b"\x00" * 8)
    (staging_words / "manifest.jsonl").write_text("{}", encoding="utf-8")
    (staging_words / "manifest.meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "words_manifest.jsonl").write_text("old", encoding="utf-8")
    (tmp_path / "words_manifest.meta.json").write_text("old", encoding="utf-8")

    monkeypatch.setattr(duden, "LIVE_WORDS_DIR", live_words)
    monkeypatch.setattr(duden, "STAGING_WORDS_DIR", staging_words)
    monkeypatch.setattr(duden, "BACKUP_ROOT", backup_root)
    monkeypatch.setattr(duden, "LIVE_MANIFEST_PATH", tmp_path / "words_manifest.jsonl")
    monkeypatch.setattr(duden, "LIVE_META_PATH", tmp_path / "words_manifest.meta.json")
    monkeypatch.setattr(duden, "STAGING_MANIFEST_PATH", staging_words / "manifest.jsonl")
    monkeypatch.setattr(duden, "STAGING_META_PATH", staging_words / "manifest.meta.json")

    backup_dir = duden.finalize_staging_to_live()
    assert backup_dir is not None
    assert (backup_dir / "words" / "old.mp3").exists()
    assert (tmp_path / "words_manifest.jsonl").exists()
    assert (tmp_path / "words_manifest.meta.json").exists()
    assert (live_words / "new.mp3").exists()


def test_finalize_skips_backup_when_live_is_already_duden(tmp_path: Path, monkeypatch):
    live_words = tmp_path / "words"
    staging_words = tmp_path / "words_duden_staging"
    live_words.mkdir()
    staging_words.mkdir()
    (live_words / "old.mp3").write_bytes(b"ID3" + b"\x00" * 8)
    (staging_words / "new.mp3").write_bytes(b"ID3" + b"\x00" * 8)
    (staging_words / "manifest.jsonl").write_text("{}", encoding="utf-8")
    (staging_words / "manifest.meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "words_manifest.jsonl").write_text(
        json.dumps({"source": "duden", "row": 1}),
        encoding="utf-8",
    )
    (tmp_path / "words_manifest.meta.json").write_text("{}", encoding="utf-8")

    def fail_backup():
        raise AssertionError("backup should not run once live is already Duden")

    monkeypatch.setattr(duden, "LIVE_WORDS_DIR", live_words)
    monkeypatch.setattr(duden, "STAGING_WORDS_DIR", staging_words)
    monkeypatch.setattr(duden, "BACKUP_ROOT", tmp_path / "backup")
    monkeypatch.setattr(duden, "LIVE_MANIFEST_PATH", tmp_path / "words_manifest.jsonl")
    monkeypatch.setattr(duden, "LIVE_META_PATH", tmp_path / "words_manifest.meta.json")
    monkeypatch.setattr(duden, "STAGING_MANIFEST_PATH", staging_words / "manifest.jsonl")
    monkeypatch.setattr(duden, "STAGING_META_PATH", staging_words / "manifest.meta.json")
    monkeypatch.setattr(duden, "backup_current_matrix_state", fail_backup)

    backup_dir = duden.finalize_staging_to_live()
    assert backup_dir is None
    assert (live_words / "new.mp3").exists()


def test_process_rows_blocks_promotion_with_invalid_status(tmp_path: Path, monkeypatch):
    live_words = tmp_path / "words"
    staging_words = tmp_path / "words_duden_staging"
    live_words.mkdir()
    staging_words.mkdir()
    (staging_words / "manifest.jsonl").write_text(
        json.dumps(
            {
                "row": 1,
                "word": "a",
                "pos": "n.",
                "gender": "",
                "output_filename": "0001_a.mp3",
                "source": "duden",
                "duden_page_url": None,
                "duden_audio_url": None,
                "file_id": None,
                "match_method": None,
                "status": "pending",
                "reason": "pending",
                "size": None,
                "sha256": None,
                "content_type": None,
                "etag": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (staging_words / "manifest.meta.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(duden, "LIVE_WORDS_DIR", live_words)
    monkeypatch.setattr(duden, "STAGING_WORDS_DIR", staging_words)
    monkeypatch.setattr(duden, "LIVE_MANIFEST_PATH", tmp_path / "words_manifest.jsonl")
    monkeypatch.setattr(duden, "LIVE_META_PATH", tmp_path / "words_manifest.meta.json")
    monkeypatch.setattr(duden, "STAGING_MANIFEST_PATH", staging_words / "manifest.jsonl")
    monkeypatch.setattr(duden, "STAGING_META_PATH", staging_words / "manifest.meta.json")
    monkeypatch.setattr(duden, "OVERRIDES_PATH", tmp_path / "duden_overrides.json")
    monkeypatch.setattr(duden, "load_overrides", lambda path: {})
    monkeypatch.setattr(duden, "current_matrix_inventory", lambda: {"live_words": {"file_count": 1}})
    monkeypatch.setattr(duden, "bootstrap_resume_staging", lambda: None)

    async def fake_resolve_row(session, row, overrides, throttle=None):
        return (
            duden.Resolution(
                row=row.row,
                word=row.word,
                pos=row.pos,
                gender=row.gender,
                output_filename=duden.filename_for_row(row),
                status="invalid",
                reason="boom",
                match_method="exception",
                duden_page_url=None,
                duden_audio_url=None,
                file_id=None,
            ),
            None,
        )

    def fail_finalize():
        raise AssertionError("finalize should not run with invalid rows")

    monkeypatch.setattr(duden, "resolve_row", fake_resolve_row)
    monkeypatch.setattr(duden, "finalize_staging_to_live", fail_finalize)

    rows = [duden.SourceRow(1, "a", "n.", "", "A1", "x", "")]
    exit_code = asyncio.run(duden.process_rows(rows, mode="resume", confirm_usage=True))
    assert exit_code == duden.EXIT_TECHNICAL_ERROR


def test_resume_refuses_before_cooldown(tmp_path: Path, monkeypatch):
    live_words = tmp_path / "words"
    staging_words = tmp_path / "words_duden_staging"
    live_words.mkdir()
    staging_words.mkdir()
    (live_words / "0001_a.mp3").write_bytes(b"ID3" + b"\x00" * 8)
    (staging_words / "manifest.jsonl").write_text(
        json.dumps(
            {
                "row": 1,
                "word": "a",
                "pos": "n.",
                "gender": "",
                "output_filename": "0001_a.mp3",
                "source": "duden",
                "duden_page_url": None,
                "duden_audio_url": None,
                "file_id": None,
                "match_method": None,
                "status": "pending",
                "reason": "pending",
                "size": None,
                "sha256": None,
                "content_type": None,
                "etag": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cooldown_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    (staging_words / "manifest.meta.json").write_text(
        json.dumps({"cooldown_until": cooldown_until}),
        encoding="utf-8",
    )

    monkeypatch.setattr(duden, "LIVE_WORDS_DIR", live_words)
    monkeypatch.setattr(duden, "STAGING_WORDS_DIR", staging_words)
    monkeypatch.setattr(duden, "LIVE_MANIFEST_PATH", tmp_path / "words_manifest.jsonl")
    monkeypatch.setattr(duden, "LIVE_META_PATH", tmp_path / "words_manifest.meta.json")
    monkeypatch.setattr(duden, "STAGING_MANIFEST_PATH", staging_words / "manifest.jsonl")
    monkeypatch.setattr(duden, "STAGING_META_PATH", staging_words / "manifest.meta.json")
    monkeypatch.setattr(duden, "OVERRIDES_PATH", tmp_path / "duden_overrides.json")
    monkeypatch.setattr(duden, "bootstrap_resume_staging", lambda: None)
    monkeypatch.setattr(duden, "load_overrides", lambda path: {})
    monkeypatch.setattr(duden, "current_matrix_inventory", lambda: {"live_words": {"file_count": 1}})

    async def fail_resolve_row(session, row, overrides, throttle=None):
        raise AssertionError("resume should stop before processing")

    monkeypatch.setattr(duden, "resolve_row", fail_resolve_row)

    rows = [duden.SourceRow(1, "a", "n.", "", "A1", "x", "")]
    exit_code = asyncio.run(duden.process_rows(rows, mode="resume", confirm_usage=True))
    assert exit_code == duden.EXIT_COOLDOWN
