# German Anki Audio

This context describes the reviewed pronunciation audio used by the German Goethe notes.

## Language

**Protected audio override**:
A user-reviewed recording, identified by its exact content, that remains authoritative across all later audio refreshes and follows its note provenance through merges. Conflicting selections, upstream revisions, and unregistered audio differences require review rather than automatic replacement.
_Avoid_: Manual edit, provider pin

**Accepted answer**:
A German response treated as correct when grading a card. It does not imply pronunciation equivalence with the note's lemma or with another accepted answer.

**Spoken form**:
The exact German text represented by a note's pronunciation audio. It is the lemma unless a different form has been explicitly reviewed.
_Avoid_: Accepted answer

**Pronunciation proxy**:
Alternative synthesis-only text used to elicit an approved pronunciation while
the displayed German and transcript target remain correctly spelled. It is
allowed only for a protected, human-reviewed artifact and must never be written
into note content.
_Avoid_: Spoken form, spelling correction

**Pronunciation approval**:
Human acceptance of one exact audio bitstream for one exact text and voice.
Transcript equality and automated audio classification are preflight evidence,
not pronunciation approval.
_Avoid_: Transcript QA

**Removed or merged note**:
A note that no longer exists as an independent deck entry because its useful content was merged into a surviving note or because the entry was excluded from the canonical lexeme inventory.
_Avoid_: Deleted word
