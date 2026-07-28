"""Schema and review policy for the Goethe Werkstatt templates.

The legacy fields are intentionally kept as source data.  This module derives
the unambiguous, article-bearing answer field and applies the small set of
human-reviewed production hints/disablements used by the English -> German
card.  It is deliberately independent of AnkiConnect and of the HTML
templates so it can be used by migration, rollout, and offline audits.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "review" / "goethe_production_overrides.json"

ENABLED = "1"
DISABLED = ""
_ARTICLES = {"der", "die", "das"}
_TAG_RE = re.compile(r"<[^>]*>")
_ANNOTATION_RE = re.compile(r"\s+\((?:pl|sg)\.\)\s*$", re.I)
_ARTICLE_RE = re.compile(r"^(der|die|das)\s+(.+)$", re.I)
_SEMANTIC_DELIMITER_RE = re.compile(r"[\s,;/|]+")
_SEMANTIC_DASH_RE = re.compile(r"[\u2010-\u2015\u2212]+")
_WORD_BOUNDARY_RE = re.compile(r"[^\w\u00c0-\uFFFF]+", re.UNICODE)


class PolicyError(ValueError):
    """Raised when a record or review policy cannot be resolved safely."""


def _text(value: Any, *, field: str = "value") -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PolicyError(f"{field} must be text, got {type(value).__name__}")
    return value


def _normalise(value: Any) -> str:
    """Normalise text used in visible-cue comparisons."""
    value = html.unescape(_text(value))
    value = _TAG_RE.sub(" ", value)
    value = unicodedata.normalize("NFC", value)
    value = value.replace("’", "'")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalise_semantic(value: Any) -> str:
    """Normalise an English cue for cross-level semantic comparisons.

    The production prompt is intentionally audited globally.  CEFR, POS,
    gender, and example text are useful context, but they are not separate
    meanings and therefore must not split a collision group.  English source
    rows occasionally use a slash, comma, or semicolon for the same list of
    senses; treating those delimiters uniformly prevents a false exemption.
    """
    value = html.unescape(_text(value))
    value = _TAG_RE.sub(" ", value)
    value = unicodedata.normalize("NFC", value)
    value = value.replace("â€™", "'").replace("’", "'")
    value = _SEMANTIC_DASH_RE.sub("-", value)
    value = _SEMANTIC_DELIMITER_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def split_full_answers(value: Any) -> list[str]:
    """Split a pipe-delimited full-answer field, rejecting empty members."""
    if isinstance(value, (list, tuple)):
        values = [_text(item, field="answer") for item in value]
    else:
        raw = _text(value, field="AcceptedFullAnswersDE")
        if not raw.strip():
            return []
        values = raw.split("|")
    result = [item.strip() for item in values if item.strip()]
    if len(result) != len(values):
        raise PolicyError("full answers contain an empty member")
    if any("|" in item or "\r" in item or "\n" in item for item in result):
        raise PolicyError("full answers contain a pipe or line break")
    return result


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        key = _normalise(value)
        if not key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _strip_annotation(value: str) -> str:
    return _ANNOTATION_RE.sub("", value).strip()


def _article_prefix(value: str, *, explicit_metadata: bool = False) -> tuple[str | None, str]:
    """Return an article prefix and lexical remainder when one is present.

    A capitalised ``Das macht nichts`` is a phrase, not an article-bearing
    answer.  The small case heuristic avoids misclassifying that common A1
    answer while still accepting ``Der Arzt`` and lower-case forms.
    """
    match = _ARTICLE_RE.match(value.strip())
    if not match:
        return None, value.strip()
    token, rest = match.group(1).lower(), match.group(2).strip()
    first = value.strip().split(None, 1)[0]
    if explicit_metadata or first.islower() or (rest and rest[0].isupper()):
        return token, rest
    return None, value.strip()


def _article_values(fields: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    saw_metadata = False
    for name in ("AcceptedArticlesDE", "Article"):
        raw = _text(fields.get(name, ""), field=name).strip()
        if not raw:
            continue
        saw_metadata = True
        for part in raw.split("|"):
            part = part.strip()
            if not part:
                raise PolicyError(f"{name} contains an empty member")
            pieces = [piece.strip().casefold() for piece in part.split("/")]
            if any(piece not in _ARTICLES for piece in pieces):
                raise PolicyError(f"{name} contains an invalid article: {part!r}")
            values.extend(pieces)
    values = _dedupe(values)
    if saw_metadata and not values:
        raise PolicyError("article metadata is present but empty")
    return values


def _validate_answer_form(value: str) -> str:
    value = _strip_annotation(_text(value, field="answer"))
    if not value or any(ord(char) < 32 for char in value):
        raise PolicyError("full answer is empty or contains control characters")
    # One inherited Wortgruppen entry uses ``<br>`` as a lexical separator;
    # preserve that harmless markup, but reject every other tag.
    markup_remainder = re.sub(r"<br\s*/?>", "", value, flags=re.I)
    decoded_remainder = re.sub(r"<br\s*/?>", "", html.unescape(value), flags=re.I)
    if (
        "|" in value
        or "<" in markup_remainder
        or ">" in markup_remainder
        or "<" in decoded_remainder
        or ">" in decoded_remainder
    ):
        raise PolicyError(f"unsafe full answer: {value!r}")
    article, lexical = _article_prefix(value, explicit_metadata=True)
    if article and not lexical:
        raise PolicyError(f"full answer has no lexical form: {value!r}")
    return value


def _canonical_forms(value: Any) -> list[str]:
    forms = [_validate_answer_form(item) for item in split_full_answers(value)]
    forms = _dedupe(forms)
    if not forms:
        raise PolicyError("full answer list is empty")
    return forms


def derive_full_answers(fields: Mapping[str, Any], override: Any = None) -> list[str]:
    """Derive canonical answer forms from legacy fields.

    A single lexical answer may safely receive one or more reviewed articles.
    Multiple lexical answers combined with article metadata are ambiguous and
    fail closed; callers must provide an explicit reviewed override instead.
    Slash-separated lexical text is deliberately *not* split.
    """
    if override is not None:
        return _canonical_forms(override)
    if not isinstance(fields, Mapping):
        raise PolicyError("record fields must be a mapping")

    raw_answers = _text(fields.get("AcceptedAnswersDE", ""), field="AcceptedAnswersDE").strip()
    answers = split_full_answers(raw_answers) if raw_answers else []
    if not answers:
        lemma = _text(fields.get("Lemma", ""), field="Lemma").strip()
        if lemma:
            answers = [lemma]
    if not answers:
        raise PolicyError("record has no lexical answer or Lemma")

    articles = _article_values(fields)
    explicit: list[tuple[str | None, str]] = [
        _article_prefix(answer, explicit_metadata=bool(articles)) for answer in answers
    ]
    has_prefix = any(article for article, _ in explicit)
    if has_prefix:
        if not all(article for article, _ in explicit):
            raise PolicyError("mixed article-bearing and lexical answers require an override")
        prefixes = _dedupe(article for article, _ in explicit if article)
        if articles and set(prefixes) != set(articles):
            raise PolicyError("article-bearing answers disagree with article metadata")
        return _canonical_forms([
            f"{article} {lexical}" for article, (_, lexical) in explicit if article
        ])

    lexical = [_strip_annotation(answer) for answer in answers]
    lexical = _dedupe(lexical)
    if not lexical:
        raise PolicyError("record has no lexical answer")
    if not articles:
        return [_validate_answer_form(answer) for answer in lexical]
    if len(lexical) != 1:
        raise PolicyError(
            "multiple lexical answers plus article metadata require an explicit override"
        )
    return _canonical_forms([f"{article} {lexical[0]}" for article in articles])


def _normalise_policy(data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise PolicyError("production policy must be a JSON object")
    if (
        "version" in data
        and "schema_version" in data
        and data["version"] != data["schema_version"]
    ):
        raise PolicyError("production policy version fields disagree")
    version = data.get("version", data.get("schema_version"))
    if type(version) is not int or version != 1:
        raise PolicyError(f"unsupported production policy version: {version!r}")
    unknown = set(data) - {"version", "schema_version", "answers", "production"}
    if unknown:
        raise PolicyError(f"unknown production policy keys: {sorted(unknown)}")

    raw_answers = data.get("answers")
    raw_production = data.get("production")
    if not isinstance(raw_answers, Mapping) or not isinstance(raw_production, Mapping):
        raise PolicyError("production policy requires object-valued answers and production maps")

    answers: dict[str, str] = {}
    for source_id, raw in raw_answers.items():
        if (
            not isinstance(source_id, str)
            or not source_id.strip()
            or source_id != source_id.strip()
        ):
            raise PolicyError("answer override has an invalid SourceID")
        forms = _canonical_forms(raw)
        answers[source_id] = "|".join(forms)

    production: dict[str, dict[str, str]] = {}
    for source_id, raw in raw_production.items():
        if (
            not isinstance(source_id, str)
            or not source_id.strip()
            or source_id != source_id.strip()
        ):
            raise PolicyError("production override has an invalid SourceID")
        if not isinstance(raw, Mapping):
            raise PolicyError(f"production override {source_id!r} must be an object")
        if set(raw) - {"enabled", "hint"}:
            raise PolicyError(f"unknown keys in production override {source_id!r}")
        enabled_raw = raw.get("enabled", True)
        if type(enabled_raw) is bool:
            enabled = ENABLED if enabled_raw else DISABLED
        elif isinstance(enabled_raw, str) and enabled_raw in {ENABLED, DISABLED}:
            enabled = enabled_raw
        else:
            raise PolicyError(
                f"production override {source_id!r} must encode enabled as '1' or ''"
            )
        if "hint" in raw and raw["hint"] is None:
            raise PolicyError(f"production hint {source_id!r} cannot be null")
        hint = _text(raw.get("hint", ""), field=f"production hint {source_id}").strip()
        decoded_hint = html.unescape(hint)
        if (
            any(char in hint for char in "|<>\r\n")
            or any(char in decoded_hint for char in "<>")
            or any(ord(char) < 32 for char in hint)
        ):
            raise PolicyError(f"unsafe production hint for {source_id!r}")
        if not enabled and hint:
            raise PolicyError(f"disabled production record {source_id!r} cannot have a hint")
        production[source_id] = {"enabled": enabled, "hint": hint}
    return {"version": 1, "answers": answers, "production": production}


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate key in production policy: {key!r}")
        result[key] = value
    return result


def load_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    """Load and validate the reviewed JSON policy."""
    path = Path(path)
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot load production policy {path}: {exc}") from exc
    return _normalise_policy(data)


def visible_cue(fields: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the text visible before the learner supplies a production answer."""
    if not isinstance(fields, Mapping):
        raise PolicyError("record fields must be a mapping")
    return tuple(
        _normalise(fields.get(name, ""))
        for name in ("CEFR", "POS", "MeaningEN", "Example1EN", "Gender", "ProductionHint")
    )


