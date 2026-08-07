"""Audit Goethe English glosses for redundant or over-broad translations.

This is a read-only audit of the canonical Goethe A1-B1 notes.  It does not
apply any changes to Anki or to the English-audit manifest.  The lexical
candidate list is deliberately conservative: it is checked against Cambridge,
Duden, Collins, and the Goethe/source context, then resolved either as a
dictionary-backed retention or as a proposed revision.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "review" / "goethe_english_audit_v5.jsonl"
SNAPSHOT = ROOT / "data" / "build" / "anki_notes.jsonl"
DEFAULT_REPORT = ROOT / "review" / "goethe_meaning_gloss_audit_v1.jsonl"
DEFAULT_SUMMARY = ROOT / "docs" / "GOETHE_MEANING_GLOSS_AUDIT_V1.md"


class GlossAuditError(RuntimeError):
    """Raised when the audit input or generated report is inconsistent."""


@dataclass(frozen=True)
class Decision:
    decision: str
    classification: str
    recommended_meaning_en: str
    note_en: str
    reason: str
    confidence: str


def review(
    recommended: str,
    note: str,
    reason: str,
    *,
    confidence: str = "medium",
    classification: str = "POTENTIAL_SYNONYM_BUNDLE",
    decision: str = "REVIEW",
) -> Decision:
    return Decision(
        decision=decision,
        classification=classification,
        recommended_meaning_en=recommended,
        note_en=note,
        reason=reason,
        confidence=confidence,
    )


def revise(
    recommended: str,
    note: str,
    reason: str,
    *,
    confidence: str = "high",
    classification: str = "REDUNDANT_OR_OVERBROAD_GLOSS",
) -> Decision:
    return review(
        recommended,
        note,
        reason,
        confidence=confidence,
        classification=classification,
        decision="PROPOSE_REVISE",
    )


def keep_explained(reason: str, *, classification: str = "DICTIONARY_BACKED_GLOSSES") -> Decision:
    return Decision(
        decision="KEEP_EXPLAINED",
        classification=classification,
        recommended_meaning_en="",
        note_en="",
        reason=reason,
        confidence="high",
    )


# High-confidence findings and deliberately bounded review proposals.  The
# evidence URLs are inherited from the existing v5 row rather than invented
# here; the report makes the current row and the proposed core gloss explicit.
DECISIONS: dict[str, Decision] = {
    "A2-0935": revise(
        "exciting",
        "gripping; suspenseful are contextual alternatives for books/films, not separate core glosses here.",
        "Cambridge gives the adjective sense as exciting; the Goethe example is a general film sentence.",
    ),
    "A2-0335": revise(
        "hard-working",
        "diligent is a useful synonym; busy is not the same adjective sense.",
        "The Goethe example describes a diligent, hard-working student; busy overstates or changes the sense.",
    ),
    "B1-MAIN-0178": revise(
        "to upset; to get upset; to annoy",
        "nervous and exciting belong to the related forms aufgeregt/aufregend used in the examples.",
        "The note is headed by the infinitive aufregen, but its final two glosses describe related adjectival forms. Preserve the distinct verb sense to annoy, and keep the related forms as example notes rather than direct infinitive glosses.",
        classification="RELATED_FORM_SCOPE",
    ),
    "B1-MAIN-2492": revise(
        "subway",
        "underground; tube (British English)",
        "Cambridge gives subway as the American core term and labels underground/tube as British alternatives.",
        classification="AMERICAN_VARIANT_CORE",
    ),
    "A1-84886454532": revise(
        "to get; to receive",
        "to have is a context-specific rendering of Was bekommen Sie?; it is not the core infinitive gloss.",
        "Duden and Collins give receive/get as the core meanings. The Goethe ordering example supports have as a contextual translation, but not as a standalone learner gloss.",
        classification="CONTEXTUAL_TRANSLATION_NOT_CORE_SENSE",
    ),
    "A2-0316": revise(
        "celebration; party",
        "feast is possible in a food-focused context, but is not the core sense in the Goethe holiday and birthday examples.",
        "Duden and Collins support the celebration/festival sense of Fest; the Goethe examples are a holiday greeting and a birthday party, not a meal or banquet.",
        classification="CONTEXT_SCOPE",
    ),
    "A2-0758": revise(
        "sweater",
        "jumper is the British-English regional equivalent.",
        "Collins and Cambridge support both terms for the garment, but this deck's reviewed target is American English, where sweater is the better core gloss.",
        classification="AMERICAN_VARIANT_CORE",
    ),
    "A2-0901": review(
        "program",
        "show; broadcast are broader/contextual alternatives for the television sense.",
        "Both Goethe examples describe a television program; the current three terms overlap rather than representing separate examples.",
        confidence="medium",
    ),
    "A2-1059": review(
        "to forbid",
        "to prohibit; forbidden/prohibited is the passive/participle form used by the example.",
        "The headword is a verb, while the example uses verboten. Separate the active infinitive from the related passive form.",
        classification="RELATED_FORM_SCOPE",
    ),
    "A2-1093": review(
        "to choose; to dial; to vote",
        "to select overlaps with to choose and can be a note rather than a fourth active gloss.",
        "The examples support choosing and dialing; Cambridge also distinguishes voting/electing as a separate sense.",
        classification="MULTI_SENSE_WITH_REDUNDANT_PAIR",
    ),
    "A1-84886454686": review(
        "finished; ready",
        "done is a natural contextual synonym of finished.",
        "The examples distinguish a person being done from an item being ready; done need not be a third core gloss.",
        confidence="medium",
    ),
    "A1-84886455058": review(
        "end; conclusion",
        "finish is a contextual verb/noun alternative.",
        "The examples use end/conclusion expressions; finish is not needed as a third noun gloss.",
        confidence="medium",
    ),
    "B1-MAIN-1539": review(
        "wage",
        "wages is the plural form, not a separate meaning.",
        "The row alternates singular and plural English for the same German lexeme.",
        classification="INFLECTIONAL_DUPLICATE",
    ),
    "B1-MAIN-1227": review(
        "contents",
        "content is the uncountable/abstract variant.",
        "The Goethe example is specifically the contents of a packet; the two forms should not be presented as separate senses.",
        classification="INFLECTIONAL_OR_COUNT_VARIANT",
    ),
    "B1-MAIN-2811": review(
        "tool",
        "tools is only the plural form.",
        "The current gloss duplicates singular and plural English rather than adding a second sense.",
        classification="INFLECTIONAL_DUPLICATE",
    ),
    "B1-WG-0016": review(
        "TV",
        "television is the expanded form.",
        "The two glosses are an abbreviation and its expansion, not separate meanings.",
        classification="ABBREVIATION_DUPLICATE",
    ),
    "A2-0078": keep_explained(
        "Cambridge explicitly supplies exciting and thrilling for the relevant adjective sense; this is not treated as an accidental synonym bundle.",
    ),
    "A1-84886454891": keep_explained(
        "Cambridge uses store as the core US term and the existing dictionary evidence also records shop as a valid regional alternative.",
        classification="REGIONAL_VARIANT_PAIR",
    ),
    "A1-84886455037": keep_explained(
        "Cambridge explicitly lists right and correct for the adjective; retain both unless the deck adopts a one-gloss editorial policy.",
    ),
    "B1-MAIN-2439": keep_explained(
        "Cambridge explicitly lists fatal and deadly for the literal sense, with additional context-dependent uses; no automatic collapse is proposed.",
    ),
    "B1-MAIN-2872": keep_explained(
        "Cambridge distinguishes absolutely beautiful and absolutely wonderful by context; the existing two glosses are sense-backed.",
    ),
    "B1-MAIN-2806": keep_explained(
        "The Goethe examples distinguish advertising as a mass noun from an individual advert/advertisement.",
        classification="COUNT_MASS_DISTINCTION",
    ),
}


# Conservative lexical triage set from the first pass.  Every member is now
# resolved by FINAL_REVIEW_KEEP_REASONS or an explicit proposal below; the set
# remains as a regression guard against silently losing a candidate.
POSSIBLE_SYNONYM_IDS = {
    "A1-84886454520", "A1-84886454673", "A1-84886454891",
    "A1-84886454916", "A1-84886454993", "A1-84886455037",
    "A1-84886455062", "A1-84886455064", "A1-84886455181",
    "A1-MAIN-0019", "A1-MAIN-0029", "A2-0078", "A2-0091",
    "A2-0404", "A2-0758", "A2-0792", "A2-0896", "A2-0946",
    "A2-1099", "A2-1195", "A2-1196", "B1-MAIN-0319",
    "B1-MAIN-0351", "B1-MAIN-0354", "B1-MAIN-0367",
    "B1-MAIN-0419", "B1-MAIN-0602", "B1-MAIN-0702",
    "B1-MAIN-0756", "B1-MAIN-0763", "B1-MAIN-0767",
    "B1-MAIN-0901", "B1-MAIN-0930", "B1-MAIN-1079",
    "B1-MAIN-1082", "B1-MAIN-1135", "B1-MAIN-1449",
    "B1-MAIN-1539", "B1-MAIN-1744", "B1-MAIN-1850",
    "B1-MAIN-1945", "B1-MAIN-1946", "B1-MAIN-2184",
    "B1-MAIN-2226", "B1-MAIN-2364", "B1-MAIN-2439",
    "B1-MAIN-2528", "B1-MAIN-2550", "B1-MAIN-2552",
    "B1-MAIN-2570", "B1-MAIN-2574", "B1-MAIN-2596",
    "B1-MAIN-2604", "B1-MAIN-2627", "B1-MAIN-2659",
    "B1-MAIN-2667", "B1-MAIN-2740", "B1-MAIN-2930",
    "B1-MAIN-2942", "B1-WG-0016", "A2-0316", "A2-0335",
    "A2-0901", "A2-1059", "A2-1093", "A2-0935",
    "B1-MAIN-0178", "B1-MAIN-2492", "B1-MAIN-2811",
    "B1-MAIN-1227",
}


# The first pass intentionally left these rows as REVIEW.  They are now
# resolved here after a second check against Duden, Collins, and the Goethe
# examples.  KEEP_EXPLAINED means the current gloss remains useful; it does
# not mean every item is a strict one-to-one synonym.
FINAL_REVIEW_KEEP_REASONS: dict[str, tuple[str, str]] = {
    "A1-84886454520": (
        "Collins lists start, begin, and formal commence; Duden defines the same beginning/start sense. The Goethe game sentence supports the ordinary verb, while commence remains a useful register variant.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "A1-84886454673": (
        "Duden separates not-right/not-correct uses of falsch, and Collins supports wrong and incorrect. The three Goethe examples all fit the error sense.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A1-84886454686": (
        "Duden distinguishes completed/finished from the state of being ready/done. The Goethe examples explicitly cover a person being done and a car being ready.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "A1-84886454916": (
        "Duden and Collins support rather/preferably as usage variants of lieber. The Goethe sentence expresses preference, so both remain useful learner-facing glosses.",
        "CONTEXTUAL_USAGE_VARIANT",
    ),
    "A1-84886454993": (
        "Duden marks Opa as familiar for grandfather, while Collins supports grandad and grandpa as English regional variants. Both current glosses are valid.",
        "REGIONAL_VARIANT_PAIR",
    ),
    "A1-84886455058": (
        "Duden gives Schluss as an ending/final point and separately records Schluss machen. The Goethe examples use both end/finally and finish/wrap up, so finish is not extraneous here.",
        "PHRASE_AND_NOUN_SENSES",
    ),
    "A1-84886455062": (
        "Duden and Collins support quick and fast for the speed adverb. The Goethe driving examples make fast the natural rendering, but quick is a valid common variant.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A1-84886455064": (
        "Collins gives beautiful and lovely for the appearance sense and also records nice/good for the broader evaluative sense. The Goethe examples cover lovely, nice, and beautiful contexts.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "A1-84886455181": (
        "Duden and Collins support perhaps and maybe, while the Goethe examples also use may and might. These are normal English modal alternatives, not a false German sense split.",
        "DICTIONARY_BACKED_MODAL_VARIANTS",
    ),
    "A1-MAIN-0019": (
        "Duden treats anfangen as a synonym of beginnen, and Collins lists start and begin. The Goethe examples all describe an activity or event starting; both common verbs are useful.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A1-MAIN-0029": (
        "Collins and Duden support the telephone sense; call and phone are ordinary English variants. Every Goethe example is a phone call.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A2-0091": (
        "Duden defines beenden as bringing something to an end, and Collins supports finish/end. The Goethe education example naturally uses finish; end remains a valid equivalent.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A2-0404": (
        "Duden distinguishes exactness from accuracy, and the Goethe examples cover exactly, for sure, and accurate. Exactly and precisely are both valid glosses for the core adverb.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "A2-0792": (
        "Duden and Collins support talk/speak for reden. The Goethe examples use talk about and talk so much; speak remains a valid general equivalent.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A2-0896": (
        "Duden and Collins support the strong negative adjective with awful/dreadful/terrible. Both current glosses fit the Goethe clothing and film examples.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A2-0901": (
        "Collins and Duden support the television sense as program/show/broadcast. The Goethe examples specifically use television programs, while the other two are normal contextual equivalents.",
        "TELEVISION_SENSE_VARIANTS",
    ),
    "A2-0946": (
        "Duden and Collins support floor/level for Stockwerk, with story as a regional English equivalent. The Goethe building example is unambiguously a floor.",
        "REGIONAL_AND_CONTEXTUAL_VARIANT",
    ),
    "A2-1059": (
        "Duden and Collins support forbid/prohibit for the active verb. The Goethe sentence uses verboten as a state, so the passive forbidden/prohibited gloss is deliberately retained with the active pair.",
        "RELATED_FORM_SCOPE",
    ),
    "A2-1093": (
        "Collins and Duden distinguish choose/select, dial, and vote/elect senses of wählen. The Goethe examples cover dial and choose; the full polysemy is worth retaining.",
        "POLYSEMOUS_VERB",
    ),
    "A2-1099": (
        "Duden and Collins support try/attempt for versuchen. The Goethe examples use the ordinary try construction; attempt is a valid more formal alternative.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "A2-1195": (
        "Duden and Collins support close/shut for the separable verb zumachen. The window example uses close, while shut is a natural everyday alternative.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "A2-1196": (
        "Duden and Collins support show/demonstrate. The route example uses show; demonstrate is the more formal but still valid equivalent.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-0319": (
        "Cambridge, Collins, and the German sense support need/require; require is the slightly more formal equivalent. The Goethe request is ordinary need-context.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-0351": (
        "Duden and Collins support own/possess for the ownership sense. The Goethe car example is ordinary own-context, while possess is a valid formal alternative.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-0354": (
        "Duden and Collins support get/obtain for the procurement sense of besorgen. The Goethe ticket example uses get; obtain remains a valid formal variant.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-0367": (
        "Duden and Collins support amount/sum for a monetary Betrag. The bank-transfer example naturally uses amount; sum is a valid equivalent in the same domain.",
        "MONETARY_SYNONYM_PAIR",
    ),
    "B1-MAIN-0419": (
        "Duden and Collins support bloom/blossom for plant flowering, with natural collocation differences. The Goethe trees example specifically uses in blossom.",
        "COLLOCATION_VARIANT",
    ),
    "B1-MAIN-0602": (
        "Duden and Collins support haste/hurry, and the Goethe fixed expressions use hurry/no rush. Both glosses describe the same noun sense without a misleading extra meaning.",
        "FIXED_EXPRESSION_VARIANT",
    ),
    "B1-MAIN-0702": (
        "Duden and Collins support happen/occur for sich ereignen; occur is the natural formal choice for the accident example, while happen is the ordinary alternative.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-0756": (
        "Duden and Collins support story/narrative for the literary noun Erzählung. The Goethe text is a story, and narrative is a valid literary register variant.",
        "LITERARY_REGISTER_VARIANT",
    ),
    "B1-MAIN-0763": (
        "Duden and Collins support floor and the regional English equivalent story for Etage. The Goethe office location uses floor; retaining both is useful for American/British exposure.",
        "REGIONAL_VARIANT_PAIR",
    ),
    "B1-MAIN-0767": (
        "Duden and Collins support possibly/perhaps for eventuell. The Goethe sentence expresses possibility, not the English false friend eventual; both current glosses are accurate.",
        "FALSE_FRIEND_CONTROLLED_VARIANTS",
    ),
    "B1-MAIN-0901": (
        "Duden and Collins support joy/pleasure for Freude. The Goethe collocation makes pleasure/enjoy natural, while joy remains the direct noun gloss.",
        "COLLOCATION_VARIANT",
    ),
    "B1-MAIN-0930": (
        "Duden and Collins support work/function for funktionieren. The Goethe examples deliberately cover a machine working and a marriage functioning/working.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "B1-MAIN-1079": (
        "Duden and Collins support found/establish for gründen. The company example uses found; establish is a valid formal equivalent and is not the unrelated verb find.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-1082": (
        "Duden and Collins support reason/grounds for Grund. The Goethe examples use reason; grounds is a valid formal or legal plural variant.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-1135": (
        "Duden and Collins support lift/raise, and the two Goethe examples explicitly distinguish lifting a parcel from raising a hand.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "B1-MAIN-1227": (
        "Duden distinguishes the contents of a container from abstract content, and the Goethe packet example requires the count plural contents. Both forms are useful to show the count/mass contrast.",
        "COUNT_MASS_DISTINCTION",
    ),
    "B1-MAIN-1449": (
        "Duden and Collins support bend/curve for the road sense of Kurve. The Goethe traffic examples use the common British road word bend, while curve remains a valid general equivalent.",
        "ROAD_SENSE_VARIANT",
    ),
    "B1-MAIN-1539": (
        "Duden and Collins support wage/wages; the Goethe examples intentionally include singular Lohn and plural Löhne. This is a useful number/context distinction, not a false translation.",
        "COUNT_VARIANT_IN_SOURCE_EXAMPLES",
    ),
    "B1-MAIN-1744": (
        "Duden and Collins support normally/usually as ordinary adverb variants. The Goethe question uses usually, while normally is equally natural.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "B1-MAIN-1850": (
        "Duden and Collins support duty/obligation for Pflicht. The Goethe insurance example renders the predicate as compulsory; both noun glosses remain accurate.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "B1-MAIN-1945": (
        "Duden and Collins support react/respond. The Goethe written-message example naturally uses respond, while react covers the broader verb sense.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "B1-MAIN-1946": (
        "Duden and Collins support reaction/response for Reaktion. The Goethe example is a characteristic reaction; response is a valid equivalent in communication contexts.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "B1-MAIN-2184": (
        "Duden and Collins support vertical/perpendicular as related geometric uses of senkrecht. The Goethe line example is vertical; perpendicular remains useful for the geometric relation.",
        "GEOMETRIC_SENSE_PAIR",
    ),
    "B1-MAIN-2226": (
        "Duden and Collins support worry/concern for Sorge. The Goethe reflexive expression uses worry, while concern is the normal noun variant.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "B1-MAIN-2364": (
        "Duden and Collins support sum/amount for a monetary Summe. The Goethe example uses amount; sum is the direct mathematical/financial equivalent.",
        "MONETARY_SYNONYM_PAIR",
    ),
    "B1-MAIN-2528": (
        "Duden and Collins support hug/embrace for umarmen. Hug is the everyday Goethe-context rendering; embrace remains a valid formal/literary variant.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-2550": (
        "Duden and Collins support approximately/about for ungefähr. The Goethe walking-time estimate naturally uses about; approximately is the formal equivalent.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-2552": (
        "Duden and Collins support unbelievable/incredible for unglaublich. The Goethe exclamation expresses disbelief, and both current glosses fit that use.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "B1-MAIN-2570": (
        "Duden and Collins support teach/instruct. The Goethe school-subject example uses teach; instruct remains a valid formal sense.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-2574": (
        "Duden and Collins support different/dissimilar. The Goethe comparison is ordinary different-context; dissimilar is a valid more formal adjective.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-2596": (
        "Duden and Collins support change/alter for both the transitive organisation example and the reflexive person example. Alter is a formal equivalent, not a different German lexeme.",
        "CONTEXTUAL_REGISTER_VARIANT",
    ),
    "B1-MAIN-2604": (
        "Duden and Collins support ban/prohibition for Verbot. The Goethe sentence uses ban on, while prohibition is the formal noun equivalent.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-2627": (
        "Duden and Collins support behavior/conduct for Verhalten. The Goethe example is ordinary behavior-context; conduct is a valid formal equivalent.",
        "REGISTER_AND_SPELLING_VARIANT",
    ),
    "B1-MAIN-2659": (
        "Duden and Collins support sensible/reasonable, and the two Goethe examples deliberately distinguish a sensible person from a reasonable proposal.",
        "CONTEXTUAL_SENSE_PAIR",
    ),
    "B1-MAIN-2667": (
        "Duden and Collins support crazy/mad. The Goethe examples cover both a person and an idea; mad is also a common British equivalent.",
        "REGIONAL_AND_CONTEXTUAL_VARIANT",
    ),
    "B1-MAIN-2740": (
        "Duden and Collins support suggest/propose for vorschlagen. The Goethe sentence uses suggest; propose is a valid formal alternative.",
        "DICTIONARY_BACKED_REGISTER_VARIANT",
    ),
    "B1-MAIN-2811": (
        "Duden and Collins support tool and tools/equipment. The Goethe repair example is plural in English, so the count variant is useful rather than a second German sense.",
        "COUNT_VARIANT_IN_SOURCE_EXAMPLES",
    ),
    "B1-MAIN-2930": (
        "Duden and Collins support close/shut for the door sense of zugehen. The Goethe example uses close; shut is a natural everyday alternative.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "B1-MAIN-2942": (
        "Duden and Collins support cope/manage for zurechtkommen. The Goethe reply uses manage on my own; cope is the direct equivalent in the same self-sufficiency sense.",
        "DICTIONARY_BACKED_SYNONYM_PAIR",
    ),
    "B1-WG-0016": (
        "Duden and Collins support television and the abbreviation TV. The source headword and example intentionally use TV, so retaining the expansion is helpful.",
        "ABBREVIATION_EXPANSION_PAIR",
    ),
}


DIRECT_REVIEW_CANDIDATE_IDS = set(FINAL_REVIEW_KEEP_REASONS) | {
    "A1-84886454532",
    "A2-0316",
    "A2-0758",
}

for _source_id, (_reason, _classification) in FINAL_REVIEW_KEEP_REASONS.items():
    DECISIONS[_source_id] = keep_explained(_reason, classification=_classification)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise GlossAuditError(f"cannot read JSONL: {path}") from exc


def load_manifest() -> dict[str, Any]:
    import goethe_english_audit as english_audit

    manifest = english_audit.load_json(MANIFEST)
    english_audit.validate_manifest(manifest)
    return manifest


def load_snapshot_rows() -> list[dict[str, Any]]:
    return load_jsonl(SNAPSHOT)


def load_live_rows() -> list[dict[str, Any]]:
    import export_goethe_notes_jsonl as exporter

    return exporter.load_live_rows()


def choose_rows(source: str) -> tuple[list[dict[str, Any]], str, str | None]:
    if source == "snapshot":
        return load_snapshot_rows(), "checked-in snapshot", None
    if source == "live":
        return load_live_rows(), "AnkiConnect live collection", None
    try:
        return load_live_rows(), "AnkiConnect live collection", None
    except Exception as exc:  # noqa: BLE001 - auto mode must provide a safe fallback
        return load_snapshot_rows(), "checked-in snapshot (live fallback)", str(exc)


def split_gloss(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


_DUDEN_PATH_OVERRIDES = {
    "A1-84886454916": "lieber_besser_moeglichst",
    "A1-84886455181": "vielleicht_eventuell_circa",
    "B1-MAIN-2550": "suchen/dudenonline/ungefähr",
    "B1-MAIN-2627": "suchen/dudenonline/Verhalten",
    "B1-WG-0016": "suchen/dudenonline/TV",
}

_COLLINS_PATH_OVERRIDES = {
    # Collins disambiguates schön from schon with a numbered page.
    "A1-84886455064": "schon_2",
}


def _ascii_dictionary_slug(value: str) -> str:
    value = str(value or "").lower()
    value = value.replace("(sich)", "").replace("sich ", "")
    value = value.replace("ß", "ss")
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def secondary_evidence(source_id: str, lemma: str) -> list[dict[str, str]]:
    """Return corrected independent-source links for the second-pass review."""
    duden_path = _DUDEN_PATH_OVERRIDES.get(source_id)
    if duden_path is None:
        duden_path = _ascii_dictionary_slug(lemma)
    collins_path = _COLLINS_PATH_OVERRIDES.get(source_id)
    if collins_path is None:
        collins_path = _ascii_dictionary_slug(lemma)
    if duden_path.startswith("suchen/"):
        duden_url = "https://www.duden.de/" + quote(duden_path, safe="/")
    else:
        duden_url = "https://www.duden.de/rechtschreibung/" + quote(duden_path, safe="/")
    return [
        {
            "provider": "Duden",
            "url": duden_url,
            "supports": "German sense, usage and part of speech",
        },
        {
            "provider": "Collins",
            "url": "https://www.collinsdictionary.com/dictionary/german-english/" + quote(collins_path, safe="-"),
            "supports": "Independent German-English translation and register/variant check",
        },
    ]


def default_decision(current: str) -> Decision:
    parts = split_gloss(current)
    if len(parts) <= 1 and "/" not in current and " or " not in current.lower():
        return Decision(
            decision="KEEP",
            classification="SINGLE_CORE_GLOSS",
            recommended_meaning_en=current,
            note_en="",
            reason="Single core gloss; no redundant multi-gloss pattern.",
            confidence="high",
        )
    return Decision(
        decision="KEEP",
        classification="MULTI_GLOSS_RETAINED",
        recommended_meaning_en=current,
        note_en="",
        reason="No high-confidence same-sense reduction was recorded in this second pass; retain the reviewed multi-sense or qualified gloss.",
        confidence="medium",
    )


def triage_decision(source_id: str, current: str) -> Decision:
    known = DECISIONS.get(source_id)
    if known is not None:
        if known.recommended_meaning_en:
            return known
        return Decision(
            decision=known.decision,
            classification=known.classification,
            recommended_meaning_en=current,
            note_en=known.note_en,
            reason=known.reason,
            confidence=known.confidence,
        )
    if source_id in POSSIBLE_SYNONYM_IDS:
        parts = split_gloss(current)
        recommended = parts[0] if parts else current
        note = "; ".join(parts[1:])
        return review(
            recommended,
            note,
            "Lexical triage found overlapping glosses; confirm directly against Cambridge and the Goethe examples before changing the deck.",
            confidence="low",
        )
    return default_decision(current)


def build_report(rows: list[dict[str, Any]], manifest: dict[str, Any], source_label: str) -> list[dict[str, Any]]:
    entries = manifest.get("entries", {})
    live_by_source = {str(row.get("source_id", "")): row for row in rows}
    expected_ids = set(entries)
    actual_ids = set(live_by_source)
    if len(rows) != len(live_by_source):
        raise GlossAuditError("duplicate source_id in input rows")
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise GlossAuditError(f"source identity mismatch; missing={missing[:3]} extra={extra[:3]}")

    report: list[dict[str, Any]] = []
    for source_id, entry in entries.items():
        live = live_by_source[source_id]
        current = str(live.get("meaning_en", ""))
        expected = str(entry.get("desired_meaning_en", ""))
        decision = triage_decision(source_id, current)
        flagged = decision.decision != "KEEP"
        report.append({
            "schema_version": 1,
            "source_id": source_id,
            "stable_guid": entry.get("stable_guid", live.get("guid", "")),
            "anki_note_id": live.get("anki_note_id"),
            "card_count": len(live.get("card_ids", [])),
            "lemma": live.get("lemma", entry.get("lemma", "")),
            "cefr": live.get("cefr", entry.get("cefr", "")),
            "pos": live.get("pos", entry.get("pos", "")),
            "current_meaning_en": current,
            "manifest_meaning_en": expected,
            "meaning_matches_manifest": current == expected,
            "gloss_parts": split_gloss(current),
            "source": source_label,
            "decision": decision.decision,
            "classification": decision.classification,
            "recommended_meaning_en": decision.recommended_meaning_en,
            "note_en": decision.note_en,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "example_count": len(entry.get("desired_examples", [])),
            "evidence": entry.get("evidence", []),
            "review_basis": "Duden + Collins + Goethe/source context" if flagged else "",
            "secondary_evidence": secondary_evidence(source_id, str(live.get("lemma", entry.get("lemma", "")))) if flagged else [],
        })
    return report


def validate_report(report: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    expected_ids = set(manifest.get("entries", {}))
    actual_ids = [row.get("source_id") for row in report]
    if len(report) != len(expected_ids) or set(actual_ids) != expected_ids:
        raise GlossAuditError("generated report does not cover the canonical catalog exactly")
    if len(actual_ids) != len(set(actual_ids)):
        raise GlossAuditError("generated report contains duplicate source_id values")
    if any(not row.get("decision") for row in report):
        raise GlossAuditError("generated report contains a row without a decision")
    if any(not row.get("evidence") for row in report if row["decision"] != "KEEP"):
        raise GlossAuditError("flagged row is missing dictionary evidence")
    if any(row["decision"] == "REVIEW" for row in report):
        unresolved = [row["source_id"] for row in report if row["decision"] == "REVIEW"]
        raise GlossAuditError(f"second-pass review left unresolved rows: {unresolved[:5]}")
    if any(not row.get("secondary_evidence") for row in report if row["decision"] != "KEEP"):
        raise GlossAuditError("flagged row is missing independent secondary evidence")
    candidate_rows = [row for row in report if row["source_id"] in DIRECT_REVIEW_CANDIDATE_IDS]
    if len(candidate_rows) != 65:
        raise GlossAuditError(f"direct-review candidate count changed: {len(candidate_rows)}")
    if any(row["decision"] == "REVIEW" for row in candidate_rows):
        raise GlossAuditError("direct-review candidate remains unresolved")
    spannend = next(row for row in report if row["source_id"] == "A2-0935")
    if spannend["decision"] != "PROPOSE_REVISE" or spannend["recommended_meaning_en"] != "exciting":
        raise GlossAuditError("spannend regression decision is missing")


def write_jsonl(report: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in report),
        encoding="utf-8",
        newline="\n",
    )


def render_summary(report: list[dict[str, Any]], source_label: str, live_error: str | None) -> str:
    counts = Counter(row["decision"] for row in report)
    flagged = [row for row in report if row["decision"] != "KEEP"]
    proposals = [row for row in flagged if row["decision"] == "PROPOSE_REVISE"]
    review_rows = [row for row in flagged if row["decision"] == "REVIEW"]
    explained = [row for row in flagged if row["decision"] == "KEEP_EXPLAINED"]
    candidate_rows = [row for row in report if row["source_id"] in DIRECT_REVIEW_CANDIDATE_IDS]
    candidate_proposals = [row for row in candidate_rows if row["decision"] == "PROPOSE_REVISE"]
    candidate_keeps = [row for row in candidate_rows if row["decision"] == "KEEP_EXPLAINED"]
    lines = [
        "# Goethe meaning/gloss audit v1",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Data source: {source_label}",
        "Scope: canonical Goethe Werkstatt A1-B1 English meaning fields.",
        "",
        "## Result",
        "",
        f"- Notes audited: {len(report)}; cards represented: {sum(row['card_count'] for row in report)}.",
        f"- Proposed revisions: {len(proposals)} total ({len(candidate_proposals)} from the 65 direct-review candidates).",
        f"- Direct-review candidates completed: {len(candidate_rows)}; retained as valid: {len(candidate_keeps)}; unresolved: {len(review_rows)}.",
        f"- Dictionary-backed rows retained with explanation: {len(explained)}.",
        f"- Unflagged rows retained: {counts.get('KEEP', 0)}.",
        "- No deck, Anki note, source Markdown, or v5 manifest was modified by this audit.",
        "",
        "The second pass checked the 65 candidates against Duden, Collins, and the Goethe/source context. Valid regional, register, count, collocation, and polysemy variants were retained; only core-gloss or context risks became proposals.",
        "",
        "## Proposed revisions",
        "",
        "| Source | Lemma | Current | Recommendation | Confidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in proposals:
        lines.append(
            f"| `{row['source_id']}` | `{row['lemma']}` | {row['current_meaning_en']} | {row['recommended_meaning_en']} | {row['confidence']} |"
        )
    lines.extend([
        "",
        "## Completed direct review",
        "",
        f"All {len(candidate_rows)} former REVIEW rows are resolved. {len(candidate_keeps)} retain their current glosses with an explicit reason; {len(candidate_proposals)} have a proposed core-gloss change. The per-note JSONL carries the Duden and Collins links used for each flagged row.",
        "",
        "| Outcome | Count |",
        "| --- | ---: |",
        f"| Retain current gloss (`KEEP_EXPLAINED`) | {len(candidate_keeps)} |",
        f"| Propose core-gloss revision (`PROPOSE_REVISE`) | {len(candidate_proposals)} |",
        f"| Unresolved (`REVIEW`) | {len(review_rows)} |",
    ])
    lines.extend([
        "",
        "## Controls and issues",
        "",
        "- Every report row carries the canonical source identity and the existing v5 evidence list.",
        "- Every flagged row additionally carries corrected Duden and Collins links in `secondary_evidence`; the v5 manifest itself was not rewritten.",
        "- `A2-0935` (`spannend`) is the regression case: the proposed core gloss is `exciting`; `gripping` and `suspenseful` are recorded as contextual notes.",
    ])
    if live_error:
        lines.append(f"- Live Anki read failed in auto mode and the checked-in snapshot was used: `{live_error}`")
    else:
        lines.append("- AnkiConnect live read succeeded; no fallback was used.")
    lines.extend([
        "- This is a report-only pass. Any future application requires a separate reviewed change and the repository's backup/verification gates.",
        "",
        "The complete per-note artifact is `review/goethe_meaning_gloss_audit_v1.jsonl`.",
        "",
    ])
    return "\n".join(lines)


def write_summary(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", nargs="?", default="audit", choices=["audit"])
    parser.add_argument("--source", choices=["auto", "live", "snapshot"], default="auto")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest()
        rows, source_label, live_error = choose_rows(args.source)
        report = build_report(rows, manifest, source_label)
        validate_report(report, manifest)
        write_jsonl(report, args.report)
        write_summary(render_summary(report, source_label, live_error), args.summary)
    except (GlossAuditError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    counts = Counter(row["decision"] for row in report)
    print(json.dumps({
        "report": str(args.report),
        "summary": str(args.summary),
        "notes": len(report),
        "cards": sum(row["card_count"] for row in report),
        "decisions": dict(sorted(counts.items())),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
