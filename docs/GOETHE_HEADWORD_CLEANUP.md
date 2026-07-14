# Goethe headword cleanup

The legacy A1 export split many Goethe entries into one note per English gloss.
The canonical source lists one headword with multiple meanings/examples, so the
cleanup keeps one exact `CEFR + Lemma + POS` note and combines its reviewed
content.

The reviewed policy is [goethe_headword_merges.json](../review/goethe_headword_merges.json).
The workflow is:

```text
python tools/goethe_headword_cleanup.py compile
python tools/goethe_headword_cleanup.py dry-run
python tools/goethe_headword_cleanup.py apply --confirmation MERGE_GOETHE_HEADWORDS
python tools/goethe_headword_cleanup.py verify
```

The completed live deck has 1,530 notes and 3,060 cards. The 66 deleted notes
had review history; their scheduling is preserved only in the pre-cleanup APKG
backup because AnkiConnect cannot merge two notes' review logs into one card.
The survivor is selected by total reps, then `MAIN` source refs, then note ID.

The eight true lexical distinctions remain separate: `bitte/Bitte`,
`essen/Essen`, `leben/Leben`, `sie/Sie`, `mal/Mal`, `morgen/Morgen`,
`Orange/orange`, and `weg/Weg`.
