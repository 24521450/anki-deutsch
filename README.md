# Deutsch Resources

This repository contains the German Goethe A1–B1 Anki resources and their
maintenance tools: source word lists, reviewed corrections, card templates,
audio workflows, and validation scripts. The repository root is the German
project root; the separate English IELTS pipeline is out of scope.

## Contents

- [Project layout](#project-layout)
- [Quick start](#quick-start)
- [Working with live Anki data](#working-with-live-anki-data)
- [Source lists and inventories](#source-lists-and-inventories)
- [Word audio](#word-audio)
- [Example sentence audio](#example-sentence-audio)
- [Content, answers, and card templates](#content-answers-and-card-templates)
- [Other tools](#other-tools)
- [Policies and detailed guides](#policies-and-detailed-guides)

## Project layout

| Path | Purpose |
| --- | --- |
| `sources/goethe/` | Goethe word lists, reference PDFs, and thematic inventories. |
| `review/` | Reviewed overrides, audit decisions, provenance, and policy registries. |
| `data/build/anki_notes.jsonl` | Checked-in, reconciled A1–B1 note snapshot used by audits and tests. |
| `design/GoetheWerkstatt/` | Anki note templates, styling, and browser-side audio and answer helpers. |
| `tools/` | Source validation, Anki maintenance, audio generation, and export tools. |
| `tests/` | German-resource tests. Run them explicitly with `python -m pytest`. |
| `docs/` | Workflow guides, decisions, and dated update notes. |
| `audio/` | Generated audio, staging files, checkpoints, and local manifests; ignored by default. |

The thematic inventory files are `Goethe_A1_Wortgruppen.md`,
`Goethe_A2_Wortgruppen.md`, and `Goethe_B1_Wortgruppen.md` under
`sources/goethe/`. Temporary reports and manifests may also live in ignored
hidden `tools/.*` paths.

The latest recorded corpus refresh reports **3,420 notes and 6,840 cards**:
804 A1 notes, 680 A2 notes, and 1,936 B1 notes. See the
[22-09-2026 update](docs/updates/22-09-2026-goethe.md) for the reviewed
content and card changes behind that snapshot.

## Quick start

Run the project checks from the repository root:

```powershell
python -m pytest
python tools/validate_goethe_wortgruppen.py
python tools/a1_preflight.py
```

The Duden download tools are source-audio preparation tools. Inspect their
options before use:

```powershell
python tools/download_duden_a1_audio.py --help
python tools/download_duden_a2_audio.py --help
python tools/download_duden_b1_audio.py --help
```

Gemini audio workflows use the pinned dependencies in
`requirements-word-audio.txt`. In PowerShell, install them and provide the key
for the current process:

```powershell
python -m pip install -r requirements-word-audio.txt
$env:GEMINI_API_KEY = "your-key"
```

For multiple distinct keys, set `GEMINI_API_KEYS` to a comma-separated list in
the process environment. Never commit API keys or write them into manifests,
logs, or review files.

## Working with live Anki data

Before each live Anki session, confirm that AnkiConnect is available with its
read-only `version` action. Live changes must use the repository tools; never
edit `collection.anki2` directly.

For a mutating workflow, follow its guarded sequence: audit the exact scope,
create the scheduled APKG backup and local snapshot, inspect a dry run, apply
with the workflow's explicit confirmation token, and verify the result. Use the
provided rollback command if verification fails. Check the tool's help or its
guide before running a command against the live collection.

Audio workflows update their designated audio fields and verify that note IDs,
card IDs, scheduling, and review history remain intact. Old media that is no
longer referenced is not deleted automatically because another deck may use
the same filenames.

## Source lists and inventories

Source Markdown lives in `sources/goethe/`. Preserve row order, existing
columns, and Unicode text. When extracting sentences from PDFs, use
coordinate-aware evidence, fail closed rather than guessing, and validate row
counts before handoff.

Validate the structured thematic inventories and their source provenance with:

```powershell
python tools/validate_goethe_wortgruppen.py
```

The checked-in `data/build/anki_notes.jsonl` is a reconciled snapshot, not the
source of truth for edits. Regenerate it only after the reviewed live-note
batch has been reconciled.

## Word audio

The A1–B1 deck workflow chooses audio in this order: protected, user-reviewed
recordings; validated Duden audio; newly resolved exact Duden audio; exact
Wikimedia Commons pronunciation; Wiktionary German pronunciation; then Gemini
TTS. Reviewed direct Duden pages take precedence over sitemap fallback when
their headword and spoken form are pinned. Only the `WordAudio` field is
updated by this workflow.

For a Duden refresh, audit first, prepare the sources, and snapshot the live
deck. Then dry-run, apply with the confirmation token, and verify each scope.
The standard pilot and full sequence is:

```powershell
python tools/goethe_word_audio.py audit
python tools/goethe_word_audio.py prepare --confirm-duden-usage --confirm-commons-license --refresh-duden-fallbacks
python tools/goethe_word_audio.py snapshot
python tools/goethe_word_audio.py apply --scope pilot --dry-run
python tools/goethe_word_audio.py apply --scope pilot --confirmation APPLY_GOETHE_WORD_AUDIO
python tools/goethe_word_audio.py verify --scope pilot
python tools/goethe_word_audio.py apply --scope full --dry-run
python tools/goethe_word_audio.py apply --scope full --confirmation APPLY_GOETHE_WORD_AUDIO
python tools/goethe_word_audio.py verify --scope full
```

For a reviewed single-note repair, repeat the same `--note-id NOTE_ID` on
`prepare`, `apply`, and `verify`; the manifest is restricted to that exact
subset. Use `protect-current` to register an MP3 already selected in Anki:

```powershell
python tools/goethe_word_audio.py protect-current --note-id NOTE_ID --reason "why this recording was selected"
```

To audit all live Gemini fallbacks, prepare the `gemini-audit` scope. Only
`wrong_certain` results can be applied; timeouts, source conflicts, and
transcript mismatches stay in review. An interrupted scan can resume offline
from its checkpoint. See [ADR 0002](docs/adr/0002-bounded-gemini-word-audio-audit.md)
for the fail-closed rules.

```powershell
python tools/goethe_word_audio.py prepare --scope gemini-audit --confirm-duden-usage --confirm-commons-license
python tools/goethe_word_audio.py snapshot
python tools/goethe_word_audio.py apply --scope gemini-audit --dry-run
python tools/goethe_word_audio.py apply --scope gemini-audit --confirmation APPLY_GOETHE_WORD_AUDIO
python tools/goethe_word_audio.py verify --scope gemini-audit
```

Additional scopes include `protected` for registered recordings and `edge`
for fields still pointing to historical Edge media. The exact scope is locked
to the prepared manifest. The level-specific Duden downloaders prepare source
audio; they are not separate deck-update pipelines.

## Example sentence audio

`tools/goethe_example_audio.py` manages example recordings across A1, A2, and
B1. Its transcript QA fails closed, and its live update preserves the Anki
notes, cards, scheduling, and review history. See the [full example-audio
guide](docs/GOETHE_EXAMPLE_AUDIO.md) for provider details and rollback policy.

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

To restore the snapshotted example-audio fields:

```powershell
python tools/goethe_example_audio.py rollback --confirmation ROLLBACK_GOETHE_EXAMPLE_AUDIO
```

## Content, answers, and card templates

The source example grammar uses `<br data-example-boundary>` between
independent examples. A plain `<br>` continues the same example, such as a
dialogue reply. Check the grammar and reviewed PDF-coordinate evidence before
building completion data:

```powershell
python tools/goethe_example_boundaries.py check
```

The English audit and completion workflow is:

```powershell
python tools/goethe_english_audit.py inspect
python tools/goethe_english_audit.py check-batch --batch V5-01
python tools/goethe_english_audit.py compile
python tools/goethe_completion.py build
python tools/goethe_completion.py dry-run
python tools/goethe_completion.py apply --confirmation COMPLETE_GOETHE_A1_A2_B1
python tools/goethe_completion.py verify
python tools/export_goethe_notes_jsonl.py
```

The v5 audit requires every canonical note and source example to be reviewed,
and blocks unresolved answer collisions. Completion apply checks exact note
and card IDs and creates a scheduled APKG backup before destructive changes.
The historical v4 process is documented in
[`GOETHE_ENGLISH_AUDIT_V4.md`](docs/GOETHE_ENGLISH_AUDIT_V4.md).

For the September issue and card-contract refresh, the staged commands are:

```powershell
python tools/goethe_issue_refresh.py content
python tools/goethe_issue_refresh.py merge
python tools/goethe_issue_refresh.py cleanup
python tools/goethe_issue_refresh.py variants
python tools/goethe_issue_refresh.py variant-markup
python tools/build_goethe_dictionary_links.py
python tools/reconcile_goethe_build.py
```

Content edits, duplicate merges, and pronunciation variants are separate
guarded operations. The dedicated English-content, answer-contract, and
template refresh tools (`goethe_english_content_refresh.py`,
`goethe_answer_contract_refresh.py`, and `goethe_template_refresh.py`) also use
audit → backup → apply → verify → rollback.
The dictionary-link builder writes the reviewed direct-entry map and does not
modify the live deck.

To refresh reviewed example target highlights:

```powershell
python tools/goethe_target_highlight_refresh.py audit
python tools/goethe_target_highlight_refresh.py backup
python tools/goethe_target_highlight_refresh.py apply --confirmation APPLY_GOETHE_TARGET_HIGHLIGHT_REFRESH
python tools/goethe_target_highlight_refresh.py verify
```

Run these commands with Anki Desktop available. The audit must match the
reviewed 208-note/286-example v3 manifest; apply is limited to reviewed
`ExampleTargetSpansJSON` values and repository model templates. Roll back with:

```powershell
python tools/goethe_target_highlight_refresh.py rollback --confirmation ROLLBACK_GOETHE_TARGET_HIGHLIGHT_REFRESH
```

## Other tools

- Audit legacy example-audio repetition outliers with
  `python tools/goethe_example_audio.py audit-repetitions --percentile 95`.
- `tools/goethe_b1_media.py` is a non-mutating compatibility shim for the
  all-level word- and example-audio workflows.
- Matrix TTS scripts require a local
  `mavis mcp call matrix matrix_synthesize_speech` setup and are local resource
  tools.

## Policies and detailed guides

- [Example-audio workflow and QA](docs/GOETHE_EXAMPLE_AUDIO.md)
- [Bounded Gemini word-audio audit](docs/adr/0002-bounded-gemini-word-audio-audit.md)
- [Copula-suppressed `sein` audio decision](docs/adr/0001-copula-suppressed-sein-audio.md)
- [A1 Duden word-audio plan](docs/PLAN_A1_WORD_AUDIO.md)
- [Goethe Werkstatt migration](docs/GOETHE_WERKSTATT_MIGRATION.md)
- [Duplicate audit](docs/GOETHE_REDUNDANCY_AUDIT.md) and [cleanup procedure](docs/GOETHE_REDUNDANCY_CLEANUP.md)
- [Meaning and gloss audit](docs/GOETHE_MEANING_GLOSS_AUDIT_V1.md)
- Dated change notes live in `docs/updates/`. New notes use the `DD-MM-YYYY`
  date prefix.

When two notes are confirmed to represent the same lexeme and sense, keep the
lowest-level survivor in the order A1 → A2 → B1 and merge useful provenance,
meanings, examples, and audio into it. Delete every redundant note and its
cards regardless of review history; do not archive or suspend a duplicate just
to preserve its revlog. Create a scheduled APKG backup before the merge.
