"""Preflight + dry-run manifest. Vol=2, speed=1.0 per user 2026-07-03."""
from __future__ import annotations
import hashlib, json, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources" / "goethe" / "Goethe_A1.md"
OUT_DIR = ROOT / "audio" / "a1"
WORDS_DIR = OUT_DIR / "words"
MANIFEST = OUT_DIR / "words_manifest.jsonl"
META = OUT_DIR / "words_manifest.meta.json"

EXPECTED_ROWS = 685
SEED = "goethe-a1-words-v1"
VOICE_TARGETS = {
    "German_FriendlyMan": 343,
    "German_SweetLady": 342,
}
TTS_PARAMS = {"speed": 1.0, "pitch": 0, "volume": 2, "emotion": "neutral"}


def slugify(word):
    s = word.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = s.encode("ascii", errors="ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "x"


def parse_markdown(md_path):
    rows = []
    in_table = False
    header_seen = False
    row_num = 0
    for line in md_path.open("r", encoding="utf-8"):
        s = line.rstrip("\n")
        if not s.startswith("|"):
            in_table = False; header_seen = False; continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not in_table:
            if cells and cells[0].lstrip().startswith("Word"):
                in_table = True; continue
            continue
        if not header_seen:
            header_seen = True; continue
        if len(cells) < 5: continue
        row_num += 1
        rows.append({"row": row_num, "word": cells[0].strip().strip("*").strip(),
                     "pos": cells[1], "gender": cells[2], "cefr": cells[3],
                     "sentence": cells[4] if len(cells) > 4 else "",
                     "note": cells[5] if len(cells) > 5 else ""})
    return rows


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORDS_DIR.mkdir(parents=True, exist_ok=True)

    src_bytes = SOURCE.read_bytes()
    src_sha = hashlib.sha256(src_bytes).hexdigest()
    print(f"source_sha256={src_sha}")

    rows = parse_markdown(SOURCE)
    print(f"parsed_rows={len(rows)}")
    assert len(rows) == EXPECTED_ROWS, f"expected {EXPECTED_ROWS}, got {len(rows)}"

    seen = {}
    for r in rows:
        slug = slugify(r["word"])
        fn = f"{r['row']:04d}_{slug}.mp3"
        r["slug"] = slug
        r["output_filename"] = fn
        r["output_path"] = str(WORDS_DIR / fn)
        assert fn not in seen, f"dup {fn}"
        seen[fn] = r["row"]

    voices = list(VOICE_TARGETS.keys())
    pool = []
    for v, c in VOICE_TARGETS.items():
        pool.extend([v] * c)
    rng = random.Random(SEED)
    rng.shuffle(pool)
    assert len(pool) == len(rows)
    for r, v in zip(rows, pool):
        r["voice"] = v

    actual = {}
    for r in rows:
        actual[r["voice"]] = actual.get(r["voice"], 0) + 1
    assert actual == VOICE_TARGETS, f"{actual} != {VOICE_TARGETS}"
    print(f"voice_counts={actual}")

    with MANIFEST.open("w", encoding="utf-8") as f:
        for r in rows:
            obj = {"row": r["row"], "word": r["word"], "slug": r["slug"],
                   "voice": r["voice"], "tts_params": TTS_PARAMS,
                   "output_filename": r["output_filename"],
                   "output_path": r["output_path"], "spoken_text": r["word"],
                   "tts_status": "pending"}
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    meta = {"plan": "docs/PLAN_A1_WORD_AUDIO.md", "source_path": str(SOURCE),
            "source_sha256": src_sha, "source_size": len(src_bytes),
            "row_count": len(rows), "seed": SEED,
            "voice_targets": VOICE_TARGETS, "voice_actual": actual,
            "tts_params": TTS_PARAMS, "engine": "matrix",
            "matrix_tool": "matrix_synthesize_speech",
            "output_dir": str(WORDS_DIR), "manifest_path": str(MANIFEST)}
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote manifest + meta. preflight PASS")


if __name__ == "__main__":
    main()
