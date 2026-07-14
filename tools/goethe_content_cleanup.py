"""Compile and safely apply the reviewed Goethe A1/A2 content cleanup."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import export_goethe_notes_jsonl as export_jsonl
import goethe_examples
import goethe_completion as completion
import goethe_werkstatt_migrate as gw


ROOT = gw.ROOT
DEFAULT_AUDIT = ROOT.parent / "Goethe_English_Audit.md"
MANIFEST = ROOT / "review" / "goethe_content_corrections.json"
STATE = ROOT / "tools" / ".goethe_content_cleanup"
SNAPSHOT = STATE / "snapshot.json"
MODEL = gw.MODEL
PARENT_DECK = "Goethe Institute"
BASE_NOTES = 1601
BASE_CARDS = 3202
FINAL_NOTES = 1596
FINAL_CARDS = 3192
VERIFIED_TAG = "goethe::quality::english_verified::british"
TRANSLATION_TAG = "goethe::quality::translation_review_needed"
DELETE_TAG = "goethe::quality::delete_after_english_audit"
APPLY_CONFIRMATION = "APPLY_GOETHE_CONTENT_CLEANUP"
DELETE_CONFIRMATION = "DELETE_FIVE_AUDITED_GOETHE_NOTES"
ROLLBACK_CONFIRMATION = "ROLLBACK_GOETHE_CONTENT_CLEANUP"
DELETION_MAP = {
    1584886454471: 1584886454470,
    1584886454757: 1584886454756,
    1584886454972: 1584886454971,
    1584886455083: 1584886455084,
    1584886455254: 1584886455253,
}
PILOT_IDS = [
    1497484860724, 1497484860927, 1497484861212, 1497484861317,
    1497484861333, 1497484861402, 1497484861613, 1497484861617,
    1497484861802, 1584886454462, 1584886454487, 1584886454495,
    1584886454658, 1584886454865, 1584886454979, 1584886455069,
    1584886455187, 1584886455222, 1584886455225, 1584886455226,
]
SCHEDULE_KEYS = gw.SCHEDULE_KEYS


class CleanupError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    except Exception:
        if os.path.exists(name):
            os.unlink(name)
        raise


def require_anki() -> None:
    if gw.anki("version") != 6:
        raise CleanupError("unexpected AnkiConnect API version")


def field_values(note: dict[str, Any]) -> dict[str, str]:
    return {name: note.get("fields", {}).get(name, {}).get("value", "") for name in gw.FIELDS}


def live_records(*, with_card_info: bool = False) -> dict[int, dict[str, Any]]:
    require_anki()
    ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(ids):
        notes.extend(gw.anki("notesInfo", notes=batch))
    by_note: dict[int, list[dict[str, Any]]] = {}
    if with_card_info:
        card_ids = [int(card_id) for note in notes for card_id in note.get("cards", [])]
        for batch in gw.chunks(card_ids, 20):
            for card in gw.anki("cardsInfo", cards=batch):
                by_note.setdefault(int(card["note"]), []).append(card)
    else:
        for note in notes:
            note_id = int(note["noteId"])
            by_note[note_id] = [{"cardId": int(card_id), "note": note_id} for card_id in note.get("cards", [])]
    return {
        int(note["noteId"]): {
            "note_id": int(note["noteId"]),
            "model": note["modelName"],
            "fields": field_values(note),
            "tags": sorted(note.get("tags", [])),
            "cards": sorted(by_note.get(int(note["noteId"]), []), key=lambda item: int(item["cardId"])),
        }
        for note in notes
    }


def validate_inventory(records: dict[int, dict[str, Any]], *, final: bool = False) -> None:
    expected_notes = FINAL_NOTES if final else BASE_NOTES
    expected_cards = FINAL_CARDS if final else BASE_CARDS
    cards = sum(len(record["cards"]) for record in records.values())
    if len(records) != expected_notes or cards != expected_cards:
        raise CleanupError(f"expected {expected_notes} notes / {expected_cards} cards, got {len(records)} / {cards}")
    if any(record["model"] != MODEL or len(record["cards"]) != 2 for record in records.values()):
        raise CleanupError("unexpected model or card count")


def fingerprint(record: dict[str, Any]) -> str:
    return canonical_hash({"model": record["model"], "fields": record["fields"], "tags": record["tags"]})


def table_rows(lines: list[str], start: str, end: str) -> list[list[str]]:
    try:
        left, right = lines.index(start), lines.index(end)
    except ValueError as exc:
        raise CleanupError(f"audit section missing: {start}") from exc
    rows = []
    for line in lines[left + 1:right]:
        if not re.match(r"^\|\s*\d", line):
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def parse_audit(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    meanings = table_rows(lines, "## 1. Gloss cần sửa", "## 2. Câu ví dụ cần sửa")
    examples = table_rows(lines, "## 2. Câu ví dụ cần sửa", "## 3. Gloss đang để dấu ba chấm")
    ellipses = table_rows(lines, "## 3. Gloss đang để dấu ba chấm", "## 4. Usage note cần sửa hoặc xóa")
    usage = table_rows(lines, "## 4. Usage note cần sửa hoặc xóa", "## 5. Lỗi dữ liệu và tính nhất quán")
    if (len(meanings), len(examples), len(ellipses), len(usage)) != (101, 237, 16, 16):
        raise CleanupError("audit row counts changed")
    return {"meanings": meanings, "examples": examples, "ellipses": ellipses, "usage": usage}


def examples_from_fields(fields: dict[str, str]) -> list[dict[str, str]]:
    result = []
    for index in range(1, 5):
        if fields[f"Example{index}DE"]:
            result.append({
                "de": fields[f"Example{index}DE"], "en": fields[f"Example{index}EN"],
                "audio": fields[f"Example{index}Audio"],
            })
    result.extend(export_jsonl.overflow_examples(fields["MoreExamplesHTML"]))
    return result


def render_examples(fields: dict[str, str], examples: list[dict[str, str]]) -> None:
    for index in range(1, 5):
        item = examples[index - 1] if index <= len(examples) else {"de": "", "en": "", "audio": ""}
        fields[f"Example{index}DE"] = item["de"]
        fields[f"Example{index}EN"] = item["en"]
        fields[f"Example{index}Audio"] = item.get("audio", "")
    fields["MoreExamplesHTML"] = goethe_examples.render_overflow(examples[4:])


def normalized_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    for _ in range(3):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", value).strip())


BRITISH_REPLACEMENTS = [
    (r"\bcell phones\b", "mobile phones"), (r"\bcell phone\b", "mobile phone"),
    (r"\bvacations\b", "holidays"), (r"\bvacation\b", "holiday"),
    (r"\bmovies\b", "films"), (r"\bmovie\b", "film"),
    (r"\bapartments\b", "flats"), (r"\bapartment\b", "flat"),
    (r"\belevators\b", "lifts"), (r"\belevator\b", "lift"),
    (r"\bfries\b", "chips"), (r"\bgarbage\b", "rubbish"),
    (r"\btrash\b", "rubbish"), (r"\bsweaters\b", "jumpers"),
    (r"\bsweater\b", "jumper"), (r"\bin the fall\b", "in the autumn"),
    (r"\bdriver's license\b", "driving licence"),
    (r"\bdriving license\b", "driving licence"),
    (r"\bfavorites\b", "favourites"), (r"\bfavorite\b", "favourite"),
    (r"\bcolors\b", "colours"), (r"\bcolor\b", "colour"),
    (r"\bneighbors\b", "neighbours"), (r"\bneighbor\b", "neighbour"),
    (r"\btheaters\b", "theatres"), (r"\btheater\b", "theatre"),
    (r"\bcenters\b", "centres"), (r"\bcenter\b", "centre"),
    (r"\borganizing\b", "organising"), (r"\borganized\b", "organised"),
    (r"\borganize\b", "organise"), (r"\bprograms\b", "programmes"),
    (r"\bprogram\b", "programme"), (r"\breport cards\b", "school reports"),
    (r"\breport card\b", "school report"), (r"\bpants\b", "trousers"),
    (r"\bto practice\b", "to practise"), (r"\bI practice\b", "I practise"),
    (r"\byou practice\b", "you practise"), (r"\bwe practice\b", "we practise"),
    (r"\bthey practice\b", "they practise"),
]


def britishise(value: str) -> str:
    value = normalized_text(value)
    value = re.sub(r"\bcan not\b", "cannot", value, flags=re.I)
    for pattern, replacement in BRITISH_REPLACEMENTS:
        value = re.sub(pattern, replacement, value, flags=re.I)
    value = re.sub(r"\bcomputer programmes\b", "computer programs", value, flags=re.I)
    value = re.sub(r"\bcomputer programme\b", "computer program", value, flags=re.I)
    return value


def find_example(examples: list[dict[str, str]], german: str, english: str) -> dict[str, str]:
    german_key, english_key = normalized_text(german), normalized_text(english)
    matches = [item for item in examples if normalized_text(item["de"]) == german_key and normalized_text(item["en"]) == english_key]
    if len(matches) != 1:
        raise CleanupError(f"expected one example match, got {len(matches)}: {german!r} / {english!r}")
    return matches[0]


MEANING_OVERRIDES = {
    1497484860927: "yes (contradicting a negative); modal particle",
    1497484861246: "knowledge; skills",
    1497484861286: "to give notice; to quit",
    1497484861317: "desire; inclination",
    1497484861333: "just (modal particle)",
    1497484861402: "remedy; means",
    1497484861617: "oneself; personally",
}


def compile_manifest(audit_path: Path) -> dict[str, Any]:
    audit = parse_audit(audit_path)
    records = live_records()
    validate_inventory(records)
    desired = {note_id: copy.deepcopy(record["fields"]) for note_id, record in records.items()}

    for row in audit["meanings"]:
        note_id, current, proposed = int(row[0]), row[3], row[5]
        if normalized_text(desired[note_id]["MeaningEN"]) != normalized_text(current):
            raise CleanupError(f"meaning drift: {note_id}")
        desired[note_id]["MeaningEN"] = proposed
    for row in audit["ellipses"]:
        note_id, proposed = int(row[0]), row[5]
        if desired[note_id]["MeaningEN"] not in {"…", "..."}:
            raise CleanupError(f"ellipsis drift: {note_id}")
        desired[note_id]["MeaningEN"] = proposed
    for row in audit["usage"]:
        note_id, current, proposed = int(row[0]), row[2], row[4]
        if normalized_text(desired[note_id]["UsageNoteEN"]) != normalized_text(current):
            raise CleanupError(f"usage drift: {note_id}")
        desired[note_id]["UsageNoteEN"] = "" if proposed == "Xóa ghi chú" else proposed
    for row in audit["examples"]:
        note_ids = [int(value.strip()) for value in row[1].split(",")]
        for note_id in note_ids:
            examples = examples_from_fields(desired[note_id])
            item = find_example(examples, row[3], row[4])
            item["en"] = row[5]
            render_examples(desired[note_id], examples)

    for note_id, value in MEANING_OVERRIDES.items():
        desired[note_id]["MeaningEN"] = value
    desired[1497484861317]["UsageNoteEN"] = "Lust haben auf = to feel like having or doing something."
    desired[1497484861333]["UsageNoteEN"] = "A modal particle that softens a request or remark."
    desired[1497484860927]["UsageNoteEN"] = "Doch contradicts a negative answer and also adds modal emphasis."

    phrase_updates = {
        1584886455222: ("Wie bitte?", "pardon?"),
        1584886455225: ("Auf Wiederhören!", "goodbye (on the phone)"),
        1584886455226: ("Auf Wiedersehen!", "goodbye"),
    }
    for note_id, (lemma, meaning) in phrase_updates.items():
        fields = desired[note_id]
        fields.update({
            "Lemma": lemma, "MeaningEN": meaning, "POS": "phrase", "Article": "", "Gender": "",
            "AcceptedAnswersDE": lemma, "AcceptedArticlesDE": "", "WordAudio": "",
        })

    mal_examples = examples_from_fields(desired[1497484861333])
    mal_examples = [item for item in mal_examples if normalized_text(item["de"]) not in {
        "Das erste Mal war ich vor fünf Jahren in England.", "Tschüss, bis zum nächsten Mal!",
    }]
    render_examples(desired[1497484861333], mal_examples)

    for note_id, fields in desired.items():
        fields["FormOrVariantNote"] = re.sub(r"^\s*\|\s*", "", fields["FormOrVariantNote"])
        fields["MeaningEN"] = britishise(fields["MeaningEN"])
        fields["UsageNoteEN"] = britishise(fields["UsageNoteEN"])
        examples = examples_from_fields(fields)
        cleaned_examples = []
        for item in examples:
            old_de = item["de"]
            new_de = normalized_text(old_de)
            new_de = new_de.replace("schrecklick", "schrecklich")
            new_de = new_de.replace("Usere Vermiterin", "Unsere Vermieterin")
            audio = item.get("audio", "")
            if new_de != normalized_text(old_de):
                audio = ""
            cleaned_examples.append({"de": new_de, "en": britishise(item["en"]), "audio": audio})
        if note_id == 1584886454865:
            cleaned_examples = [item for item in cleaned_examples if item["de"] not in {"Im Zug fahre ich immer 2.", "Klasse."}]
        render_examples(fields, cleaned_examples)

    for source_id, target_id in DELETION_MAP.items():
        source, target = desired[source_id], desired[target_id]
        target_examples = examples_from_fields(target)
        seen = {normalized_text(item["de"]) for item in target_examples}
        for item in examples_from_fields(source):
            key = normalized_text(item["de"])
            if source_id == 1584886454471 and key.rstrip("!") == "Auf Wiedersehen.":
                continue
            if key not in seen:
                target_examples.append(item)
                seen.add(key)
        render_examples(target, target_examples)
        refs = completion.split_answers(target["SourceRefs"]) + completion.split_answers(source["SourceRefs"])
        target["SourceRefs"] = "|".join(dict.fromkeys(refs))
        answers = completion.split_answers(target["AcceptedAnswersDE"]) + completion.split_answers(source["AcceptedAnswersDE"])
        target["AcceptedAnswersDE"] = "|".join(dict.fromkeys(answers))
    desired[1584886454470]["MeaningEN"] = "on; in; open"

    survivor_ids = sorted(set(records) - set(DELETION_MAP))
    updates = {
        str(note_id): desired[note_id]
        for note_id in survivor_ids if desired[note_id] != records[note_id]["fields"]
    }
    manifest = {
        "schema_version": 1,
        "compiled_utc": now_utc(),
        "audit_file": audit_path.name,
        "audit_sha256": hash_file(audit_path),
        "baseline": {"notes": BASE_NOTES, "cards": BASE_CARDS},
        "target": {"notes": FINAL_NOTES, "cards": FINAL_CARDS, "english": "British"},
        "expected_fingerprints": {str(note_id): fingerprint(record) for note_id, record in records.items()},
        "updates": updates,
        "survivor_ids": survivor_ids,
        "pilot_ids": [note_id for note_id in PILOT_IDS if note_id in survivor_ids],
        "deletions": [
            {
                "note_id": note_id, "target_note_id": target_id,
                "card_ids": [int(card["cardId"]) for card in records[note_id]["cards"]],
                "lemma": records[note_id]["fields"]["Lemma"], "meaning": records[note_id]["fields"]["MeaningEN"],
            }
            for note_id, target_id in DELETION_MAP.items()
        ],
        "counts": {
            "field_updates": len(updates), "meaning_rows": 117,
            "example_rows": 237, "usage_rows": 16, "trimmed_form_notes": 235,
        },
    }
    return manifest


def command_compile(args: argparse.Namespace) -> None:
    audit_path = Path(args.audit).resolve()
    manifest = compile_manifest(audit_path)
    atomic_json(MANIFEST, manifest)
    print(json.dumps({"manifest": str(MANIFEST), **manifest["counts"]}, ensure_ascii=False, indent=2))


def load_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        raise CleanupError("correction manifest missing; run compile")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise CleanupError("unsupported correction manifest")
    return manifest


def command_audit(_: argparse.Namespace) -> None:
    manifest = load_manifest()
    records = live_records()
    validate_inventory(records)
    mismatches = [
        note_id for note_id, record in records.items()
        if manifest["expected_fingerprints"].get(str(note_id)) != fingerprint(record)
    ]
    if mismatches:
        raise CleanupError(f"live deck differs from compiled baseline: {mismatches[:5]}")
    if set(records) != set(map(int, manifest["expected_fingerprints"])):
        raise CleanupError("live note ID set differs from manifest")
    print(json.dumps({"notes": len(records), "cards": BASE_CARDS, "updates": len(manifest["updates"]), "delete": 5}, indent=2))


def all_reviews(card_ids: list[int]) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for batch in gw.chunks(sorted(card_ids), 250):
        reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    return reviews


def model_snapshot() -> dict[str, Any]:
    return {
        "fields": gw.anki("modelFieldNames", modelName=MODEL),
        "templates": gw.anki("modelTemplates", modelName=MODEL),
        "styling": gw.anki("modelStyling", modelName=MODEL),
    }


def schedule(card: dict[str, Any]) -> dict[str, Any]:
    return {key: card.get(key) for key in SCHEDULE_KEYS}


def command_snapshot(_: argparse.Namespace) -> None:
    command_audit(argparse.Namespace())
    manifest = load_manifest()
    records = live_records(with_card_info=True)
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_before_content_cleanup_{stamp}.apkg"
    result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.as_posix(), includeSched=True)
    if not result or not backup.exists():
        raise CleanupError("APKG export failed")
    cards = [card for record in records.values() for card in record["cards"]]
    card_ids = [int(card["cardId"]) for card in cards]
    reviews = all_reviews(card_ids)
    snapshot = {
        "schema_version": 1, "created_utc": now_utc(), "manifest_sha256": hash_file(MANIFEST),
        "backup": str(backup), "backup_sha256": hash_file(backup),
        "notes": {str(note_id): {"fields": record["fields"], "tags": record["tags"], "model": record["model"]} for note_id, record in records.items()},
        "cards": {str(card["cardId"]): schedule(card) for card in cards},
        "reviews": reviews, "reviews_sha256": canonical_hash(reviews), "model": model_snapshot(),
    }
    atomic_json(SNAPSHOT, snapshot)
    print(json.dumps({"backup": str(backup), "notes": len(records), "cards": len(cards), "reviews_sha256": snapshot["reviews_sha256"]}, indent=2))


def load_ready() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_manifest()
    if not SNAPSHOT.exists():
        raise CleanupError("snapshot missing")
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if snapshot.get("manifest_sha256") != hash_file(MANIFEST):
        raise CleanupError("manifest changed after snapshot")
    return manifest, snapshot


def allowed_state(note_id: int, record: dict[str, Any], manifest: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    before = snapshot["notes"][str(note_id)]
    desired = manifest["updates"].get(str(note_id), before["fields"])
    if record["fields"] not in (before["fields"], desired):
        return False
    allowed_tags = set(before["tags"])
    desired_tags = (allowed_tags - {TRANSLATION_TAG}) | {VERIFIED_TAG}
    return set(record["tags"]) in (allowed_tags, desired_tags)


def update_fields(note_ids: list[int], manifest: dict[str, Any]) -> None:
    actions = [
        {"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": manifest["updates"][str(note_id)]}}}
        for note_id in note_ids if str(note_id) in manifest["updates"]
    ]
    for batch in gw.chunks(actions, 40):
        results = gw.anki("multi", actions=batch)
        errors = [item.get("error") for item in results if isinstance(item, dict) and item.get("error")]
        if errors:
            raise CleanupError(f"Anki field update failed: {errors[:3]}")


def update_quality_tags(note_ids: list[int]) -> None:
    if not note_ids:
        return
    gw.anki("removeTags", notes=note_ids, tags=TRANSLATION_TAG)
    gw.anki("addTags", notes=note_ids, tags=VERIFIED_TAG)


def selected_ids(manifest: dict[str, Any], scope: str) -> list[int]:
    return manifest["pilot_ids"] if scope == "pilot" else manifest["survivor_ids"]


def command_apply(args: argparse.Namespace) -> None:
    if not args.dry_run and args.confirmation != APPLY_CONFIRMATION:
        raise CleanupError(f"confirmation must equal {APPLY_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    validate_inventory(records)
    bad = [note_id for note_id, record in records.items() if not allowed_state(note_id, record, manifest, snapshot)]
    if bad:
        raise CleanupError(f"live deck changed since snapshot: {bad[:5]}")
    ids = selected_ids(manifest, args.scope)
    field_changes = [note_id for note_id in ids if str(note_id) in manifest["updates"] and records[note_id]["fields"] != manifest["updates"][str(note_id)]]
    tag_changes = [note_id for note_id in ids if VERIFIED_TAG not in records[note_id]["tags"] or TRANSLATION_TAG in records[note_id]["tags"]]
    print(json.dumps({"scope": args.scope, "field_changes": len(field_changes), "tag_changes": len(tag_changes), "dry_run": args.dry_run}, indent=2))
    if args.dry_run:
        return
    update_fields(field_changes, manifest)
    update_quality_tags(tag_changes)


def verify_survivors(records: dict[int, dict[str, Any]], manifest: dict[str, Any], snapshot: dict[str, Any], ids: list[int]) -> None:
    for note_id in ids:
        record = records[note_id]
        before = snapshot["notes"][str(note_id)]
        expected_fields = manifest["updates"].get(str(note_id), before["fields"])
        expected_tags = (set(before["tags"]) - {TRANSLATION_TAG}) | {VERIFIED_TAG}
        actual_fields = dict(record["fields"])
        if not expected_fields.get("WordAudio") and actual_fields.get("WordAudio", "").startswith("[sound:"):
            actual_fields["WordAudio"] = ""
        if actual_fields != expected_fields or set(record["tags"]) != expected_tags:
            raise CleanupError(f"content verification failed: {note_id}")


def verify_unchanged_state(records: dict[int, dict[str, Any]], snapshot: dict[str, Any], *, final: bool) -> None:
    current_cards = {str(card["cardId"]): schedule(card) for record in records.values() for card in record["cards"]}
    expected_cards = snapshot["cards"]
    if final:
        deleted_cards = {str(card_id) for item in load_manifest()["deletions"] for card_id in item["card_ids"]}
        expected_cards = {key: value for key, value in expected_cards.items() if key not in deleted_cards}
    if current_cards != expected_cards:
        raise CleanupError("survivor card IDs or scheduling changed")
    current_reviews = all_reviews([int(key) for key in current_cards])
    expected_reviews = {key: snapshot["reviews"].get(key, []) for key in current_cards}
    if canonical_hash(current_reviews) != canonical_hash(expected_reviews):
        raise CleanupError("survivor review history changed")
    if model_snapshot() != snapshot["model"]:
        raise CleanupError("model fields/templates/styling changed")


def command_verify(args: argparse.Namespace) -> None:
    manifest, snapshot = load_ready()
    records = live_records(with_card_info=True)
    validate_inventory(records, final=args.post_delete)
    if args.post_delete:
        if set(DELETION_MAP) & set(records):
            raise CleanupError("deleted note still present")
        ids = manifest["survivor_ids"]
    else:
        ids = selected_ids(manifest, args.scope)
    verify_survivors(records, manifest, snapshot, ids)
    verify_unchanged_state(records, snapshot, final=args.post_delete)
    print(json.dumps({"scope": args.scope, "post_delete": args.post_delete, "notes": len(records), "cards": sum(len(item["cards"]) for item in records.values()), "verified": len(ids)}, indent=2))


def command_delete(args: argparse.Namespace) -> None:
    if args.confirmation != DELETE_CONFIRMATION:
        raise CleanupError(f"confirmation must equal {DELETE_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records(with_card_info=True)
    validate_inventory(records)
    verify_survivors(records, manifest, snapshot, manifest["survivor_ids"])
    verify_unchanged_state(records, snapshot, final=False)
    delete_ids = [item["note_id"] for item in manifest["deletions"]]
    for item in manifest["deletions"]:
        if item["note_id"] not in records or [int(card["cardId"]) for card in records[item["note_id"]]["cards"]] != item["card_ids"]:
            raise CleanupError(f"delete target changed: {item['note_id']}")
    review_count = sum(len(snapshot["reviews"].get(str(card_id), [])) for item in manifest["deletions"] for card_id in item["card_ids"])
    if review_count != 48:
        raise CleanupError(f"expected 48 review entries on delete targets, got {review_count}")
    gw.anki("addTags", notes=delete_ids, tags=DELETE_TAG)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = STATE / f"Goethe_Institute_tagged_pre_delete_{stamp}.apkg"
    result = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.as_posix(), includeSched=True)
    if not result or not backup.exists():
        raise CleanupError("tagged pre-delete APKG export failed")
    gw.anki("deleteNotes", notes=delete_ids)
    audit = {
        "deleted_utc": now_utc(), "note_ids": delete_ids,
        "card_ids": [card_id for item in manifest["deletions"] for card_id in item["card_ids"]],
        "review_entries": review_count, "backup": str(backup), "backup_sha256": hash_file(backup),
    }
    atomic_json(STATE / "deletion_audit.json", audit)
    command_verify(argparse.Namespace(scope="full", post_delete=True))


def command_rollback(args: argparse.Namespace) -> None:
    if args.confirmation != ROLLBACK_CONFIRMATION:
        raise CleanupError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    manifest, snapshot = load_ready()
    records = live_records()
    if set(DELETION_MAP) - set(records):
        raise CleanupError(f"deleted notes require APKG recovery: {snapshot['backup']}")
    ids = selected_ids(manifest, args.scope)
    actions = [
        {"action": "updateNoteFields", "params": {"note": {"id": note_id, "fields": snapshot["notes"][str(note_id)]["fields"]}}}
        for note_id in ids if records[note_id]["fields"] != snapshot["notes"][str(note_id)]["fields"]
    ]
    for batch in gw.chunks(actions, 40):
        gw.anki("multi", actions=batch)
    for note_id in ids:
        before, current = set(snapshot["notes"][str(note_id)]["tags"]), set(records[note_id]["tags"])
        add, remove = before - current, current - before
        if add:
            gw.anki("addTags", notes=[note_id], tags=" ".join(sorted(add)))
        if remove:
            gw.anki("removeTags", notes=[note_id], tags=" ".join(sorted(remove)))
    print(json.dumps({"scope": args.scope, "restored": len(ids)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("--audit", default=str(DEFAULT_AUDIT))
    compile_cmd.set_defaults(func=command_compile)
    sub.add_parser("audit").set_defaults(func=command_audit)
    sub.add_parser("snapshot").set_defaults(func=command_snapshot)
    apply = sub.add_parser("apply")
    apply.add_argument("--scope", choices=("pilot", "full"), default="pilot")
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--confirmation")
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify")
    verify.add_argument("--scope", choices=("pilot", "full"), default="pilot")
    verify.add_argument("--post-delete", action="store_true")
    verify.set_defaults(func=command_verify)
    delete = sub.add_parser("delete")
    delete.add_argument("--confirmation", required=True)
    delete.set_defaults(func=command_delete)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--scope", choices=("pilot", "full"), default="full")
    rollback.add_argument("--confirmation", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (CleanupError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
