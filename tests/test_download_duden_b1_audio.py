from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


TEST_FILE = Path(__file__).resolve()
PROJECT_ROOT = TEST_FILE.parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import download_duden_b1_audio as b1  # noqa: E402


@pytest.fixture(autouse=True)
def restore_common_config():
    common = b1.common
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


def _write_manifest_row(path: Path, *, word: str, filename: str, sha256: str, size: int, file_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "row": 1,
                "word": word,
                "pos": "n.",
                "gender": "f.",
                "output_filename": filename,
                "source": "duden",
                "duden_page_url": f"https://www.duden.de/rechtschreibung/{word}",
                "duden_audio_url": f"https://cdn.duden.de/_media_/audio/{file_id}.mp3",
                "file_id": file_id,
                "match_method": "exact-page",
                "status": "ok",
                "reason": "matched",
                "size": size,
                "sha256": sha256,
                "content_type": "audio/mpeg",
                "etag": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_b1_config_points_to_source_audio_overrides_and_reuse_order():
    b1.configure_b1()
    common = b1.common
    assert common.SOURCE_PATH == PROJECT_ROOT / "sources" / "goethe" / "Goethe_B1.md"
    assert common.AUDIO_ROOT == PROJECT_ROOT / "audio" / "b1"
    assert common.OVERRIDES_PATH == PROJECT_ROOT / "review" / "duden_b1_overrides.json"
    assert common.EXPECTED_ROWS == 2969
    assert common.PREFER_FIRST_EXACT_CANDIDATE
    assert common.REUSE_LIVE_SOURCES == (
        (PROJECT_ROOT / "audio" / "a2" / "words", PROJECT_ROOT / "audio" / "a2" / "words_manifest.jsonl"),
        (PROJECT_ROOT / "audio" / "a1" / "words", PROJECT_ROOT / "audio" / "a1" / "words_manifest.jsonl"),
    )

    rows = common.parse_markdown_wordlist(common.SOURCE_PATH)
    assert len(rows) == 2969


def test_b1_reuse_prefers_a2_before_a1_with_b1_filename(tmp_path: Path, monkeypatch):
    b1.configure_b1()
    common = b1.common
    a1_words = tmp_path / "a1_words"
    a2_words = tmp_path / "a2_words"
    staging = tmp_path / "b1_staging"
    a1_words.mkdir()
    a2_words.mkdir()
    staging.mkdir()

    a1_audio = a1_words / "0007_bank.mp3"
    a2_audio = a2_words / "0033_bank.mp3"
    a1_audio.write_bytes(b"ID3" + b"A1" * 16)
    a2_audio.write_bytes(b"ID3" + b"A2" * 16)
    a1_manifest = tmp_path / "a1_manifest.jsonl"
    a2_manifest = tmp_path / "a2_manifest.jsonl"
    _write_manifest_row(
        a1_manifest,
        word="Bank",
        filename=a1_audio.name,
        sha256=hashlib.sha256(a1_audio.read_bytes()).hexdigest(),
        size=a1_audio.stat().st_size,
        file_id="A1",
    )
    _write_manifest_row(
        a2_manifest,
        word="Bank",
        filename=a2_audio.name,
        sha256=hashlib.sha256(a2_audio.read_bytes()).hexdigest(),
        size=a2_audio.stat().st_size,
        file_id="A2",
    )

    monkeypatch.setattr(common, "REUSE_LIVE_SOURCES", ((a2_words, a2_manifest), (a1_words, a1_manifest)))
    monkeypatch.setattr(common, "REUSE_LIVE_WORDS_DIR", None)
    monkeypatch.setattr(common, "REUSE_LIVE_MANIFEST_PATH", None)
    monkeypatch.setattr(common, "_REUSE_INDEX_CACHE", None)
    monkeypatch.setattr(common, "STAGING_WORDS_DIR", staging)

    row = common.SourceRow(42, "Bank", "n.", "f.", "B1", "x", "")
    resolution = common.reuse_existing_duden_audio(row)

    assert resolution is not None
    assert resolution.output_filename == "0042_bank.mp3"
    assert resolution.file_id == "A2"
    assert resolution.match_method == "reuse-duden-manifest"
    assert (staging / "0042_bank.mp3").read_bytes() == a2_audio.read_bytes()
