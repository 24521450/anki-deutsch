# Goethe lexeme duplicate audit

> Read-only audit. No note, card, scheduling, tag, deck, or review history was modified.

Generated: `2026-08-24T14:01:47.214108+00:00`

Scope: **3421 notes / 6842 cards** — A1: 804, A2: 680, B1: 1937

## Summary

| Decision | Groups |
|---|---:|
| `MERGE_PROPOSED` | 1 |
| `REVIEW_REQUIRED` | 5 |
| `KEEP_SEPARATE_HOMOGRAPH` | 43 |

All proposed actions are pending explicit user approval.

## Integrity checks

- Regional-prefix notes: **0**
- Regional duplicate groups: **0** (0 proposed merges, 0 reviews)
- Field anomalies: **0**
- Card anomalies: **0**
- Template anomalies: **0**

## lexeme-94de8f9523 — Feier- / Feier

**Decision:** `MERGE_PROPOSED` · **Approval:** `PENDING_APPROVAL`

Same POS and English sense; only spacing, hyphenation, or transliteration differs.

Evidence status: `RULE_REVIEWED` — Same POS and English sense; only spacing, hyphenation, or transliteration differs.

Signals: `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `SURVIVE` | `1497484861018` | `1497484862534, 1497484862535` | A1 | n. | Feier- | celebration | 16 / 16 | die / f. |
| `DELETE_AFTER_APPROVAL` | `1785175711653` | `1785175711653, 1785175711654` | A2 | n. | Feier | celebration | 0 / 0 | die / f. |

### Proposed merged payload

- Survivor note: `1497484861018`
- Levels/provenance retained: `A1, A2` / `A1-MAIN-0217|A2-0301|A2-MAIN-0313|B1-MAIN-0812`
- English meanings retained for reconciliation: `celebration`
- Accepted German answers: `Feier- | Feier`
- Unique word-audio assets: `1`; unique examples: `4`
- History: Keep the survivor cards and delete every redundant non-survivor, including its old review history.

### Content to review

- `1497484861018` provenance: `A1-MAIN-0217|A2-0301`; word audio: `duden`.
  - z. B. Feierabend, Feiertag — e.g. finishing time; public holiday
  - Am Montag ist Feiertag. — Monday is a holiday.
- `1785175711653` provenance: `A2-MAIN-0313|B1-MAIN-0812`; word audio: `duden`.
  - Wann hast du Feierabend? — When do you finish work?
  - Am ersten Mai ist ein Feiertag. — The first of May is a public holiday.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Feier-) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Feier-)

## lexeme-6d0efdb302 — Februar

**Decision:** `REVIEW_REQUIRED` · **Approval:** `PENDING_APPROVAL`

Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Evidence status: `LOOKUP_REQUIRED` — Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Signals: `accepted_answer_overlap`, `same_surface_casefold`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584887177234` | `1584887177421, 1584887177422` | A1 | n. | Februar | February | 10 / 10 | der / m. |
| `KEEP_CURRENT` | `1784075693862` | `1784075693863, 1784075693864` | B1 | n. | Februar | February; Feber (Austrian variant) | 0 / 0 | der / m. |

### Content to review

- `1584887177234` provenance: `A1-84887177234|A1-WG-0077|A2-WG-0136`; word audio: `duden`.
  - Mein Geburtstag ist im Februar. — My birthday is in February.
- `1784075693862` provenance: `B1-WG-0310`; word audio: `gemini`.
  - In Österreich heißt der Februar auch Feber. — In Austria, February is also called Feber.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Februar) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Februar)

## lexeme-d892bc841d — Januar

**Decision:** `REVIEW_REQUIRED` · **Approval:** `PENDING_APPROVAL`

Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Evidence status: `LOOKUP_REQUIRED` — Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Signals: `accepted_answer_overlap`, `same_surface_casefold`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584887177233` | `1584887177419, 1584887177420` | A1 | n. | Januar | January | 10 / 10 | der / m. |
| `KEEP_CURRENT` | `1784075693769` | `1784075693769, 1784075693770` | B1 | n. | Januar | January; Jänner (Austrian variant) | 0 / 0 | der / m. |

### Content to review

- `1584887177233` provenance: `A1-84887177233|A1-WG-0076|A2-WG-0135`; word audio: `duden`.
  - Mein Geburtstag ist im Januar. — My birthday is in January.
- `1784075693769` provenance: `B1-WG-0309`; word audio: `gemini`.
  - In Österreich heißt der Januar auch Jänner. — In Austria, January is also called Jänner.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Januar) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Januar)

## lexeme-7b81890f82 — klar

**Decision:** `REVIEW_REQUIRED` · **Approval:** `PENDING_APPROVAL`

Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Evidence status: `LOOKUP_REQUIRED` — Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Signals: `accepted_answer_overlap`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454863` | `1584886456086, 1584886456087` | A1 | interj. | klar | of course; naturally | 13 / 13 |  |
| `KEEP_CURRENT` | `1784075570805` | `1784075570805, 1784075570806` | A2 | adj., interj. | klar | sure; of course; clear | 2 / 2 |  |

### Content to review

- `1584886454863` provenance: `A1-84886454863|A1-MAIN-0362`; word audio: `duden`.
  - Kommst du mit?<br>– Klar! — Are you coming with me?<br>– Sure!
- `1784075570805` provenance: `A2-MAIN-0529|B1-MAIN-1332`; word audio: `duden`.
  - Kommst du mit? – Klar! — Are you coming along? – Of course!
  - Ich komme morgen zu deiner Party, das ist doch klar. — I'll come to your party tomorrow, that's for sure.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/klar) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/klar)

## lexeme-ae1685bd2d — Pädagogische Hochschule

