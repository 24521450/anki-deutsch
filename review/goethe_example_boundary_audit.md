# Goethe example-boundary audit

Audit date: 2026-07-26

## Method

Markdown `<br>` boundaries were compared with Unicode-preserving word coordinates from the original Goethe PDFs. Every decision below was also checked for syntactic and discourse dependence; matching A2/B1 examples were used where available. Same-line geometry is evidence, not an automatic merge rule.

- A1 produced 54 same-line boundary signals before correction.
- 40 are corrected `MERGE` cases: `Achtung`, `Heimat`, and all 38 candidates below.
- 13 dash-led reply boundaries remain handled by the dialogue parser.
- `A1-MAIN-0363` (`Klasse`) remains handled by `goethe_source_text_overrides.json`.
- Unresolved candidates: 0.
- A2 and B1 use different layouts; absence of an exact same-line match there is not treated as proof.

## Existing confirmed corrections

| Source ID | Lemma | PDF page | Decision | Correct example |
|---|---|---:|---|---|
| `A1-MAIN-0008` | Achtung | 9 | `MERGE` | Achtung! Das dürfen Sie nicht tun. |
| `A1-MAIN-0313` | Heimat | 17 | `MERGE` | Ich komme aus der Schweiz. Das ist meine Heimat. |

## Resolved A1 candidates

