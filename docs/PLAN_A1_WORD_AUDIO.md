# Duden A1 Word Audio Plan

## Summary

Replace the active Goethe A1 word audio set with Duden pronunciation audio.

Keep the 685 source rows, the row order, and the current numbered filenames.
Do not change `Goethe_A1.md`. Do not generate sentence audio. Do not wire this
into the deck pipeline in this phase.

## Output Layout

- Staging audio: `audio/a1/words_duden_staging/`
- Live audio: `audio/a1/words/`
- Live manifest: `audio/a1/words_manifest.jsonl`
- Live metadata: `audio/a1/words_manifest.meta.json`
- Matrix backup: `audio/a1/matrix_backup/`
- Manual overrides: `review/duden_overrides.json`

The staging directory is the working area. When the run is accepted, the
staging directory is promoted to `words/`. Matrix files are kept only in the
backup tree and are never used as fallback.

## Filename Contract

Keep the existing row-numbered filenames, for example:

```text
0004_abfahrt.mp3
0034_an_sein.mp3
0483_pommes_frites.mp3
0553_sie.mp3
```

The filename stem is derived from the exact source word, NFC-normalized, with
ASCII-only transliteration for the path. Do not rewrite the source word to a
different lemma just to make the filename look cleaner.

## Manifest Schema

Each JSONL row must contain:

- `row`
- `word`
- `pos`
- `gender`
- `output_filename`
- `source`
- `duden_page_url`
- `duden_audio_url`
- `file_id`
- `match_method`
- `status`
- `reason`
- `size`
- `sha256`
- `content_type`
- `etag`

Allowed `status` values:

- `ok`
- `unresolved`
- `ambiguous`
- `invalid`

## Resolution Rules

- Match by exact source headword after Unicode NFC normalization.
- Verify page content and canonical URL. A slug is only a candidate.
- Match POS and gender explicitly.
- Do not auto-normalize phrases or stems:
  - no `all-` -> `alle`
  - no `sich kümmern` -> `kümmern`
  - no phrase shortening
- If multiple pages or audio candidates remain after POS and gender checks, mark
  the row `ambiguous` and resolve it through `duden_overrides.json`.
- If the page exists but no exact audio is available, mark it `unresolved`.
- If the page or audio fails validation after retries, mark it `invalid`.

## Tool

Use `tools/download_duden_a1_audio.py` with these modes:

- `preflight`
- `pilot`
- `full`
- `resume`

The tool must:

- rate-limit to one request at a time
- retry with a bounded backoff
- honor `Retry-After`
- write files atomically
- resume by file hash
- keep Matrix out of the live path

Before any download mode runs, the user must confirm the Duden licensing and
robots constraints:

- [Duden Rechte und Lizenzen](https://www.duden.de/form/license-request)
- [Duden robots.txt](https://www.duden.de/robots.txt)

## Pilot Set

Run the first pass on these 12 items:

| Word | Note |
|---|---|
| `Abfahrt` | noun |
| `Straße` | noun |
| `Frühstück` | noun |
| `an sein` | phrase |
| `Pommes frites` | plural phrase |
| `Ausländer` | noun |
| `sie` | lower-case check |
| `Sie` | pronoun check |
| `essen` | verb check |
| `Essen` | noun check |
| `all-` | stem check |
| `sich kümmern` | reflexive phrase |

If a pilot word is missing from the source file, report it explicitly and do
not silently substitute another entry.

## Execution

1. Preflight:
   - verify the source still has 685 rows
   - verify duplicate output filenames do not exist
   - inventory the current Matrix audio, manifest, and metadata
   - write the staging manifest skeleton and metadata atomically

2. Pilot:
   - require explicit usage confirmation
   - resolve and download the 12 pilot items into `words_duden_staging/`
   - keep unresolved and ambiguous rows in the manifest
   - stop for manual review before the full run if the pilot does not match

3. Full:
   - require explicit usage confirmation
   - process all 685 rows
   - write all resolved audio into staging
   - keep unresolved rows out of the live audio set

4. Resume:
   - skip only files that are still valid by hash and size
   - redownload missing, corrupt, or mismatched files
   - keep the same manifest schema and status values

5. Promote:
   - back up the Matrix live state byte-for-byte
   - promote staging to `words/`
   - promote the staging manifest and metadata to the live paths

## Acceptance Checks

- 685 manifest rows
- filenames remain numbered and ASCII-only
- live MP3 count equals the number of `ok` rows
- all live files have `source=duden`
- every live file has a Duden page URL and audio URL
- no Matrix files remain in the live `words/` directory
- unresolved and ambiguous rows are listed in the report
- `Essen/essen`, `Leben/leben`, `Bitte/bitte`, `Sie/sie` stay separate where
  they exist in the source set
- backup contents are byte-identical to the pre-migration Matrix state

## Out Of Scope

- no changes to `Goethe_A1.md`
- no sentence audio
- no deck packaging
- no pipeline integration
- no fallback to Matrix once Duden migration starts