**Decision:** `REVIEW_REQUIRED` · **Approval:** `PENDING_APPROVAL`

Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Evidence status: `LOOKUP_REQUIRED` — Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Signals: `accepted_answer_overlap`, `same_surface_casefold`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075681858` | `1784075681858, 1784075681859` | B1 | n. | Pädagogische Hochschule | university college of teacher education | 0 / 0 | die / f. |
| `KEEP_CURRENT` | `1784075682137` | `1784075682137, 1784075682138` | B1 | n. | Pädagogische Hochschule | university of teacher education | 0 / 0 | die / f. |

### Content to review

- `1784075681858` provenance: `B1-WG-0126`; word audio: `gemini`.
  - Österreich: Die Pädagogische Hochschule bildet Lehrkräfte aus. — Austria: The university college of teacher education trains teachers.
- `1784075682137` provenance: `B1-WG-0133`; word audio: `gemini`.
  - Schweiz: Die Pädagogische Hochschule bildet Lehrpersonen aus. — Switzerland: The university of teacher education trains teachers.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/P%C3%A4dagogische%20Hochschule) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/P%C3%A4dagogische%20Hochschule)

## lexeme-22b8d23434 — SMS

**Decision:** `REVIEW_REQUIRED` · **Approval:** `PENDING_APPROVAL`

Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Evidence status: `LOOKUP_REQUIRED` — Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal.

Signals: `accepted_answer_overlap`, `same_surface_casefold`, `shared_german_example`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1783863834101` | `1783863834101, 1783863834102` | A2 | n. | SMS | SMS | 5 / 5 | die / f. |
| `KEEP_CURRENT` | `1784075675699` | `1784075675699, 1784075675700` | B1 | n. | SMS | SMS; text message | 0 / 0 | die/das / f./n. |

### Content to review

- `1783863834101` provenance: `A2-WG-0006`; word audio: `duden`.
  - Ich schicke dir später eine SMS. — I'll send you a text message later.
- `1784075675699` provenance: `B1-WG-0015`; word audio: `gemini`.
  - Ich schicke dir später eine SMS. — I'll send you a text message later.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/SMS) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/SMS)

## lexeme-c3237c7396 — arm / Arm

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484860770` | `1497484862038, 1497484862039` | A2 | adj. | arm | poor | 0 / 0 |  |
| `KEEP_CURRENT` | `1584886454467` | `1584886455294, 1584886455295` | A1 | n. | Arm | arm | 10 / 10 | der / m. |

### Content to review

- `1497484860770` provenance: `A2-0053|A2-MAIN-0048|B1-MAIN-0152`; word audio: `duden`.
  - Sie haben nicht viel Geld, sie sind arm. — They do not have much money, they are poor.
- `1584886454467` provenance: `A1-84886454467|A1-MAIN-0046|A2-MAIN-0049|B1-MAIN-0151`; word audio: `duden`.
  - Mein Arm tut weh. — My arm hurts.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Arm) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Arm)

## lexeme-dca75e3f36 — bar / Bar

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454515` | `1584886455390, 1584886455391` | A1 | adj. | bar | cash | 13 / 13 |  |
| `KEEP_CURRENT` | `1784075509214` | `1784075509214, 1784075509215` | B1 | n. | Bar | bar (drinks venue or counter) | 0 / 0 | die / f. |

### Content to review

- `1584886454515` provenance: `A1-84886454515|A1-MAIN-0086|A2-MAIN-0109|B1-MAIN-0258`; word audio: `duden`.
  - Muss ich bar zahlen oder geht‘s auch mit Karte? — Do I have to pay in cash, or can I also pay by card?
- `1784075509214` provenance: `B1-MAIN-0257|B1-WG-0025`; word audio: `duden`.
  - Setzen wir uns doch an die Bar! — Let's sit at the bar!
  - Ich treffe meine Freundin in der Hotelbar. — I'm meeting my friend in the hotel bar.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/bar) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/bar)

## lexeme-947c1559ef — bio / Bio-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075517833` | `1784075517833, 1784075517834` | B1 | adv. | bio | organic (standalone adjective) | 0 / 0 |  |
| `KEEP_CURRENT` | `1784075517928` | `1784075517928, 1784075517929` | B1 | adj. | Bio- | organic (prefix) | 0 / 0 |  |

### Content to review

- `1784075517833` provenance: `B1-MAIN-0398`; word audio: `gemini`.
  - Biologische Lebensmittel gibt es jetzt auch im Supermarkt. — Organic food is now also available in supermarkets.
- `1784075517928` provenance: `B1-MAIN-0399`; word audio: `commons`.
  - Ich kaufe nur noch Biogemüse. — I only buy organic vegetables now.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/bio) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/bio)

## lexeme-57f2316225 — bitte / Bitte

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454556` | `1584886455472, 1584886455473` | A1 | part. | bitte | please | 10 / 10 |  |
| `KEEP_CURRENT` | `1584886454557` | `1584886455474, 1584886455475` | A1 | n. | Bitte | request | 18 / 18 | die / f. |

### Content to review

- `1584886454556` provenance: `A1-84886454555|A1-84886454556|A1-MAIN-0116|A2-MAIN-0160|B1-MAIN-0406`; word audio: `duden`.
  - Eine Tasse Kaffee, bitte! — A cup of coffee, please!
  - Sprechen Sie bitte leise! — Please speak softly!
  - Eine Tasse Kaffee bitte! - Bitte schön! — A cup of coffee please! - Please!
  - Wie bitte? Sprechen Sie bitte ein bisschen lauter! — I'm sorry, what? Please speak a little louder!
- `1584886454557` provenance: `A1-84886454557|A1-MAIN-0117|A2-MAIN-0161|B1-MAIN-0405`; word audio: `duden`.
  - Ich habe noch eine Bitte. — I have one more request.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Bitte) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Bitte)

## lexeme-c304217598 — braten / Braten

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484860870` | `1497484862238, 1497484862239` | A2 | v. | braten | to fry | 15 / 15 |  |
| `KEEP_CURRENT` | `1784075519149` | `1784075519149, 1784075519150` | B1 | n. | Braten | roast (joint of meat) | 0 / 0 | der / m. |

### Content to review

- `1497484860870` provenance: `A2-0153|A2-MAIN-0174|B1-MAIN-0431`; word audio: `duden`.
  - Braten Sie das Fleisch in etwas Öl! — Fry the meat in a little oil!
  - Der Fisch brät in der Pfanne. — The fish is frying in the pan.
- `1784075519149` provenance: `B1-MAIN-0430`; word audio: `duden`.
  - Nehmen Sie noch etwas Soße zum Braten? — Would you like some more gravy with the roast?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/braten) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/braten)

## lexeme-ef4b0ea031 — erst / erst-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861022` | `1497484862542, 1497484862543` | A2 | adv. | erst | not until; only | 16 / 16 |  |
| `KEEP_CURRENT` | `1584887177194` | `1584887177341, 1584887177342` | A1 | adj. | erst- | first | 12 / 12 |  |

