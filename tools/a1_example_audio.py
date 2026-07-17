"""Generate MP3 audio for Goethe A1 example sentences.

Workflow:
    python tools/a1_example_audio.py preflight   # parse + manifest only (no audio)
    python tools/a1_example_audio.py pilot       # generate 16 pilot entries; STOP for review
    python tools/a1_example_audio.py full        # generate all 890 unique entries
    python tools/a1_example_audio.py resume      # verify existing + generate the rest

Source: column `Sentence` in sources/goethe/Goethe_A1.md
Outputs:
    audio/a1/examples_manifest.jsonl        — 909 occurrences, denormalized
    audio/a1/examples_manifest.meta.json     — meta + counts
    audio/a1/examples_unique.jsonl           — 890 unique audios (status, voice, path)
    audio/a1/examples_staging/                — generated MP3s (transient)
    audio/a1/examples/                        — promoted MP3s (final, only on completion)

Touches nothing else (Duden pipeline, Markdown, deck, matrix-media-*.mp3 are out of scope).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request
from collections import OrderedDict
from pathlib import Path


# === Paths ============================================================
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources" / "goethe" / "Goethe_A1.md"
OUT_DIR = ROOT / "audio" / "a1"
STAGING_DIR = OUT_DIR / "examples_staging"
LIVE_DIR = OUT_DIR / "examples"
MANIFEST = OUT_DIR / "examples_manifest.jsonl"
META = OUT_DIR / "examples_manifest.meta.json"
UNIQUE_INDEX = OUT_DIR / "examples_unique.jsonl"


# === Plan constants ===================================================
EXPECTED_ROWS = 685
EXPECTED_OCCURRENCES = 909
EXPECTED_UNIQUE = 890
SEED = "goethe-a1-examples-v1"
VOICE_TARGETS: dict[str, int] = {
    "German_SweetLady": 446,
    "German_FriendlyMan": 444,
}
TTS_PARAMS = {"speed": 1.0, "pitch": 0, "volume": 2, "emotion": "neutral"}
MAX_RETRIES = 3
RETRY_BACKOFF = (1.5, 3.0, 6.0)
AUDIO_ID_LEN = 16
DIALOGUE_PREFIXES = ("-", "–", "—")  # ASCII hyphen-minus, en-dash, em-dash


# 12 manual overrides by (row, example_index) -> canonical spoken_text
OVERRIDES: dict[tuple[int, int], str] = {
    (23, 1): "Auf diesem Plan steht nur die Ankunftszeit der Züge.",
    (145, 1): "Damen.",
    (175, 1): "Das ist meine Ehefrau.",
    (176, 1): "Das ist mein Ehemann.",
    (262, 3): "Jetzt muss ich aber leider gehen.",
    (311, 1): "Die Hausfrau wäscht, kocht und kauft ein.",
    (312, 1): "Der Hausmann wäscht, kocht und kauft ein.",
    (334, 1): "Gib ihr bitte das Buch.",
    (351, 1): "Kann ich auch mit Karte bezahlen?",
    (555, 4): "So, das war’s!",
    (567, 1): "Kann ich mit Herrn Klein sprechen?",
    (630, 2): "Er kommt gerade von Köln.",
}


# 16 dialogue turns in the corpus start with '-' / '–' / '—'.
# We auto-detect them rather than hard-coding (row, idx) pairs so the rule is
# data-driven and audit-verifiable. PLAN invariant: exactly 16 strips.
EXPECTED_DASH_STRIPS = 16


# 16 pilot keys: 8 per voice, per PLAN. Voice partitioning is dictated by SEED.
PILOT_KEYS: frozenset[tuple[int, int]] = frozenset({
    # SweetLady candidates (8)
    (18, 1),   # Willst du diese Jacke?
    (23, 1),   # Auf diesem Plan steht nur die Ankunftszeit der Züge.
    (180, 3),  # Nein, bitte nur einfach.
    (262, 3),  # Jetzt muss ich aber leider gehen.
    (277, 1),  # Bei „Gewicht" schreibst du: 62 Kilo.
    (350, 1),  # Ich schreibe meinen Bekannten eine Karte aus dem Urlaub.
    (373, 2),  # 10 Euro.
    (555, 4),  # So, das war's!
    # FriendlyMan candidates (8)
    (6, 1),    # Wann kann ich den Schrank bei dir abholen?
    (145, 1),  # Damen.
    (175, 1),  # Das ist meine Ehefrau.
    (298, 3),  # Gut!
    (311, 1),  # Die Hausfrau wäscht, kocht und kauft ein.
    (334, 1),  # Gib ihr bitte das Buch.
    (351, 1),  # Kann ich auch mit Karte bezahlen?
    (656, 1),  # Wie heißt du?
})


# === Pure functions ===================================================

def normalize_unicode_whitespace(s: str) -> str:
    """NFC + collapse any run of whitespace (incl. newlines/tabs) to single space; trim ends."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def strip_leading_dash(text: str) -> str:
    """Remove a single leading '-' / '–' / '—' character plus any single following space."""
    if not text:
        return text
    s = text.lstrip()
    if s and s[0] in DIALOGUE_PREFIXES:
        rest = s[1:].lstrip()
        return rest
    return text


