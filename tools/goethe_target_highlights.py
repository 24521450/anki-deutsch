"""Precompute deterministic target-word ranges for Goethe card examples."""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import goethe_examples


class HighlightError(RuntimeError):
    pass


class _Candidate(str):
    def __new__(
        cls, value: str, *, case_sensitive: bool = False, case_mode: str = "fold",
        role: str = "standalone", pair_key: str = "", boundary_mode: str = "default",
        sequence_index: int = -1, sequence_size: int = 0,
    ) -> _Candidate:
        candidate = super().__new__(cls, value)
        candidate.case_sensitive = case_sensitive
        candidate.case_mode = "exact" if case_sensitive else case_mode
        candidate.role = role
        candidate.pair_key = pair_key
        candidate.boundary_mode = boundary_mode
        candidate.sequence_index = sequence_index
        candidate.sequence_size = sequence_size
        return candidate


ROOT = Path(__file__).resolve().parents[1]
VERB_POLICY_PATH = ROOT / "review" / "goethe_verb_target_policy.json"
HIGHLIGHT_POLICY_PATH = ROOT / "review" / "goethe_target_highlight_audit_v3.json"


@lru_cache(maxsize=1)
def verb_policy() -> dict[str, Any]:
    try:
        value = json.loads(VERB_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HighlightError(f"verb target policy is unavailable: {exc}") from exc
    if value.get("schema_version") != 1:
        raise HighlightError("unsupported verb target policy schema")
    blank = value.get("blank_pos_verb_source_ids")
    specs = value.get("verb_specs")
    if not isinstance(blank, list) or len(blank) != 22 or len(set(blank)) != 22:
        raise HighlightError("verb target policy must contain 22 blank-POS identities")
    if not isinstance(specs, dict):
        raise HighlightError("verb target policy specs are missing")
    overrides = value.get("exact_overrides")
    if not isinstance(overrides, list) or len(overrides) != 15:
        raise HighlightError("verb target policy must contain 15 semantic overrides")
    return value


@lru_cache(maxsize=1)
def highlight_policy() -> dict[str, Any]:
    try:
        value = json.loads(HIGHLIGHT_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HighlightError(f"target-highlight audit is unavailable: {exc}") from exc
    if value.get("schema_version") != 1 or not isinstance(value.get("cases"), list):
        raise HighlightError("unsupported target-highlight audit schema")
    seen: set[tuple[str, int]] = set()
    allowed = {"missing_certain", "needs_review", "valid_blank"}
    for case in value["cases"]:
        if not isinstance(case, dict) or set(case) != {
            "source_id", "example_index", "text", "status", "reason", "targets",
        }:
            raise HighlightError("target-highlight audit case has invalid fields")
        key = (case["source_id"], case["example_index"])
        if (
            not isinstance(key[0], str) or not key[0]
            or not isinstance(key[1], int) or isinstance(key[1], bool) or key[1] < 1
            or key in seen or case["status"] not in allowed
            or not isinstance(case["text"], str) or not case["text"]
            or not isinstance(case["reason"], str) or not case["reason"]
            or not isinstance(case["targets"], list)
        ):
            raise HighlightError("target-highlight audit case is invalid")
        if case["status"] == "missing_certain" and not case["targets"]:
            raise HighlightError("missing-certain audit case has no targets")
        if case["status"] != "missing_certain" and case["targets"]:
            raise HighlightError("non-repair audit case must not have targets")
        seen.add(key)
    return value


def _audit_target_ranges(text: str, targets: list[Any]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for target in targets:
        occurrence: int | None = None
        if isinstance(target, str):
            surface = target
        elif (
            isinstance(target, dict) and set(target) == {"text", "occurrence"}
            and isinstance(target.get("text"), str)
            and isinstance(target.get("occurrence"), int)
            and not isinstance(target.get("occurrence"), bool)
            and target["occurrence"] > 0
        ):
            surface = target["text"]
            occurrence = target["occurrence"]
        else:
            raise HighlightError("reviewed audit target is invalid")
        starts = [match.start() for match in re.finditer(re.escape(surface), text)]
        if not starts or (occurrence is not None and occurrence > len(starts)):
            raise HighlightError("reviewed audit target is absent")
        if occurrence is not None:
            starts = [starts[occurrence - 1]]
        result.extend((start, start + len(surface)) for start in starts)
    ranges = sorted(set(result))
    if any(start < prior_end for prior_end, (start, _) in zip((end for _, end in ranges), ranges[1:])):
        raise HighlightError("reviewed audit targets overlap")
    return ranges


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def visible_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(str(value or ""))
    parser.close()
    return html.unescape("".join(parser.parts))


def split_pipe(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def _strip_gender_qualifier(value: str) -> str:
    return re.sub(r"\s*\((?:männlich|weiblich)\)\s*$", "", value, flags=re.I).strip()


def _notation_candidates(value: str) -> list[_Candidate]:
    """Translate Goethe dictionary notation into visible surface components."""
    value = str(value or "").strip()
    if not value:
        return []

    sequence = [part.strip() for part in re.split(r"\s*(?:…|\.{3})\s*", value) if part.strip()]
    if len(sequence) >= 2:
        pair_key = "notation:" + re.sub(r"\s+", " ", value).casefold()
        return [
            _Candidate(
                part, role="sequence", pair_key=pair_key,
                sequence_index=index, sequence_size=len(sequence),
            )
            for index, part in enumerate(sequence)
        ]

    has_slash = "/" in value
    has_bound_edge = value.startswith("-") or value.endswith("-")
    optional = re.search(r"\(([^()]*)\)", value)
    if not has_slash and not has_bound_edge and not optional:
        return []

    variants = value.split("/") if has_slash else [value]
    if optional:
        before, after = value[:optional.start()], value[optional.end():]
        if not before and after.startswith("-"):
            variants = [after[1:], optional.group(1) + after[1:]]
        else:
            variants = [before + after, before + optional.group(1) + after]

    result: list[_Candidate] = []
    for variant in variants:
        variant = re.sub(r"\s+", " ", variant).strip()
        if not variant:
            continue
        leading = variant.startswith("-")
        trailing = variant.endswith("-")
        surface = variant.removeprefix("-").removesuffix("-").strip()
        if not surface:
            continue
        boundary_mode = "suffix" if leading else "prefix" if trailing else "default"
        result.append(_Candidate(surface, boundary_mode=boundary_mode))
    return result


def _umlaut(value: str) -> str:
    for index in range(len(value) - 2, -1, -1):
        if value[index:index + 2].casefold() == "au":
            replacement = "Äu" if value[index].isupper() else "äu"
            return value[:index] + replacement + value[index + 2:]
    replacements = {"a": "ä", "o": "ö", "u": "ü", "A": "Ä", "O": "Ö", "U": "Ü"}
    for index in range(len(value) - 1, -1, -1):
        if value[index] in replacements:
            return value[:index] + replacements[value[index]] + value[index + 1 :]
    return value


def _add_first_person(values: list[str], infinitive: str) -> None:
    if re.search(r"en$", infinitive, re.I):
        values.append(infinitive[:-2] + "e")


def _usable_verb_forms(fields: dict[str, str]) -> str:
    raw = str(fields.get("VerbFormsRaw") or "").strip()
    if raw and not re.fullmatch(r"(?:A|CH)\)", raw, flags=re.I):
        return raw.split(";", 1)[0].strip()
    source = html.unescape(str(fields.get("SourceNoteRaw") or ""))
    if source.lstrip().startswith("{"):
        prefix = source.split("|", 1)[0].strip()
        try:
            parsed = json.loads(prefix)
        except json.JSONDecodeError:
            parsed = {}
        form = str(parsed.get("Verbformen") or "").strip()
        if form:
            return form.split(";", 1)[0].strip()
    match = re.search(r"(?:^|\|)\s*source:\s*(.*)$", source, flags=re.I)
    if not match:
        return ""
    return re.split(r";\s*(?:Expansion/context:)?", match.group(1), maxsplit=1, flags=re.I)[0].strip()


def _split_forms(raw: str) -> list[str]:
    values: list[str] = []
    for chunk in str(raw or "").split(","):
        chunk = re.sub(r"\b(?:jdn?\.|jdm\.)\b", "", chunk, flags=re.I).strip()
        if not chunk:
            continue
        alternatives = [part.strip() for part in chunk.split("/")]
        for alternative in alternatives:
            alternative = re.sub(r"\s*\((?:von|auf|CH|A)\)\s*$", "", alternative, flags=re.I).strip()
            if alternative:
                values.append(alternative)
    return values


def _strip_verb_lemma(value: str) -> str:
    value = re.sub(r"^\(sich\)\s*", "", value, flags=re.I)
    value = re.sub(r"^sich\s+", "", value, flags=re.I)
    value = re.sub(r"\s+\([^()]+\)\s*$", "", value).strip()
    return value


def _verb_stem(infinitive: str) -> str:
    if re.search(r"eln$", infinitive, flags=re.I):
        return infinitive[:-1]
    if re.search(r"ern$", infinitive, flags=re.I):
        return infinitive[:-1]
    if re.search(r"en$", infinitive, flags=re.I):
        return infinitive[:-2]
    if re.search(r"n$", infinitive, flags=re.I):
        return infinitive[:-1]
    return infinitive


def _weak_participle(infinitive: str, particle: str = "") -> str:
    stem = _verb_stem(infinitive)
    if not stem or infinitive.casefold().endswith("ieren"):
        return ""
    if not particle and re.match(r"^(?:be|emp|ent|er|ge|miss|ver|zer)", infinitive, flags=re.I):
        return ""
    ending = "et" if re.search(r"[dt]$", stem, flags=re.I) else "t"
    return particle + "ge" + stem + ending


def _verb_surfaces(infinitive: str, raw_forms: list[str]) -> set[str]:
    surfaces = {infinitive}
    stem = _verb_stem(infinitive)
    if not stem:
        return surfaces
    if infinitive.casefold().endswith("eln"):
        contracted = stem[:-2] + stem[-1]
        surfaces.update({stem + "e", contracted + "e", stem + "st", stem + "t", infinitive})
    elif infinitive.casefold().endswith("ern"):
        surfaces.update({stem + "e", stem[:-1] + "re", stem + "st", stem + "t", infinitive})
    else:
        needs_e = bool(re.search(r"(?:[td]|[bcdfgkp]m|[bcdfgkp]n)$", stem, flags=re.I))
        surfaces.update({stem, stem + "e", stem + "en"})
        if needs_e:
            surfaces.update({stem + "est", stem + "et"})
        else:
            second = stem + ("t" if re.search(r"[sxzß]$", stem, flags=re.I) else "st")
            surfaces.update({second, stem + "t"})

    stop = {"hat", "ist", "sind", "wird", "sein", "haben", "sich"}
    simple_forms: list[str] = []
    for form in raw_forms:
        for part in form.split():
            if re.fullmatch(r"\([^()]+\)", part):
                continue
            clean = part.strip(".,;:()")
            if clean and clean.casefold() not in stop:
                surfaces.add(clean)
                simple_forms.append(clean)
    for form in simple_forms:
        lower = form.casefold()
        if lower.endswith("t") and len(form) > 3:
            present_stem = form[:-1]
            surfaces.update({present_stem, present_stem + "e", present_stem + "st", present_stem + "t"})
        if lower.endswith("te") and len(form) > 4:
            surfaces.update({form + "n", form + "st", form + "t"})

    irregular = {
        "sein": ("bin", "bist", "ist", "sind", "seid", "war", "waren", "gewesen"),
        "haben": ("habe", "hast", "hat", "haben", "habt", "hatte", "hatten", "gehabt"),
        "werden": ("werde", "wirst", "wird", "werden", "werdet", "wurde", "wurden", "geworden"),
        "dürfen": ("darf", "darfst", "dürfen", "dürft"),
        "können": ("kann", "kannst", "können", "könnt"),
        "mögen": ("mag", "magst", "mögen", "mögt"),
        "müssen": ("muss", "musst", "müssen", "müsst"),
        "sollen": ("soll", "sollst", "sollen", "sollt"),
        "wissen": ("weiß", "weißt", "wissen", "wisst", "wusste", "gewusst"),
    }
    surfaces.update(irregular.get(infinitive.casefold(), ()))
    participle = _weak_participle(infinitive)
    if participle:
        surfaces.add(participle)
    return {value for value in surfaces if len(value) > 1}


def _verb_spec(fields: dict[str, str], lemma: str, raw_forms: list[str]) -> dict[str, Any] | None:
    source_id = str(fields.get("SourceID") or "")
    policy = verb_policy()
    pos = str(fields.get("POS") or "")
    is_verb = bool(re.fullmatch(r"v\.?", pos, flags=re.I))
    if not is_verb and source_id not in policy["blank_pos_verb_source_ids"]:
        return None
    reviewed = policy["verb_specs"].get(source_id)
    if reviewed:
        return dict(reviewed)

    lexical = _strip_verb_lemma(lemma)
    tokens = lexical.split()
    if not tokens:
        return None
    if len(tokens) > 1:
        head = tokens[-1]
        if not re.search(r"(?:en|eln|ern|sein|haben|werden|lassen|leben|bleiben|gehen|geben|sagen|fahren)$", head, flags=re.I):
            return None
        predicate = [token for token in tokens[:-1] if token.casefold() not in {"sich", "etwas", "über"}]
        return {"head": head, "predicate": predicate}

    head = lexical
    for form in raw_forms:
        parts = form.split()
        if len(parts) < 2:
            continue
        particle = parts[-1].strip(".,;:()")
        if len(particle) > 1 and head.casefold().startswith(particle.casefold()):
            base = head[len(particle):]
            if re.search(r"(?:en|eln|ern)$", base, flags=re.I):
                return {"head": base, "particle": particle}
    return {"head": head}


def candidate_terms(fields: dict[str, str]) -> list[str]:
    lemma = str(fields.get("Lemma") or "").strip()
    lexical_lemma = _strip_gender_qualifier(lemma)
    accepted = [_strip_gender_qualifier(value) for value in split_pipe(fields.get("AcceptedAnswersDE", ""))]
    values = [lemma, lexical_lemma, *accepted]
    for value in [lemma, lexical_lemma, *accepted]:
        values.extend(_notation_candidates(value))
    lexical_bases: list[str] = []
    seen_bases: set[str] = set()
    for value in [lexical_lemma, *accepted]:
        key = value.casefold()
        if value and key not in seen_bases:
            seen_bases.add(key)
            lexical_bases.append(value)

    noun = str(fields.get("NounFormsRaw") or "")
    for suffix in re.findall(r"-(?:e|en|er|n|s)\b", noun, flags=re.I):
        values.extend(base + suffix[1:] for base in lexical_bases)
    marker_suffixes = re.findall(
        r'(?:¨-|"-?)(en|er|e|n|s)?(?!\w)',
        noun,
        flags=re.I,
    )
    if marker_suffixes:
        suffix = max(marker_suffixes, key=len)
        values.extend(
            _Candidate(_umlaut(base) + suffix, case_sensitive=True)
            for base in lexical_bases
        )
    if re.search(r"-[AÄÖÜäöü]", noun):
        ending = re.search(r",\s*(e|er|en|n)?\b", noun, flags=re.I)
        values.extend(
            _umlaut(base) + (ending.group(1) if ending and ending.group(1) else "")
            for base in lexical_bases
        )

    pos = str(fields.get("POS") or "")
    if re.fullmatch(r"n\.?", pos, flags=re.I):
        for base in lexical_bases:
            if not base or re.search(r"[/…()]|\.{3}", base):
                continue
            compound_surfaces = {base, _umlaut(base) + "e", _umlaut(base) + "er"}
            standalone_surfaces: set[str] = set()
            if base.casefold().endswith("in") and len(base) > 3:
                masculine = base[:-2]
                standalone_surfaces.update({masculine, masculine + "e", masculine + "en"})
            elif base.casefold().endswith("e") and len(base) > 2:
                standalone_surfaces.add(base[:-1] + "in")
            elif base.casefold().endswith("er"):
                standalone_surfaces.add(base + "in")
            values.extend(
                _Candidate(surface, boundary_mode="compound")
                for surface in compound_surfaces if surface
            )
            values.extend(standalone_surfaces)
    if re.fullmatch(r"n\.?", pos, flags=re.I) and re.search(r"-(?:n|en)\b", noun, flags=re.I):
        for base in lexical_bases:
            if re.search(r"e$", base, flags=re.I):
                values.extend(base[:-1] + ending for ending in ("e", "en", "em", "er", "es"))

    raw_forms = _split_forms(_usable_verb_forms(fields))
    spec = _verb_spec(fields, lexical_lemma, raw_forms)
    if spec:
        pair_key = "verb:" + (str(fields.get("SourceID") or "") or lexical_lemma.casefold())
        head = str(spec["head"])
        head_surfaces = _verb_surfaces(head, raw_forms)
        particle = str(spec.get("particle") or "")
        role = "head" if particle or spec.get("predicate") else "standalone"
        for value in head_surfaces:
            value_role = role
            value_pair = pair_key
            if particle and value.casefold().startswith(particle.casefold()) and value.casefold() != particle.casefold():
                value_role = "standalone"
                value_pair = ""
            values.append(_Candidate(
                value, case_mode="verb", role=value_role, pair_key=value_pair,
            ))
        values.extend(
            _Candidate(str(value), case_mode="verb")
            for value in spec.get("forms") or []
        )
        values.extend(
            _Candidate(str(value), case_mode="verb", role=role, pair_key=pair_key)
            for value in spec.get("head_forms") or []
        )
        if particle:
            values.append(_Candidate(particle, role="particle", pair_key=pair_key))
            joined = _strip_verb_lemma(lexical_lemma).replace(" ", "")
            if joined:
                values.append(_Candidate(joined, case_mode="verb"))
            if head.casefold().startswith("zu"):
                pass
            else:
                values.append(_Candidate(particle + "zu" + head, case_mode="verb"))
            participle = _weak_participle(head, particle)
            if participle:
                values.append(_Candidate(participle, case_mode="verb"))
        for predicate in spec.get("predicate") or []:
            values.append(_Candidate(str(predicate), role="predicate", pair_key=pair_key))
        for form in raw_forms:
            values.append(_Candidate(form, case_mode="verb"))

    if re.search(r"adj|adv|det|pron", pos, flags=re.I):
        inflection_bases = [lexical_lemma] + [value for value in accepted if value.endswith("-")]
        for inflection_base in dict.fromkeys(inflection_bases):
            base = inflection_base.removesuffix("-")
            if not base or re.search(r"[/…()]|\.{3}|\s", base):
                continue
            values.append(_Candidate(base, boundary_mode="prefix"))
            values.extend(base + ending for ending in ("e", "en", "em", "er", "es"))
            if base.casefold().endswith("el"):
                contracted = base[:-2] + base[-1]
                values.extend(contracted + ending for ending in ("e", "en", "em", "er", "es"))
            values.extend((_umlaut(base) + "er", base + "er"))

    result: list[str] = []
    seen: set[tuple[str, str, str, str, str, int, int]] = set()
    for value in values:
        case_sensitive = bool(getattr(value, "case_sensitive", False))
        case_mode = str(getattr(value, "case_mode", "fold"))
        role = str(getattr(value, "role", "standalone"))
        pair_key = str(getattr(value, "pair_key", ""))
        boundary_mode = str(getattr(value, "boundary_mode", "default"))
        sequence_index = int(getattr(value, "sequence_index", -1))
        sequence_size = int(getattr(value, "sequence_size", 0))
        value = str(value or "").strip()
        key = (
            value.casefold(), case_mode, role, pair_key, boundary_mode,
            sequence_index, sequence_size,
        )
        if value and key not in seen:
            seen.add(key)
            result.append(_Candidate(
                value, case_sensitive=case_sensitive, case_mode=case_mode,
                role=role, pair_key=pair_key, boundary_mode=boundary_mode,
                sequence_index=sequence_index, sequence_size=sequence_size,
            ))
    return sorted(result, key=len, reverse=True)


def _bounded_pattern(candidate: str, pos: str) -> str:
    escaped = re.escape(candidate)
    boundary_mode = str(getattr(candidate, "boundary_mode", "default"))
    if boundary_mode == "prefix":
        return rf"(?<!\w){escaped}"
    if boundary_mode == "suffix":
        return rf"(?<=\w){escaped}(?!\w)"
    if boundary_mode == "compound":
        return rf"(?<=\w){escaped}"
    is_noun = bool(re.fullmatch(r"n\.?", str(pos or ""), flags=re.I))
    if len(candidate) < 4 or not is_noun:
        return rf"(?<!\w){escaped}(?!\w)"
    return escaped


def match_ranges(text: str, candidates: Iterable[str], pos: str = "") -> list[tuple[int, int]]:
    occurrences: list[tuple[int, int, str, str, int, int]] = []
    for candidate in candidates:
        if not candidate:
            continue
        flags = 0 if getattr(candidate, "case_sensitive", False) else re.I
        matcher = re.compile(_bounded_pattern(candidate, pos), flags=flags)
        for match in matcher.finditer(text):
            if getattr(candidate, "case_mode", "fold") == "verb":
                surface = match.group(0)
                if surface and surface[0].isupper() and str(candidate)[0].islower():
                    prefix = re.sub(r"[\s\"'„“»«(\[]+$", "", text[:match.start()])
                    if prefix and prefix[-1] not in ".!?;:\n–—":
                        continue
            occurrences.append((
                match.start(), match.end(), str(getattr(candidate, "role", "standalone")),
                str(getattr(candidate, "pair_key", "")),
                int(getattr(candidate, "sequence_index", -1)),
                int(getattr(candidate, "sequence_size", 0)),
            ))

    def clause_at(index: int) -> int:
        return len(re.findall(r"[,.!?;:\n–—]", text[:index]))

    retained: list[tuple[int, int]] = []
    grouped: dict[str, list[tuple[int, int, str, str, int, int]]] = {}
    for item in occurrences:
        if item[2] == "standalone" or not item[3]:
            retained.append(item[:2])
        else:
            grouped.setdefault(item[3], []).append(item)
    for items in grouped.values():
        sequence_items = [item for item in items if item[2] == "sequence"]
        if sequence_items:
            size = max(item[5] for item in sequence_items)
            cursor = 0
            while size > 0:
                chain: list[tuple[int, int, str, str, int, int]] = []
                for sequence_index in range(size):
                    choices = [
                        item for item in sequence_items
                        if item[4] == sequence_index and item[0] >= cursor
                    ]
                    if not choices:
                        chain = []
                        break
                    chosen = min(choices, key=lambda item: item[0])
                    chain.append(chosen)
                    cursor = chosen[1]
                if not chain:
                    break
                retained.extend(item[:2] for item in chain)
            continue
        heads = [item for item in items if item[2] == "head"]
        particles = [item for item in items if item[2] == "particle"]
        predicates = [item for item in items if item[2] == "predicate"]
        retained.extend(item[:2] for item in predicates)
        used: set[tuple[int, int]] = set()
        for head in heads:
            clause = clause_at(head[0])
            if particles:
                choices = [
                    item for item in particles
                    if clause_at(item[0]) == clause and item[0] >= head[1] and item[:2] not in used
                ]
                if not choices:
                    continue
                chosen = max(choices, key=lambda item: item[0])
                used.add(chosen[:2])
                retained.extend((head[:2], chosen[:2]))
            elif any(clause_at(item[0]) == clause for item in predicates):
                retained.append(head[:2])
    ranges = list(set(retained))
    ranges.sort(key=lambda value: (value[0], -(value[1] - value[0])))
    selected: list[tuple[int, int]] = []
    for value in ranges:
        if not selected or value[0] >= selected[-1][1]:
            selected.append(value)
    return selected


def _utf16_offset(value: str, index: int) -> int:
    return len(value[:index].encode("utf-16-le")) // 2


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def example_texts(fields: dict[str, str]) -> list[str]:
    return [visible_text(example.get("de", "")) for example in goethe_examples.parse_fields(fields)]


def build_target_spans(fields: dict[str, str]) -> str:
    candidates = candidate_terms(fields)
    pos = str(fields.get("POS") or "")
    source_id = str(fields.get("SourceID") or "")
    overrides = {
        int(item["example_index"]): item
        for item in verb_policy()["exact_overrides"]
        if item["source_id"] == source_id
    }
    audit_cases = {
        int(item["example_index"]): item
        for item in highlight_policy()["cases"]
        if item["source_id"] == source_id
    }
    texts = example_texts(fields)
    if audit_cases and max(audit_cases) > len(texts):
        raise HighlightError(f"reviewed audit example is absent: {source_id}")
    result = []
    for index, text in enumerate(texts, 1):
        ranges = [
            [_utf16_offset(text, start), _utf16_offset(text, end)]
            for start, end in match_ranges(text, candidates, pos)
        ]
        override = overrides.get(index)
        if override:
            if override.get("text") != text:
                raise HighlightError(f"reviewed override text drift: {source_id} example {index}")
            ranges.extend([list(pair) for pair in override["ranges"]])
            ranges = [list(pair) for pair in sorted({tuple(pair) for pair in ranges})]
        audit_case = audit_cases.get(index)
        if audit_case:
            if audit_case["text"] != text:
                raise HighlightError(f"reviewed audit text drift: {source_id} example {index}")
            if audit_case["status"] == "missing_certain":
                ranges.extend([
                    [_utf16_offset(text, start), _utf16_offset(text, end)]
                    for start, end in _audit_target_ranges(text, audit_case["targets"])
                ])
                ranges = [list(pair) for pair in sorted({tuple(pair) for pair in ranges})]
        result.append(ranges)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def build_spans(fields: dict[str, str]) -> str:
    """Short public alias used by rollout tooling and future pipelines."""
    return build_target_spans(fields)


def parse_target_spans(value: str, texts: list[str]) -> list[list[tuple[int, int]]]:
    try:
        raw = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HighlightError("target spans are not valid JSON") from exc
    if not isinstance(raw, list) or len(raw) != len(texts):
        raise HighlightError("target span/example counts differ")
    parsed: list[list[tuple[int, int]]] = []
    for ranges, text in zip(raw, texts):
        if not isinstance(ranges, list):
            raise HighlightError("target span entry is not a list")
        limit = _utf16_length(text)
        prior_end = 0
        item: list[tuple[int, int]] = []
        for pair in ranges:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or any(not isinstance(offset, int) or isinstance(offset, bool) for offset in pair)
            ):
                raise HighlightError("target range must contain two integer offsets")
            start, end = pair
            if start < prior_end or start < 0 or end <= start or end > limit:
                raise HighlightError("target range is overlapping or out of bounds")
            item.append((start, end))
            prior_end = end
        parsed.append(item)
    return parsed


def populate_target_spans(records: Iterable[dict[str, Any]]) -> None:
    for record in records:
        fields = record.get("fields", record)
        fields["ExampleTargetSpansJSON"] = build_target_spans(fields)