### Content to review

- `1497484861022` provenance: `A2-0305|A2-MAIN-0290|B1-MAIN-0749`; word audio: `duden`.
  - Wir können erst morgen kommen. — We cannot come until tomorrow.
  - Dina ist keine 18, sie ist erst 16 Jahre alt. — Dina is not 18, she is only 16 years old.
- `1584887177194` provenance: `A1-84887177194|A1-WG-0035|A2-WG-0214|B1-MAIN-0750|B1-WG-0276`; word audio: `gemini`.
  - Ich war zum ersten Mal allein im Urlaub. — I went on vacation alone for the first time.
  - Ich wohne im ersten Stock. — I live on the second floor.
  - An erster Stelle kommt die Schule. — School comes first.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/erst-) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/erst-)

## lexeme-2f16a2df94 — essen / Essen

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454666` | `1584886455692, 1584886455693` | A1 | v. | essen | to eat | 13 / 13 |  |
| `KEEP_CURRENT` | `1584886454667` | `1584886455694, 1584886455695` | A1 | n. | Essen | food; meal | 10 / 10 | das / n. |

### Content to review

- `1584886454666` provenance: `A1-84886454666|A1-MAIN-0204|A2-MAIN-0293|B1-MAIN-0761`; word audio: `duden`.
  - Was gibt es zu essen? — What is there to eat?
- `1584886454667` provenance: `A1-84886454667|A1-MAIN-0205|A2-MAIN-0294|B1-MAIN-0760`; word audio: `duden`.
  - Das Essen ist heute sehr gut. — The food is very good today.
  - Das Essen in der Cafeteria ist meistens ganz gut. — The food in the cafeteria is usually quite good.
  - Darf ich Sie zum Essen einladen? — May I invite you to dinner?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/essen) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/essen)

## lexeme-196931d93c — Ferien / Ferien-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861025` | `1497484862548, 1497484862549` | A2 | n. | Ferien | vacation | 0 / 0 | die / pl. |
| `KEEP_CURRENT` | `1784075542201` | `1784075542201, 1784075542202` | B1 | adj. | Ferien- | vacation- (prefix) | 0 / 0 |  |

### Content to review

- `1497484861025` provenance: `A2-0308|A2-MAIN-0316|B1-MAIN-0818`; word audio: `duden`.
  - Bald haben wir Ferien. — Soon we will have vacations.
  - Fährst du in den Ferien weg oder bleibst du zu Hause? — Do you leave during the vacations or do you stay at home?
- `1784075542201` provenance: `B1-MAIN-0819`; word audio: `duden`.
  - Ich suche eine günstige Ferienwohnung. — I'm looking for an inexpensive vacation apartment.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Ferien) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Ferien)

## lexeme-1520c1903a — fernsehen / Fernsehen

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454685` | `1584886455730, 1584886455731` | A1 | v. | fernsehen | to watch TV | 20 / 20 |  |
| `KEEP_CURRENT` | `1784075542391` | `1784075542391, 1784075542392` | B1 | n. | Fernsehen | television (medium) | 0 / 0 | das / n. |

### Content to review

- `1584886454685` provenance: `A1-84886454685|A1-MAIN-0221|A2-MAIN-0317|B1-MAIN-0822`; word audio: `duden`.
  - Wollen wir heute Abend mal fernsehen? — Shall we watch TV this evening?
  - Lass uns heute Abend mal fernsehen. — Let's watch TV tonight.
- `1784075542391` provenance: `B1-MAIN-0821`; word audio: `duden`.
  - Was gibt es heute Abend im Fernsehen? — What's on TV tonight?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/fernsehen) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/fernsehen)

## lexeme-e8d375ea6b — Fest / fest

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861033` | `1497484862564, 1497484862565` | A2 | n. | Fest | celebration; party | 8 / 8 | das / n. |
| `KEEP_CURRENT` | `1784075542486` | `1784075542486, 1784075542487` | B1 | adj. | fest | firm; fixed; soundly | 0 / 0 |  |

### Content to review

- `1497484861033` provenance: `A2-0316|A2-MAIN-0320|B1-MAIN-0825`; word audio: `duden`.
  - Frohes Fest! — Happy Holiday!
  - Am Wochenende feiern wir ein Fest. Meine Tochter hat Geburtstag. — We are having a party this weekend. It is my daughter's birthday.
- `1784075542486` provenance: `B1-MAIN-0826`; word audio: `duden`.
  - Mein Kollege glaubt fest daran, dass er die neue Stelle bekommt. — My colleague firmly believes that he will get the new job.
  - Als wir nach Hause kamen, haben die Kinder schon fest geschlafen. — When we got home, the children were already sound asleep.
  - Für die nächste Familienfeier gibt es noch keinen festen Termin. — There is no fixed date yet for the next family celebration.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Fest) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Fest)

## lexeme-bf84001497 — fett / Fett

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861040` | `1497484862578, 1497484862579` | A2 | adj. | fett | fat | 13 / 13 |  |
| `KEEP_CURRENT` | `1784075543245` | `1784075543245, 1784075543246` | B1 | n. | Fett | fat | 0 / 0 | das / n. |

### Content to review

- `1497484861040` provenance: `A2-0323|A2-MAIN-0322|B1-MAIN-0835`; word audio: `duden`.
  - Die Wurst ist mir zu fett. — The sausage is too fatty for me.
- `1784075543245` provenance: `B1-MAIN-0834`; word audio: `duden`.
  - Man soll nicht so viel Fett essen. — You shouldn't eat so much fat.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/fett) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/fett)

## lexeme-40ae9cdbd5 — geehrt- / geehrt

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861095` | `1497484862688, 1497484862689` | A2 | adj. | geehrt- | dear (formal salutation) | 22 / 22 |  |
| `KEEP_CURRENT` | `1784075550867` | `1784075550867, 1784075550868` | B1 | adv. | geehrt | dear (formal salutation) | 0 / 0 |  |

### Content to review

- `1497484861095` provenance: `A2-0378|A2-MAIN-0375`; word audio: `commons`.
  - Sehr geehrte Damen und Herren, — Dear Sir or Madam,