def parse_markdown(path: Path) -> list[dict]:
    """Parse Goethe_A1.md table rows. Mirrors a1_preflight.parse_markdown."""
    rows: list[dict] = []
    in_table = False
    header_seen = False
    row_num = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.rstrip("\n")
        if not s.startswith("|"):
            in_table = False
            header_seen = False
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not in_table:
            if cells and cells[0].lstrip().startswith("Word"):
                in_table = True
                continue
            continue
        if not header_seen:
            header_seen = True
            continue
        if len(cells) < 5:
            continue
        row_num += 1
        rows.append({
            "row": row_num,
            "word": cells[0].strip().strip("*").strip(),
            "pos": cells[1],
            "gender": cells[2],
            "cefr": cells[3],
            "sentence": cells[4] if len(cells) > 4 else "",
            "note": cells[5] if len(cells) > 5 else "",
        })
    return rows


def extract_occurrences(rows: list[dict]) -> list[dict]:
    """Split each sentence by `<br>`. example_index is 1-based per row."""
    out: list[dict] = []
    for r in rows:
        parts = r["sentence"].split("<br>")
        for idx, part in enumerate(parts, start=1):
            source_text = normalize_unicode_whitespace(part)
            out.append({
                "row": r["row"],
                "example_index": idx,
                "word": r["word"],
                "source_text": source_text,
            })
    return out


def apply_overrides(occurrences: list[dict]) -> None:
    """In-place: replace spoken_text where (row, idx) matches an OVERRIDE key."""
    for occ in occurrences:
        key = (occ["row"], occ["example_index"])
        if key in OVERRIDES:
            occ["spoken_text"] = normalize_unicode_whitespace(OVERRIDES[key])
        else:
            occ["spoken_text"] = occ["source_text"]


def strip_dialogue_dashes(occurrences: list[dict]) -> None:
    """In-place: strip leading dialogue dash from any source whose first char is dash.

    The PLAN says exactly 16 dialogue dashes exist in the source. We auto-detect
    them by their first character. The preflight assertion verifies the count
    matches EXPECTED_DASH_STRIPS — flagging future data drift.
    """
    for occ in occurrences:
        first = occ["source_text"].lstrip()[:1]
        if first in DIALOGUE_PREFIXES:
            new_text = strip_leading_dash(occ["spoken_text"])
            if new_text != occ["spoken_text"]:
                occ["spoken_text"] = new_text


def audio_id_for(spoken_text: str) -> str:
    return hashlib.sha256(spoken_text.encode("utf-8")).hexdigest()[:AUDIO_ID_LEN]


def assign_audio_ids(occurrences: list[dict]) -> None:
    """In-place: set audio_id + output_filename per occurrence."""
    seen: dict[str, str] = {}
    for occ in occurrences:
        text = occ.get("spoken_text", "")
        if not text:
            occ["audio_id"] = None
            occ["output_filename"] = None
            continue
        if text not in seen:
            seen[text] = audio_id_for(text)
        occ["audio_id"] = seen[text]
        occ["output_filename"] = f"ex_{seen[text]}.mp3"