def semantic_cue(fields: Mapping[str, Any]) -> str:
    """Return the global English meaning key used by ambiguity audits.

    Only ``MeaningEN`` participates.  In particular, level, part of speech,
    gender, and examples must not make two otherwise identical English cues
    appear distinct.
    """
    if not isinstance(fields, Mapping):
        raise PolicyError("record fields must be a mapping")
    return _normalise_semantic(fields.get("MeaningEN", ""))


def _audit_answers(fields: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the canonical accepted-answer set for semantic comparisons."""
    raw = fields.get("AcceptedFullAnswersDE", "")
    if isinstance(raw, str) and raw.strip():
        forms = split_full_answers(raw)
    elif isinstance(raw, (list, tuple)) and raw:
        forms = split_full_answers(raw)
    else:
        forms = derive_full_answers(fields)
    return tuple(sorted({_normalise(form) for form in forms if _normalise(form)}))


def _record_fields(records: Any) -> list[MutableMapping[str, Any]]:
    """Extract mutable field dictionaries from common manifest shapes."""
    if isinstance(records, Mapping):
        if isinstance(records.get("fields"), MutableMapping):
            return [records["fields"]]
        if isinstance(records.get("records"), (Mapping, list, tuple)):
            return _record_fields(records["records"])
        # A mapping keyed by note/source ID is a common manifest shape.
        values = list(records.values())
        if values and all(
            isinstance(value, Mapping)
            and ("fields" in value or "SourceID" in value or "Lemma" in value)
            for value in values
        ):
            result: list[MutableMapping[str, Any]] = []
            for value in values:
                result.extend(_record_fields(value))
            return result
        if isinstance(records, MutableMapping):
            return [records]
        raise PolicyError("record fields must be mutable")
    if isinstance(records, (list, tuple)):
        result = []
        for record in records:
            result.extend(_record_fields(record))
        return result
    raise PolicyError("records must be a mapping or sequence")


def audit_visible_cues(records: Any) -> dict[str, Any]:
    """Audit active records for identical visible English-side cues."""
    fields_list = _record_fields(records)
    grouped: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    active = 0
    disabled = 0
    for index, fields in enumerate(fields_list):
        enabled = fields.get("ProductionEnabled", ENABLED)
        if enabled not in {ENABLED, DISABLED}:
            raise PolicyError(f"invalid ProductionEnabled encoding at record {index}: {enabled!r}")
        if enabled != ENABLED:
            disabled += 1
            continue
        active += 1
        source_id = _text(fields.get("SourceID", ""), field="SourceID").strip() or f"record-{index}"
        lemma = _text(fields.get("Lemma", ""), field="Lemma").strip()
        grouped[visible_cue(fields)].append((source_id, lemma))

    groups: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    for cue in sorted(grouped):
        members = sorted(grouped[cue], key=lambda item: (item[0], item[1].casefold()))
        group = {
            "cue": list(cue),
            "source_ids": [source_id for source_id, _ in members],
            "lemmas": [lemma for _, lemma in members],
        }
        groups.append(group)
        if len({source_id for source_id, _ in members}) > 1:
            collisions.append(group)
    return {
        "records": len(fields_list),
        "active": active,
        "disabled": disabled,
        "groups": groups,
        "collision_groups": collisions,
        "collisions": collisions,
    }


def audit_semantic_ambiguity(
    records: Any,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Audit EN -> DE prompts across the complete A1-B1 corpus.

    A group is formed from the normalized ``MeaningEN`` alone.  Groups with a
    single accepted-answer set are safe (for example duplicated source rows
    that intentionally point at the same German answer).  When answer sets
    differ, every active member must have a non-empty, unique
    ``ProductionHint`` that does not contain its German answer.  This keeps
    distinctions such as ``Kuchen``/``Torte`` and ``Führung``/``Rundgang``
    visible without changing the reviewed English meaning.

    ``strict=False`` returns a report for review tooling.  ``strict=True``
    raises :class:`PolicyError` when any group is unresolved, a hint leaks an
    answer, or an active record has no English meaning.  The separate strict
    switch lets level-specific/offline manifests be inspected before the full
    reviewed corpus is available.
    """
    fields_list = _record_fields(records)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    active = 0
    disabled = 0
    missing_meaning: list[str] = []

    for index, fields in enumerate(fields_list):
        enabled = fields.get("ProductionEnabled", ENABLED)
        if enabled not in {ENABLED, DISABLED}:
            raise PolicyError(
                f"invalid ProductionEnabled encoding at record {index}: {enabled!r}"
            )
        source_id = (
            _text(fields.get("SourceID", ""), field="SourceID").strip()
            or f"record-{index}"
        )
        if enabled != ENABLED:
            disabled += 1
            continue
        active += 1
        cue = semantic_cue(fields)
        if not cue:
            missing_meaning.append(source_id)
            continue
        answers = _audit_answers(fields)
        hint = _text(
            fields.get("ProductionHint", ""), field=f"ProductionHint {source_id}"
        ).strip()
        grouped[cue].append(
            {
                "source_id": source_id,
                "lemma": _text(fields.get("Lemma", ""), field="Lemma").strip(),
                "answers": answers,
                "hint": hint,
            }
        )

    groups: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    invalid_hints: list[dict[str, Any]] = []
    same_answer_exemptions = 0
    hinted_groups = 0

    for cue in sorted(grouped):
        members = sorted(
            grouped[cue],
            key=lambda item: (item["source_id"], item["lemma"].casefold()),
        )
        answer_sets = sorted({tuple(item["answers"]) for item in members})
        group_answers = tuple(
            sorted({answer for member in members for answer in member["answers"]})
        )
        hint_keys: dict[str, list[str]] = defaultdict(list)
        for member in members:
            hint = member["hint"]
            if hint:
                hint_key = _normalise_semantic(hint)
                hint_keys[hint_key].append(member["source_id"])
                if _hint_reveals_answer(hint, group_answers):
                    invalid_hints.append(
                        {
                            "source_id": member["source_id"],
                            "hint": hint,
                            "reason": "hint_contains_group_accepted_german_answer",
                        }
                    )
            else:
                hint_keys[""].append(member["source_id"])

        duplicate_hints = {
            key: source_ids
            for key, source_ids in hint_keys.items()
            if key and len(source_ids) > 1
        }
        distinct_answers = len(answer_sets) > 1
        complete_hints = (
            len(members) > 1
            and all(member["hint"] for member in members)
            and not duplicate_hints
            and not any(
                item["source_id"] == member["source_id"]
                for item in invalid_hints
                for member in members
            )
        )
        if len(members) > 1 and not distinct_answers:
            status = "same_answers"
            same_answer_exemptions += 1
        elif distinct_answers and complete_hints:
            status = "hinted"
            hinted_groups += 1
        elif len(members) == 1:
            status = "single"
        else:
            status = "unresolved"

        group = {
            "semantic_key": cue,
            "source_ids": [member["source_id"] for member in members],
            "lemmas": [member["lemma"] for member in members],
            "answer_sets": [list(answer_set) for answer_set in answer_sets],
            "hints": [member["hint"] for member in members],
            "status": status,
        }
        if duplicate_hints:
            group["duplicate_hints"] = duplicate_hints
        groups.append(group)
        if distinct_answers and status == "unresolved":
            collisions.append(group)

    report = {
        "records": len(fields_list),
        "active": active,
        "disabled": disabled,
        "groups": groups,
        "semantic_groups": groups,
        "collisions": collisions,
        "collision_groups": collisions,
        "invalid_hints": invalid_hints,
        "missing_meaning": sorted(missing_meaning),
        "same_answer_exemptions": same_answer_exemptions,
        "hinted_groups": hinted_groups,
    }
    if strict and (collisions or invalid_hints or missing_meaning):
        details: list[str] = []
        if collisions:
            details.append(
                "unresolved semantic groups: "
                + ", ".join(
                    "/".join(group["source_ids"]) for group in collisions
                )
            )
        if invalid_hints:
            details.append(
                "invalid production hints: "
                + ", ".join(item["source_id"] for item in invalid_hints)
            )
        if missing_meaning:
            details.append(
                "active records without MeaningEN: " + ", ".join(missing_meaning)
            )
        raise PolicyError("; ".join(details))
    return report


def audit_global_ambiguity(records: Any, *, strict: bool = False) -> dict[str, Any]:
    """Compatibility alias for callers that use the global-audit name."""
    return audit_semantic_ambiguity(records, strict=strict)


def _hint_reveals_answer(hint: str, answers: Sequence[str]) -> bool:
    hint_key = _normalise_semantic(hint)
    if not hint_key:
        return False
    for answer in answers:
        answer_key = _normalise_semantic(answer)
        article, lexical = _article_prefix(answer, explicit_metadata=True)
        lexical_key = _normalise_semantic(lexical)
        if hint_key in {answer_key, lexical_key}:
            return True
        # A hint such as "the Kuchen entry" is just as answer-revealing as
        # the bare word.  Ignore one-character forms (for example the
        # reviewed regional hint ``A``), but reject complete lexical tokens
        # and multi-word answer phrases.
        for candidate in (answer_key, lexical_key):
            if len(candidate) < 3:
                continue
            pattern = rf"(?<![\w\u00c0-\uFFFF]){re.escape(candidate)}(?![\w\u00c0-\uFFFF])"
            if re.search(pattern, hint_key, flags=re.UNICODE):
                return True
    return False


def apply_policy(
    records: Any,
    policy: Mapping[str, Any] | str | Path | None = None,
    *,
    strict: bool = True,
    semantic_strict: bool = False,
) -> dict[str, Any]:
    """Apply answer/production policy in place and return an audit report.

    ``strict=True`` additionally verifies that every reviewed SourceID occurs
    in the supplied record set.  Partial level-specific manifests can pass
    ``strict=False``; all record-level validation and legacy visible-cue
    checks remain enabled in either mode.  ``semantic_strict=True`` enables
    the cross-level EN -> DE audit; it is kept opt-in while a reviewed
    full-corpus ambiguity catalog is being completed.
    """
    if policy is None:
        normalised = load_policy()
    elif isinstance(policy, (str, Path)):
        normalised = load_policy(policy)
    else:
        normalised = _normalise_policy(policy)
    fields_list = _record_fields(records)
    if not fields_list:
        raise PolicyError("cannot apply production policy to an empty record set")

    seen: set[str] = set()
    overrides_applied: list[str] = []
    enabled_count = 0
    disabled_count = 0
    planned: list[tuple[MutableMapping[str, Any], dict[str, str]]] = []
    review_overrides: dict[str, dict[str, Any]] = {}
    try:
        import goethe_review_policy
        review_overrides = goethe_review_policy.load_policy().get("records", {})
    except ImportError:
        review_overrides = {}
    for index, fields in enumerate(fields_list):
        source_id = _text(fields.get("SourceID", ""), field="SourceID").strip()
        if not source_id:
            raise PolicyError(f"record {index} has no SourceID")
        if source_id in seen:
            raise PolicyError(f"duplicate SourceID in records: {source_id}")
        seen.add(source_id)

        old_enabled = fields.get("ProductionEnabled")
        if old_enabled not in (None, ENABLED, DISABLED):
            raise PolicyError(f"invalid existing ProductionEnabled for {source_id}: {old_enabled!r}")
        rule = normalised["production"].get(source_id, {"enabled": ENABLED, "hint": ""})
        enabled = rule["enabled"]
        hint = rule["hint"]
        if enabled == ENABLED:
            override = normalised["answers"].get(source_id)
            reviewed = review_overrides.get(source_id, {}).get("set", {})
            if reviewed.get("AcceptedFullAnswersDE"):
                override = reviewed["AcceptedFullAnswersDE"]
            elif fields.get("Lemma", "").strip().startswith("sich "):
                stem = fields["Lemma"].strip()[5:]
                override = f"sich {stem}|s {stem}"
            full_answers = derive_full_answers(fields, override=override)
            enabled_count += 1
        else:
            full_answers = []
            disabled_count += 1
        if _hint_reveals_answer(hint, full_answers):
            raise PolicyError(f"production hint reveals answer for {source_id}: {hint!r}")

        target_spans = fields.get("ExampleTargetSpansJSON")
        if target_spans is None:
            target_spans = ""
        elif not isinstance(target_spans, str):
            raise PolicyError(f"ExampleTargetSpansJSON must be text for {source_id}")
        planned.append((fields, {
            "AcceptedFullAnswersDE": "|".join(full_answers),
            "ProductionEnabled": enabled,
            "ProductionHint": hint,
            "ExampleTargetSpansJSON": target_spans,
        }))
        if source_id in normalised["answers"]:
            overrides_applied.append(source_id)

    if strict:
        unknown = (set(normalised["answers"]) | set(normalised["production"])) - seen
        if unknown:
            raise PolicyError(f"review policy SourceIDs missing from records: {sorted(unknown)}")

    # Audit a detached view first.  A failed review must not leave a caller's
    # records half-mutated (important for completion manifests and dry runs).
    staged_fields: list[dict[str, Any]] = []
    for fields, update in planned:
        staged = dict(fields)
        staged.update(update)
        staged_fields.append(staged)
    report = audit_visible_cues(staged_fields)
    if report["collisions"]:
        details = "; ".join(
            ", ".join(group["source_ids"]) for group in report["collisions"]
        )
        raise PolicyError(f"active visible-cue collisions remain: {details}")
    semantic_report = audit_semantic_ambiguity(staged_fields)
    if semantic_strict:
        # Re-run in strict mode so the error includes the exact unresolved
        # source IDs and invalid hints rather than silently returning a
        # partial completion manifest.
        audit_semantic_ambiguity(staged_fields, strict=True)
    for fields, update in planned:
        fields.update(update)
    report.update({
        "enabled": enabled_count,
        "disabled": disabled_count,
        "overrides_applied": sorted(overrides_applied),
        "semantic_ambiguity": semantic_report,
    })
    return report


__all__ = [
    "DISABLED",
    "ENABLED",
    "POLICY_PATH",
    "PolicyError",
    "apply_policy",
    "audit_global_ambiguity",
    "audit_semantic_ambiguity",
    "audit_visible_cues",
    "derive_full_answers",
    "load_policy",
    "split_full_answers",
    "semantic_cue",
    "visible_cue",
]
