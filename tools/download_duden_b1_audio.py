from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import download_duden_a1_audio as common


ROOT = common.ROOT
AUDIO_ROOT = ROOT / "audio" / "b1"

PILOT_WORDS = [
    "ab",
    "Abenteuer",
    "aber",
    "Abitur",
    "abhängig",
    "Bank",
    "Schüler",
    "Schülerin",
    "Vertrag",
    "vereinbaren",
    "Wetter",
    "Wohnung",
    "Zeitung",
    "Zug",
]


def configure_b1() -> None:
    common.SOURCE_PATH = ROOT / "sources" / "goethe" / "Goethe_B1.md"
    common.AUDIO_ROOT = AUDIO_ROOT
    common.LIVE_WORDS_DIR = AUDIO_ROOT / "words"
    common.STAGING_WORDS_DIR = AUDIO_ROOT / "words_duden_staging"
    common.LIVE_MANIFEST_PATH = AUDIO_ROOT / "words_manifest.jsonl"
    common.LIVE_META_PATH = AUDIO_ROOT / "words_manifest.meta.json"
    common.OVERRIDES_PATH = ROOT / "review" / "duden_b1_overrides.json"
    common.BACKUP_ROOT = AUDIO_ROOT / "pre_migration_backup"
    common.DUDEN_CHECKPOINT_ROOT = AUDIO_ROOT / "duden_checkpoints"
    common.MISSING_AUDIT_PATH = AUDIO_ROOT / "duden_missing_audit.jsonl"
    common.STAGING_MANIFEST_PATH = common.STAGING_WORDS_DIR / "manifest.jsonl"
    common.STAGING_META_PATH = common.STAGING_WORDS_DIR / "manifest.meta.json"
    common.EXPECTED_ROWS = 2969
    common.PILOT_WORDS = list(PILOT_WORDS)
    common.REUSE_LIVE_WORDS_DIR = None
    common.REUSE_LIVE_MANIFEST_PATH = None
    common.REUSE_LIVE_SOURCES = (
        (ROOT / "audio" / "a2" / "words", ROOT / "audio" / "a2" / "words_manifest.jsonl"),
        (ROOT / "audio" / "a1" / "words", ROOT / "audio" / "a1" / "words_manifest.jsonl"),
    )
    common._REUSE_INDEX_CACHE = None
    common.PREFER_FIRST_EXACT_CANDIDATE = True


def main(argv: list[str] | None = None) -> int:
    configure_b1()
    return common.main(argv or sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
