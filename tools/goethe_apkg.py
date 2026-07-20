"""Read-only validation helpers for scheduled Anki package snapshots."""
from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path
from zipfile import BadZipFile, ZipFile


COLLECTION_MEMBERS = (
    "collection.anki21b",
    "collection.anki21",
    "collection.anki2",
)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_collection(path: Path) -> tuple[str, bytes]:
    """Return the newest collection payload as uncompressed SQLite bytes."""
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise BadZipFile("APKG CRC validation failed")
        names = set(archive.namelist())
        member = next((name for name in COLLECTION_MEMBERS if name in names), None)
        if member is None:
            raise BadZipFile("APKG has no Anki collection")
        payload = archive.read(member)

    if member == "collection.anki21b":
        try:
            import zstandard

            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)) as reader:
                payload = reader.read()
        except Exception as exc:  # Optional codec boundary; normalise to validation failure.
            raise BadZipFile("cannot decompress collection.anki21b") from exc
    if not payload.startswith(b"SQLite format 3\x00"):
        raise BadZipFile("APKG collection is not SQLite")
    return member, payload


def valid_apkg(path: Path) -> bool:
    """Require a readable, CRC-clean package containing an Anki collection."""
    try:
        read_collection(path)
        return True
    except (OSError, BadZipFile):
        return False


def wait_for_valid_apkg(
    path: Path, *, timeout: float = 60.0, poll_interval: float = 0.25,
) -> bool:
    """Wait for AnkiConnect to finish flushing an exported package."""
    deadline = time.monotonic() + timeout
    while True:
        if valid_apkg(path):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_interval, remaining))
