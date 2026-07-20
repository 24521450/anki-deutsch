# Deutsch Resources

This repository is a German Anki resource project split out from the IELTS deck repo.

## Scope

- `sources/goethe/` contains source Goethe word lists and reference PDFs.
- `sources/goethe/Goethe_A1_Wortgruppen.md`, `Goethe_A2_Wortgruppen.md`, and
  `Goethe_B1_Wortgruppen.md` contain the thematic inventories omitted from the
  alphabetical word-list files.
- `tools/` contains German audio generation and Duden lookup tools.
- `audio/` is generated output. MP3s, logs, checkpoints, staging directories, and generated manifests are ignored by default.
- `review/duden_overrides.json` and level-specific override files are the hand-reviewed Duden policy files.
- `docs/PLAN_A1_WORD_AUDIO.md` documents the current A1 word-audio plan.
- `tests/` contains German-resource tests. They are outside the default pytest suite because `pyproject.toml` limits default collection to root `tests/`.

## Current Workflows

Word audio:

The Goethe word-audio fallback order is validated Duden, exact Wikimedia
Commons pronunciation, Wiktionary German pronunciation audio, then Edge TTS.
The canonical workflow covers A1, A2, and B1 together. Only the `WordAudio`
field is updated; scheduling and review history are snapshotted and verified
before and after an apply.

```powershell
python tools/goethe_word_audio.py audit
python tools/goethe_word_audio.py prepare --confirm-duden-usage --confirm-commons-license
python tools/goethe_word_audio.py snapshot
python tools/goethe_word_audio.py apply --scope pilot --dry-run
python tools/goethe_word_audio.py apply --scope pilot --confirmation APPLY_GOETHE_WORD_AUDIO
python tools/goethe_word_audio.py verify --scope pilot
python tools/goethe_word_audio.py apply --scope full --dry-run
python tools/goethe_word_audio.py apply --scope full --confirmation APPLY_GOETHE_WORD_AUDIO
python tools/goethe_word_audio.py verify --scope full
```

The level-specific Duden downloaders remain source-audio preparation tools.
They are not separate deck-update pipelines.

Example sentence audio:

```powershell
python tools/goethe_example_audio.py audit
python tools/goethe_example_audio.py prepare --scope pilot
python tools/goethe_example_audio.py prepare --scope full
python tools/goethe_example_audio.py snapshot
python tools/goethe_example_audio.py apply --scope pilot --dry-run
python tools/goethe_example_audio.py apply --scope pilot --confirmation APPLY_GOETHE_EXAMPLE_AUDIO
python tools/goethe_example_audio.py verify --scope pilot
python tools/goethe_example_audio.py apply --scope full --dry-run
python tools/goethe_example_audio.py apply --scope full --confirmation APPLY_GOETHE_EXAMPLE_AUDIO
python tools/goethe_example_audio.py verify --scope full
```

This A1-B1 workflow uses deterministic Edge TTS voices and preserves Anki
scheduling and review history. See `docs/GOETHE_EXAMPLE_AUDIO.md`.

English audit and completion:

```powershell
python tools/goethe_english_audit.py inspect
python tools/goethe_english_audit.py check-batch --batch B1-01
python tools/goethe_english_audit.py compile
python tools/goethe_completion.py build
python tools/goethe_completion.py dry-run
python tools/goethe_completion.py apply --confirmation COMPLETE_GOETHE_A1_A2_B1
python tools/goethe_completion.py verify
python tools/export_goethe_notes_jsonl.py
```

The checked-in v4 audit has one row per canonical A1-B1 note: 3,493 notes and
6,986 cards in total, including 1,968 B1 notes. Of those B1 notes, 199 retain
no Goethe example. The current scope restores the distinct Swiss
`Sekundarstufe I` and `Sekundarstufe II` identities. `B1-WG-0066` (`E-Mail`)
is provenance on the existing A1 note, not a separate B1 card. Live audit,
completion apply, and the final JSONL snapshot
fail closed until every B1 row has evidence-backed review and the collision
checks pass. Completion apply validates exact note/card IDs and creates a
scheduled APKG backup before any duplicate is deleted. See
`docs/GOETHE_ENGLISH_AUDIT_V4.md`.

`tools/goethe_b1_media.py` is a non-mutating compatibility shim that points to
the two all-level audio workflows.

Duden dictionary audio:

```powershell
python tools/download_duden_a1_audio.py --help
python tools/download_duden_a2_audio.py --help
python tools/download_duden_b1_audio.py --help
```

Run German-resource tests explicitly:

```powershell
python -m pytest
```

Validate the structured A1-B1 thematic inventories and their source provenance:

```powershell
python tools/validate_goethe_wortgruppen.py
```

## Notes

The Matrix TTS scripts currently depend on a local `mavis mcp call matrix matrix_synthesize_speech` setup. Treat them as local resource tooling.
