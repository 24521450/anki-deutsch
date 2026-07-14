# Goethe A1+A2 Example Audio

`tools/goethe_example_audio.py` replaces every German example recording in the
live `Goethe Werkstatt` notes with Edge TTS while preserving note IDs, card IDs,
scheduling, review history, model templates, and styling.

## Audio policy

- Voices: `de-DE-KatjaNeural` and `de-DE-ConradNeural`, selected deterministically per spoken text.
- Rate, volume, and pitch: Edge defaults (`+0%`, `+0%`, `+0Hz`).
- Identical spoken text reuses one MP3.
- Displayed German is unchanged. TTS input is NFC-normalised, whitespace is collapsed, a leading dialogue dash is removed, and spaced `/` is converted to a pause.
- Media names are content-addressed as `_goethe_example_edge_<sha256>.mp3`.

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
python tools/goethe_example_audio.py apply --scope pilot --confirmation APPLY_GOETHE_EXAMPLE_AUDIO
python tools/goethe_example_audio.py verify --scope pilot
python tools/goethe_example_audio.py apply --scope full --confirmation APPLY_GOETHE_EXAMPLE_AUDIO
python tools/goethe_example_audio.py verify --scope full
```

`snapshot` exports an APKG with scheduling and records all note fields, tags,
cards, reviews, and model data before mutation. Generated MP3s, the manifest,
snapshot, and APKG live in ignored `audio/` or `tools/.*` paths. Roll back note
audio fields with:

```powershell
python tools/goethe_example_audio.py rollback --confirmation ROLLBACK_GOETHE_EXAMPLE_AUDIO
```

Legacy Google/Yandex media is deliberately left unreferenced rather than
deleted automatically, because other decks may still use those filenames.
