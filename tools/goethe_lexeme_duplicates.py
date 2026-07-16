#!/usr/bin/env python3
"""Read-only audit for Goethe notes split by display qualifiers or spelling."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from typing import Any
from urllib.parse import quote
import zipfile

import goethe_examples
import goethe_target_highlights as target_highlights
import goethe_template_policy as production_policy
import goethe_werkstatt_migrate as gw


ROOT = Path(__file__).resolve().parents[1]
JSON_REPORT = ROOT / "review" / "goethe_lexeme_duplicate_audit.json"
MARKDOWN_REPORT = ROOT / "review" / "goethe_lexeme_duplicate_audit.md"
APPLY_REPORT = ROOT / "review" / "goethe_lexeme_variant_apply.json"
APPLY_MARKDOWN = ROOT / "review" / "goethe_lexeme_variant_apply.md"
STATE = ROOT / "tools" / ".goethe_lexeme_duplicates"
MODEL = gw.MODEL
PARENT_DECK = "Goethe Institute"
ARCHIVE_TAG = "goethe::archive::merged_duplicate"
MERGED_TAG = "goethe::quality::lexeme_merged"
APPLY_CONFIRMATION = "APPLY-13-LEXEME-MERGES"
LEVEL_RANK = {"A1": 0, "A2": 1, "B1": 2}
DECISIONS = ("MERGE_PROPOSED", "REVIEW_REQUIRED", "KEEP_SEPARATE_HOMOGRAPH")
EVIDENCE_REVIEWS = {
    (1497484860819, 1497484860820, 1584886454531): {
        "status": "REVIEWED",
        "assessment": "Cambridge treats Bekannte as one masculine-feminine noun meaning acquaintance; Goethe A1/A2/B1 also use one m./f. headword.",
        "links": [{"provider": "Cambridge", "url": "https://dictionary.cambridge.org/dictionary/german-english/bekannte"}],
    },
    (1497484861859, 1584886455204): {
        "status": "REVIEWED",
        "assessment": "Duden lists wehtun as the recommended spelling and weh tun as an alternative spelling of the same verb.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/wehtun"}],
    },
    (1584886454914, 1784075580640): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Duden records lieb as one adjective and includes its inflected attributive uses; the trailing dash is source notation, not a separate lexeme.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/lieb"}],
    },
    (1783863833253, 1784075521894): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Duden classifies dabei as an adverb and explicitly records dabei sein as a separated construction. The A2 v. label is source metadata error; both notes share the same lemma, audio, and examples.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/dabei"}],
    },
    (1584886455228, 1784075666586): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Duden classifies willkommen as an adjective, including Herzlich willkommen!; the A1 interj. label and B1 adj., interj. label represent the same lexeme and sense.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/willkommen"}],
    },
    (1784075508824, 1784075509014): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Goethe lists Bancomat/Bankomat as one Austrian/Swiss entry for an ATM; Duden records the same sense and pronunciation.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/Bancomat"}],
    },
    (1784075521334, 1784075612245): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Goethe lists chic/schick as one entry, and Duden accepts both spellings in the uninflected form.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/chic"}],
    },
    (1784075541355, 1784075599727): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Fantasie is the recommended spelling and Phantasie an alternative spelling of the same noun and sense.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/Fantasie"}],
    },
    (1784075555490, 1784075555583): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Goethe lists Glace/Glacé together for the Swiss ice-cream sense represented by both notes.",
        "links": [{"provider": "Goethe", "url": "https://www.goethe.de/pro/relaunch/prf/de/Goethe-Zertifikat_B1_Wortliste.pdf"}],
    },
    (1784075600940, 1784075601033): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Duden lists Portmonee as an alternative spelling of Portemonnaie; both notes have the same wallet sense.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/Portemonnaie"}],
    },
    (1784075611037, 1784075619811): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Duden lists Sauce as an alternative spelling of Soße; Goethe presents them as one noun entry.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/Sosze"}],
    },
    (1584886454612, 1783863833286, 1784075524920): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Disco/Disko are spelling variants and Diskothek is the full form used by Goethe for the same venue sense.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/Disko"}],
    },
    (1584886454788, 1784075563308): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Goethe groups Hähnchen/Hühnchen for the same German culinary chicken sense represented in this deck.",
        "links": [{"provider": "Goethe", "url": "https://www.goethe.de/pro/relaunch/prf/de/Goethe-Zertifikat_B1_Wortliste.pdf"}],
    },
    (1584886455036, 1784075605256): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Goethe lists Rezeption/Reception as one hotel front-desk entry.",
        "links": [{"provider": "Goethe", "url": "https://www.goethe.de/pro/relaunch/prf/de/Goethe-Zertifikat_B1_Wortliste.pdf"}],
    },
    (1497484861745, 1584886455149): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "tschüs and tschüss are accepted spellings of the same farewell interjection.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/tschuess"}],
    },
    (1497484861396, 1584886454963): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "nächste is an inflected citation form of the adjective recorded as nächst- in the lower-level Goethe entry.",
        "links": [{"provider": "Goethe", "url": "https://www.goethe.de/pro/relaunch/prf/de/A1_SD1_Wortliste_02.pdf"}],
    },
    (1497484861476, 1784075600003): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Pizzen is a plural form of Pizza, not a separate lexeme; Goethe records both in one noun entry.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/Pizza"}],
    },
    (1497484861364, 1784075584927): {
        "decision": "MERGE_PROPOSED",
        "status": "REVIEWED",
        "assessment": "Goethe records meist(ens), and Duden directly equates the adverbial senses used by both notes.",
        "links": [{"provider": "Duden", "url": "https://www.duden.de/rechtschreibung/meistens"}],
    },
}
EXPECTED_MERGE_GROUPS = {
    (1497484861364, 1784075584927),
    (1497484861396, 1584886454963),
    (1497484861476, 1784075600003),
    (1497484861745, 1584886455149),
    (1584886454612, 1783863833286, 1784075524920),
    (1584886454788, 1784075563308),
    (1584886455036, 1784075605256),
    (1784075508824, 1784075509014),
    (1784075521334, 1784075612245),
    (1784075541355, 1784075599727),
    (1784075555490, 1784075555583),
    (1784075600940, 1784075601033),
    (1784075611037, 1784075619811),
}
CANONICAL_OVERRIDES = {
    1584886454531: {
        "Lemma": "Bekannte", "MeaningEN": "acquaintance", "POS": "n.",
        "Article": "der/die", "Gender": "m./f.", "AcceptedAnswersDE": "Bekannte",
        "AcceptedArticlesDE": "der|die",
    },
    1783863833253: {
        "Lemma": "dabei", "MeaningEN": "with you; present or included; while doing so",
        "POS": "adv.", "AcceptedAnswersDE": "dabei",
    },
    1584886454914: {
        "Lemma": "lieb", "MeaningEN": "dear; kind", "POS": "adj.",
        "AcceptedAnswersDE": "lieb|lieb-",
    },
    1584886455204: {
        "Lemma": "wehtun", "MeaningEN": "to hurt", "POS": "v.",
        "AcceptedAnswersDE": "wehtun|weh tun", "FormOrVariantNote": "also: weh tun",
    },
    1584886455228: {
        "Lemma": "willkommen", "MeaningEN": "welcome", "POS": "adj.",
        "AcceptedAnswersDE": "willkommen",
    },
    1784075508824: {
        "Lemma": "Bancomat", "MeaningEN": "ATM", "POS": "n.",
        "Article": "der", "Gender": "m.", "NounFormsRaw": "-en",
        "AcceptedAnswersDE": "Bancomat|Bankomat", "AcceptedArticlesDE": "der",
        "RegionalVariants": "der Bancomat/Bankomat, -en (A, CH); → D: Geldautomat",
        "FormOrVariantNote": "also: Bankomat",
    },
    1784075521334: {
        "Lemma": "chic", "MeaningEN": "chic", "POS": "adj.",
        "AcceptedAnswersDE": "chic|schick", "FormOrVariantNote": "also: schick",
    },
    1784075541355: {
        "Lemma": "Fantasie", "MeaningEN": "imagination; fantasy", "POS": "n.",
        "Article": "die", "Gender": "f.", "NounFormsRaw": "-n",
        "AcceptedAnswersDE": "Fantasie|Phantasie", "AcceptedArticlesDE": "die",
        "FormOrVariantNote": "also: Phantasie",
    },
    1784075555490: {
        "Lemma": "Glace", "MeaningEN": "ice cream", "POS": "n.",
        "Article": "die/das", "Gender": "f./n.", "NounFormsRaw": "-n",
        "AcceptedAnswersDE": "Glace|Glacé", "AcceptedArticlesDE": "die|das",
        "RegionalVariants": "die/das Glace/Glacé, -n (CH); → D, A: Eis",
        "FormOrVariantNote": "also: Glacé (CH)",
    },
    1784075600940: {
        "Lemma": "Portemonnaie", "MeaningEN": "wallet", "POS": "n.",
        "Article": "das", "Gender": "n.", "NounFormsRaw": "-s",
        "AcceptedAnswersDE": "Portemonnaie|Portmonee", "AcceptedArticlesDE": "das",
        "RegionalVariants": "das Portemonnaie/Portmonee, -s (D, CH); → Brieftasche; A: Geldbörse",
        "FormOrVariantNote": "also: Portmonee",
    },
    1784075611037: {
        "Lemma": "Sauce", "MeaningEN": "sauce", "POS": "n.",
        "Article": "die", "Gender": "f.", "NounFormsRaw": "-n",
        "AcceptedAnswersDE": "Sauce|Soße", "AcceptedArticlesDE": "die",
        "FormOrVariantNote": "also: Soße",
    },
    1584886454612: {
        "Lemma": "Disco", "MeaningEN": "disco; discotheque", "POS": "n.",
        "Article": "die", "Gender": "f.",
        "NounFormsRaw": "Discos/Diskos/Diskotheken",
        "AcceptedAnswersDE": "Disco|Disko|Diskothek", "AcceptedArticlesDE": "die",
        "FormOrVariantNote": "also: Disko; full form: Diskothek",
    },
    1584886454788: {
        "Lemma": "Hähnchen", "MeaningEN": "chicken", "POS": "n.",
        "Article": "das", "Gender": "n.", "NounFormsRaw": "-",
        "AcceptedAnswersDE": "Hähnchen|Hühnchen", "AcceptedArticlesDE": "das",
        "RegionalVariants": "das Hähnchen/Hühnchen, - (D); → A: Hend(e)l; Poulet, -s (CH)",
        "FormOrVariantNote": "also: Hühnchen",
    },
    1584886455036: {
        "Lemma": "Rezeption", "MeaningEN": "reception; front desk", "POS": "n.",
        "Article": "die", "Gender": "f.", "NounFormsRaw": "-en",
        "AcceptedAnswersDE": "Rezeption|Reception", "AcceptedArticlesDE": "die",
        "FormOrVariantNote": "also: Reception",
    },
    1584886455149: {
        "Lemma": "tschüss", "MeaningEN": "bye", "POS": "interj.",
        "AcceptedAnswersDE": "tschüss|tschüs",
        "FormOrVariantNote": "also: tschüs",
    },
    1584886454963: {
        "Lemma": "nächst-", "MeaningEN": "next", "POS": "adj.",
        "AcceptedAnswersDE": "nächst-|nächste",
        "FormOrVariantNote": "inflected forms: nächster, nächstes",
    },
    1497484861476: {
        "Lemma": "Pizza", "MeaningEN": "pizza", "POS": "n.",
        "Article": "die", "Gender": "f.", "NounFormsRaw": "-s/Pizzen",
        "AcceptedAnswersDE": "Pizza|Pizzen", "AcceptedArticlesDE": "die",
        "FormOrVariantNote": "plural: Pizzen",
    },
    1497484861364: {
        "Lemma": "meistens", "MeaningEN": "mostly", "POS": "adv.",
        "AcceptedAnswersDE": "meistens|meist", "FormOrVariantNote": "also: meist",
    },
}
EXPECTED_SURVIVORS = {
    (1497484861364, 1784075584927): 1497484861364,
    (1497484861396, 1584886454963): 1584886454963,
    (1497484861476, 1784075600003): 1497484861476,
    (1497484861745, 1584886455149): 1584886455149,
    (1584886454612, 1783863833286, 1784075524920): 1584886454612,
    (1584886454788, 1784075563308): 1584886454788,
    (1584886455036, 1784075605256): 1584886455036,
    (1784075508824, 1784075509014): 1784075508824,
    (1784075521334, 1784075612245): 1784075521334,
    (1784075541355, 1784075599727): 1784075541355,
    (1784075555490, 1784075555583): 1784075555490,
    (1784075600940, 1784075601033): 1784075600940,
    (1784075611037, 1784075619811): 1784075611037,
}
EXPECTED_CARD_IDS = {
    1497484861364: (1497484863226, 1497484863227),
    1497484861396: (1497484863290, 1497484863291),
    1497484861476: (1497484863450, 1497484863451),
    1497484861745: (1497484863988, 1497484863989),
    1584886454612: (1584886455584, 1584886455585),
    1783863833286: (1783863833286, 1783863833287),
    1784075524920: (1784075524920, 1784075524921),
    1584886454788: (1584886455936, 1584886455937),
    1784075563308: (1784075563308, 1784075563309),
    1584886454963: (1584886456286, 1584886456287),
    1584886455036: (1584886456432, 1584886456433),
    1784075605256: (1784075605256, 1784075605257),
    1584886455149: (1584886456658, 1584886456659),
    1784075508824: (1784075508824, 1784075508825),
    1784075509014: (1784075509014, 1784075509015),
    1784075521334: (1784075521334, 1784075521335),
    1784075612245: (1784075612245, 1784075612246),
    1784075541355: (1784075541355, 1784075541356),
    1784075599727: (1784075599727, 1784075599728),
    1784075555490: (1784075555491, 1784075555492),
    1784075555583: (1784075555583, 1784075555584),
    1784075600940: (1784075600940, 1784075600941),
    1784075601033: (1784075601033, 1784075601034),
    1784075611037: (1784075611037, 1784075611038),
    1784075619811: (1784075619811, 1784075619812),
    1784075584927: (1784075584927, 1784075584928),
    1784075600003: (1784075600003, 1784075600004),
}
QUALIFIER_RE = re.compile(
    r"\s*\((?:männlich|weiblich|maskulin|feminin|neutral|singular|plural|regional)\)\s*$",
    re.IGNORECASE,
)


class AuditError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(unicodedata.normalize("NFC", str(value or "")))).strip()


def normalized(value: str) -> str:
    return clean(value).casefold()


def strip_display_annotation(value: str) -> str:
    text = clean(value)
    qualified = QUALIFIER_RE.sub("", text)
    if qualified != text:
        return qualified
    # General trailing annotations are candidate signals only, never enough
    # on their own to approve a merge.
    return re.sub(r"\s*\([^()]+\)\s*$", "", text).strip()


def strip_article(value: str) -> str:
    return re.sub(r"^(?:der|die|das|ein|eine)\s+", "", clean(value), flags=re.IGNORECASE).strip()


def candidate_keys(value: str) -> set[str]:
    text = clean(value)
    annotated = strip_display_annotation(text)
    values = {text, annotated, strip_article(text), strip_article(annotated)}
    return {normalized(item) for item in values if item}


def orthographic_key(value: str) -> str:
    bases = candidate_keys(value)
    base = min(bases, key=lambda item: (len(item), item)) if bases else ""
    base = base.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[\s./_()\-]+", "", base)


def split_answers(value: str) -> list[str]:
    return [clean(item) for item in str(value or "").split("|") if clean(item)]


def meaning_tokens(value: str) -> set[str]:
    stop = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "and", "or", "be"}
    return {token for token in re.findall(r"[a-z]+", normalized(value)) if token not in stop}


def meaning_overlap(left: str, right: str) -> float:
    a, b = meaning_tokens(left), meaning_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def pos_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z]+", normalized(value)) if token}


def word_audio_source(value: str) -> str:
    match = re.search(r"_goethe_word_([a-z_]+)_", value or "")
    return match.group(1) if match else ("other" if value else "missing")


def total_reps(record: dict[str, Any]) -> int:
    return sum(int(card.get("reps", 0)) for card in record.get("cards", []))


def total_reviews(record: dict[str, Any]) -> int:
    return sum(int(card.get("review_count", 0)) for card in record.get("cards", []))


def choose_survivor(group: list[dict[str, Any]]) -> dict[str, Any]:
    def key(record: dict[str, Any]) -> tuple[int, int, int, int, int]:
        fields = record["fields"]
        order = fields.get("OriginalOrder", "")
        return (
            LEVEL_RANK.get(fields.get("CEFR", ""), 99),
            -total_reps(record),
            -int("-MAIN-" in fields.get("SourceRefs", "")),
            int(order) if str(order).isdigit() else 10**9,
            int(record["note_id"]),
        )
    return min(group, key=key)


def proposed_actions(group: list[dict[str, Any]], survivor: dict[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    for record in group:
        note_id = int(record["note_id"])
        if note_id == int(survivor["note_id"]):
            result[note_id] = "SURVIVE"
        else:
            result[note_id] = "DELETE_AFTER_APPROVAL"
    return result


def pair_reasons(left: dict[str, Any], right: dict[str, Any]) -> set[str]:
    a, b = left["fields"], right["fields"]
    reasons: set[str] = set()
    if normalized(a.get("Lemma", "")) == normalized(b.get("Lemma", "")):
        reasons.add("same_surface_casefold")
    if candidate_keys(a.get("Lemma", "")) & candidate_keys(b.get("Lemma", "")):
        if clean(a.get("Lemma", "")) != clean(b.get("Lemma", "")):
            reasons.add("qualifier_or_article_normalization")
    if orthographic_key(a.get("Lemma", "")) == orthographic_key(b.get("Lemma", "")):
        if normalized(a.get("Lemma", "")) != normalized(b.get("Lemma", "")):
            reasons.add("spacing_hyphen_or_transliteration")
    left_answers = {normalized(item) for item in split_answers(a.get("AcceptedAnswersDE", ""))}
    right_answers = {normalized(item) for item in split_answers(b.get("AcceptedAnswersDE", ""))}
    if left_answers & right_answers:
        reasons.add("accepted_answer_overlap")
    left_refs = set(split_answers(a.get("SourceRefs", "")))
    right_refs = set(split_answers(b.get("SourceRefs", "")))
    if left_refs & right_refs:
        reasons.add("shared_source_ref")
    if a.get("WordAudio") and a.get("WordAudio") == b.get("WordAudio"):
        reasons.add("same_word_audio")
    left_examples = {normalized(item.get("de", "")) for item in left.get("examples", []) if item.get("de")}
    right_examples = {normalized(item.get("de", "")) for item in right.get("examples", []) if item.get("de")}
    if left_examples & right_examples:
        reasons.add("shared_german_example")
    return reasons


def classify_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [record["fields"] for record in group]
    pairs = [(group[i], group[j]) for i in range(len(group)) for j in range(i + 1, len(group))]
    reasons = sorted({reason for left, right in pairs for reason in pair_reasons(left, right)})
    poses = {field.get("POS", "") for field in fields}
    pos_sets = [pos_tokens(field.get("POS", "")) for field in fields]
    pos_overlap = bool(pos_sets) and all(pos_sets[0] & item for item in pos_sets[1:])
    exact_meaning = len({normalized(field.get("MeaningEN", "")) for field in fields}) == 1
    overlaps = [meaning_overlap(left["fields"].get("MeaningEN", ""), right["fields"].get("MeaningEN", "")) for left, right in pairs]
    min_overlap = min(overlaps, default=0.0)
    qualifier_split = any(QUALIFIER_RE.search(field.get("Lemma", "")) for field in fields)
    same_pos = len(poses) == 1
    spelling_signal = "spacing_hyphen_or_transliteration" in reasons
    same_surface = "same_surface_casefold" in reasons
    same_written_form = len({clean(field.get("Lemma", "")) for field in fields}) == 1
    reviewed = EVIDENCE_REVIEWS.get(tuple(sorted(int(record["note_id"]) for record in group)))

    if reviewed and reviewed.get("decision"):
        decision = reviewed["decision"]
        explanation = reviewed["assessment"]
    elif qualifier_split and same_pos and min_overlap > 0:
        decision = "MERGE_PROPOSED"
        explanation = "Same lexeme and POS; gender/display qualifier carries metadata rather than a separate sense."
    elif same_pos and exact_meaning and spelling_signal:
        decision = "MERGE_PROPOSED"
        explanation = "Same POS and English sense; only spacing, hyphenation, or transliteration differs."
    elif same_pos and exact_meaning and same_surface:
        decision = "MERGE_PROPOSED"
        explanation = "Same surface lexeme, POS, and English sense."
    elif same_written_form and exact_meaning and pos_overlap:
        decision = "MERGE_PROPOSED"
        explanation = "Same written lexeme and English sense; the POS labels overlap rather than identify separate lexemes."
    elif same_written_form and min_overlap == 0:
        decision = "KEEP_SEPARATE_HOMOGRAPH"
        explanation = "The identical spelling is polysemous here: the recorded English senses do not overlap."
    elif not same_pos and not pos_overlap:
        decision = "KEEP_SEPARATE_HOMOGRAPH"
        explanation = "Surface collision belongs to distinct grammatical categories; shared translation tokens do not make it one lexeme."
    elif same_surface and not same_written_form and not exact_meaning:
        decision = "KEEP_SEPARATE_HOMOGRAPH"
        explanation = "Capitalisation distinguishes separate entries and their English senses differ."
    elif spelling_signal and not exact_meaning:
        decision = "KEEP_SEPARATE_HOMOGRAPH"
        explanation = "The marked combining form or expression has a distinct source sense, not merely alternate spelling."
    else:
        decision = "REVIEW_REQUIRED"
        explanation = "Signals overlap, but source morphology, POS, or sense prevents an automatic merge proposal."
    return {
        "decision": decision, "reason_codes": reasons, "explanation": explanation,
        "same_pos": same_pos, "pos_overlap": pos_overlap,
        "minimum_meaning_overlap": round(min_overlap, 3),
    }


class DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def _heuristic_candidate_groups(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    indexes: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        fields = record["fields"]
        # Candidate generation is deliberately lemma-only. Accepted answers,
        # shared provenance, and audio are supporting signals; using them as
        # join keys would pull synonyms and derivational gender pairs into a
        # same-lexeme audit.
        for surface in candidate_keys(fields.get("Lemma", "")):
            indexes[("surface", surface)].append(index)
        orthographic = orthographic_key(fields.get("Lemma", ""))
        if len(orthographic) >= 3:
            indexes[("orthographic", orthographic)].append(index)
    dsu = DisjointSet(len(records))
    for members in indexes.values():
        unique = list(dict.fromkeys(members))
        for other in unique[1:]:
            dsu.union(unique[0], other)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, record in enumerate(records):
        grouped[dsu.find(index)].append(record)
    return [group for group in grouped.values() if len(group) > 1]


def candidate_groups(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Return heuristic groups plus explicitly reviewed variant groups.

    Reviewed groups are removed from heuristic generation first.  This keeps a
    spelling/form review such as ``meist/meistens`` from being transitively
    joined to the distinct combining form ``meist-``.
    """
    by_id = {int(record["note_id"]): record for record in records}
    explicit_ids = {note_id for group in EXPECTED_MERGE_GROUPS for note_id in group}
    groups = _heuristic_candidate_groups([
        record for record in records if int(record["note_id"]) not in explicit_ids
    ])
    for member_ids in sorted(EXPECTED_MERGE_GROUPS):
        group = [by_id[note_id] for note_id in member_ids if note_id in by_id]
        if len(group) > 1:
            groups.append(group)
    return groups


