# Goethe A1/A2 redundancy audit

## Outcome

- Live inventory: **1707 notes / 3414 cards**.
- Two card directions per note are intentional and are not duplicates.
- `KEEP`: **1601 notes**.
- `MERGE`: **3 notes**.
- `REMOVE_CANDIDATE`: **24 notes**.
- `REVIEW_HISTORY`: **79 notes**.

## Decision policy

- `KEEP`: distinct learning unit or the canonical survivor.
- `MERGE`: move unique examples/variants/source refs into the named target.
- `REMOVE_CANDIDATE`: unreviewed mechanical number/date/time/unit drill.
- `REVIEW_HISTORY`: content duplicate with review history; do not delete automatically.
- `MANUAL_REVIEW`: possible redundancy without a safe deterministic decision.

## Non-KEEP notes

| Decision | Note | CEFR | Lemma | Reps | Reason | Target |
|---|---:|---|---|---:|---|---:|
| REVIEW_HISTORY | 1497484861911 | A1 | zum Beispiel | 3 | shared_source_refs_with_reviews | 1584886454529 |
| REVIEW_HISTORY | 1584886454454 | A1 | Anschluss | 11 | shared_source_refs_with_reviews | 1584886454453 |
| REVIEW_HISTORY | 1584886454475 | A1 | aufhören | 11 | shared_source_refs_with_reviews | 1584886454474 |
| REVIEW_HISTORY | 1584886454476 | A1 | auf sein | 12 | shared_source_refs_with_reviews | 1584886455087 |
| REVIEW_HISTORY | 1584886454478 | A1 | aufstehen | 12 | shared_source_refs_with_reviews | 1584886454477 |
| REVIEW_HISTORY | 1584886454507 | A1 | Bahn | 9 | shared_source_refs_with_reviews | 1584886454506 |
| REVIEW_HISTORY | 1584886454525 | A1 | beide | 7 | shared_source_refs_with_reviews | 1584886454526 |
| REVIEW_HISTORY | 1584886454544 | A1 | bestellen | 7 | shared_source_refs_with_reviews | 1584886454543 |
| REVIEW_HISTORY | 1584886454555 | A1 | bitte | 7 | shared_source_refs_with_reviews | 1584886454556 |
| REVIEW_HISTORY | 1584886454560 | A1 | bleiben | 10 | shared_source_refs_with_reviews | 1584886454561 |
| REVIEW_HISTORY | 1584886454589 | A1 | da | 9 | shared_source_refs_with_reviews | 1584886454591 |
| REVIEW_HISTORY | 1584886454590 | A1 | da | 6 | shared_source_refs_with_reviews | 1584886454588 |
| REVIEW_HISTORY | 1584886454592 | A1 | Dame | 5 | shared_source_refs_with_reviews | 1584886454593 |
| REVIEW_HISTORY | 1584886454597 | A1 | Dank | 9 | shared_source_refs_with_reviews | 1584886454596 |
| REVIEW_HISTORY | 1584886454625 | A1 | dürfen | 7 | shared_source_refs_with_reviews | 1584886454627 |
| REVIEW_HISTORY | 1584886454626 | A1 | dürfen | 7 | shared_source_refs_with_reviews | 1584886454627 |
| REVIEW_HISTORY | 1584886454638 | A1 | einfach | 7 | shared_source_refs_with_reviews | 1584886454640 |
| REVIEW_HISTORY | 1584886454655 | A1 | Ende | 7 | shared_source_refs_with_reviews | 1584886454656 |
| REVIEW_HISTORY | 1584886454686 | A1 | fertig | 7 | shared_source_refs_with_reviews | 1584886454687 |
| REVIEW_HISTORY | 1584886454718 | A1 | für | 5 | shared_source_refs_with_reviews | 1584886454719 |
| REVIEW_HISTORY | 1584886454720 | A1 | für | 10 | shared_source_refs_with_reviews | 1584886454719 |
| REVIEW_HISTORY | 1584886454734 | A1 | gegen | 6 | shared_source_refs_with_reviews | 1584886454733 |
| REVIEW_HISTORY | 1584886454736 | A1 | gehen | 9 | shared_source_refs_with_reviews | 1584886454738 |
| REVIEW_HISTORY | 1584886454737 | A1 | gehen | 7 | shared_source_refs_with_reviews | 1584886454738 |
| REVIEW_HISTORY | 1584886454760 | A1 | gleich | 14 | shared_source_refs_with_reviews | 1584886454761 |
| REVIEW_HISTORY | 1584886454777 | A1 | Gruß | 7 | shared_source_refs_with_reviews | 1584886454778 |
| REVIEW_HISTORY | 1584886454782 | A1 | gut | 7 | shared_source_refs_with_reviews | 1584886454781 |
| REVIEW_HISTORY | 1584886454783 | A1 | gut | 5 | shared_source_refs_with_reviews | 1584886454781 |
| REVIEW_HISTORY | 1584886454784 | A1 | gut | 7 | shared_source_refs_with_reviews | 1584886454785 |
| REVIEW_HISTORY | 1584886454798 | A1 | Haus | 7 | shared_source_refs_with_reviews | 1584886454797 |
| REVIEW_HISTORY | 1584886454805 | A1 | heißen | 9 | shared_source_refs_with_reviews | 1584886454804 |
| REVIEW_HISTORY | 1584886454813 | A1 | hier | 7 | shared_source_refs_with_reviews | 1584886454812 |
| REVIEW_HISTORY | 1584886454815 | A1 | Hilfe | 6 | shared_source_refs_with_reviews | 1584886454814 |
| REVIEW_HISTORY | 1584886454831 | A1 | in | 10 | shared_source_refs_with_reviews | 1584886454830 |
| REVIEW_HISTORY | 1584886454832 | A1 | in | 10 | shared_source_refs_with_reviews | 1584886454830 |
| REVIEW_HISTORY | 1584886454849 | A1 | Karte | 6 | shared_source_refs_with_reviews | 1584886454850 |
| REVIEW_HISTORY | 1584886454864 | A1 | Klasse | 6 | shared_source_refs_with_reviews | 1584886454865 |
| REVIEW_HISTORY | 1584886454872 | A1 | kommen | 7 | shared_source_refs_with_reviews | 1584886454871 |
| REVIEW_HISTORY | 1584886454874 | A1 | können | 6 | shared_source_refs_with_reviews | 1584886454873 |
| REVIEW_HISTORY | 1584886454898 | A1 | leben | 7 | shared_source_refs_with_reviews | 1584886454899 |
| REVIEW_HISTORY | 1584886454954 | A1 | Moment | 6 | shared_source_refs_with_reviews | 1584886454953 |
| REVIEW_HISTORY | 1584886454961 | A1 | nach | 5 | shared_source_refs_with_reviews | 1584886454960 |
| REVIEW_HISTORY | 1584886454965 | A1 | Name | 7 | shared_source_refs_with_reviews | 1584886454964 |
| REVIEW_HISTORY | 1584886454970 | A1 | neu | 6 | shared_source_refs_with_reviews | 1584886454969 |
| REVIEW_HISTORY | 1584886454976 | A1 | noch | 8 | shared_source_refs_with_reviews | 1584886454978 |
| REVIEW_HISTORY | 1584886454981 | A1 | Nummer | 7 | shared_source_refs_with_reviews | 1587762765058 |
| REVIEW_HISTORY | 1584886454982 | A1 | Nummer | 6 | shared_source_refs_with_reviews | 1587762765058 |
| REVIEW_HISTORY | 1584886455004 | A1 | Platz | 8 | shared_source_refs_with_reviews | 1584886455005 |
| REVIEW_HISTORY | 1584886455054 | A1 | schlecht | 7 | shared_source_refs_with_reviews | 1584886455055 |
| REVIEW_HISTORY | 1584886455070 | A1 | Schule | 7 | shared_source_refs_with_reviews | 1584886455069 |
| REVIEW_HISTORY | 1584886455079 | A1 | sehen | 6 | shared_source_refs_with_reviews | 1584886455078 |
| REVIEW_HISTORY | 1584886455081 | A1 | sehr | 6 | shared_source_refs_with_reviews | 1584886455082 |
| REVIEW_HISTORY | 1584886455085 | A1 | sein | 7 | shared_source_refs_with_reviews | 1584886455084 |
| REVIEW_HISTORY | 1584886455099 | A1 | so | 6 | shared_source_refs_with_reviews | 1584886455097 |
| REVIEW_HISTORY | 1584886455103 | A1 | sollen | 6 | shared_source_refs_with_reviews | 1584886455104 |
| REVIEW_HISTORY | 1584886455110 | A1 | spielen | 6 | shared_source_refs_with_reviews | 1584886455109 |
| REVIEW_HISTORY | 1584886455125 | A1 | Stunde | 6 | shared_source_refs_with_reviews | 1584887177204 |
| REVIEW_HISTORY | 1584886455146 | A1 | (sich) treffen | 6 | shared_source_refs_with_reviews | 1584886455145 |
| REVIEW_HISTORY | 1584886455151 | A1 | tun | 6 | shared_source_refs_with_reviews | 1584886455150 |
| REVIEW_HISTORY | 1584886455192 | A1 | wann | 14 | shared_source_refs_with_reviews | 1584886455193 |
| REVIEW_HISTORY | 1584886455194 | A1 | wann | 14 | shared_source_refs_with_reviews | 1584886455193 |
| REVIEW_HISTORY | 1584886455196 | A1 | warten | 10 | shared_source_refs_with_reviews | 1584886455195 |
| REVIEW_HISTORY | 1584886455198 | A1 | was | 5 | shared_source_refs_with_reviews | 1584886455199 |
| REVIEW_HISTORY | 1584886455202 | A1 | (sich) waschen | 7 | shared_source_refs_with_reviews | 1584886455201 |
| REVIEW_HISTORY | 1584886455220 | A1 | wie | 6 | shared_source_refs_with_reviews | 1584886455221 |
| REVIEW_HISTORY | 1584886455223 | A1 | wie | 8 | shared_source_refs_with_reviews | 1584886455221 |
| REVIEW_HISTORY | 1584886455233 | A1 | wo | 7 | shared_source_refs_with_reviews | 1584886455232 |
| REVIEW_HISTORY | 1584886455234 | A1 | wo | 12 | shared_source_refs_with_reviews | 1584886455232 |
| REVIEW_HISTORY | 1584886455237 | A1 | wohin | 6 | shared_source_refs_with_reviews | 1584886455236 |
| REVIEW_HISTORY | 1584886455249 | A1 | Zimmer | 7 | shared_source_refs_with_reviews | 1584886455248 |
| REVIEW_HISTORY | 1584886455250 | A1 | Zimmer | 9 | shared_source_refs_with_reviews | 1584886455248 |
| REVIEW_HISTORY | 1584886455251 | A1 | Zimmer | 9 | shared_source_refs_with_reviews | 1584886455248 |
| REVIEW_HISTORY | 1584886455260 | A1 | zusammen | 8 | shared_source_refs_with_reviews | 1584886455261 |
| REVIEW_HISTORY | 1584886455262 | A1 | zwischen | 13 | shared_source_refs_with_reviews | 1584886455263 |
| REVIEW_HISTORY | 1584887177226 | A1 | Tag | 5 | shared_source_refs_with_reviews | 1584887177205 |
| REVIEW_HISTORY | 1586187263997 | A1 | (sich) waschen | 6 | shared_source_refs_with_reviews | 1584886455201 |
| MERGE | 1783863833474 | A1 | ein halb | 0 | fraction_variant | 1584887177212 |
| REVIEW_HISTORY | 1783863833505 | A1 | ein Viertel | 1 | fraction_variant_with_reviews | 1584887177213 |
| REMOVE_CANDIDATE | 1783863833535 | A1 | heute ist der 1. März | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863833564 | A1 | Berlin, 12. April 2002 | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863833598 | A1 | 0.03 Uhr | 0 | mechanical_example |  |
| REVIEW_HISTORY | 1783863833628 | A1 | 7.15 Uhr | 1 | mechanical_example_with_reviews |  |
| REMOVE_CANDIDATE | 1783863833662 | A1 | 13.17 Uhr | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863833693 | A1 | 24.00 Uhr | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863833786 | A1 | 1 Euro | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863835883 | A2 | 1 Franke | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863835914 | A2 | 1 m | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863835944 | A2 | 1,50 m | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863835975 | A2 | 1 cm | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836005 | A2 | 2 km | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836037 | A2 | 1% | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836067 | A2 | 1 l | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836098 | A2 | 1 g / 1 kg | 0 | mechanical_example |  |
| REVIEW_HISTORY | 1783863836126 | A2 | 10 Grad Celsius | 1 | mechanical_example_with_reviews |  |
| REMOVE_CANDIDATE | 1783863836160 | A2 | achtzehnhundertachtundvierzig | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836192 | A2 | Heute ist der 20.2.2012 | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836223 | A2 | Berlin, 14.3.2013 | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836410 | A2 | sieben Uhr drei | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836439 | A2 | drei Uhr fünfzehn | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836470 | A2 | fünfzehn Uhr dreißig / halb vier | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836501 | A2 | halb zwölf | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836532 | A2 | vierzehn Uhr fünf / fünf nach zwei | 0 | mechanical_example |  |
| REMOVE_CANDIDATE | 1783863836564 | A2 | vierzehn Uhr fünfundfünfzig / fünf vor drei | 0 | mechanical_example |  |
| MERGE | 1783863836595 | A2 | viertel nach zehn | 0 | clock_quarter_example | 1783863833754 |
| MERGE | 1783863836641 | A2 | viertel vor sieben | 0 | clock_quarter_example | 1783863833754 |
| REMOVE_CANDIDATE | 1783863836921 | A2 | zweitausendeins | 0 | mechanical_example |  |

## Cleanup plan

1. Keep all `REVIEW_HISTORY` notes until a separate history-preserving decision is approved.
2. For each `MERGE`, copy unique examples, variants, audio and `SourceRefs` to the target before retiring the source note.
3. Recheck every `REMOVE_CANDIDATE` is still unreviewed immediately before deletion.
   Their generated word audio belongs only to the retired drill; do not treat it as reusable dictionary audio.
4. Encode approved decisions in the completion pipeline so retired notes are not rebuilt.
5. Export a pre-change `.apkg`, tag and verify exact note/card IDs, apply through AnkiConnect, then export and verify again.

No Anki note, card, scheduling record or source Markdown was changed by this audit.
