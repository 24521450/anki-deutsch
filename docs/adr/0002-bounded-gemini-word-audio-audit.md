# ADR 0002: Bounded, pinned Gemini word-audio audit

Status: accepted

## Context

An exact human recording could remain behind Gemini TTS when a reviewed Duden
page was not attached to every source identity, or when a copula-suppressed
spoken form retained the note's verb POS during provider lookup. Negative
Duden lookups were also retried indefinitely: sequential page timeouts were
swallowed, and semantic ASR had neither a per-file timeout nor a checkpoint.

## Decision

Audit every live A1-B1 Gemini `WordAudio` note in this order: exact Duden,
reviewed Duden page, exact Commons/Wiktionary, then Gemini. A reviewed spoken
form is looked up without the container note's POS/gender, but the resulting
recording must still match the exact spoken form.

Classify results as `wrong_certain`, `needs_review`, or `valid_fallback`.
Only `wrong_certain` is an apply scope. Human replacements require an exact
independent ASR transcript. Ambiguity, metadata conflict, transport failure,
timeout, or transcript mismatch is `needs_review`; it is never treated as
proof that a human source is unavailable.

Provider calls and ASR are bounded. Conclusive exact-identity negative Duden
results survive resolver revisions. Technical errors are checkpointed instead
of swallowed, ASR has a per-file timeout, and each ASR result is persisted.
An interrupted audit resumes from provider and ASR caches without narrowing
the live Gemini baseline to an older report.

Every approved replacement is recorded in
`review/goethe_word_audio_approved.json`, keyed by source identity and pinned
to lemma, spoken form, provider, source URL, audio URL, upstream revision or
ETag, MP3 SHA-256, ASR model, and transcript. A later provider or hash mismatch
fails closed instead of falling back to Gemini.

## Consequences

- Network outages can increase `needs_review`, but cannot silently authorize
  Gemini or a different recording.
- A missing ASR credential does not block the report; candidates remain under
  review. Reviewed local ASR evidence may be pinned explicitly.
- Applying an audit always requires a scheduled APKG backup and changes only
  the report's `wrong_certain` note IDs.
- `absolut`, both `dagegen` identities, `erlaubt sein`, and `verabredet sein`
  are permanent regression fixtures.
