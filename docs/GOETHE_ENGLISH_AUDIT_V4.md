# Goethe English audit v4

The v4 catalog covers the current canonical A1–B1 corpus with one JSONL row per
note. All rows now carry an evidence-backed final review; live mutation still
requires the compile, snapshot, dry-run, backup, and verification gates below.

## Current status

`review/goethe_english_audit_v4.jsonl` contains 3,493 canonical rows:

| Level | Rows | Review state |
| --- | ---: | --- |
| A1 | 818 | reviewed, migrated from v3 |
| A2 | 707 | reviewed, migrated from v3 |
| B1 | 1,968 | reviewed across `B1-01` through `B1-08` |

The A1/A2 migration collapses all 1,531 v3 source rows into 1,525 current
canonical notes. Six aliases are carried by five survivor rows; their evidence
is unioned and the current canonical German examples remain authoritative.

The strict B1 source-identity audit corrected three source rows that had been
incorrectly collapsed during the earlier import:

- `B1-WG-0066` (`E-Mail`) is distinct from `B1-WG-0015` (`SMS`), but resolves
  to the existing lowest-level A1 `E-Mail` note;
- `B1-WG-0130` is the Swiss `Sekundarstufe I` inventory; and
- `B1-WG-0131` is the Swiss `Sekundarstufe II` inventory.

The latter two are no longer provenance aliases of the broader
`A2-WG-0089` Switzerland row. These repairs increased B1 from 1,966 to 1,968
canonical notes without inventing examples; B1 therefore has 199 canonical
notes with no Goethe example.

For historical context, after the strict `B1-01` checkpoint on 2026-07-20 the
then-current catalog contained 1,775 reviewed rows and the validator reported:

- 1,716 unreviewed and unsupported B1 rows;
- 170 same-level English prompt-collision groups;
- 2 placeholder/invalid B1 glosses;
- 7 pending B1 rows containing known US spellings;
- 197 canonical B1 notes with no Goethe example in the pre-split scope.

`B1-01` itself contains 250 reviewed rows and 320 reviewed examples with 29
`KEEP`, 221 `REVISE`, 108 difficult rows, and no remaining row or prompt
collision blocker. It removed every collision involving a `B1-01` row. At that
checkpoint, eight of the original cross-batch groups contained two or more
pending external rows, so the global collision count fell by 36 rather than 44.

At that checkpoint, those counts were blockers rather than automatically
accepted exceptions. FreeDict similarity and the old B1 override file remain
only under `legacy_hints` with
`classification=hint_only_not_review_evidence`.

## Row contract

Every row has `schema_version=4`, one canonical `source_id`, all provenance in
`source_refs`, and a unique `stable_guid`. The stable GUID is `LegacyGUID` when
available and otherwise `goethe:<SourceID>`; live note IDs are not identities
and are deliberately absent.

English review fields are `decision`, expected/desired meaning and examples,
`review_status`, `difficult`, `reason`, and `evidence`. Each retained example
has an explicit `goethe` or `review-authored` origin. Expected and desired
German example sequences must be identical, which lets the apply path retain
audio by German text.

The pre-unification live collection currently has 34 A1 notes whose historical
merge survivor `SourceID` or `SourceRefs` order differs from the v4 canonical
representation. They are accepted only when the stable GUID, CEFR, reviewed
lemma, and exact unique source-ref set all match. Guarded audit apply then
canonicalises `SourceID` and ordered `SourceRefs`; missing, extra, duplicate, or
cross-wired provenance still fails closed.

## Commands and safety gates

Inspect the canonical audit state:

```powershell
python tools/goethe_english_audit.py inspect
```

Rebuild it deterministically from the ignored completion snapshot and the v3
catalog:

```powershell
python tools/goethe_english_audit.py scaffold
```

Rebuilding replaces manual v4 review work, so only do it deliberately against
the intended `tools/.goethe_completion/manifest.json`. It does not contact
Anki. Once any B1 row is reviewed, `scaffold` refuses to overwrite the catalog
unless `--force-overwrite-reviewed` is supplied explicitly.
`audit_goethe_b1_english.py` may refresh FreeDict triage hints, but those hints
cannot satisfy evidence validation.

The release gate is:

```powershell
python tools/goethe_english_audit.py compile
```

`compile`, `dry-run`, `snapshot`, `pilot`, `apply`, and `verify` all require:

- exactly 3,493 rows with level counts 818/707/1,968;
- every row reviewed with a final `KEEP` or `REVISE` decision;
- specific Cambridge, Duden, or Collins evidence URLs and a support statement;
- two distinct evidence domains for every row marked difficult;
- British learner glosses and complete English for retained examples;
- zero same-level English prompt collisions;
- unchanged German example text and exactly 199 B1 no-example rows.

If any condition stops holding, snapshot and live mutation fail closed.

## B1 review procedure

Review `audit_batch` values `B1-01` through `B1-08`; each contains at most 250
canonical notes. For each row, check the sense and part of speech against the
Goethe context, use Cambridge as the primary bilingual source, use Duden for
German sense/POS, and use Collins as the bilingual fallback. Set `difficult`
explicitly, record two domains when it is true, replace `PENDING` with `KEEP` or
`REVISE`, and only then set `review_status=reviewed`.

Validate an editorial batch without contacting Anki:

```powershell
python tools/goethe_english_audit.py check-batch --batch B1-01
```

The batch gate checks every row and reports internal and cross-batch prompt
collisions separately. A batch checkpoint is not the same as the live `pilot`
command; no live command is available until the complete catalog passes
`compile`.

With all batches and collision review complete, the live sequence is
`dry-run` → `snapshot` → `pilot` → `verify --scope pilot` → full
`apply` → `verify --scope full`. The mutation confirmation is
`APPLY_GOETHE_ENGLISH_AUDIT_V4`. Applied notes lose the v3, unversioned English,
and translation-review tags and gain
`goethe::quality::english_audited::v4::british`.