def live_records() -> list[dict[str, Any]]:
    if gw.anki("version") != 6:
        raise AuditError("unexpected AnkiConnect version")
    note_ids = gw.anki("findNotes", query=f'note:"{MODEL}"')
    notes: list[dict[str, Any]] = []
    for batch in gw.chunks(note_ids, 250):
        notes.extend(gw.anki("notesInfo", notes=batch))
    card_ids = [int(card_id) for note in notes for card_id in note.get("cards", [])]
    cards: list[dict[str, Any]] = []
    for batch in gw.chunks(card_ids, 250):
        cards.extend(gw.anki("cardsInfo", cards=batch))
    by_note: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        by_note[int(card["note"])].append(card)
    records = []
    for note in notes:
        fields = {name: value.get("value", "") for name, value in note.get("fields", {}).items()}
        note_id = int(note["noteId"])
        records.append({
            "note_id": note_id, "fields": fields, "tags": sorted(note.get("tags", [])),
            "cards": by_note[note_id], "examples": goethe_examples.parse_fields(fields),
        })
    return records


def card_projection(card: dict[str, Any]) -> dict[str, Any]:
    keys = ("cardId", "ord", "deckName", "queue", "type", "due", "interval", "reps", "lapses", "left", "flags")
    return {key: card.get(key) for key in keys}


