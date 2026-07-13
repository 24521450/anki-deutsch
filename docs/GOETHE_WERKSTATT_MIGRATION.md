# Goethe Werkstatt A1/A2 migration

This runbook records the completed 20-note pilot and full A1/A2 rollout. The local
`Goethe Werkstatt Migration Bridge` adds a guarded `changeNoteTypeSafe` action
to AnkiConnect, so the GUI mapping below is now a fallback/reference only.

## Full rollout completed

- Scope: Goethe A1 + A2; B1 excluded.
- Converted after pilot: 915 A1 notes and 646 A2 notes.
- Final state: 1,581 `Goethe Werkstatt` notes / 3,162 unchanged cards.
- Templates: 1,581 German → English and 1,581 English → German cards.
- Pre-full backup: `tools/.goethe_werkstatt/Goethe_Institute_pre_full_20260712T075149Z.apkg`
- Backup SHA-256: `eda5c84e75bc8c20bad3c0b055dc06dc3da6e692f8ce10587bd2a5f5288f0678`
- Fresh review-history SHA-256: `24917a97f3717a778b60adbae46078027418a7a07abb2c79037fb1fed0071be7`
- Tags: 925 A1, 656 A2, 1,581 migrated, 224 review-needed, 20 pilot.

## Source completion completed

- Sources: A1/A2 alphabetical lists plus both Wortgruppen inventories.
- Minimum-level policy: a lexeme/sense present at A1 and A2 is assigned to A1.
- Final state after history restoration: 1,707 notes / 3,414 cards (974 A1, 733 A2).
- Source coverage: 2,201/2,201 rows and all source German example sentences.
- Added: 126 notes / 252 cards.
- Duplicate policy corrected: all 76 duplicate notes / 152 cards are retained to preserve live review history.
- Moved to lower-level deck: 28 notes; survivor card IDs and scheduling were retained.
- `MoreExamplesHTML`: 65 notes with more than four examples.
- New translations: cached in `review/goethe_completion_translations.json`; affected notes carry `goethe::quality::translation_review_needed`.
- Pre-completion backup: `tools/.goethe_completion/Goethe_Institute_pre_completion_20260712T100814Z.apkg`
- Backup SHA-256: `0af27187a4ae5f7b3dd8b3cc8978e6499381f1bc6f3bbf716c197ee37788c511`
- Deletion audit: `tools/.goethe_completion/deletion_audit.json`.
- Emergency backup before history restoration: `tools/.goethe_completion/Goethe_Institute_emergency_before_history_restore_20260712T134139Z.apkg`.
- All 3,162 pre-completion card IDs and the full pre-completion review-history hash were restored; 252 new cards were then added.

Commands used after the accepted pilot:

```powershell
python tools/goethe_werkstatt_migrate.py snapshot
python tools/goethe_werkstatt_migrate.py change-type --scope full --dry-run
python tools/goethe_werkstatt_migrate.py change-type --scope full
python tools/goethe_werkstatt_migrate.py populate --scope full
python tools/goethe_werkstatt_migrate.py verify --scope full
python -m pytest
```

## Checkpoint already created

- Legacy export: `tools/.goethe_werkstatt/legacy-inputs/Goethe Institute.txt`
- Backup: `tools/.goethe_werkstatt/legacy-inputs/Goethe Institute.apkg`
- Backup SHA-256: `54b786c84bc5ed0d8205fc263eb4432ea4728678ae106952343bf7b8f1489fc3`
- Snapshot: `tools/.goethe_werkstatt/snapshot.json`
- Snapshot: 1,581 notes / 3,162 cards
- Review-history SHA-256: `57f8aa36564610dbaa444ec775be2f4a486f9078cb3fc52f53114487b9fb4dcd`

## Automated pilot commands

```powershell
python tools/goethe_werkstatt_migrate.py change-type --scope pilot --dry-run
python tools/goethe_werkstatt_migrate.py change-type --scope pilot
python tools/goethe_werkstatt_migrate.py populate --scope pilot
python tools/goethe_werkstatt_migrate.py verify --scope pilot
```

The bridge requires a dry-run, explicit confirmation token inside the CLI,
one source model per batch, complete target-card mapping, and unchanged card
IDs. The 20-note pilot completed these commands successfully.

## Manual fallback: A1 Change Note Type

In Browse, switch to Notes mode and search:

```text
nid:1584886454452,1584886454486,1584886454531,1584886455241,1584886454930,1584886454804,1584886454929,1584886454529,1584887177209,1584887177204
```

Confirm exactly 10 notes, select all, then choose **Notes → Change Note Type**.
Target: `Goethe Werkstatt`.

Field mapping:

| New field | Old A1 field |
|---|---|
| `Lemma` | `de_word` |
| `MeaningEN` | `en_word` |
| `UsageNoteEN` | `en_note` |
| `Example1DE` | `de_sentence` |
| `Example1EN` | `en_sentence` |
| `Example1Audio` | `de_audio` |
| `SourceID` | `Note ID` |
| all other fields | `Nothing` |

Card mapping:

| New card | Old card |
|---|---|
| `German → English` | `Card 1` |
| `English → German` | `Card 2` |

No card may map to `Nothing`.

## Manual fallback: A2 Change Note Type

Search:

```text
nid:1497484860721,1497484861228,1497484860918,1497484861704,1497484860720,1497484860730,1497484861168,1497484861331,1497484860783,1497484861655
```

Confirm exactly 10 notes and change them to `Goethe Werkstatt`.

Field mapping:

| New field | Old A2 field |
|---|---|
| `Lemma` | `Wort_DE` |
| `MeaningEN` | `Wort_EN` |
| `Article` | `Artikel` |
| `NounFormsRaw` | `Plural` |
| `FormOrVariantNote` | `Hinweis` |
| `VerbFormsRaw` | `Verbformen` |
| `Example1DE` … `Example4DE` | `Satz1_DE` … `Satz4_DE` |
| `Example1EN` … `Example4EN` | `Satz1_EN` … `Satz4_EN` |
| `Example1Audio` … `Example4Audio` | `Audio_S1` … `Audio_S4` |
| `WordAudio` | `Audio_Wort` |
| `OriginalOrder` | `Original_Order` |
| all other fields | `Nothing` |

Use the same card mapping as A1.

## Populate and verify after manual fallback

Immediately after both GUI conversions:

```powershell
python tools/goethe_werkstatt_migrate.py populate --scope pilot
python tools/goethe_werkstatt_migrate.py verify --scope pilot
python -m pytest
```

`verify` must report 1,581 notes, 3,162 unchanged card IDs/schedules, and 20
target notes. Any failure stops the migration.

## Reviewer acceptance checks

Use the real Reviewer, not Card Preview:

- Empty + Enter → Incorrect, no retry.
- `bahnhof`, `BAHNHOF`, and optional correct article pass where applicable.
- Wrong article fails.
- `für=fuer`, `grüßen=gruessen`, `Straße=strasse`; missing `e` fails.
- `leidtun` and `leid tun` both pass.
- Full phrase is required; terminal `.?!` is ignored; internal hyphen remains strict.
- English → German front has no German/audio; word audio starts only after reveal.
- Example audio is manual and examples 3–4 are under **Show more examples**.
- Wrong answers show the submitted and expected answers without an extra rating hint.

The accepted pilot checklist was used as the gate for the completed full rollout.
