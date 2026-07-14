# Goethe example source policy

Examples attached to a Goethe note must occur in either the reviewed main
Markdown source for that note's CEFR level or the reviewed English-audit
manifest:

- A1 notes use `sources/goethe/Goethe_A1.md`.
- A2 notes use `sources/goethe/Goethe_A2.md`.
- Corrections in `review/goethe_source_text_overrides.json` are canonical.
- Reviewed learner examples in `review/goethe_english_audit_v3.jsonl` are canonical.
- A sentence found only in the other level is not retained.
- A note may have no example after filtering.

The rule is a level-wide whitelist. It is deliberately independent of a note's `SourceRefs`: repeated source sentences may be useful on more than one note in the same level.

## Enforcement

`tools/goethe_source_examples.py` owns source-sentence normalization. The
cleanup tool extends that whitelist with the audited examples, and completion
applies the English manifest after source filtering. A future rebuild therefore
keeps reviewed audit examples without restoring unreviewed legacy examples.

The cleanup workflow is guarded by a compiled manifest, source hashes, an APKG backup, note fingerprints, exact projected counts, explicit confirmation tokens, and post-apply checks for fields, cards, scheduling, review history, and model templates.

```powershell
python tools/goethe_example_cleanup.py compile
python tools/goethe_example_cleanup.py audit
python tools/goethe_example_cleanup.py snapshot
python tools/goethe_example_cleanup.py apply --scope pilot --dry-run
python tools/goethe_example_cleanup.py apply --scope pilot --confirmation PRUNE_GOETHE_EXAMPLES_TO_LEVEL_SOURCES
python tools/goethe_example_cleanup.py verify --scope pilot
python tools/goethe_example_cleanup.py apply --scope full --dry-run
python tools/goethe_example_cleanup.py apply --scope full --confirmation PRUNE_GOETHE_EXAMPLES_TO_LEVEL_SOURCES
python tools/goethe_example_cleanup.py verify --scope full
```

`rebaseline-model` exists only for a verified concurrent template change. It archives the prior cleanup snapshot and requires `REBASELINE_GOETHE_MODEL_AFTER_EXTERNAL_CHANGE`; it does not relax note, scheduling, or review-history checks.

## Current baseline after English audit v3 (2026-07-14)

- Notes/cards: 1,530 / 3,060
- Cleanup projection: 0 affected notes / 0 removed occurrences
- Retained example occurrences: 2,008 (988 A1, 1,020 A2)
- Notes with no example: 66 mechanical number/time/unit/ordering drills
- Retained unique example audio IDs: 1,918

The operation only removed field references. It did not delete physical MP3 files. `data/build/anki_notes.jsonl` was exported again after verification.