- `1784075550867` provenance: `B1-MAIN-0965`; word audio: `gemini`.
  - Sehr geehrte Damen und Herren, … — Dear Sir or Madam, ...

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/geehrt-) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/geehrt-)

## lexeme-fbd726bca1 — groß / Groß-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Evidence status: `RULE_REVIEWED` — The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Signals: `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454771` | `1584886455902, 1584886455903` | A1 | adj. | groß | tall; large | 8 / 8 |  |
| `KEEP_CURRENT` | `1784075557078` | `1784075557078, 1784075557079` | B1 | adj. | Groß- | great-; grand- (prefix) | 0 / 0 |  |

### Content to review

- `1584886454771` provenance: `A1-84886454770|A1-84886454771|A1-MAIN-0289|A2-MAIN-0418|B1-MAIN-1077`; word audio: `duden`.
  - Mein Bruder und ich sind gleich groß. — My brother and I are the same height.
  - Frankfurt ist eine große Stadt. — Frankfurt is a large city.
  - Unsere Wohnung ist 80 m² groß. — Our apartment is 80 m².
- `1784075557078` provenance: `B1-MAIN-1078`; word audio: `commons`.
  - z.B. die Großeltern, die Großmutter, der Großvater — e.g. grandparents, grandmother, grandfather

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/gro%C3%9F) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/gro%C3%9F)

## lexeme-56e2012da8 — Halt / halt

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075558817` | `1784075558817, 1784075558818` | B1 | n. | Halt | stop (transport) | 0 / 0 | der / m. |
| `KEEP_CURRENT` | `1784075558910` | `1784075558910, 1784075558911` | B1 | part. | halt | just; simply (modal particle) | 0 / 0 |  |

### Content to review

- `1784075558817` provenance: `B1-MAIN-1110`; word audio: `duden`.
  - Nächster Halt ist am Südbahnhof. — The next stop is S?dbahnhof.
- `1784075558910` provenance: `B1-MAIN-1111`; word audio: `duden`.
  - Es gibt leider keine Karten mehr. – Schade. Da kann man nichts machen. Das ist halt so. — Unfortunately, there are no tickets left. - That's a shame. There's nothing we can do. That's just how it is.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Halt) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Halt)

## lexeme-3c311fd682 — Heim / heim

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075560031` | `1784075560031, 1784075560032` | B1 | n. | Heim | residential home; care home | 0 / 0 | das / n. |
| `KEEP_CURRENT` | `1784075560123` | `1784075560124, 1784075560125` | B1 | adv. | heim | home (direction) | 0 / 0 |  |

### Content to review

- `1784075560031` provenance: `B1-MAIN-1137`; word audio: `duden`.
  - Meine Oma wohnt in einem Seniorenheim. — My grandmother lives in a residential home for older people.
- `1784075560123` provenance: `B1-MAIN-1138`; word audio: `duden`.
  - Ich will jetzt heim. — I want to go home now.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Heim) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Heim)

## lexeme-c69a500339 — heraus / heraus-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Evidence status: `RULE_REVIEWED` — The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Signals: `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861201` | `1497484862900, 1497484862901` | A2 | adv. | heraus | out (towards the speaker) | 7 / 7 |  |
| `KEEP_CURRENT` | `1785175711859` | `1785175711859, 1785175711860` | B1 | adv. | heraus- | out (away from the speaker) | 0 / 0 |  |

### Content to review

- `1497484861201` provenance: `A2-0484|A2-MAIN-0453`; word audio: `duden`.
  - Möchtet ihr nicht rauskommen? Das Wetter ist so schön. — Don't you want to come outside? The weather is lovely.
  - Kannst du bitte den Müll rausbringen? — Could you please take the trash out?
- `1785175711859` provenance: `B1-MAIN-1154`; word audio: `duden`.
  - Hast du schon rausgefunden, wann und wo man sich für den Kurs anmelden muss? — Have you already found out when and where you have to register for the course?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/heraus) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/heraus)

## lexeme-6d38018534 — herein / herein-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Evidence status: `RULE_REVIEWED` — The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Signals: `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861154` | `1497484862806, 1497484862807` | A2 | adv. | herein | in; come in | 17 / 17 |  |
| `KEEP_CURRENT` | `1785175711905` | `1785175711905, 1785175711906` | B1 | adv. | herein- | in (towards the speaker) | 0 / 0 |  |

### Content to review

- `1497484861154` provenance: `A2-0437|A2-MAIN-0454`; word audio: `duden`.
  - Herein! Die Tür ist offen. — Come in! The door is open.
  - Möchtest du nicht reinkommen? Ich kann uns einen Tee machen. — Don't you want to come in? I can make us some tea.
- `1785175711905` provenance: `B1-MAIN-1157`; word audio: `duden`.
  - Kommt doch herein! — Do come in!

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/herein) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/herein)

## lexeme-6d083381c9 — husten / Husten

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861205` | `1497484862908, 1497484862909` | A2 | v. | husten | to cough | 20 / 20 |  |
| `KEEP_CURRENT` | `1784075563790` | `1784075563790, 1784075563791` | B1 | n. | Husten | cough | 0 / 0 | der / m. |

### Content to review

- `1497484861205` provenance: `A2-0488|A2-MAIN-0479|B1-MAIN-1208`; word audio: `duden`.
  - Sie hustet seit zwei Tagen. Sie ist krank. — She has been coughing for two days. She is ill.
- `1784075563790` provenance: `B1-MAIN-1207`; word audio: `duden`.
  - Haben Sie ein Medikament gegen Husten? — Do you have any cough medicine?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/husten) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/husten)

## lexeme-d5b82f65b2 — kein / kein-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Evidence status: `RULE_REVIEWED` — The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Signals: `same_word_audio`, `shared_german_example`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454856` | `1584886456072, 1584886456073` | A1 | det. | kein | no; not any; none | 18 / 18 |  |
| `KEEP_CURRENT` | `1785175712009` | `1785175712009, 1785175712010` | B1 | det., pron. | kein- | no; not any (inflected determiner stem) | 0 / 0 |  |

### Content to review

