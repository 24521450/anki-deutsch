# `Pommes frites` TTS pronunciation incident

Status: resolved and protected

Incident date: 2026-07-30

Scope: three Goethe example-audio fields

Required pronunciation: `Pommes frites` close to `[pɔm fʁɪt]` ("pomm frit");
`Pommes` is one syllable in this phrase.

This document is both the incident record and the mandatory runbook for future
pronunciation exceptions. It exists because transcript QA passed while the
recordings were still audibly wrong.

## Impact

Gemini Live and Edge TTS repeatedly pronounced `Pommes` as two syllables,
approximately "Pom-mes". Some Gemini native-TTS candidates had the same
problem. The displayed sentences and automatic transcripts were correct, so
the existing lexical QA did not detect the pronunciation defect.

Only these exact occurrences were in scope:

| Note ID | Field | Displayed text |
| --- | --- | --- |
| `1584886454853` | `Example1Audio` | `Für Pommes frites braucht man Kartoffeln.` |
| `1584886455008` | `Example1Audio` | `Die Kinder essen Hähnchen mit Pommes frites.` |
| `1584886455008` | `Example2Audio` | `Die Kinder essen Bratwurst mit Pommes frites.` |

Standalone `Pommes` examples were not changed. Duden headword audio was not
changed.

## Approved resolution

The authoritative mapping is
`review/goethe_example_pronunciation_audio.json`. The files below were heard
and approved by the user, then pinned by their exact SHA-256 digest:

| Displayed text | Voice | Generation text | SHA-256 |
| --- | --- | --- | --- |
| `Für Pommes frites braucht man Kartoffeln.` | Charon | `Für Pomm fritt braucht man Kartoffeln.` | `5efa0da14e280ea761d3f5571148dc1c812b7f8cb3d65d5973154b717b79ffbb` |
| `Die Kinder essen Hähnchen mit Pommes frites.` | Charon | unchanged | `9ce246e5c264d21f6d2a96b26db6c75c1fea2d022d4f9f4444294e2e02a31fa0` |
| `Die Kinder essen Bratwurst mit Pommes frites.` | Kore | unchanged | `fe8dd0ce249423d5029417f5275aef7967f7db27bd57b8d17461d36d39ef1595` |

The Kartoffeln recording uses a pronunciation proxy only as synthesis input.
Its displayed text and transcript target remain the original, correctly
spelled German sentence. A proxy is permitted only for an individually
reviewed artifact; it must never leak into note content.

Preparation must fail closed when an approved file is missing, has the wrong
size, or does not match its recorded digest. It must not silently synthesize a
replacement.

## What was tried

1. Gemini Live received this phrase-scoped director note:

   `Aussprachehinweis: „Pommes“ in „Pommes frites“ ist einsilbig. Sprich die Verbindung ungefähr [pɔm ˈfʁɪt], nicht „Pom-mes“ aus.`

   The returned transcript matched the source, but the user still heard two
   syllables. Transcript success was therefore a false assurance about
   pronunciation.

2. Historical Edge TTS recordings were recovered and heard. They also
   pronounced the phrase incorrectly, so reverting to Edge was rejected.

3. Gemini native TTS was tested separately from Gemini Live because it supports
   detailed speaking directions. `gemini-3.1-flash-tts-preview` was more
   controllable than the tested 2.5 Flash preview. The 2.5 Pro preview was not
   usable under the available quota.

4. Prompt variants describing a rhyme with `komm` and describing French
   pronunciation did not reliably fix Kore.

5. A direct transition instruction—silent `es`, moving directly from `/m/` to
   `/f/`—worked for some voice-and-sentence combinations but not for all of
   them. Repeated Charon and Kore generations of the Kartoffeln sentence still
   failed with the original orthography.

6. The pronunciation-only proxy `Pomm fritt` finally produced correct
   Kartoffeln candidates in both voices. The user selected the male Charon
   recording. The other two approved recordings retain the original synthesis
   text.

Candidates that merely passed ASR or an automated audio assessment were not
accepted. Only the exact files heard and approved by the user were applied.

## Root causes

### Pronunciation and transcript QA test different properties

ASR transcript equality proves that the expected words are recognizable and
that lexical content was not materially lost. It does not prove syllable
count, phoneme realization, stress, accent, or naturalness. In this incident,
both a correct and an incorrect pronunciation transcribed as `Pommes frites`.

An automated Gemini audio assessment also sometimes classified a recording as
one syllable when the user heard two. Model-based assessment is useful for
triage only; it is not an acceptance oracle for this exception.

### TTS output depends on voice and sentence context

A direction that worked for one voice or one sentence did not necessarily work
for another. The Kartoffeln context remained resistant across retries, showing
that this was not only a single random bad sample. Approval must therefore be
per exact sentence, voice, and bitstream—not per prompt template.

### Windows PowerShell corrupted Unicode in an experimental path

