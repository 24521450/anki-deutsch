"""Safely migrate the live Goethe A1/A2 notes to the Goethe Werkstatt model.

This tool never edits collection.anki2. Model changes and note updates go through
AnkiConnect; changing note type remains an explicit Anki Desktop GUI step.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
LEGACY_INPUTS = ROOT / "tools" / ".goethe_werkstatt" / "legacy-inputs"
EXPORT = LEGACY_INPUTS / "Goethe Institute.txt"
BACKUP = LEGACY_INPUTS / "Goethe Institute.apkg"
SOURCE_A1 = ROOT / "sources" / "goethe" / "Goethe_A1.md"
SOURCE_A2 = ROOT / "sources" / "goethe" / "Goethe_A2.md"
SOURCE_B1 = ROOT / "sources" / "goethe" / "Goethe_B1.md"
A1_WORD_MANIFEST = ROOT / "audio" / "a1" / "words_manifest.jsonl"
OVERRIDES = ROOT / "review" / "goethe_werkstatt_overrides.json"
DESIGN = ROOT / "design" / "GoetheWerkstatt"
STATE_DIR = ROOT / "tools" / ".goethe_werkstatt"
ANKI_URL = "http://127.0.0.1:8765"

MODEL = "Goethe Werkstatt"
A1_MODEL = "Goethe Vocab List"
A2_MODEL = "Basic (and reversed card)-75aea"
A1_DECK = "Goethe Institute::A1 Wordlist"
A2_DECK = "Goethe Institute::A2 Wordlist"
B1_DECK = "Goethe Institute::B1 Wordlist"
EXPECTED_NOTES = {A1_DECK: 925, A2_DECK: 656}
EXPECTED_CARDS = {A1_DECK: 1850, A2_DECK: 1312}
EXPECTED_BACKUP_SHA256 = "54b786c84bc5ed0d8205fc263eb4432ea4728678ae106952343bf7b8f1489fc3"

FIELDS = [
    "Lemma", "MeaningEN", "CEFR", "POS", "Article", "Gender",
    "NounFormsRaw", "VerbFormsRaw", "FormOrVariantNote", "UsageNoteEN",
    "RegionalVariants", "AcceptedAnswersDE", "AcceptedArticlesDE", "WordAudio",
    "Example1DE", "Example1EN", "Example1Audio",
    "Example2DE", "Example2EN", "Example2Audio",
    "Example3DE", "Example3EN", "Example3Audio",
    "Example4DE", "Example4EN", "Example4Audio",
    "MoreExamplesHTML", "SourceID", "SourceRefs", "OriginalOrder", "SourceNoteRaw", "LegacyGUID",
    "AcceptedFullAnswersDE", "ProductionEnabled", "ProductionHint", "ExampleTargetSpansJSON",
]
ADDITIVE_FIELDS = ("AcceptedFullAnswersDE", "ProductionEnabled", "ProductionHint", "ExampleTargetSpansJSON")

PILOT_A1 = [
    1584886454452, 1584886454486, 1584886454531, 1584886455241,
    1584886454930, 1584886454804, 1584886454929, 1584886454529,
    1584887177209, 1584887177204,
]
PILOT_A2 = [
    1497484860721, 1497484861228, 1497484860918, 1497484861704,
    1497484860720, 1497484860730, 1497484861168, 1497484861331,
    1497484860783, 1497484861655,
]
PILOT_IDS = PILOT_A1 + PILOT_A2

A1_FIELD_MAP = {
    "Lemma": "de_word", "MeaningEN": "en_word", "UsageNoteEN": "en_note",
    "Example1DE": "de_sentence", "Example1EN": "en_sentence",
    "Example1Audio": "de_audio", "SourceID": "Note ID",
}
A2_FIELD_MAP = {
    "Lemma": "Wort_DE", "MeaningEN": "Wort_EN", "Article": "Artikel",
    "NounFormsRaw": "Plural", "FormOrVariantNote": "Hinweis",
    "VerbFormsRaw": "Verbformen", "WordAudio": "Audio_Wort",
    "OriginalOrder": "Original_Order",
    **{f"Example{index}DE": f"Satz{index}_DE" for index in range(1, 5)},
    **{f"Example{index}EN": f"Satz{index}_EN" for index in range(1, 5)},
    **{f"Example{index}Audio": f"Audio_S{index}" for index in range(1, 5)},
}
TEMPLATE_MAP = {"German → English": "Card 1", "English → German": "Card 2"}

SCHEDULE_KEYS = [
    "cardId", "note", "ord", "deckName", "factor", "interval", "type",
    "queue", "due", "reps", "lapses", "left", "flags",
]


class MigrationError(RuntimeError):
    pass


def anki(action: str, *, request_timeout: float = 30, **params: Any) -> Any:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    request = urllib.request.Request(ANKI_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MigrationError(f"AnkiConnect unavailable: {exc}") from exc
    if result.get("error") is not None:
        raise MigrationError(f"AnkiConnect {action}: {result['error']}")
    return result.get("result")


def chunks(values: list[Any], size: int = 100) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_backup() -> None:
    if not BACKUP.exists():
        raise MigrationError(f"backup missing: {BACKUP}")
    actual = sha256_file(BACKUP)
    if actual != EXPECTED_BACKUP_SHA256:
        raise MigrationError(f"backup SHA-256 changed: {actual}")


def field_value(note: dict[str, Any], name: str) -> str:
    return note.get("fields", {}).get(name, {}).get("value", "")


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value).strip()).casefold()


def normalize_answer(value: str) -> str:
    value = unicodedata.normalize("NFC", html.unescape(str(value or ""))).casefold().replace("’", "'")
    value = re.sub(r"\s+", " ", value.strip())
    value = re.sub(r"[.!?]+$", "", value)
    return value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def split_accepted(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


LONG_STATE_SEIN_CORES = {
    normalize_answer(value)
    for value in (
        "dabei", "dagegen", "dafür", "einverstanden", "erkältet", "erlaubt",
        "fertig", "fit", "gültig", "unterwegs", "verabredet", "verboten",
    )
}


def accepted_answer_variants(answer: str) -> list[str]:
    variants = [answer]
    if normalize_answer(answer).endswith(" sein"):
        core = answer.rsplit(None, 1)[0]
        if normalize_answer(core) in LONG_STATE_SEIN_CORES:
            variants.append(core)
    return variants


def answer_is_correct(raw: str, lemma: str, accepted_answers: str = "", accepted_articles: str = "") -> bool:
    answers = split_accepted(accepted_answers) or [lemma]
    articles = split_accepted(accepted_articles)
    variants = [variant for answer in answers for variant in accepted_answer_variants(answer)]
    expected = {normalize_answer(answer) for answer in variants}
    expected.update(normalize_answer(f"{article} {answer}") for article in articles for answer in variants)
    return bool(raw.strip()) and normalize_answer(raw) in expected


def parse_example_cell(value: str) -> list[str]:
    examples: list[str] = []
    for part in (item.strip() for item in re.split(r"<br\s*/?>", value, flags=re.I)):
        if not part:
            continue
        if examples and re.match(r"^[–—-]\s*", part):
            examples[-1] += "<br>" + part
        else:
            examples.append(part)
    return examples


def parse_markdown(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| **"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 6:
            raise MigrationError(f"unexpected markdown row: {line}")
        rows.append({
            "row": len(rows) + 1,
            "word": cells[0].strip("*"),
            "pos": cells[1],
            "gender": cells[2],
            "cefr": cells[3],
            "examples": parse_example_cell(cells[4]),
            "note": cells[5],
        })
    return rows


def source_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[normalize_key(row["word"])].append(row)
    return result


def parse_articles(value: str) -> list[str]:
    match = re.match(r"^((?:der|die|das)(?:/(?:der|die|das))*)\s+(.+)$", value.strip(), re.I)
    return match.group(1).lower().split("/") if match else []


def parse_a1_lexeme(raw: str) -> dict[str, str]:
    value = re.sub(r"\s+", " ", raw.strip())
    articles = parse_articles(value)
    if articles:
        value = re.sub(r"^(?:der|die|das)(?:/(?:der|die|das))*\s+", "", value, flags=re.I)
    forms = ""
    if "," in value:
        value, forms = value.split(",", 1)
        value, forms = value.strip(), forms.strip()
    plural_match = re.search(r"\s+\((?:pl\.|Pl\.)\)$", value)
    if plural_match:
        value = value[:plural_match.start()].strip()
        forms = forms or "(Pl.)"
    article = articles[0] if len(articles) == 1 else "/".join(articles)
    return {
        "Lemma": value,
        "Article": article,
        "AcceptedArticlesDE": "|".join(articles),
        "NounFormsRaw": forms,
    }


def gender_from_articles(articles: list[str]) -> str:
    mapping = {"der": "m.", "die": "f.", "das": "n."}
    genders = [mapping[article] for article in articles if article in mapping]
    return "/".join(gender.rstrip(".") for gender in genders) + ("." if genders else "")


def manual_audio(sound_tag: str) -> str:
    if not sound_tag:
        return ""
    match = re.fullmatch(r"\[sound:([^\]]+)]", sound_tag.strip())
    if not match:
        raise MigrationError(f"unsupported audio field: {sound_tag!r}")
    filename = html.escape(match.group(1), quote=True)
    return f'<audio class="gw-example-player" controls preload="none" src="{filename}"></audio>'


def parse_export() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows = list(csv.reader(
        [line for line in EXPORT.read_text(encoding="utf-8-sig").splitlines() if line and not line.startswith("#")],
        delimiter="\t",
    ))
    if any(len(row) != 24 for row in rows):
        raise MigrationError("export contains a non-24-column row")
    a1, a2 = [], []
    for row in rows:
        if row[2] == A1_DECK:
            a1.append({
                "guid": row[0], "legacy_id": row[3], "de_word": row[4],
                "de_sentence": row[5], "en_word": row[6], "en_sentence": row[7],
                "en_note": row[8], "de_audio": row[9],
            })
        elif row[2] == A2_DECK:
            item = {
                "guid": row[0], "word": row[3], "meaning": row[4], "article": row[5],
                "plural": row[6], "hint": row[7], "verb_forms": row[8],
                "original_order": row[17], "word_audio": row[18],
            }
            for index in range(4):
                item[f"example{index + 1}_de"] = row[9 + index * 2]
                item[f"example{index + 1}_en"] = row[10 + index * 2]
                item[f"example{index + 1}_audio"] = row[19 + index]
            a2.append(item)
    if len(a1) != EXPECTED_NOTES[A1_DECK] or len(a2) != EXPECTED_NOTES[A2_DECK]:
        raise MigrationError(f"export counts changed: A1={len(a1)}, A2={len(a2)}")
    return a1, a2


def load_overrides() -> dict[str, Any]:
    return json.loads(OVERRIDES.read_text(encoding="utf-8"))


def load_word_audio() -> dict[int, dict[str, Any]]:
    if not A1_WORD_MANIFEST.exists():
        return {}
    result = {}
    for line in A1_WORD_MANIFEST.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        path = Path(item["output_path"])
        if path.exists() and path.stat().st_size > 0:
            result[int(item["row"])] = {"path": str(path), "source_name": item["output_filename"]}
    return result


def unique_source_match(index: dict[str, list[dict[str, Any]]], lemma: str, articles: list[str]) -> dict[str, Any] | None:
    candidates = index.get(normalize_key(lemma), [])
    if articles:
        expected = set(gender_from_articles(articles).replace(".", "").split("/"))
        filtered = [row for row in candidates if row["gender"].replace(".", "") in expected]
        if filtered:
            candidates = filtered
    return candidates[0] if len(candidates) == 1 else None


def empty_fields() -> dict[str, str]:
    return {field: "" for field in FIELDS}


def build_a1_fields(
    item: dict[str, str], order: int, source: dict[str, list[dict[str, Any]]],
    overrides: dict[str, Any], word_audio: dict[int, dict[str, Any]],
) -> tuple[dict[str, str], list[str], dict[str, Any] | None]:
    fields = empty_fields()
    parsed = parse_a1_lexeme(item["de_word"])
    fields.update(parsed)
    articles = parse_articles(item["de_word"])
    match = unique_source_match(source, fields["Lemma"], articles)
    fields.update({
        "MeaningEN": item["en_word"], "CEFR": "A1",
        "POS": match["pos"] if match else ("n." if articles else ""),
        "Gender": match["gender"] if match else gender_from_articles(articles),
        "UsageNoteEN": item["en_note"], "AcceptedAnswersDE": fields["Lemma"],
        "Example1DE": item["de_sentence"], "Example1EN": item["en_sentence"],
        "Example1Audio": manual_audio(item["de_audio"]),
        "SourceID": f"A1-{item['legacy_id']}", "OriginalOrder": str(order),
        "SourceNoteRaw": item["de_word"], "LegacyGUID": item["guid"],
    })
    if match:
        fields["SourceNoteRaw"] = match["note"] or item["de_word"]
    # The sole safe missing-example enrichment established during review.
    if item["legacy_id"] == "84887177204" and match:
        fields["Example1DE"] = match["examples"][0]
    override = overrides.get("a1_legacy", {}).get(item["legacy_id"], {})
    fields.update({key: str(value) for key, value in override.items()})
    tags = ["goethe::level::a1", "goethe::migration::migrated"]
    if not match:
        tags.append("goethe::quality::review_needed")
    audio = word_audio.get(match["row"]) if match else None
    if audio:
        filename = f"_goethe_a1_word_{match['row']:04d}_{audio['source_name']}"
        fields["WordAudio"] = f"[sound:{filename}]"
        audio = {**audio, "target_name": filename}
    if "/" in fields["Lemma"] and not override:
        tags.append("goethe::quality::review_needed")
    return fields, sorted(set(tags)), audio


def build_a2_fields(
    item: dict[str, str], source: dict[str, list[dict[str, Any]]], overrides: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    fields = empty_fields()
    match = unique_source_match(source, item["word"], [item["article"]] if item["article"] else [])
    articles = [item["article"]] if item["article"] in {"der", "die", "das"} else []
    fields.update({
        "Lemma": item["word"], "MeaningEN": item["meaning"], "CEFR": "A2",
        "POS": match["pos"] if match else ("n." if articles else ""),
        "Article": item["article"],
        "Gender": match["gender"] if match else gender_from_articles(articles),
        "NounFormsRaw": item["plural"], "VerbFormsRaw": item["verb_forms"],
        "FormOrVariantNote": item["hint"], "AcceptedAnswersDE": item["word"],
        "AcceptedArticlesDE": "|".join(articles), "WordAudio": item["word_audio"],
        "SourceID": f"A2-{int(item['original_order']):04d}",
        "OriginalOrder": item["original_order"], "LegacyGUID": item["guid"],
        "SourceNoteRaw": json.dumps({
            "Wort_DE": item["word"], "Artikel": item["article"], "Plural": item["plural"],
            "Hinweis": item["hint"], "Verbformen": item["verb_forms"],
        }, ensure_ascii=False, separators=(",", ":")),
    })
    for index in range(1, 5):
        fields[f"Example{index}DE"] = item[f"example{index}_de"]
        fields[f"Example{index}EN"] = item[f"example{index}_en"]
        fields[f"Example{index}Audio"] = manual_audio(item[f"example{index}_audio"])
    override = overrides.get("a2_order", {}).get(item["original_order"], {})
    fields.update({key: str(value) for key, value in override.items()})
    tags = ["goethe::level::a2", "goethe::migration::migrated"]
    if not match or ("/" in fields["Lemma"] and not override):
        tags.append("goethe::quality::review_needed")
    return fields, sorted(set(tags))


def live_notes() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    a1_ids = anki("findNotes", query=f'deck:"{A1_DECK}"')
    a2_ids = anki("findNotes", query=f'deck:"{A2_DECK}"')
    a1, a2 = [], []
    for batch in chunks(a1_ids):
        a1.extend(anki("notesInfo", notes=batch))
    for batch in chunks(a2_ids):
        a2.extend(anki("notesInfo", notes=batch))
    return a1, a2


def build_manifest() -> dict[str, Any]:
    a1_export, a2_export = parse_export()
    a1_live, a2_live = live_notes()
    a1_by_legacy = {field_value(note, "Note ID"): note for note in a1_live}
    a2_by_order = {field_value(note, "Original_Order"): note for note in a2_live}
    if len(a1_by_legacy) != 925 or len(a2_by_order) != 656:
        raise MigrationError("live source models/keys changed; build manifest before GUI conversion")
    overrides = load_overrides()
    source_a1 = source_index(parse_markdown(SOURCE_A1))
    source_a2 = source_index(parse_markdown(SOURCE_A2))
    word_audio = load_word_audio()
    notes: dict[str, Any] = {}
    source_ids: set[str] = set()
    for order, item in enumerate(a1_export, 1):
        live = a1_by_legacy.get(item["legacy_id"])
        if not live:
            raise MigrationError(f"A1 legacy key not live: {item['legacy_id']}")
        fields, tags, audio = build_a1_fields(item, order, source_a1, overrides, word_audio)
        notes[str(live["noteId"])] = {"source_model": A1_MODEL, "fields": fields, "tags": tags, "word_audio": audio}
        if int(live["noteId"]) in PILOT_A1:
            tags.append("goethe::migration::pilot")
        source_ids.add(fields["SourceID"])
    for item in a2_export:
        live = a2_by_order.get(item["original_order"])
        if not live:
            raise MigrationError(f"A2 order not live: {item['original_order']}")
        fields, tags = build_a2_fields(item, source_a2, overrides)
        notes[str(live["noteId"])] = {"source_model": A2_MODEL, "fields": fields, "tags": tags, "word_audio": None}
        if int(live["noteId"]) in PILOT_A2:
            tags.append("goethe::migration::pilot")
        source_ids.add(fields["SourceID"])
    if len(notes) != 1581 or len(source_ids) != 1581:
        raise MigrationError(f"manifest uniqueness failed: notes={len(notes)} SourceIDs={len(source_ids)}")
    for note_id, item in notes.items():
        if not item["fields"]["Lemma"] or not item["fields"]["SourceID"]:
            raise MigrationError(f"required field empty: note {note_id}")
        for field in ("AcceptedAnswersDE", "AcceptedArticlesDE"):
            value = item["fields"][field]
            if value and any(not part.strip() for part in value.split("|")):
                raise MigrationError(f"empty accepted item: note {note_id} field {field}")
    return {"version": 1, "created_utc": datetime.now(timezone.utc).isoformat(), "notes": notes}


def state_path(name: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / name


def write_manifest(manifest: dict[str, Any]) -> Path:
    path = state_path("manifest.json")
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_manifest() -> dict[str, Any]:
    path = state_path("manifest.json")
    if not path.exists():
        raise MigrationError("manifest missing; run preflight before the GUI step")
    return json.loads(path.read_text(encoding="utf-8"))


def all_card_info() -> list[dict[str, Any]]:
    cards = []
    for deck in (A1_DECK, A2_DECK, B1_DECK):
        cards.extend(anki("findCards", query=f'deck:"{deck}"'))
    result = []
    for batch in chunks(cards, 100):
        result.extend(anki("cardsInfo", cards=batch))
    return result


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def command_snapshot(_: argparse.Namespace) -> None:
    check_backup()
    a1_notes, a2_notes = live_notes()
    cards = all_card_info()
    card_ids = sorted(card["cardId"] for card in cards)
    reviews: dict[str, Any] = {}
    for batch in chunks(card_ids, 250):
        reviews.update(anki("getReviewsOfCards", cards=batch))
    snapshot = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "backup_sha256": EXPECTED_BACKUP_SHA256,
        "notes": sorted(a1_notes + a2_notes, key=lambda note: note["noteId"]),
        "cards": sorted(cards, key=lambda card: card["cardId"]),
        "reviews": reviews,
    }
    snapshot["reviews_sha256"] = canonical_hash(reviews)
    path = state_path("snapshot.json")
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"snapshot={path}")
    print(f"notes={len(snapshot['notes'])} cards={len(cards)} reviews_sha256={snapshot['reviews_sha256']}")


def command_preflight(_: argparse.Namespace) -> None:
    check_backup()
    if anki("version") != 6:
        raise MigrationError("unexpected AnkiConnect API version")
    for deck, expected in EXPECTED_NOTES.items():
        actual = len(anki("findNotes", query=f'deck:"{deck}"'))
        if actual != expected:
            raise MigrationError(f"{deck}: expected {expected} notes, got {actual}")
    for deck, expected in EXPECTED_CARDS.items():
        actual = len(anki("findCards", query=f'deck:"{deck}"'))
        if actual != expected:
            raise MigrationError(f"{deck}: expected {expected} cards, got {actual}")
    manifest = build_manifest()
    path = write_manifest(manifest)
    tag_counts = Counter(tag for note in manifest["notes"].values() for tag in note["tags"])
    print(f"manifest={path}")
    print(f"notes={len(manifest['notes'])} pilot={len(PILOT_IDS)}")
    print(f"tags={dict(sorted(tag_counts.items()))}")
    print("preflight PASS")


def templates() -> dict[str, Any]:
    highlighter = (DESIGN / "target_highlighter.js").read_text(encoding="utf-8")
    example_audio = (DESIGN / "example_audio.js").read_text(encoding="utf-8")
    word_audio = (DESIGN / "word_audio.js").read_text(encoding="utf-8")
    return {
        "German → English": {
            "Front": (DESIGN / "front_german.html").read_text(encoding="utf-8").replace("{{WordAudioController}}", word_audio),
            "Back": (DESIGN / "back_german.html").read_text(encoding="utf-8").replace("{{TargetHighlighter}}", highlighter).replace("{{ExampleAudioController}}", example_audio).replace("{{WordAudioController}}", word_audio),
        },
        "English → German": {
            "Front": (DESIGN / "front_english.html").read_text(encoding="utf-8"),
            "Back": (DESIGN / "back_english.html").read_text(encoding="utf-8").replace("{{TargetHighlighter}}", highlighter).replace("{{ExampleAudioController}}", example_audio).replace("{{WordAudioController}}", word_audio),
        },
    }


def command_create_model(_: argparse.Namespace) -> None:
    check_backup()
    names = anki("modelNames")
    model_templates = templates()
    css = (DESIGN / "styling.css").read_text(encoding="utf-8")
    if MODEL not in names:
        anki("createModel", modelName=MODEL, inOrderFields=FIELDS, css=css, cardTemplates=[
            {"Name": name, "Front": value["Front"], "Back": value["Back"]}
            for name, value in model_templates.items()
        ])
        print(f"created model: {MODEL}")
    else:
        actual = anki("modelFieldNames", modelName=MODEL)
        legacy_fields = [field for field in FIELDS if field not in {"MoreExamplesHTML", "SourceRefs"}]
        if actual == legacy_fields:
            for field in ("MoreExamplesHTML", "SourceRefs"):
                anki("modelFieldAdd", modelName=MODEL, fieldName=field, index=FIELDS.index(field))
            actual = anki("modelFieldNames", modelName=MODEL)
        old_fields = [field for field in FIELDS if field not in ADDITIVE_FIELDS]
        if actual == old_fields:
            for field in ADDITIVE_FIELDS:
                anki("modelFieldAdd", modelName=MODEL, fieldName=field, index=FIELDS.index(field))
            actual = anki("modelFieldNames", modelName=MODEL)
        if actual != FIELDS:
            raise MigrationError(f"existing {MODEL} field order differs")
        anki("updateModelTemplates", model={"name": MODEL, "templates": model_templates})
        anki("updateModelStyling", model={"name": MODEL, "css": css})
        print(f"updated templates/styles: {MODEL}")


def _source_ids(source: str, scope: str) -> list[int]:
    if scope == "pilot":
        return list(PILOT_A1 if source == "a1" else PILOT_A2)
    model = A1_MODEL if source == "a1" else A2_MODEL
    deck = A1_DECK if source == "a1" else A2_DECK
    return sorted(anki("findNotes", query=f'deck:"{deck}" note:"{model}"'))


def command_change_type(args: argparse.Namespace) -> None:
    check_backup()
    batches = [
        ("a1", A1_MODEL, A1_FIELD_MAP),
        ("a2", A2_MODEL, A2_FIELD_MAP),
    ]
    dry_runs = []
    for source, expected_model, field_map in batches:
        note_ids = _source_ids(source, args.scope)
        if not note_ids:
            raise MigrationError(f"no {source.upper()} source notes found for scope={args.scope}")
        info = []
        for batch in chunks(note_ids):
            info.extend(anki("notesInfo", notes=batch))
        wrong = [note["noteId"] for note in info if note["modelName"] != expected_model]
        if wrong:
            raise MigrationError(f"{source.upper()} notes already changed or wrong model: {wrong[:10]}")
        result = anki(
            "changeNoteTypeSafe", noteIds=note_ids, newModelName=MODEL,
            fieldMap=field_map, templateMap=TEMPLATE_MAP, dryRun=True,
        )
        expected_cards = len(note_ids) * 2
        if result["noteCount"] != len(note_ids) or result["cardCount"] != expected_cards:
            raise MigrationError(f"{source.upper()} dry-run count mismatch: {result}")
        dry_runs.append((source, note_ids, field_map, result))
        print(f"dry-run {source.upper()} PASS notes={len(note_ids)} cards={expected_cards}")
    if args.dry_run:
        return
    for source, note_ids, field_map, _ in dry_runs:
        result = anki(
            "changeNoteTypeSafe", noteIds=note_ids, newModelName=MODEL,
            fieldMap=field_map, templateMap=TEMPLATE_MAP, dryRun=False,
            confirmation="CHANGE_NOTE_TYPE_SAFE",
        )
        if not result.get("cardIdsUnchanged"):
            raise MigrationError(f"{source.upper()} bridge did not confirm card IDs")
        print(f"changed {source.upper()} notes={len(note_ids)} cards={result['cardCount']}")


def ensure_media(item: dict[str, Any]) -> None:
    audio = item.get("word_audio")
    if not audio:
        return
    data = base64.b64encode(Path(audio["path"]).read_bytes()).decode("ascii")
    stored = anki("storeMediaFile", filename=audio["target_name"], data=data)
    if stored != audio["target_name"]:
        raise MigrationError(f"unexpected stored media name: {stored}")


def command_populate(args: argparse.Namespace) -> None:
    check_backup()
    manifest = load_manifest()
    selected = PILOT_IDS if args.scope == "pilot" else [int(note_id) for note_id in manifest["notes"]]
    info = []
    for batch in chunks(selected):
        info.extend(anki("notesInfo", notes=batch))
    models = {note["noteId"]: note["modelName"] for note in info}
    wrong = [note_id for note_id in selected if models.get(note_id) != MODEL]
    if wrong:
        raise MigrationError(f"GUI Change Note Type not complete; first wrong note IDs: {wrong[:10]}")
    actions = []
    for note_id in selected:
        item = manifest["notes"][str(note_id)]
        ensure_media(item)
        actions.append({"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": item["fields"]}}})
        actions.append({"action": "addTags", "params": {"notes": [note_id], "tags": " ".join(item["tags"])}})
    for batch in chunks(actions, 80):
        results = anki("multi", actions=batch)
        errors = [
            result.get("error") for result in results
            if isinstance(result, dict) and result.get("error")
        ]
        if errors:
            raise MigrationError(f"populate errors: {errors[:3]}")
    print(f"populated={len(selected)} scope={args.scope}")


def schedule_projection(card: dict[str, Any]) -> dict[str, Any]:
    return {key: card.get(key) for key in SCHEDULE_KEYS}


def command_verify(args: argparse.Namespace) -> None:
    snapshot_path = state_path("snapshot.json")
    if not snapshot_path.exists():
        raise MigrationError("snapshot missing")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    before_cards = {card["cardId"]: card for card in snapshot["cards"]}
    current_cards = all_card_info()
    after_cards = {card["cardId"]: card for card in current_cards}
    if set(before_cards) != set(after_cards):
        raise MigrationError("card ID set changed")
    if args.scope == "pilot":
        compared_note_ids = set(PILOT_IDS)
        compared_card_ids = {
            card_id for card_id, card in before_cards.items()
            if card["note"] in compared_note_ids
        }
    else:
        compared_card_ids = set(before_cards)
    mismatches = []
    for card_id in sorted(compared_card_ids):
        if schedule_projection(before_cards[card_id]) != schedule_projection(after_cards[card_id]):
            mismatches.append(card_id)
    if mismatches:
        raise MigrationError(f"scheduling changed for cards: {mismatches[:10]}")
    note_ids = []
    for deck, expected in EXPECTED_NOTES.items():
        found = anki("findNotes", query=f'deck:"{deck}"')
        if len(found) != expected:
            raise MigrationError(f"note count changed in {deck}")
        note_ids.extend(found)
    target_expected = len(PILOT_IDS) if args.scope == "pilot" else 1581
    target_actual = len(anki("findNotes", query=f'note:"{MODEL}"'))
    if target_actual != target_expected:
        raise MigrationError(f"target model expected {target_expected}, got {target_actual}")
    reviews: dict[str, Any] = {}
    for batch in chunks(sorted(compared_card_ids), 250):
        reviews.update(anki("getReviewsOfCards", cards=batch))
    before_reviews = {
        str(card_id): snapshot["reviews"].get(str(card_id), [])
        for card_id in compared_card_ids
    }
    if canonical_hash(reviews) != canonical_hash(before_reviews):
        raise MigrationError("review history hash changed")
    print(f"verify PASS scope={args.scope} notes={len(note_ids)} cards={len(after_cards)} target={target_actual}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="build the full dry-run manifest").set_defaults(func=command_preflight)
    sub.add_parser("snapshot", help="capture IDs, scheduling and review history").set_defaults(func=command_snapshot)
    sub.add_parser("create-model", help="create/update the target model").set_defaults(func=command_create_model)
    change = sub.add_parser("change-type", help="guarded note-type conversion via the local bridge")
    change.add_argument("--scope", choices=("pilot", "full"), required=True)
    change.add_argument("--dry-run", action="store_true")
    change.set_defaults(func=command_change_type)
    for name, function in (("populate", command_populate), ("verify", command_verify)):
        child = sub.add_parser(name)
        child.add_argument("--scope", choices=("pilot", "full"), required=True)
        child.set_defaults(func=function)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