- `1584886454856` provenance: `A1-84886454856|A1-MAIN-0355|A2-MAIN-0517`; word audio: `duden`.
  - Es gibt keine Eintrittskarten mehr. — There are no more tickets.
  - Hast du keinen Hunger? — Aren't you hungry?
  - Ich habe heute leider keine Zeit. — Unfortunately I don't have time today.
  - Ich spreche leider kein Chinesisch. — Unfortunately I don't speak Chinese.
  - Ich habe keine Kinder. — I have no children.
- `1785175712009` provenance: `B1-MAIN-1311`; word audio: `duden`.
  - Ich habe leider heute keine Zeit. — Unfortunately, I do not have any time today.
  - Jetzt habe ich noch keinen Hunger. — I am not hungry yet.
  - Ich habe keine Kinder. — I have no children.
  - Was für ein Auto haben Sie? – Ich habe keins. — What kind of car do you have? – I do not have one.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/kein) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/kein)

## lexeme-8e83818059 — Klasse / klasse

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454865` | `1584886456090, 1584886456091` | A1 | n. | Klasse | class; group | 12 / 12 | die / f. |
| `KEEP_CURRENT` | `1784075570897` | `1784075570898, 1784075570899` | B1 | adj. | klasse | great; excellent | 0 / 0 |  |

### Content to review

- `1584886454865` provenance: `A1-84886454864|A1-84886454865|A1-MAIN-0363|A2-WG-0095|B1-MAIN-1333`; word audio: `duden`.
  - In unserer Klasse sind fünfundzwanzig Schüler. — In our class there are twenty five pupils.
  - Im Zug fahre ich immer 2. Klasse. — On the train I always travel in second class.
- `1784075570897` provenance: `B1-MAIN-1334`; word audio: `duden`.
  - Ich finde unseren Lehrer klasse. — I think our teacher is great.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Klasse) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Klasse)

## lexeme-0f7281c39a — kosten / Kosten

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454877` | `1584886456114, 1584886456115` | A1 | v. | kosten | to cost | 8 / 8 |  |
| `KEEP_CURRENT` | `1784075574741` | `1784075574741, 1784075574742` | B1 | n. | Kosten | costs; expenses | 0 / 0 | die / pl. |

### Content to review

- `1584886454877` provenance: `A1-84886454877|A1-MAIN-0373|A2-MAIN-0549|B1-MAIN-1396`; word audio: `duden`.
  - Wie viel kostet das?<br>– 10 Euro. — How much does that cost?<br>– 10 euros.
  - Wie viel kostet das Buch? – 20 Euro. — How much does the book cost? – 20 euros.
- `1784075574741` provenance: `B1-MAIN-1395`; word audio: `duden`.
  - Die Kosten für die Reise bekomme ich von der Firma. — The company reimburses me for the cost of the trip.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/kosten) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/kosten)

## lexeme-069ba42a43 — leben / Leben

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454899` | `1584886456158, 1584886456159` | A1 | v. | leben | to live; to be alive | 13 / 13 |  |
| `KEEP_CURRENT` | `1584886454900` | `1584886456160, 1584886456161` | A1 | n. | Leben | life | 7 / 7 | das / n. |

### Content to review

- `1584886454899` provenance: `A1-84886454898|A1-84886454899|A1-MAIN-0393|A2-MAIN-0584|B1-MAIN-1481`; word audio: `duden`.
  - Sie lebt bei ihrer Schwester. — She lives at her sister‘s.
  - Ihre Eltern leben nicht mehr. — Her parents are no longer alive.
  - Ihre Großeltern leben nicht mehr. — Her grandparents are no longer alive.
- `1584886454900` provenance: `A1-84886454900|A1-MAIN-0394|A2-MAIN-0585|B1-MAIN-1480`; word audio: `duden`.
  - Das Leben in diesem Land ist teuer. — Life in this country is expensive.
  - Hier in London ist das Leben teuer. — Life is expensive here in London.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/leben) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/leben)

## lexeme-a2e0ac3b90 — Link / link-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861308` | `1497484863114, 1497484863115` | A2 | n. | Link | link | 9 / 9 | der / m. |
| `KEEP_CURRENT` | `1785175712016` | `1785175712016, 1785175712017` | B1 | adj. | link- | left (attributive adjective stem) | 0 / 0 |  |

### Content to review

- `1497484861308` provenance: `A2-0591|A2-MAIN-0605|B1-WG-0064`; word audio: `commons`.
  - Ich schicke dir einen Link zu Deutschübungen. — I will send you a link to German exercises.
- `1785175712016` provenance: `B1-MAIN-1527`; word audio: `commons`.
  - Er hat sich das linke Bein gebrochen. — He has broken his left leg.
  - Das Haus ist auf der linken Seite. — The house is on the left-hand side.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Link) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Link)

## lexeme-4a43953cee — mal / Mal

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861333` | `1497484863164, 1497484863165` | A2 | phrase | mal | just (modal particle) | 0 / 0 |  |
| `KEEP_CURRENT` | `1497484861334` | `1497484863166, 1497484863167` | A2 | n. | Mal | time; mark | 9 / 9 | das / n. |
| `KEEP_CURRENT` | `1784075582949` | `1784075582949, 1784075582950` | B1 | part. | mal | once; just (particle) | 0 / 0 |  |

### Content to review

- `1497484861333` provenance: `A2-0616|A2-MAIN-0617`; word audio: `commons`.
  - Sag mal, wie gefällt dir mein neues Kleid? — Tell me, how do you like my new dress?
- `1497484861334` provenance: `A2-0617|B1-MAIN-1558`; word audio: `duden`.
  - Das machen wir nächstes Mal. — We will do that next time.
  - Das erste Mal war ich vor fünf Jahren in England. — I was in England for the first time five years ago.
  - Bis zum nächsten Mal. — Until next time.
- `1784075582949` provenance: `B1-MAIN-1559`; word audio: `commons`.
  - (siehe einmal) — (See “einmal”.)

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Mal) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Mal)

## lexeme-b5658c000a — mehr

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `same_surface_casefold`, `same_word_audio`, `shared_german_example`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454936` | `1584886456232, 1584886456233` | A1 | adv. | mehr | more | 13 / 13 |  |
| `KEEP_CURRENT` | `1784075584455` | `1784075584455, 1784075584456` | A2 | pron. | mehr | more | 0 / 0 |  |

### Content to review

- `1584886454936` provenance: `A1-84886454936|A1-MAIN-0425`; word audio: `duden`.
  - Dieses Auto kostet 1.000 Euro mehr als das andere. — This car costs 1000 euros more than the other.