def attach_review_counts(groups: list[list[dict[str, Any]]]) -> None:
    cards = [card for group in groups for record in group for card in record.get("cards", [])]
    card_ids = sorted({int(card["cardId"]) for card in cards})
    reviews: dict[str, Any] = {}
    for batch in gw.chunks(card_ids, 250):
        reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    for card in cards:
        card["review_count"] = len(reviews.get(str(card["cardId"]), reviews.get(int(card["cardId"]), [])))


def inventory_signature(records: list[dict[str, Any]]) -> str:
    value = [{
        "note_id": record["note_id"], "fields": record["fields"], "tags": record["tags"],
        "cards": [card_projection(card) for card in sorted(record["cards"], key=lambda card: int(card["cardId"]))],
    } for record in sorted(records, key=lambda item: item["note_id"])]
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_apkg(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            return bool({"collection.anki2", "collection.anki21"} & names) and archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def reviews_for_cards(card_ids: list[int]) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for batch in gw.chunks(sorted(set(card_ids)), 250):
        reviews.update(gw.anki("getReviewsOfCards", cards=batch))
    return reviews


def evidence_links(lemma: str) -> list[dict[str, str]]:
    encoded = quote(strip_display_annotation(lemma))
    return [
        {"provider": "Duden", "url": f"https://www.duden.de/suchen/dudenonline/{encoded}"},
        {"provider": "Cambridge", "url": f"https://dictionary.cambridge.org/dictionary/german-english/{encoded}"},
    ]


def member_projection(record: dict[str, Any], action: str) -> dict[str, Any]:
    fields = record["fields"]
    return {
        "note_id": record["note_id"], "card_ids": [int(card["cardId"]) for card in record["cards"]],
        "proposed_action": action, "reps": total_reps(record), "review_entries": total_reviews(record),
        "lemma": fields.get("Lemma", ""), "meaning_en": fields.get("MeaningEN", ""),
        "cefr": fields.get("CEFR", ""), "pos": fields.get("POS", ""),
        "article": fields.get("Article", ""), "gender": fields.get("Gender", ""),
        "noun_forms": fields.get("NounFormsRaw", ""), "form_or_variant_note": fields.get("FormOrVariantNote", ""),
        "accepted_answers_de": split_answers(fields.get("AcceptedAnswersDE", "")),
        "source_refs": split_answers(fields.get("SourceRefs", "")),
        "word_audio": fields.get("WordAudio", ""), "word_audio_source": word_audio_source(fields.get("WordAudio", "")),
        "examples": record.get("examples", []), "tags": record.get("tags", []),
        "cards": [card_projection(card) | {"review_count": card.get("review_count", 0)} for card in record["cards"]],
    }


def unique_values(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else normalized(str(value))
        if key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def merge_preview(group: list[dict[str, Any]], survivor: dict[str, Any]) -> dict[str, Any]:
    fields = [record["fields"] for record in group]
    levels = unique_values([field.get("CEFR", "") for field in fields if field.get("CEFR")])
    return {
        "survivor_note_id": int(survivor["note_id"]),
        "levels": sorted(levels, key=lambda level: LEVEL_RANK.get(level, 99)),
        "meanings_en": unique_values([field.get("MeaningEN", "") for field in fields if field.get("MeaningEN")]),
        "accepted_answers_de": unique_values([
            answer for field in fields for answer in split_answers(field.get("AcceptedAnswersDE", ""))
        ]),
        "source_refs": unique_values([
            ref for field in fields for ref in split_answers(field.get("SourceRefs", ""))
        ]),
        "word_audio": unique_values([field.get("WordAudio", "") for field in fields if field.get("WordAudio")]),
        "examples": unique_values([example for record in group for example in record.get("examples", [])]),
        "history_policy": "Keep the survivor cards and delete every redundant non-survivor, including its old review history.",
    }


def best_word_audio(group: list[dict[str, Any]], survivor: dict[str, Any]) -> str:
    priority = {"duden": 0, "commons": 1, "edge": 2, "other": 3, "missing": 4}
    values = [record["fields"].get("WordAudio", "") for record in group]
    current = survivor["fields"].get("WordAudio", "")
    return min(values, key=lambda value: (priority[word_audio_source(value)], value != current)) if values else current


def merged_fields(group: list[dict[str, Any]], survivor: dict[str, Any]) -> dict[str, str]:
    result = deepcopy(survivor["fields"])
    ordered = [survivor] + [record for record in group if record is not survivor]
    refs = unique_values([
        ref for record in ordered for ref in split_answers(record["fields"].get("SourceRefs", ""))
    ])
    refs.sort(key=lambda ref: (LEVEL_RANK.get(ref.split("-", 1)[0], 99), ref))
    result["SourceRefs"] = "|".join(refs)
    result["AcceptedArticlesDE"] = "|".join(unique_values([
        article for record in ordered for article in split_answers(record["fields"].get("AcceptedArticlesDE", ""))
    ]))
    result["RegionalVariants"] = "|".join(unique_values([
        variant for record in ordered for variant in split_answers(record["fields"].get("RegionalVariants", ""))
    ]))
    result["SourceNoteRaw"] = " | ".join(unique_values([
        record["fields"].get("SourceNoteRaw", "") for record in ordered
        if record["fields"].get("SourceNoteRaw", "")
    ]))
    result["WordAudio"] = best_word_audio(group, survivor)
    for field_name in ("NounFormsRaw", "VerbFormsRaw"):
        values = unique_values([
            record["fields"].get(field_name, "") for record in ordered if record["fields"].get(field_name)
        ])
        if values:
            result[field_name] = max(values, key=len)

    examples: list[dict[str, Any]] = []
    by_german: dict[str, dict[str, Any]] = {}
    for record in ordered:
        for example in record.get("examples", []):
            key = normalized(example.get("de", ""))
            if not key:
                continue
            if key not in by_german:
                value = dict(example)
                by_german[key] = value
                examples.append(value)
            else:
                current = by_german[key]
                if not current.get("audio") and example.get("audio"):
                    current["audio"] = example["audio"]
                if not current.get("en") and example.get("en"):
                    current["en"] = example["en"]
    goethe_examples.render_fields(result, examples)
    result.update(CANONICAL_OVERRIDES.get(int(survivor["note_id"]), {}))
    if result.get("SourceID"):
        try:
            production_policy.apply_policy([{"fields": result}], strict=False)
        except production_policy.PolicyError as exc:
            raise AuditError(f"merged production fields failed: {result.get('Lemma')}: {exc}") from exc
    result["ExampleTargetSpansJSON"] = target_highlights.build_target_spans(result)
    return result


def compile_apply_plan(records: list[dict[str, Any]]) -> dict[str, Any]:
    report = build_report(records)
    merge_groups = [group for group in report["groups"] if group["decision"] == "MERGE_PROPOSED"]
    actual = {tuple(sorted(member["note_id"] for member in group["members"])) for group in merge_groups}
    if actual != EXPECTED_MERGE_GROUPS:
        raise AuditError(f"reviewed merge groups changed: {sorted(actual)}")
    by_id = {int(record["note_id"]): record for record in records}
    groups = []
    for report_group in merge_groups:
        member_ids = [int(member["note_id"]) for member in report_group["members"]]
        group = [by_id[note_id] for note_id in member_ids]
        survivor = choose_survivor(group)
        expected_survivor = EXPECTED_SURVIVORS.get(tuple(sorted(member_ids)))
        if expected_survivor != int(survivor["note_id"]):
            raise AuditError(
                f"unexpected survivor for {member_ids}: "
                f"{survivor['note_id']} != {expected_survivor}"
            )
        actions = proposed_actions(group, survivor)
        groups.append({
            "group_id": report_group["group_id"],
            "member_ids": member_ids,
            "survivor_id": int(survivor["note_id"]),
            "archive_ids": [],
            "delete_ids": sorted(note_id for note_id, action in actions.items() if action == "DELETE_AFTER_APPROVAL"),
            "fields": merged_fields(group, survivor),
            "evidence_assessment": report_group["evidence_assessment"],
        })
    archive_ids = sorted(note_id for group in groups for note_id in group["archive_ids"])
    delete_ids = sorted(note_id for group in groups for note_id in group["delete_ids"])
    expected_deletes = sum(len(group) - 1 for group in EXPECTED_MERGE_GROUPS)
    if archive_ids or len(delete_ids) != expected_deletes:
        raise AuditError(f"unexpected archive/delete policy: archive={archive_ids}, delete={delete_ids}")
    return {
        "created_utc": now_utc(), "inventory_signature": inventory_signature(records),
        "groups": groups, "archive_ids": archive_ids, "delete_ids": delete_ids,
    }


def build_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    active_records = [record for record in records if ARCHIVE_TAG not in record.get("tags", [])]
    groups = candidate_groups(active_records)
    attach_review_counts(groups)
    output = []
    for group in groups:
        classification = classify_group(group)
        survivor = choose_survivor(group)
        actions = proposed_actions(group, survivor) if classification["decision"] == "MERGE_PROPOSED" else {
            int(record["note_id"]): "KEEP_CURRENT" for record in group
        }
        member_ids = sorted(int(record["note_id"]) for record in group)
        group_id = "lexeme-" + hashlib.sha256(",".join(map(str, member_ids)).encode()).hexdigest()[:10]
        evidence = EVIDENCE_REVIEWS.get(tuple(member_ids), {
            "status": "LOOKUP_REQUIRED" if classification["decision"] == "REVIEW_REQUIRED" else "RULE_REVIEWED",
            "assessment": classification["explanation"],
            "links": evidence_links(survivor["fields"].get("Lemma", "")),
        })
        output.append({
            "group_id": group_id, "approval": "PENDING_APPROVAL", **classification,
            "proposed_survivor": int(survivor["note_id"]) if classification["decision"] == "MERGE_PROPOSED" else None,
            "members": [member_projection(record, actions[int(record["note_id"])]) for record in sorted(group, key=lambda item: item["note_id"])],
            "merge_preview": merge_preview(group, survivor) if classification["decision"] == "MERGE_PROPOSED" else None,
            "evidence_status": evidence["status"], "evidence_assessment": evidence["assessment"],
            "evidence_links": evidence["links"],
        })
    output.sort(key=lambda item: (DECISIONS.index(item["decision"]), normalized(item["members"][0]["lemma"]), item["group_id"]))
    summary = Counter(item["decision"] for item in output)
    return {
        "schema_version": 1, "generated_utc": now_utc(), "policy": "conservative_same_lexeme",
        "scope": {
            "model": MODEL, "notes": len(records), "cards": sum(len(record["cards"]) for record in records),
            "archived_notes": len(records) - len(active_records),
            "levels": dict(Counter(record["fields"].get("CEFR", "") for record in records)),
            "inventory_signature": inventory_signature(records),
        },
        "summary": {**{decision: summary.get(decision, 0) for decision in DECISIONS}, "candidate_groups": len(output)},
        "rules": {
            "survivor_priority": "A1 -> A2 -> B1; then reps, MAIN provenance, OriginalOrder, note ID",
            "redundant_duplicate": "DELETE_AFTER_APPROVAL_REGARDLESS_OF_REVIEW_HISTORY",
            "derivational_gender_pairs": "OUT_OF_SCOPE",
        },
        "groups": output,
    }


def markdown_report(report: dict[str, Any]) -> str:
    scope, summary = report["scope"], report["summary"]
    lines = [
        "# Goethe lexeme duplicate audit", "",
        "> Read-only audit. No note, card, scheduling, tag, deck, or review history was modified.", "",
        f"Generated: `{report['generated_utc']}`", "",
        f"Scope: **{scope['notes']} notes / {scope['cards']} cards** — "
        + ", ".join(f"{level}: {count}" for level, count in sorted(scope["levels"].items())), "",
        "## Summary", "",
        "| Decision | Groups |", "|---|---:|",
    ]
    for decision in DECISIONS:
        lines.append(f"| `{decision}` | {summary[decision]} |")
    lines.extend(["", "All proposed actions are pending explicit user approval.", ""])
    for group in report["groups"]:
        title = " / ".join(dict.fromkeys(member["lemma"] for member in group["members"]))
        lines.extend([
            f"## {group['group_id']} — {title}", "",
            f"**Decision:** `{group['decision']}` · **Approval:** `{group['approval']}`", "",
            group["explanation"], "",
            f"Evidence status: `{group['evidence_status']}` — {group['evidence_assessment']}", "",
            f"Signals: {', '.join(f'`{item}`' for item in group['reason_codes']) or 'none'}", "",
            "| Action | Note ID | Card IDs | Level | POS | Lemma | Meaning | Reps / reviews | Article / gender |",
            "|---|---:|---|---|---|---|---|---:|---|",
        ])
        for member in group["members"]:
            cards = ", ".join(str(value) for value in member["card_ids"])
            article_gender = " / ".join(value for value in (member["article"], member["gender"]) if value)
            lines.append(
                f"| `{member['proposed_action']}` | `{member['note_id']}` | `{cards}` | {member['cefr']} | "
                f"{member['pos']} | {member['lemma']} | {member['meaning_en']} | "
                f"{member['reps']} / {member['review_entries']} | {article_gender} |"
            )
        if group["merge_preview"]:
            preview = group["merge_preview"]
            lines.extend([
                "", "### Proposed merged payload", "",
                f"- Survivor note: `{preview['survivor_note_id']}`",
                f"- Levels/provenance retained: `{', '.join(preview['levels'])}` / `{'|'.join(preview['source_refs'])}`",
                f"- English meanings retained for reconciliation: `{' | '.join(preview['meanings_en'])}`",
                f"- Accepted German answers: `{' | '.join(preview['accepted_answers_de'])}`",
                f"- Unique word-audio assets: `{len(preview['word_audio'])}`; unique examples: `{len(preview['examples'])}`",
                f"- History: {preview['history_policy']}",
            ])
        lines.extend(["", "### Content to review", ""])
        for member in group["members"]:
            lines.append(f"- `{member['note_id']}` provenance: `{ '|'.join(member['source_refs']) }`; word audio: `{member['word_audio_source']}`.")
            for example in member["examples"]:
                lines.append(f"  - {example.get('de', '')} — {example.get('en', '')}")
        links = " · ".join(f"[{item['provider']}]({item['url']})" for item in group["evidence_links"])
        lines.extend(["", f"Evidence lookup: {links}", ""])
    return "\n".join(lines).rstrip() + "\n"


def command_audit(_: argparse.Namespace) -> None:
    records = live_records()
    report = build_report(records)
    JSON_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_REPORT.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"json": str(JSON_REPORT), "markdown": str(MARKDOWN_REPORT), **report["scope"], **report["summary"]}, indent=2))


