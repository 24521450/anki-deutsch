"""Validate the checked-in Goethe A1-B1 Wortgruppen transcriptions."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "sources" / "goethe"
SOURCE_HEADER = ("ID", "Entry", "Detail", "CEFR", "Source Page", "Alphabetical Match", "Note")
ENRICHMENT_HEADER = (
    "Canonical Lemma", "POS", "Article", "Gender", "Noun Forms",
    "Accepted Variants", "Grammar Note", "Dictionary Sources",
)
HEADERS = {
    "A1": SOURCE_HEADER + ENRICHMENT_HEADER,
    "A2": SOURCE_HEADER + ENRICHMENT_HEADER,
    "B1": SOURCE_HEADER + ENRICHMENT_HEADER,
}
ALLOWED_POS = {"n.", "v.", "adj.", "adv.", "phrase"}
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "�")


@dataclass(frozen=True)
class LevelSpec:
    pages: tuple[int, ...]
    page_counts: dict[int, int]
    section_counts: dict[str, int]
    sections: tuple[str, ...]
    pdf_name: str
    pdf_sha256: str

    @property
    def expected_rows(self) -> int:
        return sum(self.page_counts.values())


SPECS = {
    "A1": LevelSpec(
        pages=(6, 7, 8),
        page_counts={6: 45, 7: 42, 8: 34},
        section_counts={
            "Zahlen": 39, "Datum": 6, "Uhrzeit": 8, "Zeitmaße, Zeitangaben": 6,
            "Woche/Wochentage": 9, "Tag/Tageszeiten": 7, "Monat/Monatsnamen": 12,
            "Jahr/Jahreszeiten": 4, "Währungen": 1, "Maße und Gewichte": 12,
            "Länder/Ländernamen/Nationalitäten": 5, "Farben": 8, "Himmelsrichtungen": 4,
        },
        sections=(
            "Zahlen", "Datum", "Uhrzeit", "Zeitmaße, Zeitangaben",
            "Woche/Wochentage", "Tag/Tageszeiten", "Monat/Monatsnamen",
            "Jahr/Jahreszeiten", "Währungen", "Maße und Gewichte",
            "Länder/Ländernamen/Nationalitäten", "Farben", "Himmelsrichtungen",
        ),
        pdf_name="A1_SD1_Wortliste_02.pdf",
        pdf_sha256="45fb648bc0ac02338f7898cae065953e320ab72ed0c14e13e0deffe6f1c5d64e",
    ),
    "A2": LevelSpec(
        pages=(5, 6, 7),
        page_counts={5: 86, 6: 68, 7: 71},
        section_counts={
            "Abkürzungen": 9, "Anweisungssprache zur Prüfung": 13, "Berufe": 27,
            "Familienmitglieder": 19, "Familienstand": 3, "Farben": 11,
            "Himmelsrichtungen": 4, "Länder und Nationalitäten": 5,
            "Schule und Schulfächer": 22, "Währungen und Maße": 10, "Zeitangaben": 0,
            "Datum": 3, "Feiertage": 4, "Jahreszeiten": 4, "Monate": 12,
            "Tageszeiten": 8, "Uhrzeit": 10, "Wochentage": 10, "Zeitmaße": 5,
            "Zahlen": 46,
        },
        sections=(
            "Abkürzungen", "Anweisungssprache zur Prüfung", "Berufe",
            "Familienmitglieder", "Familienstand", "Farben", "Himmelsrichtungen",
            "Länder und Nationalitäten", "Schule und Schulfächer",
            "Währungen und Maße", "Zeitangaben", "Datum", "Feiertage",
            "Jahreszeiten", "Monate", "Tageszeiten", "Uhrzeit", "Wochentage",
            "Zeitmaße", "Zahlen",
        ),
        pdf_name="Goethe-Zertifikat_A2_Wortliste.pdf",
        pdf_sha256="76cebc5fa7356fb1fb0f0bf964ad204e59a3e79753e7bad8cf3e4f0b38c352b7",
    ),
    "B1": LevelSpec(
        pages=tuple(range(8, 16)),
        page_counts={8: 77, 9: 38, 10: 28, 11: 34, 12: 35, 13: 82, 14: 34, 15: 27},
        section_counts={
            "1.1 Abkürzungen": 21, "1.2 Anglizismen": 73,
            "1.3 Anweisungssprache Zertifikat B1": 21, "1.4 Bildungseinrichtungen": 19,
            "1.5 Bildung: Schulfächer": 9, "1.6 Bildung: Schulnoten": 17,
            "1.7 Farben": 13, "1.8 Himmelsrichtungen": 4,
            "1.9 Länder, Kontinente, Nationalitäten (Staatsangehörigkeiten), Sprachen": 7,
            "1.10 Politische Begriffe": 28, "1.11 Tiere": 24,
            "1.12 Währungen, Maße und Gewichte": 18, "1.13 Zahlen, Bruchzahlen": 40,
            "1.14 Zeit": 0, "1.14.1 Datum": 4, "1.14.2 Feiertage": 6,
            "1.14.3 Jahreszeiten": 4, "1.14.4 Monatsnamen": 12,
            "1.14.5 Tageszeiten": 8, "1.14.6 Uhrzeit": 8,
            "1.14.7 Wochentage": 9, "1.14.8 Zeitangaben": 10,
        },
        sections=(
            "1.1 Abkürzungen", "1.2 Anglizismen",
            "1.3 Anweisungssprache Zertifikat B1", "1.4 Bildungseinrichtungen",
            "1.5 Bildung: Schulfächer", "1.6 Bildung: Schulnoten", "1.7 Farben",
            "1.8 Himmelsrichtungen",
            "1.9 Länder, Kontinente, Nationalitäten (Staatsangehörigkeiten), Sprachen",
            "1.10 Politische Begriffe", "1.11 Tiere",
            "1.12 Währungen, Maße und Gewichte", "1.13 Zahlen, Bruchzahlen",
            "1.14 Zeit", "1.14.1 Datum", "1.14.2 Feiertage",
            "1.14.3 Jahreszeiten", "1.14.4 Monatsnamen", "1.14.5 Tageszeiten",
            "1.14.6 Uhrzeit", "1.14.7 Wochentage", "1.14.8 Zeitangaben",
        ),
        pdf_name="Goethe-Zertifikat_B1_Wortliste.pdf",
        pdf_sha256="8860f7f0c916831b2365f66239a3ceba3be81ddba28e1224846ca8420807fe42",
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_alphabetical_headwords(path: Path) -> set[str]:
    headwords = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| \*\*(.*?)\*\* \|", line)
        if match:
            headwords.add(match.group(1))
    return headwords


def parse_inventory(path: Path, level: str) -> tuple[list[str], list[dict[str, str]]]:
    text = path.read_text(encoding="utf-8")
    sections = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith(("## ", "### "))]
    rows = []
    header = HEADERS[level]
    prefix = f"| {level}-WG-"
    header_count = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.startswith("| ID |"):
            cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
            if cells != header:
                raise ValueError(f"{path.name}:{line_number}: table header does not match the schema")
            header_count += 1
        if not line.startswith(prefix):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(header):
            raise ValueError(f"{path.name}:{line_number}: expected {len(header)} cells, got {len(cells)}")
        row = dict(zip(header, cells))
        row["_line"] = str(line_number)
        rows.append(row)
    if not header_count:
        raise ValueError(f"{path.name}: no inventory table headers found")
    return sections, rows


def validate_level(level: str) -> dict[str, object]:
    spec = SPECS[level]
    inventory_path = SOURCE_DIR / f"Goethe_{level}_Wortgruppen.md"
    wordlist_path = SOURCE_DIR / f"Goethe_{level}.md"
    pdf_path = SOURCE_DIR / spec.pdf_name
    errors: list[str] = []

    if sha256(pdf_path) != spec.pdf_sha256:
        errors.append(f"source PDF hash changed: {pdf_path.name}")

    text = inventory_path.read_text(encoding="utf-8")
    if unicodedata.normalize("NFC", text) != text:
        errors.append("inventory is not Unicode NFC")
    for marker in MOJIBAKE_MARKERS:
        if marker in text:
            errors.append(f"mojibake marker found: {marker!r}")

    sections, rows = parse_inventory(inventory_path, level)
    if tuple(sections) != spec.sections:
        errors.append(f"section order mismatch: {sections!r}")
    if len(rows) != spec.expected_rows:
        errors.append(f"expected {spec.expected_rows} rows, got {len(rows)}")

    expected_ids = [f"{level}-WG-{number:04d}" for number in range(1, len(rows) + 1)]
    actual_ids = [row["ID"] for row in rows]
    if actual_ids != expected_ids:
        errors.append("IDs are duplicated, missing, or out of order")

    headwords = parse_alphabetical_headwords(wordlist_path)
    page_counts = {page: 0 for page in spec.pages}
    section_counts = {section: 0 for section in spec.sections}
    current_section = None
    row_sections: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith(("## ", "### ")):
            current_section = line.lstrip("#").strip()
        elif line.startswith(f"| {level}-WG-"):
            row_id = line.split("|", 2)[1].strip()
            row_sections[row_id] = current_section or ""
    for row in rows:
        line = row["_line"]
        if not row["Entry"]:
            errors.append(f"line {line}: empty Entry")
        if row["CEFR"] != level:
            errors.append(f"line {line}: CEFR must be {level}")
        try:
            page = int(row["Source Page"])
        except ValueError:
            errors.append(f"line {line}: invalid source page {row['Source Page']!r}")
            continue
        if page not in page_counts:
            errors.append(f"line {line}: source page {page} outside {spec.pages}")
        else:
            page_counts[page] += 1
        section = row_sections[row["ID"]]
        if section in section_counts:
            section_counts[section] += 1
        for reference in filter(None, row["Alphabetical Match"].split("<br>")):
            if reference not in headwords:
                errors.append(f"line {line}: unknown alphabetical headword {reference!r}")
        canonical = row.get("Canonical Lemma", "")
        if not canonical:
            continue
        pos = row["POS"]
        article = row["Article"]
        gender = row["Gender"]
        grammar_note = row["Grammar Note"]
        sources = [item.strip() for item in row["Dictionary Sources"].split("<br>") if item.strip()]
        if pos not in ALLOWED_POS:
            errors.append(f"line {line}: unsupported enriched POS {pos!r}")
        if not sources or any(not source.startswith("https://") for source in sources):
            errors.append(f"line {line}: Dictionary Sources must contain HTTPS URLs")
        if pos != "n." and any((article, gender, row["Noun Forms"])):
            errors.append(f"line {line}: non-noun enrichment contains noun grammar")
        if pos == "n." and not gender:
            errors.append(f"line {line}: enriched noun is missing Gender")
        expected = {"der": "m.", "die": "f.", "das": "n."}
        articles = article.split("/") if article else []
        genders = gender.split("/") if gender else []
        valid_gender = gender == "pl." and articles == ["die"]
        valid_gender = valid_gender or (
            bool(articles) and len(articles) == len(genders)
            and all(expected.get(item) == genders[index] for index, item in enumerate(articles))
        )
        if article and not valid_gender:
            errors.append(f"line {line}: Article/Gender mismatch {article!r}/{gender!r}")
        if pos == "n." and not article and "normally used without an article" not in grammar_note.casefold():
            errors.append(f"line {line}: articleless noun needs an explicit usage note")

    if page_counts != spec.page_counts:
        errors.append(f"page counts changed: expected {spec.page_counts}, got {page_counts}")
    if section_counts != spec.section_counts:
        errors.append(f"section counts changed: expected {spec.section_counts}, got {section_counts}")
    if errors:
        raise ValueError(f"{level} Wortgruppen validation failed:\n- " + "\n- ".join(errors))
    return {"level": level, "rows": len(rows), "pages": page_counts}


def validate_all() -> list[dict[str, object]]:
    return [validate_level(level) for level in SPECS]


def main() -> None:
    for result in validate_all():
        print(f"{result['level']}: {result['rows']} rows, pages={result['pages']}")
    print("Wortgruppen validation PASS")


if __name__ == "__main__":
    main()
