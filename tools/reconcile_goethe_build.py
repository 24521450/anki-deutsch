"""Refresh the checked-in JSONL build after the reviewed live-note batch."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import goethe_examples
import goethe_english_audit as audit
import goethe_issue_refresh as live
import goethe_target_highlights as highlights


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "build" / "anki_notes.jsonl"


def read_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in OUTPUT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def html_audio_by_de(fields: dict[str, str]) -> dict[str, str]:
    return {item["de"]: item.get("audio", "") for item in goethe_examples.parse_fields(fields)}


def highlight_fields(row: dict[str, Any]) -> dict[str, str]:
    fields = {
        "Lemma": str(row.get("lemma", "")),
        "AcceptedAnswersDE": "|".join(str(value) for value in row.get("accepted_answers_de", [])),
        "NounFormsRaw": str(row.get("noun_forms_raw", "")),
        "VerbFormsRaw": str(row.get("verb_forms_raw", "")),
        "SourceNoteRaw": str(row.get("source_note_raw", "")),
        "SourceID": str(row.get("source_id", "")),
        "POS": str(row.get("pos", "")),
        "MoreExamplesHTML": str(row.get("more_examples_html", "")),
    }
    for index, example in enumerate(row.get("examples", []), 1):
        fields[f"Example{index}DE"] = str(example.get("de", ""))
    return fields


def main() -> int:
    rows = read_rows()
    entries = audit.load_json(audit.MANIFEST)["entries"]
    notes = live.all_notes()
    by_source = live.by_source(notes)
    removed = {"B1-WG-0127", "B1-WG-0134", "B1-WG-0202", "B1-WG-0209", "B1-MAIN-2947"}
    variant_ids = set(live.load_registry()) - {"B1-MAIN-2947"}
    refresh_audio_ids = variant_ids | {"A2-WG-0003"}
    refreshed = 0

    result: list[dict[str, Any]] = []
    for original in rows:
        source_id = str(original.get("source_id", ""))
        if source_id in removed:
            continue
        entry = entries.get(source_id)
        if entry is None:
            raise RuntimeError(f"missing audit entry for build row: {source_id}")
        row = copy.deepcopy(original)
        row["guid"] = entry["stable_guid"]
        row["lemma"] = entry["lemma"]
        row["meaning_en"] = entry["desired_meaning_en"]
        row["source_refs"] = list(entry["source_refs"])
        row.setdefault("word_audio_variants_html", "")

        old_audio = {str(item.get("de", "")): str(item.get("audio", "")) for item in row.get("examples", [])}
        live_pair = by_source.get(source_id)
        live_fields = live.note_fields(live_pair[1]) if live_pair else {}
        live_audio = html_audio_by_de(live_fields) if live_fields else {}
        examples: list[dict[str, Any]] = []
        for item in entry.get("desired_examples", []):
            german = str(item.get("de", ""))
            examples.append({
                "de": german,
                "en": str(item.get("en", "")),
                "audio": live_audio.get(german, old_audio.get(german, "")),
            })
        row["examples"] = examples

        if source_id in refresh_audio_ids:
            if not live_fields:
                raise RuntimeError(f"missing live audio target: {source_id}")
            row["word_audio"] = live_fields.get("WordAudio", "")
            row["word_audio_variants_html"] = live_fields.get("WordAudioVariantsHTML", "")
            refreshed += 1

        # The merged overflow inventory follows the live survivor exactly.
        if source_id in {"A1-84886455260", "B1-WG-0120", "B1-WG-0199", "B1-WG-0203"}:
            row["more_examples_html"] = live_fields.get("MoreExamplesHTML", "")
        if source_id == "A2-WG-0003":
            # ICE has a single Duden pronunciation, not a variant selector.
            row["word_audio_variants_html"] = ""
        # Rebuild spans from the canonical exported fields.  This keeps
        # regional display prefixes (which are audit provenance) from being
        # mistaken for the lexical target in examples.
        row["example_target_spans"] = json.loads(highlights.build_spans(highlight_fields(row)))
        result.append(row)

    if len(result) != 3420:
        raise RuntimeError(f"expected 3420 build rows, got {len(result)}")
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in result),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": str(OUTPUT), "rows": len(result), "refreshed_audio_rows": refreshed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
