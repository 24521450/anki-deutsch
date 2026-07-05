"""Pilot + full generator for Goethe A1 word audio.

Reads `audio/a1/words_manifest.jsonl`, calls matrix_synthesize_speech
for each pending row, downloads the resulting MP3 (either via CDN URL or
auto-saved local file), validates (exists, non-zero, ID3/MPEG signature,
hash), and saves it to the planned output_path.

Retries up to 3 times per item. Updates manifest with tts_status, hash,
size on success. Checkpoints manifest every 25 outputs (full mode).

Usage:
  python tools/a1_generate.py --pilot-only    # 12 pilot rows
  python tools/a1_generate.py                 # all pending rows
"""

from __future__ import annotations

import glob
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "audio" / "a1"
WORDS_DIR = OUT_DIR / "words"
MANIFEST = OUT_DIR / "words_manifest.jsonl"
META = OUT_DIR / "words_manifest.meta.json"

PILOT_ROWS = {1, 4, 10, 34, 62, 248, 381, 483, 532, 553, 573, 644}
MAX_RETRIES = 3
CHECKPOINT_EVERY = 25


def list_matrix_media() -> list[Path]:
    pat = str(ROOT / "matrix-media-*.mp3")
    return [Path(p) for p in glob.glob(pat)]


def newest_media(before: set[Path]) -> Path | None:
    after = set(list_matrix_media())
    diff = after - before
    if not diff:
        return None
    return max(diff, key=lambda p: p.stat().st_mtime)


def call_tts(text: str, voice: str, params: dict) -> dict:
    args_json = ROOT / "tools" / ".tts_args.json"
    payload = {
        "text": text,
        "voice_id": voice,
        "speed": params["speed"],
        "pitch": params["pitch"],
        "volume": params["volume"],
        "emotion": params["emotion"],
    }
    args_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        f'mavis mcp call matrix matrix_synthesize_speech --file "{args_json}"',
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=True,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    start = out.find("{")
    end = out.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(f"No JSON in TTS response (rc={proc.returncode}):\n{out[:500]}")
    return json.loads(out[start:end + 1])


def validate_mp3(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"missing: {path}")
    size = path.stat().st_size
    if size == 0:
        raise RuntimeError(f"zero-byte: {path}")
    with path.open("rb") as f:
        head = f.read(3)
    if head[:3] not in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        raise RuntimeError(f"bad MP3 signature: {head!r}")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"size": size, "sha256": sha, "signature": head[:3].hex()}


def fetch_output(resp: dict, before: set[Path]) -> Path:
    """Get the MP3 file from either CDN URL or auto-downloaded local file."""
    # First try local file (newer Matrix responses auto-download)
    time.sleep(0.4)
    local = newest_media(before)
    if local and local.exists() and local.stat().st_size > 0:
        return local

    # Fall back to CDN URL download
    url = resp.get("output_url", "")
    if url and url.startswith("http"):
        tmp = ROOT / f".tts_dl_{int(time.time() * 1000)}.mp3"
        urllib.request.urlretrieve(url, tmp)
        return tmp

    raise RuntimeError(f"no MP3 found. resp={resp}")


def generate_one(row: dict, attempts_left: int) -> dict:
    """Generate one MP3, validate, move to output_path. Update row dict."""
    out_path = Path(row["output_path"])

    # Skip if existing file is valid
    if out_path.exists():
        try:
            v = validate_mp3(out_path)
            row.update({
                "tts_status": "skipped_existing_valid",
                "mp3_size": v["size"],
                "mp3_sha256": v["sha256"],
                "mp3_signature": v["signature"],
            })
            return row
        except Exception:
            out_path.unlink(missing_ok=True)

    last_err = None
    while attempts_left > 0:
        attempts_left -= 1
        before = set(list_matrix_media())
        try:
            resp = call_tts(row["word"], row["voice"], row["tts_params"])
        except Exception as e:
            last_err = f"call failed: {e}"
            print(f"  attempt {MAX_RETRIES - attempts_left}/{MAX_RETRIES}: {last_err}")
            time.sleep(1.5)
            continue

        if resp.get("code") != 0:
            last_err = f"non-zero code: {resp.get('code')} msg={resp.get('message')}"
            print(f"  attempt {MAX_RETRIES - attempts_left}/{MAX_RETRIES}: {last_err}")
            time.sleep(1.5)
            continue

        try:
            src = fetch_output(resp, before)
        except Exception as e:
            last_err = f"fetch failed: {e}"
            print(f"  attempt {MAX_RETRIES - attempts_left}/{MAX_RETRIES}: {last_err}")
            time.sleep(1.5)
            continue

        try:
            v = validate_mp3(src)
        except Exception as e:
            last_err = f"validation failed: {e}"
            print(f"  attempt {MAX_RETRIES - attempts_left}/{MAX_RETRIES}: {last_err}")
            try:
                src.unlink()
            except OSError:
                pass
            time.sleep(1.5)
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(out_path))
        row.update({
            "tts_status": "ok",
            "mp3_size": v["size"],
            "mp3_sha256": v["sha256"],
            "mp3_signature": v["signature"],
        })
        return row

    raise RuntimeError(f"exhausted retries: {last_err}")


def write_manifest(rows: list[dict]) -> None:
    with MANIFEST.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    if not MANIFEST.exists():
        print(f"FAIL: manifest missing: {MANIFEST}", file=sys.stderr)
        return 2
    WORDS_DIR.mkdir(parents=True, exist_ok=True)

    rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    pilot_only = "--pilot-only" in sys.argv
    if pilot_only:
        targets = [r for r in rows if r["row"] in PILOT_ROWS]
    else:
        targets = [r for r in rows if r.get("tts_status") in (None, "pending")]
    targets.sort(key=lambda r: r["row"])
    mode = "pilot-only" if pilot_only else "full"
    print(f"mode={mode}, count={len(targets)}")

    ok = 0
    fail = 0
    for i, r in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] row={r['row']:3d} word={r['word']!r:20s} voice={r['voice']}")
        try:
            updated = generate_one(r, MAX_RETRIES)
            v = updated.get("mp3_size", "?")
            h = updated.get("mp3_sha256", "")[:12]
            print(f"  -> {updated['tts_status']} size={v} sha={h}")
            ok += 1
        except Exception as e:
            print(f"  -> FAIL: {e}")
            r["tts_status"] = "failed"
            r["error"] = str(e)
            fail += 1

        # Update row in master list
        idx = next(j for j, x in enumerate(rows) if x["row"] == r["row"])
        rows[idx] = r

        # Checkpoint manifest
        if not pilot_only and i % CHECKPOINT_EVERY == 0:
            write_manifest(rows)
            print(f"  [checkpoint] manifest written at {i}")

    write_manifest(rows)
    print(f"\nresult: ok={ok} fail={fail} total={len(targets)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
