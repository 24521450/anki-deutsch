# Goethe example-boundary audit

Audit date: 2026-07-15

## Method

The Markdown `<br>` boundaries were compared with word coordinates from the three original Goethe PDFs. A boundary is flagged only when the two adjacent Markdown fragments occur on the same physical PDF line. This is a high-confidence candidate signal, not an automatic merge rule.

- A1: 54 same-line boundaries before the `Achtung` correction.
- 1 confirmed and corrected here: `A1-MAIN-0008` (`Achtung`).
- 13 dash-led reply boundaries are already handled by the dialogue parser.
- 1 line-wrap case, `A1-MAIN-0363` (`Klasse`), is already handled by `goethe_source_text_overrides.json`.
- 39 candidates remain for manual review; none are changed by this audit.
- A2 and B1: no exact same-line matches were found. Their different PDF layouts mean this check does not prove that every boundary is correct.

## Confirmed correction

| Source ID | Lemma | PDF page | Correct example |
|---|---|---:|---|
| `A1-MAIN-0008` | Achtung | 9 | Achtung! Das dürfen Sie nicht tun. |

## Pending A1 candidates

| Source ID | Lemma | PDF page | Adjacent Markdown fragments |
|---|---|---:|---|
| `A1-MAIN-0052` | auf sein | 10 | Du brauchst den Schlüssel nicht. / Die Wohnung ist auf. |
| `A1-MAIN-0074` | automatisch | 10 | Du musst nichts machen. / Das geht automatisch. |
| `A1-MAIN-0112` | billig | 11 | Die Jacke kostet nur 10 Euro! / Die ist aber billig! |
| `A1-MAIN-0144` | da | 12 | Wir sprechen gerade über Paul. / Da kommt er ja gerade. |
| `A1-MAIN-0146` | daneben | 13 | Du kennst doch die Post. / Daneben ist die Bank. |
| `A1-MAIN-0160` | Doktor | 13 | Meine Tochter ist krank. / Wir gehen zum Doktor. |
| `A1-MAIN-0171` | Durst | 13 | Hast du etwas zu trinken? / Ich habe großen Durst. |
| `A1-MAIN-0179` | ein- | 13 | Ich nehme ein Bier. / Willst du auch eins? |
| `A1-MAIN-0228` | Fisch | 14 | Ich esse gern Fisch. / Fleisch mag ich nicht. |
| `A1-MAIN-0231` | fliegen | 15 | Ich fliege nicht gern. / Deshalb fahre ich mit dem Zug. |
| `A1-MAIN-0238` | fragen | 15 | Er möchte Sie etwas fragen. / Wann kommen Sie? |
| `A1-MAIN-0304` | hallo | 16 | Hallo Inge! / Wie geht’s? |
| `A1-MAIN-0313` | Heimat | 17 | Ich komme aus der Schweiz. / Das ist meine Heimat. |
| `A1-MAIN-0323` | Hilfe | 17 | Hilfe! / Bitte helfen Sie mir! |
| `A1-MAIN-0329` | hören | 17 | Hör mal! / Was ist das? |
| `A1-MAIN-0332` | Hunger | 17 | Ich habe Hunger! / Wann ist das Essen fertig? |
| `A1-MAIN-0341` | Jacke | 18 | Zieh dir eine Jacke an. / Es ist kalt. |
| `A1-MAIN-0346` | jung | 18 | – Was? / Noch so jung? |
| `A1-MAIN-0347` | Junge | 18 | Ich habe zwei Kinder. / Einen Jungen und ein Mädchen. |
| `A1-MAIN-0349` | kaputt | 18 | Das Glas war teuer. / Es geht sehr leicht kaputt. |
| `A1-MAIN-0357` | kennenlernen | 18 | Wir sind neu hier. / Wir möchten Sie kennenlernen. |
| `A1-MAIN-0380` | kulturell | 19 | Ich bin kulturell interessiert. / Ich gehe oft ins Museum. |
| `A1-MAIN-0382` | Kunde | 19 | Einen Moment, bitte. / Ich habe eine Kundin. |
| `A1-MAIN-0392` | laut | 19 | Nicht so laut! / Das Baby schläft. |
| `A1-MAIN-0396` | ledig | 19 | – Nein. / Ledig. |
| `A1-MAIN-0400` | leider | 19 | Leider kann ich nicht kommen. / Ich muss zum Arzt. |
| `A1-MAIN-0401` | leise | 19 | Seid leise. / Die anderen schlafen schon. |
| `A1-MAIN-0417` | lustig | 19 | Frau Mertens ist lustig. / Sie lacht immer. |
| `A1-MAIN-0433` | mitbringen | 20 | Ich gehe einkaufen. / Soll ich dir was mitbringen? |
| `A1-MAIN-0434` | mitkommen | 20 | Ich gehe ins Kino. / Kommst du mit? |
| `A1-MAIN-0444` | müde | 20 | Ich bin müde. / Ich gehe schlafen. |
| `A1-MAIN-0451` | nehmen | 20 | Heute gibt es Hähnchen. / Das nehme ich. |
| `A1-MAIN-0455` | nichts | 20 | Hier kaufe ich nichts. / Der Laden gefällt mir nicht. |
| `A1-MAIN-0458` | normal | 21 | 75 kg. / Sein Gewicht ist normal. |
| `A1-MAIN-0582` | Taxi | 24 | Es gibt heute keinen Bus mehr. / Er fährt mit dem Taxi. |
| `A1-MAIN-0599` | Treppe | 24 | Die Toilette? / Die Treppe hoch und dann links. |
| `A1-MAIN-0625` | Vermieter | 25 | Unser Vermieter heißt Huber. / Er wohnt auch hier. |
| `A1-MAIN-0633` | Vorsicht | 25 | Vorsicht! / Da kommt ein Auto. |
| `A1-MAIN-0644` | wehtun | 25 | Ich muss zum Arzt. / Mein Bein tut weh. |