- `1784075584455` provenance: `A2-MAIN-0630|B1-MAIN-1589`; word audio: `duden`.
  - Dieses Auto kostet 1.000 Euro mehr als das andere. — This car costs 1000 euros more than the other.
  - Mehr kann ich nicht essen! — I cannot eat any more!
  - Ich möchte mehr Taschengeld. — I want more pocket money.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/mehr) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/mehr)

## lexeme-db25fd1a9f — morgen / Morgen

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886454955` | `1584886456270, 1584886456271` | A1 | adv. | morgen | tomorrow | 10 / 10 |  |
| `KEEP_CURRENT` | `1584887177227` | `1584887177407, 1584887177408` | A1 | n. | Morgen | morning | 9 / 9 | der / m. |

### Content to review

- `1584886454955` provenance: `A1-84886454955|A1-MAIN-0443|A2-MAIN-0658`; word audio: `duden`.
  - Morgen beginnt die Schule um 10 Uhr. — Tomorrow school begins at 10 am.
  - Morgen beginnt die Schule erst um zehn Uhr. — School doesn't start until ten o'clock tomorrow.
- `1584887177227` provenance: `A1-84887177227|A1-WG-0070|A2-WG-0148|B1-WG-0322`; word audio: `duden`.
  - Am Morgen trinke ich Kaffee. — I drink coffee in the morning.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/morgen) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/morgen)

## lexeme-f7b58ce91e — Ober / ober-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075594210` | `1784075594210, 1784075594211` | B1 | n. | Ober | (male) waiter (German/Austrian usage) | 0 / 0 | der / m. |
| `KEEP_CURRENT` | `1784075594305` | `1784075594305, 1784075594306` | B1 | adj. | ober- | upper- (prefix) | 0 / 0 |  |

### Content to review

- `1784075594210` provenance: `B1-MAIN-1762`; word audio: `duden`.
  - Ich bin Ober von Beruf. — I work as a waiter.
- `1784075594305` provenance: `B1-MAIN-1763`; word audio: `gemini`.
  - Die Wohnung im oberen Stockwerk ist vermietet. — The apartment on the upper floor is let.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Ober) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Ober)

## lexeme-4e67825ff3 — Orange / orange

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861432` | `1497484863362, 1497484863363` | A2 | n. | Orange | orange | 0 / 0 | die / f. |
| `KEEP_CURRENT` | `1783863835220` | `1783863835220, 1783863835221` | A2 | adj. | orange | orange (color) | 3 / 3 |  |

### Content to review

- `1497484861432` provenance: `A2-0715|A2-MAIN-0711|B1-MAIN-1789`; word audio: `duden`.
  - Ich esse gern Orangen. — I like to eat oranges.
- `1783863835220` provenance: `A2-WG-0078|B1-WG-0168`; word audio: `duden`.
  - Meine Lieblingsfarbe ist Orange. — My favorite color is orange.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/orange) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/orange)

## lexeme-7781b375a4 — Original / original

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075596268` | `1784075596268, 1784075596269` | B1 | n. | Original | original (noun) | 0 / 0 | das / n. |
| `KEEP_CURRENT` | `1784075596363` | `1784075596363, 1784075596364` | B1 | adj. | original | original (adjective) | 0 / 0 |  |

### Content to review

- `1784075596268` provenance: `B1-MAIN-1798`; word audio: `duden`.
  - Das Original ist für Sie. Wir bekommen die Kopie. — You can have the original; we'll take the copy.
- `1784075596363` provenance: `B1-MAIN-1799`; word audio: `duden`.
  - Ich muss das originale Dokument abgeben. — I have to hand in the original document.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Original) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Original)

## lexeme-08ac3b70ac — Paar / ein paar / paar

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861443` | `1497484863384, 1497484863385` | A2 | n. | Paar | pair; couple | 13 / 13 | das / n. |
| `KEEP_CURRENT` | `1497484861444` | `1497484863386, 1497484863387` | A2 | phrase | ein paar | a few | 12 / 15 |  |
| `KEEP_CURRENT` | `1784075596545` | `1784075596545, 1784075596546` | B1 | det., pron. | paar | a few; a small number of | 0 / 0 |  |

### Content to review

- `1497484861443` provenance: `A2-0726|A2-MAIN-0715|B1-MAIN-1802`; word audio: `duden`.
  - Romeo und Julia sind ein Paar. — Romeo and Juliet are a couple.
  - Ich brauche ein Paar Schuhe. — I need a pair of shoes.
- `1497484861444` provenance: `A2-0727|A2-MAIN-0296`; word audio: `commons`.
  - Wir fahren ein paar Tage ans Meer. — We are going to the seaside for a few days.
  - Hast du ein paar Minuten Zeit? — Do you have a few minutes?
- `1784075596545` provenance: `B1-MAIN-1803`; word audio: `duden`.
  - Ich komme gleich. Es dauert nur ein paar Minuten. — I'll be there shortly. It will only take a few minutes.
  - Wir fahren mit ein paar Freunden in Urlaub. — We are going on vacation with a few friends.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Paar) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Paar)

## lexeme-6ea0aa6cd0 — Recht / recht / recht-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075605564` | `1784075605564, 1784075605565` | B1 | n. | Recht | law; right; being right | 0 / 0 | das / n. |
| `KEEP_CURRENT` | `1784075605657` | `1784075605657, 1784075605658` | B1 | adj. | recht | acceptable; right | 0 / 0 |  |
| `KEEP_CURRENT` | `1785175712051` | `1785175712052, 1785175712053` | B1 | adj. | recht- | right (attributive adjective stem) | 0 / 0 |  |

### Content to review

- `1784075605564` provenance: `B1-MAIN-1955`; word audio: `duden`.
  - Nach deutschem Recht kann er dafür nicht bestraft werden. — Under German law, he cannot be punished for that.
  - Ich hatte Vorfahrt. Ich war im Recht. — I had right of way. I was in the right.
  - Die Rechnung stimmt nicht? Dann haben Sie das Recht, das Geld zurückzubekommen. — Is the check incorrect? Then you have the right to get your money back.
