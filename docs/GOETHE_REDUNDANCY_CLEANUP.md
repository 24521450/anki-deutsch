# Goethe redundancy cleanup

## Process

1. Revalidated all 1,707 live notes and their exact 3,414 card IDs against the audit.
2. Exported an original scheduling-preserving backup.
3. Tagged all 106 target notes with `delete`, merged unique content into canonical notes, and exported the tagged pre-delete state.
4. Deleted only the audited note IDs through AnkiConnect.
5. Verified the remaining inventory, card IDs, scheduling tuples, merge targets, and empty `tag:delete` result before exporting the post-cleanup state.

## Result

- Deleted **106 notes / 212 cards**.
- Remaining inventory: **1601 notes / 3202 cards**.
- Merged content into **67 canonical notes** before deletion.
- Every surviving card ID and scheduling tuple is unchanged.
- The three standalone phrase merges were `ein halb`, `viertel nach zehn`, and `viertel vor sieben`.
- The completion rebuild now produces **1,601 records, 0 new notes, 0 deletions**, with 27 reviewed mechanical source rows intentionally excluded from standalone-card generation.

## Backups and audit

- Original backup: `C:\Users\admin\Downloads\anki-deutsch\tools\.goethe_redundancy\Goethe_Institute_before_redundancy_cleanup_20260713T005924Z.apkg`
- Tagged pre-delete backup: `C:\Users\admin\Downloads\anki-deutsch\tools\.goethe_redundancy\Goethe_Institute_tagged_pre_delete_20260713T005924Z.apkg`
- Post-cleanup backup: `C:\Users\admin\Downloads\anki-deutsch\tools\.goethe_redundancy\Goethe_Institute_after_redundancy_cleanup_20260713T005924Z.apkg`
- Exact deletion audit: `C:\Users\admin\Downloads\anki-deutsch\tools\.goethe_redundancy\deletion_audit_20260713T005924Z.json`

SHA-256:

- Original backup: `56E9DA11BC748551EE6BB3FE74C1042EB7B67199E9DE4E66B60A3F624AD1022F`
- Tagged backup: `F873A2555FD199A6360BBB1BF17DDE6EB6564396CD900C3DE58AA3379B60F7E8`
- Post-cleanup backup: `B102C532F96EE6E3CACADE246BEB2207EB31DE0304AE1A1B7FDBE8AC4BE17B1F`
- Deletion audit: `F63333022FB5D43E8C1371504FCF6C1132FA978BD266009AD7F8F1633BCFC6F2`

## Deviations and issues

- The initial audit assumption that every Wortgruppe-only note was unreviewed was stale. Three redundant notes had reviews and were still deleted under the user-approved policy, after their preservation requirements were handled.
- Exact text/POS identity found only 72 historical duplicate notes. Grouping by identical `SourceRefs` correctly recovered all 76.
- The first rebuild check proposed recreating `A2-WG-0114` (`1 Euro`). It shared the deleted A1 card but was not in the initial skip list; the policy was corrected and the final rebuild is clean.
- `goethe_completion.py dry-run` initially failed because `build` leaves translations to its normal second stage. All 115 requested translations were already cached; `translate` made no cache changes and the final dry-run passed.

## Open items

- Generated audio belonging exclusively to deleted mechanical drills remains ordinary orphan-media cleanup work; it was not deleted during this note/card cleanup.
- B1 was outside this historical audit and cleanup. The artifact is retained
  for provenance only; the active A1-A2-B1 completion pipeline applies the
  lowest-level survivor policy and its guarded B1 release gate.

All destructive operations were performed through AnkiConnect; `collection.anki2` was not edited.