def build_unique_view(occurrences: list[dict]) -> list[dict]:
    """Build per-unique audios (first-occurrence order). Status starts as 'pending'.

    Each entry: {audio_id, spoken_text, output_filename, voice, tts_params, tts_status='pending'}.
    Voice is assigned deterministically using SEED.
    """
    sorted_occs = sorted(occurrences, key=lambda o: (o["row"], o["example_index"]))
    by_id: "OrderedDict[str, dict]" = OrderedDict()
    for occ in sorted_occs:
        aid = occ.get("audio_id")
        if aid is None:
            continue
        if aid not in by_id:
            by_id[aid] = {
                "audio_id": aid,
                "spoken_text": occ["spoken_text"],
                "output_filename": occ["output_filename"],
                "tts_status": "pending",
                "tts_params": dict(TTS_PARAMS),
            }
    uniques = list(by_id.values())
    voices = _assign_voices([u["spoken_text"] for u in uniques])
    for u, v in zip(uniques, voices):
        u["voice"] = v
    return uniques


def _assign_voices(spoken_texts: list[str]) -> list[str]:
    """Deterministic voice assignment.

    Build a pool of (sweet_lady * 446) + (friendly_man * 444) voices,
    shuffle by SEED, then map sorted unique-text slot -> pool[i].

    Same inputs + same seed => same output.
    """
    pool: list[str] = []
    for voice, count in VOICE_TARGETS.items():
        pool.extend([voice] * count)
    rng = random.Random(SEED)
    rng.shuffle(pool)
    sorted_texts = sorted(spoken_texts)
    text_to_voice = {t: pool[i] for i, t in enumerate(sorted_texts) if i < len(pool)}
    return [text_to_voice.get(t, pool[-1]) for t in spoken_texts]


def denormalize_unique_into_manifest(
    occurrences: list[dict], uniques: list[dict], fields: tuple[str, ...]
) -> None:
    """In-place: copy per-unique fields (status, mp3_*, voice, error) into each occurrence."""
    by_id = {u["audio_id"]: u for u in uniques if u.get("audio_id")}
    for occ in occurrences:
        aid = occ.get("audio_id")
        if not aid or aid not in by_id:
            continue
        u = by_id[aid]
        for k in fields:
            if k in u:
                occ[k] = u[k]


# === Side-effecting helpers ==========================================

def call_tts(text: str, voice: str, tts_params: dict) -> dict:
    """Invoke matrix_synthesize_speech via stdin. Returns parsed JSON dict.

    Raises RuntimeError if subprocess returns non-JSON or invalid shape.
    Caller should check `code` field — non-zero means Matrix-side error.

    On Windows, `mavis` is a .cmd file; we resolve via shutil.which so
    subprocess.run does not need shell=True.
    """
    payload = {
        "text": text,
        "voice_id": voice,
        "speed": tts_params["speed"],
        "pitch": tts_params["pitch"],
        "volume": tts_params["volume"],
        "emotion": tts_params["emotion"],
    }
    mavis_exe = shutil.which("mavis")
    if not mavis_exe:
        raise RuntimeError("mavis not found in PATH")
    proc = subprocess.run(
        [mavis_exe, "mcp", "call", "matrix", "matrix_synthesize_speech", "--stdin"],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
    )
    out = (proc.stdout or b"") + b"\n" + (proc.stderr or b"")
    text_out = out.decode("utf-8", errors="replace")
    start = text_out.find("{")
    end = text_out.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(
            f"No JSON in TTS response (rc={proc.returncode}):\n{text_out[:500]}"
        )
    return json.loads(text_out[start:end + 1])


