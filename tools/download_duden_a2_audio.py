from __future__ import annotations

import sys
import json
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import download_duden_a1_audio as common


ROOT = common.ROOT
AUDIO_ROOT = ROOT / "audio" / "a2"

PILOT_WORDS = [
    "abgeben",
    "abschließen",
    "Achtung",
    "Frühstück",
    "auf jeden/keinen Fall",
    "Bekannte",
    "essen",
    "Essen",
    "Bank",
    "See",
    "Schüler",
    "Schülerin",
    "zurück-",
]


def configure_a2() -> None:
    common.SOURCE_PATH = ROOT / "sources" / "goethe" / "Goethe_A2.md"
    common.AUDIO_ROOT = AUDIO_ROOT
    common.LIVE_WORDS_DIR = AUDIO_ROOT / "words"
    common.STAGING_WORDS_DIR = AUDIO_ROOT / "words_duden_staging"
    common.LIVE_MANIFEST_PATH = AUDIO_ROOT / "words_manifest.jsonl"
    common.LIVE_META_PATH = AUDIO_ROOT / "words_manifest.meta.json"
    common.OVERRIDES_PATH = ROOT / "review" / "duden_a2_overrides.json"
    common.BACKUP_ROOT = AUDIO_ROOT / "pre_migration_backup"
    common.DUDEN_CHECKPOINT_ROOT = AUDIO_ROOT / "duden_checkpoints"
    common.MISSING_AUDIT_PATH = AUDIO_ROOT / "duden_missing_audit.jsonl"
    common.STAGING_MANIFEST_PATH = common.STAGING_WORDS_DIR / "manifest.jsonl"
    common.STAGING_META_PATH = common.STAGING_WORDS_DIR / "manifest.meta.json"
    common.EXPECTED_ROWS = 1147
    common.PILOT_WORDS = list(PILOT_WORDS)
    common.REUSE_LIVE_WORDS_DIR = ROOT / "audio" / "a1" / "words"
    common.REUSE_LIVE_MANIFEST_PATH = ROOT / "audio" / "a1" / "words_manifest.jsonl"
    common.REUSE_LIVE_SOURCES = ()
    common._REUSE_INDEX_CACHE = None
    common.PREFER_FIRST_EXACT_CANDIDATE = True


def source_rows_from_live_manifest() -> list[common.SourceRow]:
    rows: list[common.SourceRow] = []
    for line in common.LIVE_MANIFEST_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows.append(
            common.SourceRow(
                row=int(item["row"]),
                word=common.normalize_text(str(item.get("word") or "")),
                pos=common.normalize_text(str(item.get("pos") or "")),
                gender=common.normalize_text(str(item.get("gender") or "")),
                cefr="A2",
                sentence="",
                note="",
            )
        )
    rows.sort(key=lambda row: row.row)
    if len(rows) != common.EXPECTED_ROWS:
        raise RuntimeError(f"expected {common.EXPECTED_ROWS} live manifest rows, got {len(rows)}")
    if len({common.filename_for_row(row) for row in rows}) != len(rows):
        raise RuntimeError("duplicate output filename detected in live manifest")
    return rows


def main(argv: list[str] | None = None) -> int:
    configure_a2()
    args = common.parse_args(argv or sys.argv[1:])
    if args.mode == "audit-missing":
        return asyncio.run(common.process_audit_missing(source_rows_from_live_manifest(), confirm_usage=args.confirm_usage))
    if args.mode == "fill-missing":
        return asyncio.run(common.process_fill_missing(source_rows_from_live_manifest(), confirm_usage=args.confirm_usage))
    return common.main(argv or sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
