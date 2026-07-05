from __future__ import annotations

import hashlib
import asyncio
import json
import sys
from pathlib import Path

import pytest


TEST_FILE = Path(__file__).resolve()
PROJECT_ROOT = TEST_FILE.parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import download_duden_a2_audio as a2  # noqa: E402


@pytest.fixture(autouse=True)
def restore_common_config():
    common = a2.common
    names = [
        "SOURCE_PATH",
        "AUDIO_ROOT",
        "LIVE_WORDS_DIR",
        "STAGING_WORDS_DIR",
        "LIVE_MANIFEST_PATH",
        "LIVE_META_PATH",
        "OVERRIDES_PATH",
        "BACKUP_ROOT",
        "DUDEN_CHECKPOINT_ROOT",
        "MISSING_AUDIT_PATH",
        "STAGING_MANIFEST_PATH",
        "STAGING_META_PATH",
        "EXPECTED_ROWS",
        "PILOT_WORDS",
        "REUSE_LIVE_WORDS_DIR",
        "REUSE_LIVE_MANIFEST_PATH",
        "REUSE_LIVE_SOURCES",
        "_REUSE_INDEX_CACHE",
        "PREFER_FIRST_EXACT_CANDIDATE",
    ]
    snapshot = {name: getattr(common, name) for name in names}
    yield
    for name, value in snapshot.items():
        setattr(common, name, value)


def test_a2_config_points_to_separate_source_audio_and_overrides():
    a2.configure_a2()
    common = a2.common
    assert common.SOURCE_PATH == PROJECT_ROOT / "sources" / "goethe" / "Goethe_A2.md"
    assert common.AUDIO_ROOT == PROJECT_ROOT / "audio" / "a2"
    assert common.OVERRIDES_PATH == PROJECT_ROOT / "review" / "duden_a2_overrides.json"
    assert common.EXPECTED_ROWS == 1147
    assert common.PREFER_FIRST_EXACT_CANDIDATE

    rows = [
        common.SourceRow(index, word, "v.", "", "A2", "", "")
        for index, word in enumerate(common.PILOT_WORDS, start=1)
    ]
    pilot_words = {row.word for row in common.rows_for_pilot(rows)}
    assert set(common.PILOT_WORDS) <= pilot_words
    assert common.gender_matches("m/f.", "der oder die")


def test_a2_prefers_first_audio_candidate_when_duden_has_multiple(monkeypatch):
    a2.configure_a2()
    common = a2.common
    html = """
    <html>
      <head><link rel="canonical" href="https://www.duden.de/rechtschreibung/aktiv" /></head>
      <body>
        <h1>aktiv</h1>
        <dl><dt class="tuple__key">Wortart:</dt><dd class="tuple__val">Adjektiv</dd></dl>
        <button class="pronunciation-guide__sound" data-href="https://cdn.duden.de/_media_/audio/ID1.mp3" data-file-id="ID1"></button>
        <button class="pronunciation-guide__sound" data-href="https://cdn.duden.de/_media_/audio/ID2.mp3" data-file-id="ID2"></button>
      </body>
    </html>
    """

    async def fake_fetch_page(session, url, throttle=None):
        return 200, html, {}

    monkeypatch.setattr(common, "fetch_page", fake_fetch_page)
    row = common.SourceRow(9, "aktiv", "adj.", "", "A2", "x", "")
    resolution, _ = asyncio.run(common.resolve_row(None, row, {}))

    assert resolution.status == "ok"
    assert resolution.match_method == "exact-headword-first-candidate"
    assert resolution.file_id == "ID1"


def test_a2_reuse_copies_exact_a1_duden_match_with_a2_filename(tmp_path: Path, monkeypatch):
    a2.configure_a2()
    common = a2.common
    source_words = tmp_path / "a1_words"
    staging = tmp_path / "a2_staging"
    source_words.mkdir()
    staging.mkdir()
    audio = source_words / "0005_abgeben.mp3"
    audio.write_bytes(b"ID3" + b"\x00" * 32)
    sha256 = hashlib.sha256(audio.read_bytes()).hexdigest()
    manifest = tmp_path / "a1_manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "row": 5,
                "word": "abgeben",
                "pos": "v.",
                "gender": "",
                "output_filename": audio.name,
                "source": "duden",
                "duden_page_url": "https://www.duden.de/rechtschreibung/abgeben",
                "duden_audio_url": "https://cdn.duden.de/_media_/audio/ID1.mp3",
                "file_id": "ID1",
                "match_method": "exact-page",
                "status": "ok",
                "reason": "matched",
                "size": audio.stat().st_size,
                "sha256": sha256,
                "content_type": "audio/mpeg",
                "etag": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(common, "REUSE_LIVE_WORDS_DIR", source_words)
    monkeypatch.setattr(common, "REUSE_LIVE_MANIFEST_PATH", manifest)
    monkeypatch.setattr(common, "_REUSE_INDEX_CACHE", None)
    monkeypatch.setattr(common, "STAGING_WORDS_DIR", staging)

    row = common.SourceRow(3, "abgeben", "v.", "", "A2", "x", "")
    resolution = common.reuse_existing_duden_audio(row)

    assert resolution is not None
    assert resolution.output_filename == "0003_abgeben.mp3"
    assert resolution.match_method == "reuse-duden-manifest"
    assert (staging / "0003_abgeben.mp3").read_bytes() == audio.read_bytes()
