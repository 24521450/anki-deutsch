# ADR 0001: Copula-suppressed spoken forms for `sein` headwords

## Status

Accepted

## Decision

For reviewed Goethe word notes where `sein` is only a basic copula, the deck
word-audio spoken form may omit it: `fit sein` becomes `fit`, `erkältet sein`
becomes `erkältet`, and `an sein` becomes `an`.

This is a deck-learning convention, not a dictionary rule. Independent
`sein`, lexical or idiomatic phrases, and unreviewed constructions retain their
full spoken form. Every suppression is declared in the word-audio review
registry and never changes example-sentence audio.

When a suppression changes the spoken identity, Duden matching uses the POS and
headword of the spoken component. The original headword POS is not used to
reject a reviewed component lookup. Exact headword, audio, protected-audio,
and fallback safety checks remain mandatory.

## Consequences

- `fit sein` can use Duden's adjective entry for `fit` instead of falling back
  because the source note is classified as a verb phrase.
- Broad regex stripping is prohibited; unknown `sein` phrases remain pending.
- Protected manual audio remains authoritative.
