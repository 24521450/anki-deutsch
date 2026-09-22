"""Compatibility entrypoint for the guarded answer-contract refresh.

The old script applied a fixed, incomplete set of 32 corrections and could
mutate legacy content fields.  Keep its filename usable for operators while
routing every command through the current exact-ID, APKG-backed workflow.
"""
from __future__ import annotations

from goethe_answer_contract_refresh import main


if __name__ == "__main__":
    raise SystemExit(main())
