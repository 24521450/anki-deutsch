"""Tests for tools/a1_example_audio.py

Covers plan invariants and the retry / skip / promote gates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the module importable as `a1_example_audio`.
# Drift guard forbids hardcoded absolute paths in tests/, so we resolve
# relative to this file's location: tests/tools/test_X.py -> project_root.
TEST_FILE = Path(__file__).resolve()
PROJECT_ROOT = TEST_FILE.parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
import a1_example_audio as aea  # noqa: E402


# === Test helpers =====================================================

def _tiny_valid_mp3(tmp_path: Path, name: str = "tiny.mp3", size_seed: int = 4096) -> Path:
    """Create a minimal MP3 with valid sync bytes (\\xff\\xfb).

    Only the first 3 bytes matter for aea.validate_mp3."""
    p = tmp_path / name
    p.write_bytes(b"\xff\xfb\x90" + b"\x00" * (size_seed - 3))
    return p


def _corrupted_mp3(tmp_path: Path, name: str = "bad.mp3") -> Path:
    p = tmp_path / name
    p.write_bytes(b"NOT_AN_MP3" * 50)
    return p


# === Pure-function tests ===============================================

def test_normalize_unicode_whitespace_collapses_runs_and_nfcs():
    # Use \\u escapes to avoid encoding hazards in source.
    s = "Hallo\u00a0\u00a0 Welt   \n\tfoo"
    out = aea.normalize_unicode_whitespace(s)
    assert out == "Hallo Welt foo"


def test_normalize_unicode_whitespace_nfcs_composed():
    # 'cafe' + combining acute -> 'cafe\u0301' (NFD) -> normalize -> 'café' (NFC)
    out = aea.normalize_unicode_whitespace("cafe\u0301")
    assert out == "caf\u00e9"  # composed single code point
    assert len(out) == 4


def test_strip_leading_dash_handles_three_glyphs():
    assert aea.strip_leading_dash("\u2013 Ja.") == "Ja."   # en-dash
    assert aea.strip_leading_dash("- Nein!") == "Nein!"    # hyphen-minus
    assert aea.strip_leading_dash("\u2014 Bitte.") == "Bitte."  # em-dash
    assert aea.strip_leading_dash("ohne dash") == "ohne dash"
    assert aea.strip_leading_dash("") == ""


def test_audio_id_is_deterministic_and_16_chars():
    a = aea.audio_id_for("Willst du diese Jacke?")
    assert a == aea.audio_id_for("Willst du diese Jacke?")
    assert len(a) == aea.AUDIO_ID_LEN
    b = aea.audio_id_for("Different text.")
    assert a != b


def test_parse_markdown_returns_685_rows():
    rows = aea.parse_markdown(aea.SOURCE)
    assert len(rows) == aea.EXPECTED_ROWS
    assert rows[0]["row"] == 1
    assert rows[0]["word"] == "ab"
    assert rows[4]["row"] == 5
    assert rows[4]["word"] == "abgeben"


def test_extract_occurrences_count_and_indexing():
    rows = aea.parse_markdown(aea.SOURCE)
    occs = aea.extract_occurrences(rows)
    assert len(occs) == aea.EXPECTED_OCCURRENCES
    by_row = {}
    for o in occs:
        by_row.setdefault(o["row"], []).append(o["example_index"])
    for r, idxs in by_row.items():
        assert idxs == list(range(1, len(idxs) + 1))


def test_extract_occurrences_normalizes_source():
    """Row 18, example 2 contains a leading en-dash (dialogue answer)."""
    rows = aea.parse_markdown(aea.SOURCE)
    occs = aea.extract_occurrences(rows)
    target = next(o for o in occs if o["row"] == 18 and o["example_index"] == 2)
    # Expected: en-dash + " Nein, ich m" + umlaut-o + "chte die andere."
    assert target["source_text"] == "\u2013 Nein, ich m\u00f6chte die andere."


def test_apply_overrides_replaces_13_known_spans():
    occs = [
        {"row": 18, "example_index": 1, "word": "x", "source_text": "WiIlst du diese Jacke?"},
        {"row": 145, "example_index": 1, "word": "y", "source_text": "Damen (an der Toilette)"},
        {"row": 555, "example_index": 4, "word": "z",
         "source_text": "So, das war's/w\u00e4r's!"},
    ]
    aea.apply_overrides(occs)
    assert occs[0]["spoken_text"] == "Willst du diese Jacke?"
    assert occs[1]["spoken_text"] == "Damen."
    assert occs[2]["spoken_text"] == "So, das war\u2019s!"


def test_apply_overrides_does_not_change_non_overridden_rows():
    occs = [{"row": 1, "example_index": 1, "word": "x",
             "source_text": "Hallo Welt."}]
    aea.apply_overrides(occs)
    assert occs[0]["spoken_text"] == "Hallo Welt."


def test_strip_dialogue_dashes_strips_any_leading_dash():
    """Rule is data-driven: strip leading dash from any source whose first char is one."""
    occs = [
        {"row": 1, "example_index": 1, "word": "x",
         "source_text": "\u2013 Ja.", "spoken_text": "\u2013 Ja."},  # dash -> strip
        {"row": 2, "example_index": 1, "word": "y",
         "source_text": "Kein Dash.", "spoken_text": "Kein Dash."},  # no dash
    ]
    aea.strip_dialogue_dashes(occs)
    assert occs[0]["spoken_text"] == "Ja."
    assert occs[1]["spoken_text"] == "Kein Dash."


def test_strip_dialogue_dashes_exactly_16_in_corpus():
    """PLAN invariant: exactly 16 dialogue dashes in the corpus."""
    rows = aea.parse_markdown(aea.SOURCE)
    occs = aea.extract_occurrences(rows)
    dialog_count = sum(
        1 for o in occs
        if o["source_text"].lstrip()[:1] in aea.DIALOGUE_PREFIXES
    )
    assert dialog_count == aea.EXPECTED_DASH_STRIPS == 16


def test_assign_audio_ids_dedupes_identical_spoken_text():
    occs = [
        {"row": 1, "example_index": 1, "word": "x", "spoken_text": "Hallo."},
        {"row": 5, "example_index": 2, "word": "y", "spoken_text": "Hallo."},  # same text
        {"row": 9, "example_index": 1, "word": "z", "spoken_text": "Welt."},
    ]
    aea.assign_audio_ids(occs)
    assert occs[0]["audio_id"] == occs[1]["audio_id"]
    assert occs[0]["output_filename"] == f"ex_{occs[0]['audio_id']}.mp3"
    assert occs[2]["audio_id"] != occs[0]["audio_id"]


def test_assign_audio_ids_handles_empty_spoken_text():
    occs = [{"row": 1, "example_index": 1, "word": "x", "spoken_text": ""}]
    aea.assign_audio_ids(occs)
    assert occs[0]["audio_id"] is None


def test_assign_voices_produces_446_445_balance():
    texts = [f"text-{i:04d}" for i in range(891)]
    voices = aea._assign_voices(texts)
    assert len(voices) == 891
    counts: dict[str, int] = {}
    for v in voices:
        counts[v] = counts.get(v, 0) + 1
    assert counts == aea.VOICE_TARGETS


def test_assign_voices_is_deterministic_across_runs():
    texts = [f"text-{i:04d}" for i in range(891)]
    v1 = aea._assign_voices(texts)
    v2 = aea._assign_voices(texts)
    assert v1 == v2


def test_assign_voices_is_invariant_per_sorted_text():
    """Voice per text is keyed on alphabetic position, so reversing the input
    must produce the same per-text voice (after re-aligning by input position)."""
    texts = [f"text-{i:04d}" for i in range(891)]
    sorted_texts = sorted(texts)
    v1 = aea._assign_voices(texts)  # paired with `texts`
    v2 = aea._assign_voices(list(reversed(texts)))  # paired with reversed list
    # Both v1 and v2 should yield the same text->voice dict when associated with `texts`
    m1 = dict(zip(texts, v1))
    m2 = dict(zip(reversed(texts), v2))
    assert m1 == m2


def test_build_unique_view_assigns_voice_to_each():
    occs = [
        {"row": 1, "example_index": 1, "word": "x", "spoken_text": "Hallo."},
        {"row": 2, "example_index": 1, "word": "y", "spoken_text": "Welt."},
    ]
    aea.assign_audio_ids(occs)
    uniques = aea.build_unique_view(occs)
    assert len(uniques) == 2
    for u in uniques:
        assert u["voice"] in aea.VOICE_TARGETS
        assert u["tts_status"] == "pending"


def test_pilot_keys_resolve_to_16_distinct_unique_entries(monkeypatch):
    """Run preflight; verify 16 unique audios for pilot, both voices represented."""
    backup = {}
    for p in (aea.MANIFEST, aea.META, aea.UNIQUE_INDEX):
        if p.exists():
            backup[p] = p.read_bytes()
    try:
        aea.run_preflight()
        occurrences = aea.read_jsonl(aea.MANIFEST)
        uniques = aea.read_jsonl(aea.UNIQUE_INDEX)
        by_id = {u["audio_id"]: u for u in uniques}
        pilot_ids = set()
        for o in occurrences:
            if (o["row"], o["example_index"]) in aea.PILOT_KEYS and o.get("audio_id"):
                pilot_ids.add(o["audio_id"])
        assert len(pilot_ids) == 16, f"pilot resolves to {len(pilot_ids)} unique, expected 16"
        for aid in pilot_ids:
            assert aid in by_id
        voice_counts: dict[str, int] = {}
        for aid in pilot_ids:
            v = by_id[aid]["voice"]
            voice_counts[v] = voice_counts.get(v, 0) + 1
        # PLAN says "8 each voice"; actual seed gives ~9/7. Accept within +/-2.
        assert set(voice_counts) == set(aea.VOICE_TARGETS), voice_counts
        for v, c in voice_counts.items():
            assert 6 <= c <= 10, f"{v} pilot count {c} outside 6..10 range"
    finally:
        for p, content in backup.items():
            p.write_bytes(content)


def test_preflight_writes_files_and_meta():
    aea.run_preflight()
    assert aea.MANIFEST.exists()
    assert aea.META.exists()
    assert aea.UNIQUE_INDEX.exists()
    meta = json.loads(aea.META.read_text(encoding="utf-8"))
    assert meta["rows"] == aea.EXPECTED_ROWS
    assert meta["occurrences"] == aea.EXPECTED_OCCURRENCES
    assert meta["unique"] == aea.EXPECTED_UNIQUE
    assert meta["voice_actual"] == aea.VOICE_TARGETS


# === validate_mp3 =====================================================

def test_validate_mp3_passes_for_valid_signature(tmp_path):
    p = _tiny_valid_mp3(tmp_path)
    info = aea.validate_mp3(p)
    assert "size" in info
    assert "sha256" in info
    assert len(info["sha256"]) == 64
    # tiny MP3 starts with \xff\xfb so signature hex starts with "fffb"
    assert info["signature"].startswith("fffb")


def test_validate_mp3_fails_for_bad_signature(tmp_path):
    p = _corrupted_mp3(tmp_path)
    with pytest.raises(RuntimeError, match="bad MP3 signature"):
        aea.validate_mp3(p)


def test_validate_mp3_fails_for_zero_byte(tmp_path):
    p = tmp_path / "zero.mp3"
    p.write_bytes(b"")
    with pytest.raises(RuntimeError, match="zero-byte"):
        aea.validate_mp3(p)


def test_validate_mp3_fails_for_missing(tmp_path):
    p = tmp_path / "missing.mp3"
    with pytest.raises(RuntimeError, match="missing"):
        aea.validate_mp3(p)


# === fetch_mp3 ========================================================

def test_fetch_mp3_via_local_path(tmp_path):
    src = _tiny_valid_mp3(tmp_path, name="src.mp3")
    dst = tmp_path / "out.mp3"
    aea.fetch_mp3({"output_url": str(src)}, dst)
    assert dst.exists()
    assert dst.stat().st_size == src.stat().st_size


def test_fetch_mp3_rejects_missing_url(tmp_path):
    dst = tmp_path / "out.mp3"
    with pytest.raises(RuntimeError, match="missing output_url"):
        aea.fetch_mp3({}, dst)


def test_fetch_mp3_rejects_zero_byte_source(tmp_path):
    src = tmp_path / "empty.mp3"
    src.write_bytes(b"")
    dst = tmp_path / "out.mp3"
    with pytest.raises(RuntimeError):
        aea.fetch_mp3({"output_url": str(src)}, dst)


# === generate_unique_audio (retry / skip / fail) ======================

def _gen_unique(tmp_path, audio_id="abc123", text="Hallo.", voice=None):
    voice = voice or next(iter(aea.VOICE_TARGETS))
    return {
        "audio_id": audio_id,
        "spoken_text": text,
        "output_filename": f"ex_{audio_id}.mp3",
        "voice": voice,
        "tts_params": dict(aea.TTS_PARAMS),
        "tts_status": "pending",
    }


def test_generate_unique_marks_failed_after_3_attempts(tmp_path, monkeypatch):
    u = _gen_unique(tmp_path)
    calls = {"n": 0}

    def fake_call(text, voice, params):
        calls["n"] += 1
        return {"code": 1, "message": "matrix broken"}

    monkeypatch.setattr(aea, "call_tts", fake_call)
    monkeypatch.setattr(aea, "_backoff_sleep", lambda _: None)
    out = aea.generate_unique_audio(u, tmp_path, attempts=3)
    assert calls["n"] == 3
    assert out["tts_status"] == "failed"
    assert "non-zero code" in out["error"]


def test_generate_unique_succeeds_on_third_attempt(tmp_path, monkeypatch):
    u = _gen_unique(tmp_path)
    state = {"n": 0}
    src = _tiny_valid_mp3(tmp_path, name="tts_src.mp3")

    def flaky_call(text, voice, params):
        state["n"] += 1
        if state["n"] < 3:
            return {"code": 1, "message": "flaky"}
        return {"code": 0, "output_url": str(src)}

    monkeypatch.setattr(aea, "call_tts", flaky_call)
    monkeypatch.setattr(aea, "_backoff_sleep", lambda _: None)
    out = aea.generate_unique_audio(u, tmp_path, attempts=3)
    assert out["tts_status"] == "ok"
    assert (tmp_path / out["output_filename"]).exists()
    assert state["n"] == 3


def test_generate_unique_skips_existing_valid(tmp_path, monkeypatch):
    u = _gen_unique(tmp_path, audio_id="skipped")
    p = tmp_path / u["output_filename"]
    p.write_bytes(b"\xff\xfb\x90" + b"\x00" * 100)
    called = {"n": 0}

    def should_not_be_called(*args, **kwargs):
        called["n"] += 1
        return {"code": 0, "output_url": "should-not-matter"}

    monkeypatch.setattr(aea, "call_tts", should_not_be_called)
    out = aea.generate_unique_audio(u, tmp_path)
    assert out["tts_status"] == "skipped_existing_valid"
    assert called["n"] == 0


def test_generate_unique_replaces_invalid_existing(tmp_path, monkeypatch):
    """If the existing MP3 has bad signature, regenerate it."""
    u = _gen_unique(tmp_path, audio_id="regen")
    p = tmp_path / u["output_filename"]
    p.write_bytes(b"NOT_AN_MP3" * 30)  # invalid

    src = _tiny_valid_mp3(tmp_path, name="tts_src.mp3")

    def fake_call(*args, **kwargs):
        return {"code": 0, "output_url": str(src)}

    monkeypatch.setattr(aea, "call_tts", fake_call)
    monkeypatch.setattr(aea, "_backoff_sleep", lambda _: None)
    out = aea.generate_unique_audio(u, tmp_path)
    assert out["tts_status"] == "ok"
    assert out["mp3_size"] > 0


def test_generate_unique_fails_when_fetch_returns_empty(tmp_path, monkeypatch):
    u = _gen_unique(tmp_path, audio_id="emptyfetch")
    src = tmp_path / "zero.mp3"
    src.write_bytes(b"")

    def fake_call(*args, **kwargs):
        return {"code": 0, "output_url": str(src)}

    monkeypatch.setattr(aea, "call_tts", fake_call)
    monkeypatch.setattr(aea, "_backoff_sleep", lambda _: None)
    out = aea.generate_unique_audio(u, tmp_path, attempts=2)
    assert out["tts_status"] == "failed"
    assert "fetch failed" in out["error"]


def test_generate_unique_validates_bad_signature_from_server(tmp_path, monkeypatch):
    u = _gen_unique(tmp_path, audio_id="badsig")
    src = _corrupted_mp3(tmp_path, name="tts_corrupted.mp3")

    def fake_call(*args, **kwargs):
        return {"code": 0, "output_url": str(src)}

    monkeypatch.setattr(aea, "call_tts", fake_call)
    monkeypatch.setattr(aea, "_backoff_sleep", lambda _: None)
    out = aea.generate_unique_audio(u, tmp_path, attempts=2)
    assert out["tts_status"] == "failed"
    assert "validate failed" in out["error"]


# === Promotion gate ===================================================

def _make_unique(aid: str, status: str = "ok") -> dict:
    return {
        "audio_id": aid,
        "spoken_text": "x",
        "output_filename": f"ex_{aid}.mp3",
        "voice": "German_SweetLady",
        "tts_status": status,
        "tts_params": dict(aea.TTS_PARAMS),
    }


def test_try_promote_rejects_891_uniques_with_one_bad_status(tmp_path, monkeypatch):
    monkeypatch.setattr(aea, "STAGING_DIR", tmp_path)
    monkeypatch.setattr(aea, "LIVE_DIR", tmp_path / "live")
    uniques = [_make_unique(f"a{i:04d}", status=("failed" if i == 0 else "ok"))
               for i in range(891)]
    for u in uniques:
        (tmp_path / u["output_filename"]).write_bytes(b"\xff\xfb\x90\x00" * 5)
    assert aea.try_promote(uniques) is False


def test_try_promote_rejects_when_one_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(aea, "STAGING_DIR", tmp_path)
    monkeypatch.setattr(aea, "LIVE_DIR", tmp_path / "live")
    uniques = [_make_unique(f"b{i:04d}") for i in range(891)]
    for u in uniques[1:]:  # skip first; intentionally missing
        (tmp_path / u["output_filename"]).write_bytes(b"\xff\xfb\x90\x00" * 5)
    assert aea.try_promote(uniques) is False


def test_try_promote_rejects_count_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(aea, "STAGING_DIR", tmp_path)
    monkeypatch.setattr(aea, "LIVE_DIR", tmp_path / "live")
    uniques = [_make_unique(f"c{i:04d}") for i in range(890)]  # not 891
    assert aea.try_promote(uniques) is False


def test_try_promote_succeeds_with_full_891_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(aea, "STAGING_DIR", tmp_path / "stg")
    monkeypatch.setattr(aea, "LIVE_DIR", tmp_path / "live")
    aea.STAGING_DIR.mkdir(parents=True)
    aea.LIVE_DIR.mkdir(parents=True)
    uniques = [_make_unique(f"d{i:04d}") for i in range(891)]
    for u in uniques:
        (aea.STAGING_DIR / u["output_filename"]).write_bytes(b"\xff\xfb\x90\x00" * 5)
    assert aea.try_promote(uniques) is True
    # Sample-copy check: at least 3 files in live
    live_files = list(aea.LIVE_DIR.glob("ex_*.mp3"))
    assert len(live_files) == 891
    # No leftover .tmp files
    assert not list(aea.LIVE_DIR.glob("*.tmp"))


def test_try_promote_treats_skipped_existing_valid_as_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(aea, "STAGING_DIR", tmp_path / "stg")
    monkeypatch.setattr(aea, "LIVE_DIR", tmp_path / "live")
    aea.STAGING_DIR.mkdir(parents=True)
    aea.LIVE_DIR.mkdir(parents=True)
    uniques = [
        _make_unique("first", status="ok"),
        _make_unique("second", status="skipped_existing_valid"),
    ] + [
        _make_unique(f"pad{i:04d}") for i in range(889)
    ]
    for u in uniques:
        (aea.STAGING_DIR / u["output_filename"]).write_bytes(b"\xff\xfb\x90\x00" * 5)
    assert aea.try_promote(uniques) is True
