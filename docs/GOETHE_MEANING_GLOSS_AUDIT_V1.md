# Goethe meaning/gloss audit v1

Generated: 2026-08-06T02:32:00.606928+00:00
Data source: AnkiConnect live collection
Scope: canonical Goethe Werkstatt A1-B1 English meaning fields.

## Result

- Notes audited: 3425; cards represented: 6850.
- Proposed revisions: 7 total (3 from the 65 direct-review candidates).
- Direct-review candidates completed: 65; retained as valid: 62; unresolved: 0.
- Dictionary-backed rows retained with explanation: 68.
- Unflagged rows retained: 3350.
- No deck, Anki note, source Markdown, or v5 manifest was modified by this audit.

The second pass checked the 65 candidates against Duden, Collins, and the Goethe/source context. Valid regional, register, count, collocation, and polysemy variants were retained; only core-gloss or context risks became proposals.

## Proposed revisions

| Source | Lemma | Current | Recommendation | Confidence |
| --- | --- | --- | --- | --- |
| `A1-84886454532` | `bekommen` | to get; to receive | to get; to receive | high |
| `A2-0316` | `Fest` | celebration; party | celebration; party | high |
| `A2-0335` | `fleißig` | hard-working | hard-working | high |
| `A2-0758` | `Pullover` | sweater | sweater | high |
| `A2-0935` | `spannend` | exciting | exciting | high |
| `B1-MAIN-0178` | `aufregen` | to upset; to get upset; to annoy | to upset; to get upset; to annoy | high |
| `B1-MAIN-2492` | `U-Bahn` | subway | subway | high |

## Completed direct review

All 65 former REVIEW rows are resolved. 62 retain their current glosses with an explicit reason; 3 have a proposed core-gloss change. The per-note JSONL carries the Duden and Collins links used for each flagged row.

| Outcome | Count |
| --- | ---: |
| Retain current gloss (`KEEP_EXPLAINED`) | 62 |
| Propose core-gloss revision (`PROPOSE_REVISE`) | 3 |
| Unresolved (`REVIEW`) | 0 |

## Controls and issues

- Every report row carries the canonical source identity and the existing v5 evidence list.
- Every flagged row additionally carries corrected Duden and Collins links in `secondary_evidence`; the v5 manifest itself was not rewritten.
- `A2-0935` (`spannend`) is the regression case: the proposed core gloss is `exciting`; `gripping` and `suspenseful` are recorded as contextual notes.
- AnkiConnect live read succeeded; no fallback was used.
- This is a report-only pass. Any future application requires a separate reviewed change and the repository's backup/verification gates.

The complete per-note artifact is `review/goethe_meaning_gloss_audit_v1.jsonl`.
