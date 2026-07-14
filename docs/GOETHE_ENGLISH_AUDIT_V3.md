# Goethe English audit v3

## Audited baseline

- Scope: all 1,530 Goethe Werkstatt A1/A2 notes and all 2,008 retained
  German-English example occurrences.
- Canonical review data: `review/goethe_english_audit_v3.jsonl`.
- Standard: concise British English for A1/A2 learners.
- Cambridge German-English is the primary bilingual source. Duden supplies
  German sense, usage, and part-of-speech evidence; Collins is the bilingual
  fallback when Cambridge has no matching entry.
- Every entry has at least one evidence URL. Ambiguous, multi-sense, phrase,
  function-word, or review-authored-example entries have two source domains.
- The legacy audit and content-cleanup manifests are not inputs to v3.

## Editorial policy

- Keep core A1/A2 senses and senses supported by Goethe examples; omit rare
  specialist meanings.
- Use semicolons between distinct senses and expand dictionary placeholders.
- Keep genuine synonyms distinct with short, accurate prompt qualifiers.
- Retain a review-authored example only after checking its German, English,
  and headword relevance. The catalog records `origin` for every example.
- Dictionary examples are not copied.

`herzlich` is the regression case for the provenance rule: its learner gloss
is `warm; sincere`, while `Herzlichen Glückwunsch!` remains `Congratulations!`.

## Safe live workflow

```powershell
python tools/goethe_english_audit.py compile
python tools/goethe_english_audit.py dry-run
python tools/goethe_english_audit.py snapshot
python tools/goethe_english_audit.py pilot --confirmation APPLY_GOETHE_ENGLISH_AUDIT_V3
python tools/goethe_english_audit.py apply --confirmation APPLY_GOETHE_ENGLISH_AUDIT_V3
python tools/goethe_english_audit.py verify
```

The snapshot includes an APKG with scheduling, the model definition, every
card scheduling tuple, and the complete review-history hash. Apply changes
only English-audit fields and tags; note/card identities and study history
must remain unchanged.
