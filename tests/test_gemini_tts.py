from __future__ import annotations

import asyncio
import math
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import gemini_tts as tts  # noqa: E402


def pcm(seconds: float = 0.2) -> bytes:
    samples = int(tts.CONFIG["sample_rate_hz"] * seconds)
    return b"".join(
        struct.pack("<h", int(4_000 * math.sin(index / 9)))
        for index in range(samples)
    )


def fake_mp3(marker: bytes = b"x") -> bytes:
    return b"ID3" + marker * 256


def patch_generation(monkeypatch, transcripts, pcm_values=None):
    synthesis_calls = []
    transcript_values = iter(transcripts)
    audio_values = iter(pcm_values or [pcm()] * len(transcripts))
    latest_transcript = {"value": ""}
    monkeypatch.setattr(tts, "_create_client", lambda: object())

    async def synthesize(client, text, voice, purpose):
        synthesis_calls.append((client, text, voice, purpose))
        latest_transcript["value"] = next(transcript_values)
        return next(audio_values), latest_transcript["value"]

    async def transcribe(client, value):
        return latest_transcript["value"]

    monkeypatch.setattr(tts, "_synthesize_pcm", synthesize)
    monkeypatch.setattr(tts, "_transcribe_pcm", transcribe)
    monkeypatch.setattr(tts, "_encode_mp3", lambda value: fake_mp3())

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(tts, "_sleep", no_sleep)
    monkeypatch.setattr(
        tts, "_now_utc", lambda: "2026-07-29T00:00:00+00:00"
    )
    return synthesis_calls


def live_response(
    *,
    audio: bytes | None = None,
    transcript: str | None = None,
    turn_complete: bool = False,
    mime_type: str = "audio/pcm;rate=24000",
    go_away: bool = False,
):
    parts = []
    if audio is not None:
        parts.append(
            SimpleNamespace(
                inline_data=SimpleNamespace(data=audio, mime_type=mime_type)
            )
        )
    content = SimpleNamespace(
        interrupted=False,
        output_transcription=(
            SimpleNamespace(text=transcript) if transcript is not None else None
        ),
        model_turn=SimpleNamespace(parts=parts) if parts else None,
        turn_complete=turn_complete,
    )
    return SimpleNamespace(
        server_content=content,
        go_away=SimpleNamespace() if go_away else None,
    )


def test_public_config_pins_live_model_voices_and_contains_no_secret():
    assert tts.VOICES == ("Kore", "Charon")
    assert tts.CONFIG["engine"] == "gemini-live-tts"
    assert tts.CONFIG["model"] == "gemini-3.1-flash-live-preview"
    assert tts.CONFIG["transcription"] == "live-output-audio-transcription"
    assert tts.CONFIG["language_code"] == "de-DE"
    assert tts._LIVE_SESSIONS_PER_VOICE == 3
    assert tts.CONFIG["sdk_version"] == "2.13.0"
    assert tts.CONFIG["encoder_version"] == "1.8.4"
    assert all("key" not in name.casefold() for name in tts.CONFIG)


def test_pommes_frites_pronunciation_override_is_exact_and_example_only():
    text = "Die Kinder essen Bratwurst mit Pommes frites."
    prompt = tts._tts_prompt(text, "example")

    assert (
        "Aussprachehinweis: „Pommes“ in „Pommes frites“ ist einsilbig. "
        "Sprich die Verbindung ungefähr [pɔm ˈfʁɪt], nicht „Pom-mes“ aus."
    ) in prompt
    assert prompt.endswith(f"TEXT:\n{text}")
    assert tts.pronunciation_override_identity(text, "example") == {
        "phrase": "Pommes frites",
        "version": 1,
    }
    for unaffected in (
        "Eine Wurst mit Pommes, bitte.",
        "pommes frites",
        "Pommes frittes",
        "Pommes fritesartig",
    ):
        assert tts.pronunciation_override_identity(unaffected, "example") is None
        assert "Aussprachehinweis" not in tts._tts_prompt(
            unaffected, "example"
        )
    assert tts.pronunciation_override_identity(
        "Pommes frites", "word"
    ) is None
    assert "Aussprachehinweis" not in tts._tts_prompt(
        "Pommes frites", "word"
    )


