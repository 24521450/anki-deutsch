# AGENTS.md

German Anki resource repo for Goethe word lists, Duden audio, and Matrix TTS workflows.

## Scope

- Work only inside this repository unless the user explicitly requests cross-repo changes.
- Source Markdown lives in `sources/goethe/`.
- Review overrides live in `review/`.
- Generated audio, manifests, checkpoints, staging directories, and local experiments live under `audio/` or hidden `tools/.*` paths and are ignored unless explicitly requested.

## Commands

- Run tests with `python -m pytest`.
- Run A1 word-audio preflight with `python tools/a1_preflight.py`.
- Inspect Duden tooling with `python tools/download_duden_a1_audio.py --help`.

## AnkiConnect

- Anki desktop may be controlled through AnkiConnect at `http://127.0.0.1:8765`.
- Check availability with the read-only `version` action before each session; do not assume Anki is running.
- Before destructive operations, identify and verify the exact target note or card.
- Do not edit `collection.anki2` directly.

## Goethe Source Rules

- Preserve row order and existing columns unless the task explicitly says otherwise.
- For PDF sentence extraction, use coordinate-aware parsing, keep Unicode text intact, fail closed instead of guessing, and validate row counts before handoff.
- Do not reintroduce the old parent `deutsch/` path prefix; this repository root is the German project root.

## Duplicate Merge Policy

- After confirming that notes are redundant versions of the same lexeme and sense, keep the lowest-level survivor in the order A1 -> A2 -> B1 and merge useful provenance, meanings, examples, and audio into it.
- Delete every redundant non-survivor note and its cards directly, regardless of reps or review history. Do not archive or suspend a duplicate solely to preserve its revlog.
- Still verify the exact note and card IDs and create a scheduled APKG backup before applying a destructive merge.

## Boundaries

- Do not modify the English IELTS pipeline here.
- Do not commit generated audio or transient manifests unless the user explicitly asks for release artifact tracking.