def command_plan(_: argparse.Namespace) -> None:
    records = live_records()
    plan = compile_apply_plan(records)
    print(json.dumps({
        "inventory_signature": plan["inventory_signature"],
        "groups": len(plan["groups"]),
        "survivors": [group["survivor_id"] for group in plan["groups"]],
        "archive_ids": plan["archive_ids"], "delete_ids": plan["delete_ids"],
    }, indent=2))


def export_backup() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = STATE / f"Goethe_Institute_pre_lexeme_merge_{stamp}.apkg"
    try:
        exported = gw.anki("exportPackage", deck=PARENT_DECK, path=backup.resolve().as_posix(), includeSched=True)
    except gw.MigrationError:
        exported = False
    if not exported:
        for _ in range(120):
            if backup.exists() and valid_apkg(backup):
                exported = True
                break
            time.sleep(1)
    if not exported or not backup.exists() or not valid_apkg(backup):
        raise AuditError("scheduled APKG backup failed validation")
    return backup


def apply_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Goethe lexeme duplicate apply report", "",
        f"Applied: `{result['applied_utc']}`", "",
        f"Backup: `{result['backup']}`", "",
        f"Backup SHA-256: `{result['backup_sha256']}`", "",
        "## Result", "",
        f"- Applied merge groups: **{len(result['groups'])}**",
        f"- Deleted redundant notes: **{len(result['delete_ids'])}**",
        f"- Deliberately discarded duplicate history: **{sum(item['reps'] for item in result['deleted_reviewed_history'])} reps / {sum(item['review_entries'] for item in result['deleted_reviewed_history'])} revlog entries**",
        f"- Live inventory: **{result['after']['notes']} notes / {result['after']['cards']} cards**",
        f"- Preserved retained-card review history: `{result['retained_reviews_preserved']}`",
        f"- Remaining merge proposals: **{result['after_audit']['MERGE_PROPOSED']}**", "",
        "## Applied groups", "",
        "| Survivor | Deleted | Evidence |", "|---:|---|---|",
    ]
    for group in result["groups"]:
        lines.append(
            f"| `{group['survivor_id']}` | `{', '.join(map(str, group['delete_ids']))}` | "
            f"{group['evidence_assessment']} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def command_apply(args: argparse.Namespace) -> None:
    if args.confirmation != APPLY_CONFIRMATION:
        raise AuditError(f"confirmation must equal {APPLY_CONFIRMATION}")
    records = live_records()
    plan = compile_apply_plan(records)
    by_id = {int(record["note_id"]): record for record in records}
    affected_ids = {note_id for group in plan["groups"] for note_id in group["member_ids"]}
    backup = export_backup()
    rechecked = live_records()
    if inventory_signature(rechecked) != plan["inventory_signature"]:
        raise AuditError("live inventory changed after backup; refusing destructive apply")
    records = rechecked
    by_id = {int(record["note_id"]): record for record in records}
    for note_id in sorted(affected_ids):
        expected_cards = EXPECTED_CARD_IDS.get(note_id)
        actual_cards = tuple(sorted(int(card["cardId"]) for card in by_id[note_id]["cards"]))
        if expected_cards is not None and actual_cards != expected_cards:
            raise AuditError(
                f"card IDs changed for note {note_id}: {actual_cards} != {expected_cards}"
            )
    affected_card_ids = [
        int(card["cardId"]) for note_id in affected_ids for card in by_id[note_id]["cards"]
    ]
    reviews_before = reviews_for_cards(affected_card_ids)
    STATE.mkdir(parents=True, exist_ok=True)
    snapshot_path = STATE / f"snapshot_{backup.stem}.json"
    snapshot_path.write_text(json.dumps({
        "created_utc": now_utc(), "inventory_signature": plan["inventory_signature"],
        "affected_notes": {str(note_id): by_id[note_id] for note_id in sorted(affected_ids)},
        "reviews": reviews_before, "backup": str(backup.resolve()), "backup_sha256": hash_file(backup),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    before_notes = len(records)
    before_cards = sum(len(record["cards"]) for record in records)
    before_projection = {
        int(record["note_id"]): {
            "fields": record["fields"], "tags": record["tags"],
            "cards": [card_projection(card) for card in record["cards"]],
        } for record in records
    }

    for group in plan["groups"]:
        survivor_id = group["survivor_id"]
        gw.anki("updateNoteFields", note={"id": survivor_id, "fields": group["fields"]})
        gw.anki("addTags", notes=[survivor_id], tags=MERGED_TAG)
    gw.anki("deleteNotes", notes=plan["delete_ids"])

    after = live_records()
    after_by_id = {int(record["note_id"]): record for record in after}
    expected_notes = before_notes - len(plan["delete_ids"])
    expected_cards = before_cards - sum(len(by_id[note_id]["cards"]) for note_id in plan["delete_ids"])
    if (len(after), sum(len(record["cards"]) for record in after)) != (expected_notes, expected_cards):
        raise AuditError("post-apply note/card inventory differs from policy")
    if set(plan["delete_ids"]) & set(after_by_id):
        raise AuditError("a redundant duplicate still exists after deletion")

    survivor_ids = {group["survivor_id"] for group in plan["groups"]}
    for group in plan["groups"]:
        survivor = after_by_id[group["survivor_id"]]
        if survivor["fields"] != group["fields"] or MERGED_TAG not in survivor["tags"]:
            raise AuditError(f"survivor merge verification failed: {group['survivor_id']}")
        try:
            target_highlights.parse_target_spans(
                survivor["fields"].get("ExampleTargetSpansJSON", ""),
                target_highlights.example_texts(survivor["fields"]),
            )
        except target_highlights.HighlightError as exc:
            raise AuditError(f"survivor target spans invalid: {group['survivor_id']}: {exc}") from exc
    allowed_changed = survivor_ids | set(plan["archive_ids"]) | set(plan["delete_ids"])
    for note_id, record in after_by_id.items():
        if note_id in allowed_changed:
            continue
        current = {"fields": record["fields"], "tags": record["tags"], "cards": [card_projection(card) for card in record["cards"]]}
        if current != before_projection[note_id]:
            raise AuditError(f"unrelated note or scheduling changed: {note_id}")
    for note_id in survivor_ids:
        if [card_projection(card) for card in after_by_id[note_id]["cards"]] != before_projection[note_id]["cards"]:
            raise AuditError(f"survivor scheduling changed: {note_id}")

    retained_card_ids = [
        int(card["cardId"]) for note_id in affected_ids - set(plan["delete_ids"])
        for card in after_by_id[note_id]["cards"]
    ]
    reviews_after = reviews_for_cards(retained_card_ids)
    expected_reviews = {
        key: value for key, value in reviews_before.items() if int(key) in set(retained_card_ids)
    }
    if canonical_hash(reviews_after) != canonical_hash(expected_reviews):
        raise AuditError("retained-card review history changed")

    after_audit = build_report(after)
    if after_audit["summary"]["MERGE_PROPOSED"] or after_audit["summary"]["REVIEW_REQUIRED"]:
        raise AuditError(f"post-apply duplicate audit is not clean: {after_audit['summary']}")
    result = {
        "schema_version": 1, "applied_utc": now_utc(),
        "backup": str(backup.resolve()), "backup_sha256": hash_file(backup),
        "snapshot": str(snapshot_path.resolve()),
        "before": {"notes": before_notes, "cards": before_cards, "inventory_signature": plan["inventory_signature"]},
        "after": {
            "notes": len(after), "cards": sum(len(record["cards"]) for record in after),
            "inventory_signature": after_audit["scope"]["inventory_signature"],
        },
        "groups": [{key: value for key, value in group.items() if key != "fields"} for group in plan["groups"]],
        "archive_ids": plan["archive_ids"], "delete_ids": plan["delete_ids"],
        "deleted_reviewed_history": [{
            "note_id": note_id,
            "card_ids": [int(card["cardId"]) for card in by_id[note_id]["cards"]],
            "reps": total_reps(by_id[note_id]),
            "review_entries": sum(len(reviews_before.get(str(card["cardId"]), [])) for card in by_id[note_id]["cards"]),
            "preserved": False,
        } for note_id in plan["delete_ids"] if total_reps(by_id[note_id]) or any(
            reviews_before.get(str(card["cardId"]), []) for card in by_id[note_id]["cards"]
        )],
        "retained_reviews_preserved": True, "after_audit": after_audit["summary"],
    }
    APPLY_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    APPLY_MARKDOWN.write_text(apply_markdown(result), encoding="utf-8")
    JSON_REPORT.write_text(json.dumps(after_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_REPORT.write_text(markdown_report(after_audit), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="read live Goethe notes and write review reports").set_defaults(func=command_audit)
    sub.add_parser("plan", help="revalidate and print the guarded apply policy").set_defaults(func=command_plan)
    apply = sub.add_parser("apply", help="backup and apply the thirteen reviewed merges")
    apply.add_argument("--confirmation", required=True)
    apply.set_defaults(func=command_apply)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (AuditError, gw.MigrationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
