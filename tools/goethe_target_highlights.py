"""Precompute deterministic target-word ranges for Goethe card examples."""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Iterable

import goethe_examples


class HighlightError(RuntimeError):
    pass


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


def _umlaut(value: str) -> str:
    replacements = {"a": "ä", "o": "ö", "u": "ü", "A": "Ä", "O": "Ö", "U": "Ü"}
    for index in range(len(value) - 1, -1, -1):
        if value[index] in replacements:
            return value[:index] + replacements[value[index]] + value[index + 1 :]
    return value


def _add_first_person(values: list[str], infinitive: str) -> None:
    if re.search(r"en$", infinitive, re.I):
        values.append(infinitive[:-2] + "e")


def candidate_terms(fields: dict[str, str]) -> list[str]:
    lemma = str(fields.get("Lemma") or "").strip()
    lexical_lemma = _strip_gender_qualifier(lemma)
    accepted = [_strip_gender_qualifier(value) for value in split_pipe(fields.get("AcceptedAnswersDE", ""))]
    values = [lemma, lexical_lemma, *accepted]
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
    if re.search(r"-[AÄÖÜäöü]", noun):
        ending = re.search(r",\s*(e|er|en|n)?\b", noun, flags=re.I)
        values.extend(
            _umlaut(base) + (ending.group(1) if ending and ending.group(1) else "")
            for base in lexical_bases
        )

    pos = str(fields.get("POS") or "")
    if re.fullmatch(r"n\.?", pos, flags=re.I) and re.search(r"-(?:n|en)\b", noun, flags=re.I):
        for base in lexical_bases:
            if re.search(r"e$", base, flags=re.I):
                values.extend(base[:-1] + ending for ending in ("e", "en", "em", "er", "es"))

    verb_lemma = re.sub(r"^\(sich\)\s*", "", lexical_lemma, flags=re.I)
    verb_lemma = re.sub(r"^sich\s+", "", verb_lemma, flags=re.I).strip()
    single_word_verb = bool(re.fullmatch(r"v\.?", pos, flags=re.I) and " " not in verb_lemma)
    if single_word_verb:
        _add_first_person(values, verb_lemma)
    irregular = {
        "sein": ("bin", "bist", "ist", "sind", "seid", "war", "waren", "gewesen"),
        "haben": ("habe", "hast", "hat", "haben", "habt", "hatte", "gehabt"),
        "werden": ("werde", "wirst", "wird", "werden", "werdet", "wurde", "geworden"),
    }
    values.extend(irregular.get(verb_lemma.casefold(), ()))

    stop = {"hat", "ist", "sind", "wird", "sein", "haben", "sich"}
    for raw_form in str(fields.get("VerbFormsRaw") or "").split(","):
        form = raw_form.strip()
        if not form:
            continue
        values.append(form)
        parts = form.split()
        for part in parts:
            if part.casefold() not in stop or verb_lemma.casefold() in {"sein", "haben", "werden"}:
                values.append(part)
        if single_word_verb and len(parts) > 1:
            particle = parts[-1]
            if len(particle) > 1 and verb_lemma.casefold().startswith(particle.casefold()) and len(verb_lemma) > len(particle) + 2:
                base_infinitive = verb_lemma[len(particle) :]
                values.extend((base_infinitive, particle))
                _add_first_person(values, base_infinitive)

    if re.search(r"adj|det|pron", pos, flags=re.I):
        inflection_bases = [lexical_lemma] + [value for value in accepted if value.endswith("-")]
        for inflection_base in dict.fromkeys(inflection_bases):
            base = inflection_base.removesuffix("-")
            values.extend(base + ending for ending in ("e", "en", "em", "er", "es"))

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return sorted(result, key=len, reverse=True)


def _bounded_pattern(candidate: str, pos: str) -> str:
    escaped = re.escape(candidate)
    is_noun = bool(re.fullmatch(r"n\.?", str(pos or ""), flags=re.I))
    if len(candidate) < 4 or not is_noun:
        return rf"(?<!\w){escaped}(?!\w)"
    return escaped


def match_ranges(text: str, candidates: Iterable[str], pos: str = "") -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for candidate in candidates:
        if not candidate:
            continue
        matcher = re.compile(_bounded_pattern(candidate, pos), flags=re.I)
        ranges.extend((match.start(), match.end()) for match in matcher.finditer(text))
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
    result = []
    for text in example_texts(fields):
        result.append([
            [_utf16_offset(text, start), _utf16_offset(text, end)]
            for start, end in match_ranges(text, candidates, pos)
        ])
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