| Source ID | Lemma | PDF page | Decision | Correct example | Evidence |
|---|---|---:|---|---|---|
| `A1-MAIN-0052` | auf sein | 10 | `MERGE` | Du brauchst den Schlüssel nicht. Die Wohnung ist auf. | Câu sau giải thích vì sao không cần chìa khóa. |
| `A1-MAIN-0074` | automatisch | 10 | `MERGE` | Du musst nichts machen. Das geht automatisch. | `Das` hồi chỉ quá trình ở câu trước. |
| `A1-MAIN-0112` | billig | 11 | `MERGE` | Die Jacke kostet nur 10 Euro! Die ist aber billig! | `Die` hồi chỉ `Jacke`; A2 dùng cùng mẫu giá–đánh giá. |
| `A1-MAIN-0144` | da | 12 | `MERGE` | Wir sprechen gerade über Paul. Da kommt er ja gerade. | `er` hồi chỉ Paul; A2/B1 có cấu trúc song song. |
| `A1-MAIN-0146` | daneben | 13 | `MERGE` | Du kennst doch die Post. Daneben ist die Bank. | `Daneben` cần mốc `Post`; A2/B1 giữ thành một example. |
| `A1-MAIN-0160` | Doktor | 13 | `MERGE` | Meine Tochter ist krank. Wir gehen zum Doktor. | Quan hệ lý do–hành động; B1 giữ đúng cặp. |
| `A1-MAIN-0171` | Durst | 13 | `MERGE` | Hast du etwas zu trinken? Ich habe großen Durst. | Yêu cầu và lý do; A2 giữ đúng cặp. |
| `A1-MAIN-0179` | ein- | 13 | `MERGE` | Ich nehme ein Bier. Willst du auch eins? | `eins` hồi chỉ `Bier`; B1 giữ đúng cặp. |
| `A1-MAIN-0228` | Fisch | 14 | `MERGE` | Ich esse gern Fisch. Fleisch mag ich nicht. | Hai câu tạo một cặp đối chiếu. |
| `A1-MAIN-0231` | fliegen | 15 | `MERGE` | Ich fliege nicht gern. Deshalb fahre ich mit dem Zug. | `Deshalb` liên kết bắt buộc; A2 nối cùng nội dung. |
| `A1-MAIN-0238` | fragen | 15 | `MERGE` | Er möchte Sie etwas fragen. Wann kommen Sie? | Câu sau là nội dung của hành động hỏi. |
| `A1-MAIN-0304` | hallo | 16 | `MERGE` | Hallo Inge! Wie geht’s? | Lời chào và câu hỏi liền mạch; A2 in cùng một example. |
| `A1-MAIN-0323` | Hilfe | 17 | `MERGE` | Hilfe! Bitte helfen Sie mir! | Lời kêu cứu và yêu cầu trợ giúp; A2 giữ đúng cặp. |
| `A1-MAIN-0329` | hören | 17 | `MERGE` | Hör mal! Was ist das? | Mệnh lệnh nghe và câu hỏi tiếp theo; A2 giữ đúng cặp. |
| `A1-MAIN-0332` | Hunger | 17 | `MERGE` | Ich habe Hunger! Wann ist das Essen fertig? | Trạng thái và câu hỏi hệ quả; A2 dùng cùng cấu trúc. |
| `A1-MAIN-0341` | Jacke | 18 | `MERGE` | Zieh dir eine Jacke an. Es ist kalt. | Mệnh lệnh và lý do; A2/B1 giữ đúng cặp. |
| `A1-MAIN-0346` | jung | 18 | `MERGE` | Claudia ist 21.<br>– Was? Noch so jung? | Một lượt thoại hoàn chỉnh; A2/B1 giữ đúng chuỗi. |
| `A1-MAIN-0347` | Junge | 18 | `MERGE` | Ich habe zwei Kinder. Einen Jungen und ein Mädchen. | Câu hai là cụm danh từ tỉnh lược phụ thuộc câu một. |
| `A1-MAIN-0349` | kaputt | 18 | `MERGE` | Das Glas war teuer. Es geht sehr leicht kaputt. | `Es` bắt buộc hồi chỉ `Das Glas`. |
| `A1-MAIN-0357` | kennenlernen | 18 | `MERGE` | Wir sind neu hier. Wir möchten Sie kennenlernen. | Một lời giới thiệu liên tục trong cùng ngữ cảnh. |
| `A1-MAIN-0380` | kulturell | 19 | `MERGE` | Ich bin kulturell interessiert. Ich gehe oft ins Museum. | Câu hai minh họa trực tiếp câu một. |
| `A1-MAIN-0382` | Kunde | 19 | `MERGE` | Einen Moment, bitte. Ich habe eine Kundin. | A2 có đúng chuỗi ghép và canonical translation/audio. |
| `A1-MAIN-0392` | laut | 19 | `MERGE` | Nicht so laut! Das Baby schläft. | Mệnh lệnh và lý do; A2 giữ đúng cặp. |
| `A1-MAIN-0396` | ledig | 19 | `MERGE` | Sind Sie verheiratet?<br>– Nein. Ledig. | Một lượt hỏi–đáp hoàn chỉnh. |
| `A1-MAIN-0400` | leider | 19 | `MERGE` | Leider kann ich nicht kommen. Ich muss zum Arzt. | Câu hai nêu lý do; A2/B1 giữ đúng cặp. |
| `A1-MAIN-0401` | leise | 19 | `MERGE` | Seid leise. Die anderen schlafen schon. | Mệnh lệnh và lý do; A2/B1 giữ đúng cặp. |
| `A1-MAIN-0417` | lustig | 19 | `MERGE` | Frau Mertens ist lustig. Sie lacht immer. | `Sie` hồi chỉ Frau Mertens và giải thích nhận xét. |
| `A1-MAIN-0433` | mitbringen | 20 | `MERGE` | Ich gehe einkaufen. Soll ich dir was mitbringen? | Câu hỏi phụ thuộc ngữ cảnh đi mua sắm; A2 giữ đúng cặp. |
| `A1-MAIN-0434` | mitkommen | 20 | `MERGE` | Ich gehe ins Kino. Kommst du mit? | Lời mời phụ thuộc câu trước; A2 giữ đúng cặp. |
| `A1-MAIN-0444` | müde | 20 | `MERGE` | Ich bin müde. Ich gehe schlafen. | Trạng thái dẫn đến hành động; A2/B1 giữ đúng cặp. |
| `A1-MAIN-0451` | nehmen | 20 | `MERGE` | Heute gibt es Hähnchen. Das nehme ich. | `Das` bắt buộc hồi chỉ `Hähnchen`. |
| `A1-MAIN-0455` | nichts | 20 | `MERGE` | Hier kaufe ich nichts. Der Laden gefällt mir nicht. | Câu hai giải thích câu một; A2 giữ đúng cặp. |
| `A1-MAIN-0458` | normal | 21 | `MERGE` | 75 kg. Sein Gewicht ist normal. | `75 kg.` chỉ có nghĩa trong câu đánh giá kế tiếp. |
| `A1-MAIN-0582` | Taxi | 24 | `MERGE` | Es gibt heute keinen Bus mehr. Er fährt mit dem Taxi. | Câu hai là giải pháp cho việc không còn xe buýt. |
| `A1-MAIN-0599` | Treppe | 24 | `MERGE` | Die Toilette? Die Treppe hoch und dann links. | Một cặp hỏi–đáp tỉnh lược; A2/B1 xác nhận. |
| `A1-MAIN-0625` | Vermieter | 25 | `MERGE` | Unser Vermieter heißt Huber. Er wohnt auch hier. | `Er` bắt buộc hồi chỉ `Unser Vermieter`. |
| `A1-MAIN-0633` | Vorsicht | 25 | `MERGE` | Vorsicht! Da kommt ein Auto. | Câu hai nêu nguy cơ làm phát sinh cảnh báo. |
| `A1-MAIN-0644` | wehtun | 25 | `MERGE` | Ich muss zum Arzt. Mein Bein tut weh. | Đau chân là lý do đi bác sĩ; chỉ câu hai chứa lexeme. |

All 38 candidates have a final decision: `MERGE=38`, `KEEP_SPLIT=0`.