@pytest.mark.parametrize(
    ("text", "voice", "purpose", "filename"),
    [
        ("", "Kore", "example", "result.mp3"),
        ("Hallo", "Puck", "example", "result.mp3"),
        ("Hallo", "Kore", "dialogue", "result.mp3"),
        ("Hallo", "Kore", "example", "result.wav"),
    ],
)
def test_invalid_input_fails_before_creating_client(
    monkeypatch, tmp_path, text, voice, purpose, filename
):
    monkeypatch.setattr(
        tts,
        "_create_client",
        lambda: (_ for _ in ()).throw(AssertionError("client created")),
    )
    with pytest.raises(tts.GeminiTTSError):
        asyncio.run(
            tts.generate_verified_mp3(
                text, voice, purpose, tmp_path / filename
            )
        )


def test_missing_api_key_fails_without_exposing_a_value(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    with pytest.raises(tts.GeminiTTSError, match="is not set"):
        tts._create_client()


def test_multiple_api_keys_create_round_robin_clients(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "first, second")
    monkeypatch.setattr(tts, "_CLIENTS", [])
    monkeypatch.setattr(tts, "_CLIENT_KEY_DIGESTS", ())
    monkeypatch.setattr(tts, "_CLIENT_CURSOR", 0)
    created = []
    monkeypatch.setattr(
        tts,
        "_new_client",
        lambda key: created.append(key) or SimpleNamespace(key=key),
    )

    assert tts._create_client().key == "first"
    assert tts._create_client().key == "second"
    assert tts._create_client().key == "first"
    assert created == ["first", "second"]


def test_qa_regeneration_rotates_to_the_next_client(monkeypatch, tmp_path):
    calls = patch_generation(
        monkeypatch,
        ["Unvollständig", "Noch falsch", "Richtiger Text."],
    )
    clients = iter(["first", "second", "third"])
    monkeypatch.setattr(tts, "_create_client", lambda: next(clients))

    asyncio.run(
        tts.generate_verified_mp3(
            "Richtiger Text.", "Kore", "example", tmp_path / "result.mp3"
        )
    )

    assert [call[0] for call in calls] == ["first", "second", "third"]


def test_independent_asr_accepts_full_audio_when_live_transcript_is_truncated(
    monkeypatch, tmp_path
):
    patch_generation(monkeypatch, ["Diesen Hut"])

    async def transcribe(client, value):
        return "Diesen Hut habe ich auf dem Flohmarkt gekauft."

    monkeypatch.setattr(tts, "_transcribe_pcm", transcribe)
    result = asyncio.run(
        tts.generate_verified_mp3(
            "Diesen Hut habe ich auf dem Flohmarkt gekauft.",
            "Kore",
            "example",
            tmp_path / "result.mp3",
        )
    )

    assert result["qa_status"] == "exact"
    assert result["qa_source"] == "gemini-3.6-flash"
    assert result["live_transcript"] == "Diesen Hut"
    assert result["asr_transcript"] == (
        "Diesen Hut habe ich auf dem Flohmarkt gekauft."
    )


def test_normalized_live_transcript_writes_atomic_metadata(monkeypatch, tmp_path):
    calls = patch_generation(
        monkeypatch,
        ["FÜR Schüler, Studenten und Rentner gibt es eine Ermäßigung!"],
    )
    target = tmp_path / "nested" / "sample.mp3"
    text = "Für Schüler, Studenten und Rentner gibt es eine Ermäßigung."

    result = asyncio.run(
        tts.generate_verified_mp3(text, "Charon", "example", target)
    )

    assert target.read_bytes() == fake_mp3()
    assert calls == [(calls[0][0], text, "Charon", "example")]
    assert result == {
        "status": "ok",
        "path": str(target),
        "size": len(fake_mp3()),
        "sha256": tts.hashlib.sha256(fake_mp3()).hexdigest(),
        "duration_seconds": 0.2,
        "qa_status": "exact",
            "asr_transcript": (
                "FÜR Schüler, Studenten und Rentner gibt es eine Ermäßigung!"
            ),
            "live_transcript": (
                "FÜR Schüler, Studenten und Rentner gibt es eine Ermäßigung!"
            ),
            "qa_source": "live-output-audio-transcription",
            "voice": "Charon",
        "created_utc": "2026-07-29T00:00:00+00:00",
    }
    assert not list(target.parent.glob(".*.mp3.tmp"))


def test_exact_normalization_does_not_collapse_german_sharp_s():
    assert (
        tts._normalized_spoken_text("Die Maße.")
        != tts._normalized_spoken_text("Die Masse.")
    )


def test_exact_normalization_accepts_unspoken_in_word_hyphen():
    assert tts._normalized_spoken_text(
        "Rauchen ist in den Gemeinschafts-räumen nicht erlaubt."
    ) == tts._normalized_spoken_text(
        "Rauchen ist in den Gemeinschaftsräumen nicht erlaubt."
    )


def test_exact_normalization_accepts_german_spoken_one_third():
    assert tts._normalized_spoken_text("⅓") == tts._normalized_spoken_text(
        "Ein Drittel."
    )


def test_ten_transcript_mismatches_preserve_existing_target(
    monkeypatch, tmp_path
):
    calls = patch_generation(monkeypatch, ["Falscher Text."] * 10)
    target = tmp_path / "existing.mp3"
    target.write_bytes(b"original")

    with pytest.raises(tts.GeminiTTSError, match="after 10 synthesis attempts"):
        asyncio.run(
            tts.generate_verified_mp3(
                "Richtiger Text.", "Kore", "example", target
            )
        )

    assert len(calls) == 10
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.mp3.tmp"))


def test_silent_pcm_is_regenerated_ten_times(monkeypatch, tmp_path):
    calls = patch_generation(
        monkeypatch,
        ["Hallo."] * 10,
        [b"\0\0" * 4_800] * 10,
    )
    with pytest.raises(tts.GeminiTTSError, match="after 10 synthesis attempts"):
        asyncio.run(
            tts.generate_verified_mp3(
                "Hallo.", "Kore", "example", tmp_path / "silent.mp3"
            )
        )
    assert len(calls) == 10


def test_live_config_uses_requested_voice_and_german_language():
    config = tts._live_config("Kore")
    assert config["response_modalities"] == ["AUDIO"]
    assert config["output_audio_transcription"] == {}
    assert config["speech_config"] == {
        "voice_config": {
            "prebuilt_voice_config": {"voice_name": "Kore"},
        },
        "language_code": "de-DE",
    }


def test_live_session_request_collects_pcm_and_transcription(monkeypatch):
    requests = []
    responses = [
        live_response(audio=pcm()[:1000], transcript="Guten"),
        live_response(
            audio=pcm()[1000:],
            transcript=" Morgen.",
            turn_complete=True,
        ),
    ]

    class Session:
        async def send_client_content(self, **request):
            requests.append(request)

        async def receive(self):
            for response in responses:
                yield response

    class Context:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            requests.append({"closed": True})

    class Live:
        def connect(self, **request):
            requests.append(request)
            return Context()

    client = SimpleNamespace(aio=SimpleNamespace(live=Live()))
    monkeypatch.setattr(tts, "_LIVE_STATES", {})

    result = asyncio.run(
        tts._synthesize_pcm(client, "Guten Morgen.", "Kore", "word")
    )

    assert result == (pcm(), "Guten Morgen.")
    assert requests[0]["model"] == "gemini-3.1-flash-live-preview"
    assert requests[0]["config"]["speech_config"]["voice_config"][
        "prebuilt_voice_config"
    ]["voice_name"] == "Kore"
    assert requests[1]["turn_complete"] is True
    assert requests[1]["turns"]["parts"][0]["text"].endswith(
        "TEXT:\nGuten Morgen."
    )


def test_live_turn_rejects_unexpected_audio_format():
    class Session:
        async def receive(self):
            yield live_response(
                audio=pcm(),
                transcript="Hallo.",
                turn_complete=True,
                mime_type="audio/wav",
            )

    with pytest.raises(tts.GeminiTTSError, match="unexpected Gemini Live"):
        asyncio.run(tts._receive_live_turn(Session()))


def test_go_away_retires_live_session(monkeypatch):
    close_calls = []

    class Session:
        async def send_client_content(self, **request):
            pass

        async def receive(self):
            yield live_response(
                audio=pcm(),
                transcript="Hallo.",
                turn_complete=True,
                go_away=True,
            )

    class Context:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            close_calls.append(True)

    class Live:
        def connect(self, **request):
            return Context()

    client = SimpleNamespace(aio=SimpleNamespace(live=Live()))
    monkeypatch.setattr(tts, "_LIVE_STATES", {})

    result = asyncio.run(
        tts._synthesize_pcm(client, "Hallo.", "Kore", "word")
    )

    assert result == (pcm(), "Hallo.")
    assert close_calls == [True]


def test_transcript_mismatch_retires_session_before_regeneration(
    monkeypatch, tmp_path
):
    transcripts = iter(
        [
            "Diese Zeitschrift",
            "Diese Zeitschrift",
            "Diese Zeitschrift",
            "Diese Zeitschrift kostet nichts.",
        ]
    )
    connects = []
    closes = []
    delays = []

    class Session:
        def __init__(self, transcript):
            self.transcript = transcript

        async def send_client_content(self, **request):
            pass

        async def receive(self):
            yield live_response(
                audio=pcm(),
                transcript=self.transcript,
                turn_complete=True,
            )

    class Context:
        def __init__(self, transcript):
            self.session = Session(transcript)

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *args):
            closes.append(self.session.transcript)

    class Live:
        def connect(self, **request):
            transcript = next(transcripts)
            connects.append(transcript)
            return Context(transcript)

    client = SimpleNamespace(aio=SimpleNamespace(live=Live()))
    monkeypatch.setattr(tts, "_LIVE_STATES", {})
    monkeypatch.setattr(tts, "_create_client", lambda: client)
    monkeypatch.setattr(tts, "_encode_mp3", lambda data: b"\xff" + b"x" * 200)

    async def transcribe(client, value):
        return connects[-1]

    monkeypatch.setattr(tts, "_transcribe_pcm", transcribe)

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(tts, "_sleep", sleep)

    result = asyncio.run(
        tts.generate_verified_mp3(
            "Diese Zeitschrift kostet nichts.",
            "Kore",
            "example",
            tmp_path / "example.mp3",
        )
    )

    assert result["qa_status"] == "exact"
    assert len(connects) == 4
    assert closes == ["Diese Zeitschrift"] * 3
    assert delays == [1.0, 2.0, 4.0]


