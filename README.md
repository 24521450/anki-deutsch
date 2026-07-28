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
- `review/goethe_word_audio_overrides.json` records protected, user-reviewed word audio.
- `docs/PLAN_A1_WORD_AUDIO.md` documents the current A1 word-audio plan.
- `tests/` contains German-resource tests. They are outside the default pytest suite because `pyproject.toml` limits default collection to root `tests/`.

## Current Workflows

Word audio:

The Goethe word-audio fallback order is validated Duden, exact Wikimedia
Commons pronunciation, Wiktionary German pronunciation audio, then Edge TTS.
The canonical workflow covers A1, A2, and B1 together. Only the `WordAudio`
field is updated; scheduling and review history are snapshotted and verified
before and after an apply.

Protected audio takes precedence over every automatic provider. It is matched
through `SourceID` and `SourceRefs`, locked to the reviewed media checksum, and
survives note merges. An unregistered live-audio difference fails closed rather
than being overwritten. To register an MP3 already selected in Anki:

```powershell
python tools/goethe_word_audio.py protect-current --note-id NOTE_ID --reason "why this recording was selected"
```

Bound headwords use the physical lemma as their spoken identity: boundary
hyphens are removed and repeated alternatives are pronounced once
(`eigen-` → `eigen`, `weg/weg-` → `weg`). Audio reuse requires both the same
lemma identity and spoken text; `SourceRefs` alone cannot restore audio after a
lemma change. `audit` reports semantic mismatches separately from harmless
provider drift. Reviewed homophones, spelling variants, and dictionary-form
audio are registered in `spoken_equivalences`; each entry is locked to its
expected lemma so it cannot silently migrate to another note.

For a reviewed repair, repeat `--note-id` on `prepare`, `apply`, and `verify`.
The targeted manifest refuses to apply without the same explicit note-ID
subset:

```powershell
python tools/goethe_word_audio.py prepare --confirm-duden-usage --confirm-commons-license --note-id NOTE_ID
python tools/goethe_word_audio.py snapshot
python tools/goethe_word_audio.py apply --scope full --dry-run --note-id NOTE_ID
python tools/goethe_word_audio.py apply --scope full --confirmation APPLY_GOETHE_WORD_AUDIO --note-id NOTE_ID
python tools/goethe_word_audio.py verify --scope full --note-id NOTE_ID
```

To prepare and apply only registered protected audio:

```powershell
python tools/goethe_word_audio.py audit
python tools/goethe_word_audio.py prepare --scope protected --confirm-duden-usage --confirm-commons-license --offline
python tools/goethe_word_audio.py snapshot
python tools/goethe_word_audio.py apply --scope protected --dry-run
python tools/goethe_word_audio.py apply --scope protected --confirmation APPLY_GOETHE_WORD_AUDIO
python tools/goethe_word_audio.py verify --scope protected
```

```powershell
python tools/goethe_word_audio.py audit
python tools/goethe_word_audio.py prepare --confirm-duden-usage --confirm-commons-license --refresh-duden-fallbacks
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
python tools/goethe_english_audit.py check-batch --batch V5-01
python tools/goethe_english_audit.py compile
python tools/goethe_completion.py build
python tools/goethe_completion.py dry-run
python tools/goethe_completion.py apply --confirmation COMPLETE_GOETHE_A1_A2_B1
python tools/goethe_completion.py verify
python tools/export_goethe_notes_jsonl.py
```

The checked-in v5 American-English audit has one row per canonical A1-B1
note: 3,425 notes and 6,850 cards in total, including 1,941 B1 notes. Every
note now has at least one example. Live audit, completion apply, and the final
JSONL snapshot fail closed until every row is reviewed, all source examples
are covered, and collision checks pass. Completion apply validates exact
note/card IDs and creates a scheduled APKG backup before any destructive
change. The production collision gate compares `MeaningEN` globally across
A1-B1 and requires distinct, answer-safe `ProductionHint` values whenever
different German answers share the same English cue; CEFR, POS, gender, and
examples do not bypass that gate. The historical v4 workflow remains documented in
`docs/GOETHE_ENGLISH_AUDIT_V4.md`.

Target-highlight refresh:

```powershell
python tools/goethe_target_highlight_refresh.py audit
python tools/goethe_target_highlight_refresh.py backup
python tools/goethe_target_highlight_refresh.py apply --confirmation APPLY_GOETHE_TARGET_HIGHLIGHT_REFRESH
python tools/goethe_target_highlight_refresh.py verify
```

Run these commands in order with Anki Desktop available. The audit must match
the reviewed 40-note/44-example manifest exactly; backup creates a scheduled
APKG and hash-checked local snapshot before apply can write. Apply is limited to
reviewed `ExampleTargetSpansJSON` values and repository model templates. To
restore the snapshotted spans and templates, use
`python tools/goethe_target_highlight_refresh.py rollback --confirmation ROLLBACK_GOETHE_TARGET_HIGHLIGHT_REFRESH`.

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
