# Deutsch Resources

This repository is a German Anki resource project split out from the IELTS deck repo.

## Scope

- `sources/goethe/` contains source Goethe word lists and reference PDFs.
- `sources/goethe/Goethe_A1_Wortgruppen.md`, `Goethe_A2_Wortgruppen.md`, and
  `Goethe_B1_Wortgruppen.md` contain the thematic inventories omitted from the
  alphabetical word-list files.
- `tools/` contains German audio generation and Duden lookup tools.
- `audio/` is generated output. MP3s, logs, checkpoints, staging directories, and generated manifests are ignored by default.
- `review/duden_overrides.json` and level-specific override files are the hand-reviewed Duden policy files.
- `docs/PLAN_A1_WORD_AUDIO.md` documents the current A1 word-audio plan.
- `tests/` contains German-resource tests. They are outside the default pytest suite because `pyproject.toml` limits default collection to root `tests/`.

## Current Workflows

Word audio:

```powershell
python tools/a1_preflight.py
python tools/a1_generate.py --pilot-only
python tools/a1_generate.py
```

Example sentence audio:

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

This A1+A2 workflow uses deterministic Edge TTS voices and preserves Anki
scheduling and review history. See `docs/GOETHE_EXAMPLE_AUDIO.md`.

Duden dictionary audio:

```powershell
python tools/download_duden_a1_audio.py --help
python tools/download_duden_a2_audio.py --help
python tools/download_duden_b1_audio.py --help
```

Run German-resource tests explicitly:

```powershell
python -m pytest
```

Validate the structured A1-B1 thematic inventories and their source provenance:

```powershell
python tools/validate_goethe_wortgruppen.py
```

## Notes

The Matrix TTS scripts currently depend on a local `mavis mcp call matrix matrix_synthesize_speech` setup. Treat them as local resource tooling.