def test_retry_uses_retry_after_for_transient_error(monkeypatch):
    class RateLimited(Exception):
        status_code = 429
        headers = {"Retry-After": "7"}

    attempts = 0
    delays = []

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RateLimited()
        return "ok"

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(tts, "_sleep", sleep)
    assert asyncio.run(tts._retry_api(operation)) == "ok"
    assert attempts == 3
    assert delays == [7.0, 7.0]


@pytest.mark.parametrize("close_code", [1000, 1008])
def test_exhausted_websocket_close_is_rejected_for_outer_regeneration(
    monkeypatch, close_code
):
    class PolicyClosed(Exception):
        code = close_code

    attempts = 0
    delays = []

    async def operation():
        nonlocal attempts
        attempts += 1
        raise PolicyClosed()

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(tts, "_sleep", sleep)
    with pytest.raises(tts._RejectedSynthesis, match="PolicyClosed"):
        asyncio.run(tts._retry_api(operation))

    assert attempts == 4
    assert delays == [1.0, 2.0, 4.0]


def test_retry_after_uses_gemini_error_message_when_headers_are_absent():
    error = RuntimeError("Quota exceeded. Please retry in 59.38424691s.")
    assert tts._retry_after(error) == pytest.approx(60.38424691)


def test_non_transient_error_is_not_retried():
    class BadRequest(Exception):
        status_code = 400

    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        raise BadRequest("secret-free failure")

    with pytest.raises(tts.GeminiTTSError, match=r"BadRequest, HTTP 400"):
        asyncio.run(tts._retry_api(operation))
    assert attempts == 1


