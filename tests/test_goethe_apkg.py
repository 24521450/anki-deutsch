from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import goethe_apkg as apkg  # noqa: E402


def test_valid_apkg_requires_collection_member_and_clean_zip(tmp_path: Path):
    missing = tmp_path / "missing.apkg"
    with ZipFile(missing, "w") as archive:
        archive.writestr("media", b"{}")
    assert not apkg.valid_apkg(missing)

    valid = tmp_path / "valid.apkg"
    with ZipFile(valid, "w") as archive:
        archive.writestr("collection.anki21", b"SQLite format 3\x00placeholder")
    assert apkg.valid_apkg(valid)

    corrupt = tmp_path / "corrupt.apkg"
    corrupt.write_bytes(valid.read_bytes()[:-4])
    assert not apkg.valid_apkg(corrupt)


def test_read_collection_prefers_real_modern_payload(tmp_path: Path):
    path = tmp_path / "modern.apkg"
    with ZipFile(path, "w") as archive:
        archive.writestr("collection.anki2", b"SQLite format 3\x00legacy")
        archive.writestr("collection.anki21", b"SQLite format 3\x00modern")

    member, payload = apkg.read_collection(path)

    assert member == "collection.anki21"
    assert payload.endswith(b"modern")


def test_read_collection_decompresses_anki21b(tmp_path: Path):
    zstandard = pytest.importorskip("zstandard")
    raw = b"SQLite format 3\x00modern-zstd"
    path = tmp_path / "modern-zstd.apkg"
    with ZipFile(path, "w") as archive:
        archive.writestr("collection.anki2", b"SQLite format 3\x00legacy")
        archive.writestr(
            "collection.anki21b", zstandard.ZstdCompressor().compress(raw),
        )

    member, payload = apkg.read_collection(path)

    assert member == "collection.anki21b"
    assert payload == raw
    assert apkg.valid_apkg(path)