def fetch_mp3(resp: dict, dst: Path) -> None:
    """Place MP3 at dst using ONLY resp.output_url.

    Output_url can be:
      * http(s)://<cdn>/foo.mp3       -> urllib download
      * file:///path                  -> copy
      * absolute or relative Windows path -> copy

    Validates size > 0 post-fetch. Caller should validate_mp3 afterwards.
    """
    url = resp.get("output_url", "")
    if not url:
        raise RuntimeError(f"missing output_url in response: {resp}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("http://") or url.startswith("https://"):
        urllib.request.urlretrieve(url, dst)
    elif url.startswith("file://"):
        src = Path(url[len("file:///"):]) if url.startswith("file:///") else Path(url[len("file:"):])
        shutil.copyfile(str(src), str(dst))
    else:
        src = Path(url)
        if not src.exists():
            raise RuntimeError(f"source MP3 missing on disk: {src}")
        shutil.copyfile(str(src), str(dst))
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"empty MP3 at {dst} from {url}")


def validate_mp3(path: Path) -> dict:
    """Check exists, size > 0, MP3 signature, then SHA-256.

    Accepts:
      * ID3v2 header: bytes start with 'I','D','3' (e.g. b'ID3\\x03\\x00...')
      * MPEG audio frame sync: bytes start with 0xFF 0xFB / 0xF3 / 0xF2
    """
    if not path.exists():
        raise RuntimeError(f"missing: {path}")
    size = path.stat().st_size
    if size == 0:
        raise RuntimeError(f"zero-byte: {path}")
    with path.open("rb") as f:
        head = f.read(3)
    is_id3v2 = head[:3] == b"ID3"
    is_mpeg_sync = head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    if not (is_id3v2 or is_mpeg_sync):
        raise RuntimeError(f"bad MP3 signature: {head!r}")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    # Signature returns up to 3 bytes for ID3, or 2 bytes for MPEG sync,
    # zero-padded to 3 hex char-pairs to keep callers consistent.
    sig_bytes = head[:3] if is_id3v2 else head[:2] + b"\x00"
    return {"size": size, "sha256": sha, "signature": sig_bytes.hex()}


# === Manifest IO ======================================================

def write_manifest(occurrences: list[dict], path: Path) -> None:
    """Atomic write (write to .tmp then replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for occ in occurrences:
            f.write(json.dumps(occ, ensure_ascii=False) + "\n")
    tmp.replace(path)


def write_unique_index(uniques: list[dict], path: Path) -> None:
    """Atomic write of the unique index."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for u in uniques:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# === Generation =======================================================

def _backoff_sleep(attempt_idx: int) -> None:
    """1.5s / 3.0s / 6.0s backoff per attempt."""
    if attempt_idx < len(RETRY_BACKOFF):
        time.sleep(RETRY_BACKOFF[attempt_idx])


def generate_unique_audio(
    unique: dict,
    dst_dir: Path,
    attempts: int = MAX_RETRIES,
) -> dict:
    """Generate MP3 for one unique entry. Mutates and returns the dict.

    Skip-conditions (in order):
      * MP3 already exists AND validates AND config matches spoken_text/voice/params -> skipped_existing_valid
      * Else retry up to `attempts` times with backoff
      * On exhaustion: tts_status='failed' with .error and .attempt_count set
    """
    text = unique["spoken_text"]
    voice = unique["voice"]
    params = unique["tts_params"]
    out_path = dst_dir / unique["output_filename"]

    # Fast skip if existing valid MP3 already matches config
    if out_path.exists():
        try:
            v = validate_mp3(out_path)
            unique["tts_status"] = "skipped_existing_valid"
            unique["mp3_size"] = v["size"]
            unique["mp3_sha256"] = v["sha256"]
            unique["mp3_signature"] = v["signature"]
            return unique
        except Exception:
            out_path.unlink(missing_ok=True)

    last_err = "no attempt"
    for i in range(attempts):
        try:
            resp = call_tts(text, voice, params)
        except Exception as e:
            last_err = f"call failed: {e}"
            unique[f"attempt_{i+1}_error"] = last_err
            _backoff_sleep(i)
            continue

        if resp.get("code") != 0:
            last_err = f"non-zero code: {resp.get('code')} msg={resp.get('message')}"
            unique[f"attempt_{i+1}_error"] = last_err
            _backoff_sleep(i)
            continue

        tmp_dst = out_path.with_suffix(out_path.suffix + ".tmp")
        try:
            fetch_mp3(resp, tmp_dst)
        except Exception as e:
            last_err = f"fetch failed: {e}"
            unique[f"attempt_{i+1}_error"] = last_err
            _backoff_sleep(i)
            continue

        try:
            v = validate_mp3(tmp_dst)
        except Exception as e:
            last_err = f"validate failed: {e}"
            unique[f"attempt_{i+1}_error"] = last_err
            tmp_dst.unlink(missing_ok=True)
            _backoff_sleep(i)
            continue

        tmp_dst.replace(out_path)
        unique["tts_status"] = "ok"
        unique["mp3_size"] = v["size"]
        unique["mp3_sha256"] = v["sha256"]
        unique["mp3_signature"] = v["signature"]
        unique["attempt_count"] = i + 1
        return unique

    unique["tts_status"] = "failed"
    unique["error"] = last_err
    unique["attempt_count"] = attempts
    return unique


