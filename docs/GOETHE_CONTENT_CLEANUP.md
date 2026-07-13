# Goethe A1/A2 content cleanup

## Result

- Standardised the live deck to reviewed British English.
- Applied 117 gloss decisions, 237 reviewed example-pair decisions, 16 usage-note decisions, and removed 235 leading form-note separators.
- Preserved `MoreExamplesHTML` as the overflow store for examples after the fourth pair.
- Corrected display-layer German text without altering the source Markdown/PDF transcription.
- Deleted 5 confirmed bad duplicate notes / 10 cards after merging their unique content.
- Final inventory: **1,596 notes / 3,192 cards** (884 A1, 712 A2).
- Every survivor retains its card IDs, scheduling tuple, and review history.
- Every survivor is tagged `goethe::quality::english_verified::british`; no `translation_review_needed` tag remains.
- Word audio is complete using Duden → Wikimedia Commons → Edge TTS; no sentence audio was generated.

## Deleted notes

| Deleted note | Survivor | Old meaning |
|---:|---:|---|
| 1584886454471 | 1584886454470 | `auf = until` |
| 1584886454757 | 1584886454756 | `Glas = glasses` |
| 1584886454972 | 1584886454971 | `nicht = no` |
| 1584886455083 | 1584886455084 | `sein = his` |
| 1584886455254 | 1584886455253 | `zu = by` |

The deletion removed the 10 associated cards and 48 review entries from the live collection, as explicitly approved. Both scheduling-preserving backups contain them.

## Recovery artifacts

- Original backup: `tools/.goethe_content_cleanup/Goethe_Institute_before_content_cleanup_20260713T031949Z.apkg`
  - SHA-256: `21d39f352bc2ce5b4ae8ad1dfe4e28d0e4ba2d4be94eb0f011aeb2725858fe48`
- Tagged pre-delete backup: `tools/.goethe_content_cleanup/Goethe_Institute_tagged_pre_delete_20260713T032138Z.apkg`
  - SHA-256: `68896a6fbd0f7d515ff0bbbb7bb352a9aa8f3816fb129a519b24739f87378634`
- Exact local deletion audit: `tools/.goethe_content_cleanup/deletion_audit.json`

## Rebuild invariants

- `goethe_completion.py build` produces 1,596 records, 0 new notes, 0 deletions, and 0 untranslated values.
- `goethe_completion.py dry-run` preserves 884 A1 and 712 A2 notes.
- The tracked correction manifest records the audited baseline and exact desired fields.
- Source-text overrides repair PDF line wrapping and display-only source typos without changing source Markdown.
