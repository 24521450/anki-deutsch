# Goethe A1-B1 Example Audio

`tools/goethe_example_audio.py` replaces every German example recording in the
live `Goethe Werkstatt` notes with Gemini TTS while preserving note IDs, card
IDs, scheduling, review history, model templates, and styling.

The all-level baseline is 3,425 notes, 6,850 cards, 5,080 example occurrences,
and 4,992 content-addressed text-and-voice requests. Prepared manifests record
the exact level set and per-level counts; manifests from retired or
incompatible workflows are rejected.

Install the pinned dependencies and provide the API key only through the
process environment:

```powershell
python -m pip install -r requirements-word-audio.txt
$env:GEMINI_API_KEY = "your-key"
```

For a faster single-writer run, provide distinct keys as a comma-separated
process-only value in `GEMINI_API_KEYS`. The generator round-robins clients,
keeps Live sessions isolated per key, and runs two turns per configured key.

Do not commit the key or write it into manifests, logs, or review files.
This corpus requires 4,992 resumable Live turns. Turns are serialized within
each voice session, while Kore and Charon sessions may run concurrently.
Expired or retiring sessions are reconnected automatically.

## Audio policy

- Live model: `gemini-3.1-flash-live-preview`.
- Voices: `Kore` and `Charon`.
- A SHA-256 hash of the note ID chooses the first voice; subsequent examples
  on that note alternate by zero-based occurrence index. A note with two or
  more examples therefore uses both voices, and the assignment is stable
  across runs.
- The content address includes the normalised spoken text, selected voice, and
  complete generation configuration. Identical text and voice reuse one MP3;
  the same text assigned to different voices produces separate files.
- Displayed German is unchanged. TTS input is NFC-normalised, whitespace is collapsed, a leading dialogue dash is removed, and spaced `/` is converted to a pause.
- Example requests containing the exact phrase `Pommes frites` receive a
  versioned pronunciation hint without changing displayed text, spoken text,
  or the transcript QA target. The override version is included only in those
  request IDs, so unrelated cached audio remains reusable.
- Three human-reviewed `Pommes frites` recordings are pinned by SHA-256 in
  `review/goethe_example_pronunciation_audio.json`. Preparation reuses only
  those exact approved native-TTS artifacts; the Kartoffeln occurrence records
  its reviewed pronunciation proxy while keeping the displayed German and ASR
  transcript target unchanged.
- The incident, failed approaches, Unicode corruption trap, and mandatory
  review/apply checklist are documented in
  [`GOETHE_POMMES_FRITES_TTS_POSTMORTEM.md`](GOETHE_POMMES_FRITES_TTS_POSTMORTEM.md).
  Its human-listening and fail-closed artifact rules are part of this audio
  policy, not optional historical notes.
- Gemini returns raw signed 16-bit little-endian PCM at 24 kHz mono. It is
  encoded locally with pinned `lameenc` settings as 128 kbps MP3.
- Media names are content-addressed as
  `_goethe_example_gemini_<sha256>.mp3`.

Every generated MP3 must pass fail-closed audio QA. The workflow rejects
malformed, silent, or implausibly sized audio and enables Live
`output_audio_transcription` for every turn. It accepts only a transcript that
matches the requested German text after conservative case, whitespace, and
punctuation normalization. When Live returns a truncated transcript for
otherwise complete PCM, `gemini-3.6-flash` performs independent WAV
transcription and must pass the same exact comparison. It retries synthesis up
to ten times and leaves
the final target absent if QA never passes. A rejected Live turn retires its
session and uses a short increasing cooldown before regeneration. Cached entries are reused only when
their checksum, size, duration, voice, non-empty transcript metadata, and
passing QA status still match.

Transcript equality is lexical QA only. It cannot approve syllable count,
phonemes, stress, accent, or naturalness. A pronunciation exception requires
human approval of every exact artifact that will be applied.

The four regular example slots use `Example1Audio` through `Example4Audio`.
Later examples carry the same player HTML inside `MoreExamplesHTML`; the shared
`goethe_examples` codec keeps this audio intact during export, completion, and
content-cleanup round trips.

On the card back, native example-audio controls are hidden. Clicking or focusing
the German sentence and pressing Enter/Space replays that sentence from the
beginning; selecting another sentence stops the previous example audio.

## Safe workflow

```powershell
python tools/goethe_example_audio.py audit
python tools/goethe_example_audio.py prepare --scope pilot
python tools/goethe_example_audio.py prepare --scope full
python tools/goethe_example_audio.py snapshot
python tools/goethe_example_audio.py apply --scope pilot --dry-run
python tools/goethe_example_audio.py apply --scope pilot --confirmation APPLY_GOETHE_EXAMPLE_AUDIO
python tools/goethe_example_audio.py verify --scope pilot
python tools/goethe_example_audio.py apply --scope full --dry-run
python tools/goethe_example_audio.py apply --scope full --confirmation APPLY_GOETHE_EXAMPLE_AUDIO
python tools/goethe_example_audio.py verify --scope full
```

`snapshot` exports an APKG with scheduling and records all note fields, tags,
cards, reviews, and model data before mutation. Generated MP3s, the manifest,
snapshot, and APKG live in ignored `audio/` or `tools/.*` paths. Every newly
stored media file is retrieved from Anki and hash-checked before note fields are
updated. Roll back note audio fields with:

```powershell
python tools/goethe_example_audio.py rollback --confirmation ROLLBACK_GOETHE_EXAMPLE_AUDIO
```

Legacy Google/Yandex media is deliberately left unreferenced rather than
deleted automatically, because other decks may still use those filenames.
Historical `_goethe_example_edge_...` files are handled the same way: after
migration they may remain unreferenced, and this workflow does not delete them.

`tools/goethe_b1_media.py` is a non-mutating deprecation shim. It exits with an
error and points to this all-level workflow instead of operating on B1 alone.