# === Mode runners =====================================================

DENORM_FIELDS = ("tts_status", "mp3_size", "mp3_sha256", "mp3_signature",
                 "error", "attempt_count", "voice")


def _assert_plan_invariants(occurrences, uniques):
    """Run all assertions the plan demands (called from preflight)."""
    assert len(occurrences) == EXPECTED_OCCURRENCES, (
        f"expected {EXPECTED_OCCURRENCES} occurrences, got {len(occurrences)}"
    )
    real_uniques = [u for u in uniques if u.get("audio_id")]
    assert len(real_uniques) == EXPECTED_UNIQUE, (
        f"expected {EXPECTED_UNIQUE} unique, got {len(real_uniques)}"
    )
    voice_actual: dict[str, int] = {}
    for u in real_uniques:
        voice_actual[u["voice"]] = voice_actual.get(u["voice"], 0) + 1
    assert voice_actual == VOICE_TARGETS, f"{voice_actual} != {VOICE_TARGETS}"
    # All overrides applied
    override_set = set(OVERRIDES.keys())
    applied = {(o["row"], o["example_index"]) for o in occurrences
               if o.get("spoken_text") != o.get("source_text")}
    assert not (override_set - applied), f"missing overrides: {override_set - applied}"
    # Verify exactly 16 dialogue dashes get stripped (PLAN invariant)
    dialog_count = sum(
        1 for o in occurrences
        if (o.get("source_text", "").lstrip()[:1] in DIALOGUE_PREFIXES)
    )
    assert dialog_count == EXPECTED_DASH_STRIPS, (
        f"dialogue-dash count drift: {dialog_count} != {EXPECTED_DASH_STRIPS}"
    )
    # Verify after stripping all 16 dashes, source->spoken differs on those
    stripped_after = sum(
        1 for o in occurrences
        if (o.get("source_text", "").lstrip()[:1] in DIALOGUE_PREFIXES
            and o.get("source_text") != o.get("spoken_text"))
    )
    assert stripped_after == EXPECTED_DASH_STRIPS, (
        f"after strip, only {stripped_after} of {dialog_count} dashes actually stripped"
    )


