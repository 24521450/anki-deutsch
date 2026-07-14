# Goethe example source policy

Examples attached to a Goethe note must occur in the reviewed main Markdown source for that note's CEFR level:

- A1 notes use `sources/goethe/Goethe_A1.md`.
- A2 notes use `sources/goethe/Goethe_A2.md`.
- Corrections in `review/goethe_source_text_overrides.json` are canonical.
- A sentence found only in the other level is not retained.
- A note may have no example after filtering.

The rule is a level-wide whitelist. It is deliberately independent of a note's `SourceRefs`: repeated source sentences may be useful on more than one note in the same level.

## Enforcement

`tools/goethe_source_examples.py` owns sentence normalization and whitelist construction. Both `tools/goethe_example_cleanup.py` and `tools/goethe_completion.py` use it, so a future completion rebuild cannot restore out-of-source examples.

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

## Applied baseline (2026-07-14)

- Notes/cards: 1,596 / 3,192
- Affected notes: 487 (443 A1, 44 A2)
- Removed example occurrences: 766
- Retained example occurrences: 1,923 (984 A1, 939 A2)
- Notes with no example: 218
- Retained unique example audio IDs: 1,780

The operation only removed field references. It did not delete physical MP3 files. `data/build/anki_notes.jsonl` was exported again after verification.