Raw non-ASCII Python source piped inline through Windows PowerShell converted
characters to `?`, including `Für`, `Hähnchen`, German quotation marks, and IPA
symbols. Those requests were invalid even though the surrounding experiment
appeared to run.

The corruption was confirmed by inspecting code points. All affected
experimental samples were discarded and regenerated from ASCII-only Python
source using `\u` escapes, with the final request text inspected before
synthesis.

### A global cache-version bump would have been too broad

The global generation configuration contributes to all 4,992 request IDs.
Bumping it would invalidate unrelated, already verified audio. The fix instead
uses exact phrase-scoped request identity and reviewed-artifact digests, keeping
the other 4,989 cached requests reusable.

## Permanent safeguards

The following are non-negotiable for pronunciation exceptions:

1. Match the smallest exact text scope. Do not use substring logic that also
   changes standalone words or unrelated sentences.
2. Preserve displayed German and the transcript QA target. If a synthesis
   proxy is unavoidable, record it separately as `generation_text`.
3. Treat ASR as lexical QA only. Never infer pronunciation approval from an
   exact transcript.
4. Treat automated pronunciation judgments as advisory only.
5. Human-listen to every exact sentence-and-voice artifact that will be
   applied.
6. Record the selected voice, engine, model, prompt version, synthesis text,
   file path, size, duration, transcript, review status, and SHA-256 digest.
7. Reuse only the approved bitstream. Missing or mismatched reviewed artifacts
   are hard errors, not regeneration opportunities.
8. Scope cache identity to the exception. Never invalidate the complete corpus
   for a phrase-local change.
9. Never pipe raw Unicode prompt source through Windows PowerShell. Use a
   checked-in UTF-8 file or ASCII source with Unicode escapes. Before a paid or
   large run, assert that expected Unicode code points are present and that no
   unexpected `?` substitution occurred.
10. Keep API keys in process environment variables only. Never put keys in
    source, review metadata, manifests, commands saved in documentation, or
    logs.
11. Before applying, identify exact note IDs and fields, create the scheduled
    APKG/snapshot, and run a dry run whose changed-note count matches the
    expected scope.
12. After applying, retrieve and hash-check Anki media, run full verification,
    and confirm that protected headword/Duden audio did not drift.

## What each check proves

| Check | Proves | Does not prove |
| --- | --- | --- |
| Exact ASR transcript | Recognizable lexical content | Correct phonemes, syllable count, stress, or accent |
| Automated audio classifier | Useful candidate triage | Human acceptance |
| Repeated generation | Availability of alternatives | That a later retry is better |
| Exact file SHA-256 | The reviewed bitstream is unchanged | The original review decision was correct |
| Dry-run note count | Mutation scope is as expected | Audio sounds correct |
| Full post-apply verify | Anki fields/media match the manifest | Subjective pronunciation quality |
| `WordAudio` before/after comparison | Headword audio was preserved | Example-audio quality |

## Mandatory runbook for a future exception

1. Reproduce by listening and write down the required phonetic distinction.
2. Enumerate exact note IDs, fields, displayed sentences, and affected cards.
3. Confirm whether the issue belongs to example audio or protected headword
   audio.
4. Generate candidates without mutating Anki. Validate the exact Unicode
   request payload first.
5. Keep candidates separated by sentence, voice, model, prompt version, and
   synthesis text.
6. Run structural audio QA and transcript QA, but label them as preflight
   checks only.
7. Have the user listen to every candidate proposed for application. Record
   acceptance per exact artifact.
8. Add accepted artifacts to the review override and pin their hashes.
9. Add or update tests for exact scoping, request identity, reviewed-artifact
   reuse, and fail-closed hash behavior.
10. Run:

    ```powershell
    python -m pytest
    python tools/goethe_example_audio.py audit
    python tools/goethe_example_audio.py prepare --scope full
    python tools/goethe_example_audio.py snapshot
    python tools/goethe_example_audio.py apply --scope full --dry-run
    ```

11. Stop if the dry run changes anything outside the reviewed note/field set.
12. Apply only after the expected scope is confirmed, then run full verify and
    a protected `WordAudio` drift comparison.
13. Keep the rollback snapshot until the applied audio has been heard in Anki.

## Verification completed for this incident

- Full test suite: 528 passed.
- Prepared requests: 4,992 selected, 0 pending.
- Dry run: two notes and three example-audio fields changed.
- Full verification: 3,425 notes and 6,850 cards verified.
- Media hashes in Anki matched the three approved artifacts.
- `WordAudio` drift: zero across all 3,425 notes.
- Audit: all 5,080 example occurrences referenced Gemini media.

The pre-apply APKG is stored in the ignored local workflow area documented by
`tools/goethe_example_audio.py`. Generated audio and snapshots remain
untracked; the tracked review override is the durable source of truth.