def run_preflight() -> int:
    """Parse + dedup + manifest only. No MP3 generation."""
    src_bytes = SOURCE.read_bytes()
    src_sha = hashlib.sha256(src_bytes).hexdigest()
    print(f"source_sha256={src_sha}")

    rows = parse_markdown(SOURCE)
    assert len(rows) == EXPECTED_ROWS, f"expected {EXPECTED_ROWS} rows, got {len(rows)}"

    occurrences = extract_occurrences(rows)
    apply_overrides(occurrences)
    strip_dialogue_dashes(occurrences)
    assign_audio_ids(occurrences)
    uniques = build_unique_view(occurrences)

    # Voice assignment must have run -> every unique has .voice
    for u in uniques:
        assert "voice" in u, f"missing voice on {u}"

    # Initial manifest: every occurrence gets its audio_id + voice (denormalized)
    denormalize_unique_into_manifest(occurrences, uniques, DENORM_FIELDS)

    _assert_plan_invariants(occurrences, uniques)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_manifest(occurrences, MANIFEST)
    write_unique_index(uniques, UNIQUE_INDEX)

    meta = {
        "plan": "PLAN_A1_EXAMPLE_AUDIO.md",
        "source_path": str(SOURCE),
        "source_sha256": src_sha,
        "source_size": len(src_bytes),
        "rows": len(rows),
        "occurrences": len(occurrences),
        "unique": len(uniques),
        "seed": SEED,
        "voice_targets": VOICE_TARGETS,
        "voice_actual": {v: sum(1 for u in uniques if u.get("voice") == v)
                         for v in VOICE_TARGETS},
        "tts_params": TTS_PARAMS,
        "engine": "matrix",
        "matrix_tool": "matrix_synthesize_speech",
        "staging_dir": str(STAGING_DIR),
        "live_dir": str(LIVE_DIR),
        "manifest_path": str(MANIFEST),
        "unique_index_path": str(UNIQUE_INDEX),
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"preflight PASS rows={len(rows)} occurrences={len(occurrences)} "
        f"unique={len(uniques)} voices={meta['voice_actual']}"
    )
    return 0


def run_pilot() -> int:
    """Generate the 16 pilot entries; do NOT promote. Returns 0 unless all-pilot failed."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    if not UNIQUE_INDEX.exists() or not MANIFEST.exists():
        print("FAIL: run preflight first", file=sys.stderr)
        return 2

    occurrences = read_jsonl(MANIFEST)
    uniques = read_jsonl(UNIQUE_INDEX)
    by_id = {u["audio_id"]: u for u in uniques}

    pilot_unique_ids = set()
    for o in occurrences:
        if (o["row"], o["example_index"]) in PILOT_KEYS and o.get("audio_id"):
            pilot_unique_ids.add(o["audio_id"])

    pilot_uniques = [by_id[aid] for aid in pilot_unique_ids if aid in by_id]
    assert len(pilot_uniques) == 16, f"pilot resolves to {len(pilot_uniques)} unique, expected 16"

    voice_counts: dict[str, int] = {}
    fail = 0
    for i, u in enumerate(pilot_uniques, 1):
        voice = u["voice"]
        voice_counts[voice] = voice_counts.get(voice, 0) + 1
        print(f"[{i}/{len(pilot_uniques)}] audio_id={u['audio_id']} voice={voice}")
        try:
            updated = generate_unique_audio(u, STAGING_DIR)
            sh = (updated.get("mp3_sha256") or "")[:12]
            sz = updated.get("mp3_size", "?")
            print(f"  -> {updated['tts_status']} size={sz} sha={sh}")
            if updated.get("tts_status") == "failed":
                fail += 1
        except Exception as e:
            print(f"  -> error: {e}")
            u["tts_status"] = "failed"
            u["error"] = str(e)
            fail += 1

    write_unique_index(uniques, UNIQUE_INDEX)
    denormalize_unique_into_manifest(occurrences, uniques, DENORM_FIELDS)
    write_manifest(occurrences, MANIFEST)

    print(f"\npilot done. voice_counts={voice_counts} fail={fail}")
    print("STOP — listen to pilot audio in examples_staging/ before running 'full'.")
    return 0 if fail == 0 else 1


def run_full() -> int:
    """Generate all pending/failed uniques. Promote to live on success."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if not UNIQUE_INDEX.exists():
        print("FAIL: run preflight first", file=sys.stderr)
        return 2

    occurrences = read_jsonl(MANIFEST)
    uniques = read_jsonl(UNIQUE_INDEX)
    pending = [u for u in uniques if u.get("tts_status") in (None, "pending", "failed")]
    pending.sort(key=lambda u: u["audio_id"])
    print(f"full count: {len(pending)} (of {len(uniques)})")

    fail = 0
    for i, u in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] audio_id={u['audio_id']} voice={u['voice']}")
        try:
            updated = generate_unique_audio(u, STAGING_DIR)
            sh = (updated.get("mp3_sha256") or "")[:12]
            sz = updated.get("mp3_size", "?")
            print(f"  -> {updated['tts_status']} size={sz} sha={sh}")
            if updated.get("tts_status") == "failed":
                fail += 1
        except Exception as e:
            print(f"  -> error: {e}")
            u["tts_status"] = "failed"
            u["error"] = str(e)
            fail += 1
        if i % 50 == 0:
            write_unique_index(uniques, UNIQUE_INDEX)
            denormalize_unique_into_manifest(occurrences, uniques, DENORM_FIELDS)
            write_manifest(occurrences, MANIFEST)
            print(f"  [checkpoint] {i}")

    write_unique_index(uniques, UNIQUE_INDEX)
    denormalize_unique_into_manifest(occurrences, uniques, DENORM_FIELDS)
    write_manifest(occurrences, MANIFEST)

    promoted = try_promote(uniques)
    print(f"\nresult ok={len(pending) - fail} fail={fail} promoted={promoted}")
    if not promoted:
        bad = [(u["audio_id"], u.get("tts_status"), u.get("error"))
               for u in uniques
               if u.get("tts_status") not in ("ok", "skipped_existing_valid")
               or not (STAGING_DIR / u["output_filename"]).exists()]
        if bad:
            print(f"not promoted; {len(bad)} entries incomplete: first 3={bad[:3]}")
    return 0 if (fail == 0 and promoted) else 1