- `1784075605657` provenance: `B1-MAIN-1956`; word audio: `duden`.
  - Ist es Ihnen recht, wenn ich morgen vorbeikomme? — Would it be all right with you if I came by tomorrow?
  - Da haben Sie recht. — You are right there.
  - Da muss ich Ihnen recht geben. — I have to agree with you there.
- `1785175712051` provenance: `B1-MAIN-1957`; word audio: `duden`.
  - Ich habe mir den rechten Arm gebrochen. — I have broken my right arm.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Recht) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Recht)

## lexeme-14928ebe6b — Schaden / schaden

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075611222` | `1784075611223, 1784075611224` | B1 | n. | Schaden | damage (noun) | 0 / 0 | der / m. |
| `KEEP_CURRENT` | `1784075611315` | `1784075611315, 1784075611316` | B1 | v. | schaden | to harm; to hurt | 0 / 0 |  |

### Content to review

- `1784075611222` provenance: `B1-MAIN-2064`; word audio: `duden`.
  - Ich hatte einen Unfall mit dem Auto. Jetzt muss ich den Schaden der Versicherung melden. — I had a car accident. Now I have to report the damage to the insurance company.
- `1784075611315` provenance: `B1-MAIN-2065`; word audio: `duden`.
  - Ein kleines Glas Wein kann nicht schaden. — A small glass of wine will not hurt.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Schaden) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Schaden)

## lexeme-1820a8a28f — schreiben / Schreiben

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886455067` | `1584886456494, 1584886456495` | A1 | v. | schreiben | to write | 14 / 14 |  |
| `KEEP_CURRENT` | `1784075613861` | `1784075613861, 1784075613862` | B1 | n. | Schreiben | formal letter | 0 / 0 | das / n. |

### Content to review

- `1584886455067` provenance: `A1-84886455067|A1-MAIN-0534|A2-MAIN-0856|B1-MAIN-2127`; word audio: `duden`.
  - Er schreibt jeden Tag fünfzig E-Mails. — He writes fifty emails a day.
  - Ich schreibe dir eine E-Mail. — I'll write you an email.
- `1784075613861` provenance: `B1-MAIN-2126`; word audio: `duden`.
  - Haben Sie mein Schreiben vom 3. März erhalten? — Did you receive my letter dated 3 March?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/schreiben) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/schreiben)

## lexeme-06809b4dc6 — Schuld / schuld

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075614802` | `1784075614802, 1784075614803` | B1 | n. | Schuld | fault or blame (noun) | 0 / 0 | die / f. |
| `KEEP_CURRENT` | `1784075614895` | `1784075614895, 1784075614896` | B1 | adv. | schuld | at fault; to blame | 0 / 0 |  |

### Content to review

- `1784075614802` provenance: `B1-MAIN-2141`; word audio: `duden`.
  - Es ist nicht meine Schuld, dass das nicht geklappt hat. — It is not my fault that it did not work.
- `1784075614895` provenance: `B1-MAIN-2142`; word audio: `gemini`.
  - Ich hatte einen Unfall. Aber ich war nicht schuld. — I had an accident, but I was not at fault.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Schuld) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Schuld)

## lexeme-e4ea2fe200 — sie / Sie

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Capitalisation distinguishes separate entries and their English senses differ.

Evidence status: `RULE_REVIEWED` — Capitalisation distinguishes separate entries and their English senses differ.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886455093` | `1584886456546, 1584886456547` | A1 | pron. | sie | she | 8 / 8 |  |
| `KEEP_CURRENT` | `1584886455094` | `1584886456548, 1584886456549` | A1 | pron. | Sie | you (polite) | 6 / 6 |  |

### Content to review

- `1584886455093` provenance: `A1-84886455093|A1-MAIN-0552`; word audio: `duden`.
  - Wie heißt sie? — What is she called?
- `1584886455094` provenance: `A1-84886455094|A1-MAIN-0553`; word audio: `commons`.
  - Wie heißen Sie, bitte? — What is your name, please?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/sie) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/sie)

## lexeme-ef163f4455 — Traum / Traum-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1497484861729` | `1497484863956, 1497484863957` | A2 | n. | Traum | dream | 0 / 0 | der / m. |
| `KEEP_CURRENT` | `1784075640668` | `1784075640668, 1784075640669` | B1 | adj. | Traum- | dream-; ideal | 0 / 0 |  |

### Content to review

- `1497484861729` provenance: `A2-1012|A2-MAIN-0989|B1-MAIN-2465`; word audio: `duden`.
  - Ich möchte eine eigene Firma, das ist mein Traum. — I want my own company, that is my dream.
- `1784075640668` provenance: `B1-MAIN-2466`; word audio: `duden`.
  - Mein Traumberuf ist Feuerwehrmann. — My dream job is to be a firefighter.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Traum) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Traum)

## lexeme-3c8307d350 — Uhr / ein Uhr

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `qualifier_or_article_normalization`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886455157` | `1584886456674, 1584886456675` | A1 | n. | Uhr | clock/watch; o'clock | 8 / 8 | die / f. |
| `KEEP_CURRENT` | `1584887177208` | `1584887177369, 1584887177370` | A1 |  | ein Uhr | one o'clock | 6 / 6 |  |

### Content to review

- `1584886455157` provenance: `A1-84886455157|A1-MAIN-0606|A2-MAIN-1009|B1-MAIN-2525`; word audio: `duden`.
  - Es ist vier Uhr. — It is four o’clock.
  - Geht deine Uhr richtig? — Is your clock working correctly?
- `1584887177208` provenance: `A1-84887177208|A1-WG-0050|A2-WG-0159|B1-WG-0333`; word audio: `gemini`.
  - Wir treffen uns um ein Uhr. — We are meeting at one o'clock.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Uhr) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Uhr)

## lexeme-05a1b6da68 — unter / unter-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Evidence status: `RULE_REVIEWED` — The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Signals: `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886455164` | `1584886456688, 1584886456689` | A1 | prep. | unter | below | 11 / 11 |  |
| `KEEP_CURRENT` | `1785175712173` | `1785175712173, 1785175712174` | B1 | prep. | unter- | lower; under- (bound form) | 0 / 0 |  |

### Content to review

- `1584886455164` provenance: `A1-84886455164|A1-MAIN-0612|A2-MAIN-1019|B1-MAIN-2558`; word audio: `duden`.
  - Unter uns wohnt eine Familie mit drei Kindern. — Below us lives a family with three children.
- `1785175712173` provenance: `B1-MAIN-2559`; word audio: `duden`.
  - Mein Pass ist im unteren Regal. — My passport is on the lower shelf.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/unter) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/unter)

## lexeme-92f10c1601 — Vertrauen / vertrauen

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075655469` | `1784075655469, 1784075655470` | B1 | n. | Vertrauen | trust; confidence | 0 / 0 | das / n. |
| `KEEP_CURRENT` | `1784075655561` | `1784075655561, 1784075655562` | B1 | v. | vertrauen | to trust | 0 / 0 |  |

