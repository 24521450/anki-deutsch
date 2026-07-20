"""Fail-fast compatibility shim for the retired B1-only media workflow."""
from __future__ import annotations

import sys


WORD_WORKFLOW = (
    "word: python tools/goethe_word_audio.py audit; "
    "python tools/goethe_word_audio.py prepare --confirm-duden-usage --confirm-commons-license; "
    "python tools/goethe_word_audio.py snapshot; "
    "python tools/goethe_word_audio.py apply --scope pilot --dry-run; "
    "python tools/goethe_word_audio.py apply --scope pilot --confirmation APPLY_GOETHE_WORD_AUDIO; "
    "python tools/goethe_word_audio.py verify --scope pilot; "
    "python tools/goethe_word_audio.py apply --scope full --dry-run; "
    "python tools/goethe_word_audio.py apply --scope full --confirmation APPLY_GOETHE_WORD_AUDIO; "
    "python tools/goethe_word_audio.py verify --scope full"
)
EXAMPLE_WORKFLOW = (
    "example: python tools/goethe_example_audio.py audit; "
    "python tools/goethe_example_audio.py prepare --scope pilot; "
    "python tools/goethe_example_audio.py prepare --scope full; "
    "python tools/goethe_example_audio.py snapshot; "
    "python tools/goethe_example_audio.py apply --scope pilot --dry-run; "
    "python tools/goethe_example_audio.py apply --scope pilot --confirmation APPLY_GOETHE_EXAMPLE_AUDIO; "
    "python tools/goethe_example_audio.py verify --scope pilot; "
    "python tools/goethe_example_audio.py apply --scope full --dry-run; "
    "python tools/goethe_example_audio.py apply --scope full --confirmation APPLY_GOETHE_EXAMPLE_AUDIO; "
    "python tools/goethe_example_audio.py verify --scope full"
)


def main(argv: list[str] | None = None) -> int:
    del argv
    print(WORD_WORKFLOW, file=sys.stderr)
    print(EXAMPLE_WORKFLOW, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