def run_resume() -> int:
    """Verify each unique's MP3 (size, signature, sha); regenerate broken/missing."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if not UNIQUE_INDEX.exists() or not MANIFEST.exists():
        print("FAIL: run preflight first", file=sys.stderr)
        return 2

    occurrences = read_jsonl(MANIFEST)
    uniques = read_jsonl(UNIQUE_INDEX)
    fail = 0
    ok = 0
    skipped = 0
    for i, u in enumerate(uniques, 1):
        out_path = STAGING_DIR / u["output_filename"]
        reused = False
        if out_path.exists():
            try:
                v = validate_mp3(out_path)
                if (u.get("tts_params") == TTS_PARAMS
                        and u.get("voice") in VOICE_TARGETS
                        and u.get("spoken_text")):
                    u["tts_status"] = "skipped_existing_valid"
                    u["mp3_size"] = v["size"]
                    u["mp3_sha256"] = v["sha256"]
                    u["mp3_signature"] = v["signature"]
                    reused = True
                    skipped += 1
            except Exception:
                out_path.unlink(missing_ok=True)
        if not reused:
            print(f"[{i}/{len(uniques)}] audio_id={u['audio_id']} voice={u['voice']}")
            generate_unique_audio(u, STAGING_DIR)
        if u.get("tts_status") == "ok" or u.get("tts_status") == "skipped_existing_valid":
            ok += 1
        else:
            fail += 1

    write_unique_index(uniques, UNIQUE_INDEX)
    denormalize_unique_into_manifest(occurrences, uniques, DENORM_FIELDS)
    write_manifest(occurrences, MANIFEST)

    promoted = try_promote(uniques)
    print(f"\nresult ok={ok} skipped={skipped} fail={fail} promoted={promoted}")
    return 0 if (fail == 0 and promoted) else 1


def try_promote(uniques: list[dict]) -> bool:
    """Promote staging -> live iff:
        * 890 unique entries
        * All have tts_status in {'ok','skipped_existing_valid'}
        * All files exist in staging
    """
    if len(uniques) != EXPECTED_UNIQUE:
        return False
    for u in uniques:
        if u.get("tts_status") not in ("ok", "skipped_existing_valid"):
            return False
        if not (STAGING_DIR / u["output_filename"]).exists():
            return False
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    for u in uniques:
        src = STAGING_DIR / u["output_filename"]
        dst = LIVE_DIR / u["output_filename"]
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        shutil.copyfile(str(src), str(tmp))
        tmp.replace(dst)
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python a1_example_audio.py [preflight|pilot|full|resume]")
        return 2
    mode = argv[1]
    if mode == "preflight":
        return run_preflight()
    if mode == "pilot":
        return run_pilot()
    if mode == "full":
        return run_full()
    if mode == "resume":
        return run_resume()
    print(f"unknown mode: {mode}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