### Content to review

- `1784075655469` provenance: `B1-MAIN-2688`; word audio: `duden`.
  - Ich habe Vertrauen zu Ihnen. — I have confidence in you.
- `1784075655561` provenance: `B1-MAIN-2689`; word audio: `duden`.
  - Ich kenne dich gut. Ich vertraue dir. — I know you well. I trust you.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Vertrauen) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Vertrauen)

## lexeme-e2c8df7504 — was für ein / was für ein-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Evidence status: `RULE_REVIEWED` — The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Signals: `same_word_audio`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886455200` | `1584886456760, 1584886456761` | A1 | det. | was für ein | what kind of | 14 / 14 |  |
| `KEEP_CURRENT` | `1785175712236` | `1785175712236, 1785175712237` | B1 | det. | was für ein- | what kind of (inflected determiner) | 0 / 0 |  |

### Content to review

- `1584886455200` provenance: `A1-84886455200|A1-MAIN-0641`; word audio: `gemini`.
  - Was für eine Farbe möchten Sie? — What color would you like?
- `1785175712236` provenance: `B1-MAIN-2777`; word audio: `gemini`.
  - Ich will mir ein Auto kaufen. – Was denn für eins? — I want to buy a car. – What kind?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/was%20f%C3%BCr%20ein) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/was%20f%C3%BCr%20ein)

## lexeme-80b8cffbb2 — Wert / wert

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1784075665242` | `1784075665242, 1784075665243` | B1 | n. | Wert | value; worth; importance | 0 / 0 | der / m. |
| `KEEP_CURRENT` | `1784075665338` | `1784075665338, 1784075665339` | B1 | adj. | wert | worth | 0 / 0 |  |

### Content to review

- `1784075665242` provenance: `B1-MAIN-2812`; word audio: `duden`.
  - Das Haus hat einen Wert von ca. 1 Mio. Euro. — The house is worth about one million euros.
  - Es hat ja doch keinen Wert. — It is of no value after all.
  - Auf Ihr Urteil lege ich großen Wert. — I attach great importance to your opinion.
- `1784075665338` provenance: `B1-MAIN-2813`; word audio: `duden`.
  - Das Auto ist vielleicht noch 1000 Euro wert. — The car is probably still worth 1000 euros.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/Wert) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/Wert)

## lexeme-2c1e67b5c3 — wissen / Wissen

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Evidence status: `RULE_REVIEWED` — Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme.

Signals: `accepted_answer_overlap`, `qualifier_or_article_normalization`, `same_surface_casefold`, `same_word_audio`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886455231` | `1584886456822, 1584886456823` | A1 | v. | wissen | to know a fact | 17 / 17 |  |
| `KEEP_CURRENT` | `1784075667330` | `1784075667330, 1784075667331` | B1 | n. | Wissen | knowledge; awareness | 0 / 0 | das / n. |

### Content to review

- `1584886455231` provenance: `A1-84886455231|A1-MAIN-0664|A2-MAIN-1123|B1-MAIN-2845`; word audio: `duden`.
  - Weißt du, wie er heißt? — Do you know what he is called?
  - Weißt du, wie der Hausmeister heißt? — Do you know what the caretaker's name is?
  - Woher wissen Sie das? — How do you know that?
- `1784075667330` provenance: `B1-MAIN-2844`; word audio: `duden`.
  - Es hat ein großes Wissen über Pflanzen. — It has extensive knowledge of plants.
  - Ich tue nichts ohne dein Wissen. — I do nothing without your knowledge.

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/wissen) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/wissen)

## lexeme-721a092fff — zurück / zurück-

**Decision:** `KEEP_SEPARATE_HOMOGRAPH` · **Approval:** `PENDING_APPROVAL`

The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Evidence status: `RULE_REVIEWED` — The marked combining form or expression has a distinct source sense, not merely alternate spelling.

Signals: `same_word_audio`, `shared_german_example`, `spacing_hyphen_or_transliteration`

| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |
|---|---:|---|---|---|---|---|---:|---|
| `KEEP_CURRENT` | `1584886455258` | `1584886456876, 1584886456877` | A1 | adv. | zurück | back | 15 / 15 |  |
| `KEEP_CURRENT` | `1785175711716` | `1785175711716, 1785175711717` | A2 | adv. | zurück- | back (separable verb particle) | 5 / 5 |  |

### Content to review

- `1584886455258` provenance: `A1-84886455258|A1-84886455259|A1-MAIN-0683|A2-MAIN-1167|B1-MAIN-2943`; word audio: `duden`.
  - Einmal Frankfurt und zurück. — A return to Frankfurt.
  - Wann kommst du zurück? — When are you coming back?
  - Eine Fahrkarte nach Frankfurt und zurück, bitte. — A ticket to Frankfurt and back, please.
  - Bitte eine Fahrkarte nach Frankfurt und zurück! — A ticket to Frankfurt and back, please!
- `1785175711716` provenance: `A2-MAIN-1168|B1-MAIN-2943-PREFIX`; word audio: `duden`.
  - Fahrt ihr nach der Party zurück nach Hause? — Are you going back home after the party?
  - Wann muss ich das Buch zurückgeben? — When do I have to return the book?
  - Wir gehen wieder zurück. — We're going back again.
  - Wann kommst du zurück? — When are you coming back?
  - Ich habe mein Buch vergessen. Ich laufe schnell nach Hause zurück. — I forgot my book. I quickly run back home.
  - Wann wirst du zurückkommen? — When will you come back?

Evidence lookup: [Duden](https://www.duden.de/suchen/dudenonline/zur%C3%BCck) · [Cambridge](https://dictionary.cambridge.org/dictionary/german-english/zur%C3%BCck)
