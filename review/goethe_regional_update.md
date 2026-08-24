# Goethe Regional Cleanup Update

**Date:** 2026-08-24
**Status:** Applied to live Anki collection
**Model:** `Goethe Werkstatt`
**Deck:** `Goethe Institute`

## What Was Done

1. Audited the complete deck for regional-prefix duplicates and malformed answer fields.
2. Added guarded regional cleanup logic with exact note/card ID checks.
3. Added durable merge routes to `review/goethe_redundancy_policy.json`.
4. Created and validated a scheduled APKG backup before mutation.
5. Updated live Anki fields, merged approved duplicates, deleted redundant notes, and reran the full audit.

## Applied Merges

| Lexeme | Survivor | Deleted notes | Result |
|---|---:|---|---|
| `Volkshochschule` | `1784075681483` | `1784075681953`, `1784075682230` | One note; source refs retained |
| `Bundesland` | `1784075685798` | `1784075686077` | One note; source refs retained |
| `Nationalrat` | `1784075686172` | `1784075686737` | One note; source refs retained |

`Volkshochschule` now has:

- `Lemma`: `Volkshochschule`
- `AcceptedAnswersDE`: `Volkshochschule`
- `AcceptedFullAnswersDE`: `die Volkshochschule`
- `SourceRefs`: `B1-WG-0120|B1-WG-0127|B1-WG-0134`
- Two retained cards: `1784075681483`, `1784075681484`

## Headword Audio Repair

The regional canonicalization changed displayed headwords, so the old country-prefixed word audio was replaced instead of being retained.

- Updated notes: `23`
- Unique media assets imported: `22`
- Sources: `9` Duden, `9` Wikimedia Commons, `5` Edge fallback recordings
- `Volkshochschule` now plays audio for `Volkshochschule`, not `Deutschland: die Volkshochschule`.
- Headword audio repair backup: `tools/.goethe_word_audio/Goethe_Institute_pre_headword_audio_20260824T145048920200Z.apkg`
- Headword audio repair backup SHA-256: `478455a0c7f35836b251647d393e238a225a814b606da9b9c3dd5fba7b1cefd1`
- Card scheduling and review history for all 46 retained cards were rechecked unchanged.

## Final Inventory

- Before: `3425 notes / 6850 cards`
- After: `3421 notes / 6842 cards`
- Regional-prefix notes: `0`
- Field anomalies: `0`
- Card anomalies: `0`
- Template anomalies: `0`
- Updated regional notes: `23`
- Deleted redundant notes: `4`

## Validation

- Full test suite: `603 passed`
- Focused cleanup/completion tests: `76 passed`
- `git diff --check`: passed
- Retained card scheduling and review history: verified unchanged

## Backup

- APKG: `tools/.goethe_lexeme_duplicates/Goethe_Institute_pre_lexeme_merge_20260824T135817792809Z.apkg`
- SHA-256: `10f2b5b6fd8a72c3afbd1877a8686db11a7c9d4c0016f3f8d68152e4be893168`

## Issues and Open Items

- The first regional planning pass exposed an unsupported `gemini` audio-source label; the selector was made fail-safe and the plan was rerun successfully.
- The legacy thirteen-merge command has a stale pre-existing merge guard, so regional cleanup uses a separate guarded `apply-regional` path.
- Five non-regional groups remain `REVIEW_REQUIRED`: `Februar`, `Januar`, `klar`, `Pädagogische Hochschule`, and `SMS`. They were intentionally not auto-merged because their meanings or grammatical roles differ.
- A future full corpus rebuild should re-baseline the global completion scope after this four-note reduction; the live Anki update itself is complete.

## Related Files

- `review/goethe_regional_apply.json`
- `review/goethe_lexeme_duplicate_audit.md`
- `tools/goethe_lexeme_duplicates.py`
- `review/goethe_redundancy_policy.json`