def test_lame_encoder_produces_mp3_with_pinned_settings():
    encoded = tts._encode_mp3(pcm(0.5))
    tts._validate_mp3(encoded)
    assert len(encoded) > 128


def test_close_client_releases_sessions_and_transports(monkeypatch):
    calls = []

    class Context:
        async def __aexit__(self, *args):
            calls.append("session")

    class AsyncClient:
        async def aclose(self):
            calls.append("async")

    class Client:
        aio = AsyncClient()

        def close(self):
            calls.append("sync")

    monkeypatch.setattr(tts, "_CLIENTS", [Client(), Client()])
    monkeypatch.setattr(tts, "_CLIENT_KEY_DIGESTS", ("one", "two"))
    monkeypatch.setattr(tts, "_CLIENT_CURSOR", 7)
    monkeypatch.setattr(
        tts,
        "_LIVE_STATES",
        {
            (1, "Kore"): {
                "slots": [{
                    "context": Context(),
                    "session": object(),
                    "created_at": 1.0,
                    "retire": False,
                }],
            }
        },
    )

    asyncio.run(tts.close_client())

    assert calls == ["session", "async", "sync", "async", "sync"]
    assert tts._CLIENTS == []
    assert tts._CLIENT_KEY_DIGESTS == ()
    assert tts._CLIENT_CURSOR == 0
    assert tts._LIVE_STATES == {}
