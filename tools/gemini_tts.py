"""Shared, fail-closed Gemini TTS generation and audio verification."""
from __future__ import annotations

import array
import asyncio
import hashlib
import io
import importlib.metadata
import os
import re
import sys
import tempfile
import unicodedata
import wave
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar


VOICES = ("Kore", "Charon")
CONFIG = {
    "engine": "gemini-live-tts",
    "sdk": "google-genai",
    "sdk_version": "2.13.0",
    "model": "gemini-3.1-flash-live-preview",
    "transcription": "live-output-audio-transcription",
    "encoder": "lameenc",
    "encoder_version": "1.8.4",
    "bitrate_kbps": 128,
    "encoder_quality": 2,
    "pcm_format": "s16le",
    "sample_rate_hz": 24_000,
    "channels": 1,
    "sample_width_bytes": 2,
    "language_code": "de-DE",
    "example_prompt_version": 1,
    "word_prompt_version": 1,
    "live_session_version": 1,
    "config_version": 2,
}

_PURPOSES = frozenset({"example", "word"})
_POMMES_FRITES_OVERRIDE = {
    "phrase": "Pommes frites",
    "version": 1,
    "hint": (
        "Aussprachehinweis: „Pommes“ in „Pommes frites“ ist einsilbig. "
        "Sprich die Verbindung ungefähr [pɔm ˈfʁɪt], nicht „Pom-mes“ aus."
    ),
}
_SYNTHESIS_ATTEMPTS = 10
_QA_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 60.0, 60.0)
_API_ATTEMPTS = 4
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_MAX_RETRY_DELAY_SECONDS = 120.0
_TRANSIENT_STATUS_CODES = frozenset(
    {408, 409, 425, 429, 500, 502, 503, 504, 1000, 1008, 1011}
)
_MIN_DURATION_SECONDS = 0.1
_MAX_DURATION_SECONDS = 300.0
_MIN_SIGNAL_PEAK = 8
_LIVE_TURN_TIMEOUT_SECONDS = 90.0
_LIVE_SESSION_MAX_SECONDS = 8 * 60.0
_LIVE_SESSIONS_PER_VOICE = 3
_INDEPENDENT_ASR_MODEL = "gemini-3.6-flash"
_CLIENTS: list[Any] = []
_CLIENT_KEY_DIGESTS: tuple[str, ...] = ()
_CLIENT_CURSOR = 0
_LIVE_STATES: dict[tuple[int, str], dict[str, Any]] = {}

_T = TypeVar("_T")


class GeminiTTSError(RuntimeError):
    """Raised when Gemini audio cannot be generated and verified safely."""


class _RejectedSynthesis(GeminiTTSError):
    """A synthesis result that may succeed if regenerated."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_version(distribution: str, expected: str) -> None:
    try:
        actual = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise GeminiTTSError(f"{distribution} {expected} is required") from exc
    if actual != expected:
        raise GeminiTTSError(f"{distribution} {expected} is required; found {actual}")


def _api_keys() -> tuple[str, ...]:
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    if raw_keys.strip():
        keys = tuple(
            key.strip() for key in raw_keys.split(",") if key.strip()
        )
    else:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        keys = (key,) if key else ()
    if not keys:
        raise GeminiTTSError("GEMINI_API_KEY or GEMINI_API_KEYS is not set")
    if len(set(keys)) != len(keys):
        raise GeminiTTSError("GEMINI_API_KEYS contains duplicate values")
    return keys


def _new_client(key: str) -> Any:
    _require_version(CONFIG["sdk"], CONFIG["sdk_version"])
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiTTSError(
            f"{CONFIG['sdk']} {CONFIG['sdk_version']} is required"
        ) from exc
    return genai.Client(api_key=key)


def _create_client() -> Any:
    global _CLIENTS, _CLIENT_KEY_DIGESTS, _CLIENT_CURSOR
    keys = _api_keys()
    digests = tuple(
        hashlib.sha256(key.encode("utf-8")).hexdigest() for key in keys
    )
    if _CLIENTS and _CLIENT_KEY_DIGESTS != digests:
        raise GeminiTTSError(
            "Gemini API key set changed; close the cached clients first"
        )
    if not _CLIENTS:
        _CLIENTS = [_new_client(key) for key in keys]
        _CLIENT_KEY_DIGESTS = digests
        _CLIENT_CURSOR = 0
    client = _CLIENTS[_CLIENT_CURSOR % len(_CLIENTS)]
    _CLIENT_CURSOR += 1
    return client


async def close_client() -> None:
    """Release cached SDK transports before the owning event loop is closed."""
    global _CLIENTS, _CLIENT_KEY_DIGESTS, _CLIENT_CURSOR, _LIVE_STATES
    clients = _CLIENTS
    states = [
        state
        for pool in _LIVE_STATES.values()
        for state in pool.get("slots", [])
    ]
    _LIVE_STATES = {}
    _CLIENTS = []
    _CLIENT_KEY_DIGESTS = ()
    _CLIENT_CURSOR = 0
    for state in states:
        await _close_live_state(state)
    for client in clients:
        try:
            await client.aio.aclose()
        finally:
            client.close()


def _live_config(voice: str) -> dict[str, Any]:
    return {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": voice},
            },
            "language_code": CONFIG["language_code"],
        },
        "output_audio_transcription": {},
        "system_instruction": (
            "Du bist eine deutsche Vorlesestimme. Sprich den vom Benutzer "
            "markierten TEXT exakt und vollständig in natürlichem Standarddeutsch. "
            "Füge kein Wort hinzu und lasse kein Wort aus."
        ),
    }


def _live_pool(client: Any, voice: str) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    pool_key = (id(client), voice)
    pool = _LIVE_STATES.get(pool_key)
    if pool is None or pool["loop"] is not loop:
        slots = [{
            "loop": loop,
            "context": None,
            "session": None,
            "created_at": 0.0,
            "retire": False,
        } for _ in range(_LIVE_SESSIONS_PER_VOICE)]
        available: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for slot in slots:
            available.put_nowait(slot)
        pool = {"loop": loop, "slots": slots, "available": available}
        _LIVE_STATES[pool_key] = pool
    return pool


async def _close_live_state(state: dict[str, Any]) -> None:
    context = state.get("context")
    state["context"] = None
    state["session"] = None
    state["created_at"] = 0.0
    state["retire"] = False
    if context is not None:
        try:
            await context.__aexit__(None, None, None)
        except Exception:
            pass


async def _ensure_live_session(
    client: Any, voice: str, state: dict[str, Any]
) -> Any:
    loop = asyncio.get_running_loop()
    if (
        state["session"] is not None
        and loop.time() - state["created_at"] < _LIVE_SESSION_MAX_SECONDS
        and not state["retire"]
    ):
        return state["session"]
    await _close_live_state(state)
    context = client.aio.live.connect(
        model=CONFIG["model"],
        config=_live_config(voice),
    )
    session = await context.__aenter__()
    state["context"] = context
    state["session"] = session
    state["created_at"] = loop.time()
    return session


def _tts_prompt(text: str, purpose: str) -> str:
    if purpose == "example":
        instruction = (
            "Lies den folgenden Text exakt und vollständig in natürlichem "
            "Standarddeutsch (Hochdeutsch), klar, ruhig und in mittlerem Tempo vor. "
            "Füge nichts hinzu und lasse nichts aus."
        )
    else:
        instruction = (
            "Sprich das folgende deutsche Wort oder die folgende deutsche Wendung "
            "in natürlichem Standarddeutsch (Hochdeutsch) klar und genau einmal aus. "
            "Füge nichts hinzu und lasse nichts aus."
        )
    override = pronunciation_override_identity(text, purpose)
    hint = (
        f"\n\n{_POMMES_FRITES_OVERRIDE['hint']}"
        if override is not None
        else ""
    )
    return f"{instruction}{hint}\n\nTEXT:\n{text}"


def pronunciation_override_identity(
    text: str, purpose: str
) -> dict[str, Any] | None:
    """Return the scoped cache identity for an exact pronunciation override."""
    phrase = _POMMES_FRITES_OVERRIDE["phrase"]
    if (
        purpose == "example"
        and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text)
    ):
        return {
            "phrase": phrase,
            "version": _POMMES_FRITES_OVERRIDE["version"],
        }
    return None


async def _receive_live_turn(session: Any) -> tuple[bytes, str, bool]:
    pcm_parts: list[bytes] = []
    transcript_parts: list[str] = []
    turn_complete = False
    retire = False
    async for response in session.receive():
        if getattr(response, "go_away", None) is not None:
            retire = True
        content = getattr(response, "server_content", None)
        if content is None:
            continue
        if getattr(content, "interrupted", False):
            raise _RejectedSynthesis("Gemini Live interrupted the audio turn")
        transcription = getattr(content, "output_transcription", None)
        transcription_text = getattr(transcription, "text", None)
        if isinstance(transcription_text, str) and transcription_text:
            transcript_parts.append(transcription_text)
        model_turn = getattr(content, "model_turn", None)
        for part in getattr(model_turn, "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            data = getattr(blob, "data", None)
            if not data:
                continue
            mime_type = getattr(blob, "mime_type", None)
            if mime_type != f"audio/pcm;rate={CONFIG['sample_rate_hz']}":
                raise _RejectedSynthesis(
                    f"unexpected Gemini Live audio format: {mime_type}"
                )
            if not isinstance(data, bytes):
                raise _RejectedSynthesis("Gemini Live returned non-byte PCM")
            pcm_parts.append(data)
        if getattr(content, "turn_complete", False):
            turn_complete = True
            break
    if not turn_complete:
        raise _RejectedSynthesis("Gemini Live ended before turn completion")
    if not pcm_parts:
        raise _RejectedSynthesis("Gemini Live returned no audio")
    transcript = "".join(transcript_parts).strip()
    if not transcript:
        raise _RejectedSynthesis("Gemini Live returned no output transcription")
    return b"".join(pcm_parts), transcript, retire


async def _synthesize_pcm(
    client: Any, text: str, voice: str, purpose: str
) -> tuple[bytes, str]:
    pool = _live_pool(client, voice)
    state = await pool["available"].get()
    try:
        session = await _ensure_live_session(client, voice, state)
        try:
            await session.send_client_content(
                turns={
                    "role": "user",
                    "parts": [{"text": _tts_prompt(text, purpose)}],
                },
                turn_complete=True,
            )
            pcm, transcript, retire = await asyncio.wait_for(
                _receive_live_turn(session),
                timeout=_LIVE_TURN_TIMEOUT_SECONDS,
            )
            if (
                _normalized_spoken_text(transcript)
                != _normalized_spoken_text(text)
            ):
                retire = True
            state["retire"] = retire
            if retire:
                await _close_live_state(state)
            return pcm, transcript
        except Exception:
            await _close_live_state(state)
            raise
    finally:
        pool["available"].put_nowait(state)


def _pcm_duration(pcm: bytes) -> float:
    if not pcm or len(pcm) % CONFIG["sample_width_bytes"]:
        raise _RejectedSynthesis("Gemini TTS returned malformed PCM")
    if pcm.startswith(b"RIFF"):
        raise _RejectedSynthesis("Gemini TTS returned WAV instead of raw PCM")
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    duration = len(samples) / CONFIG["sample_rate_hz"]
    if not _MIN_DURATION_SECONDS <= duration <= _MAX_DURATION_SECONDS:
        raise _RejectedSynthesis(f"implausible PCM duration: {duration:.3f}s")
    if max(abs(sample) for sample in samples) <= _MIN_SIGNAL_PEAK:
        raise _RejectedSynthesis("Gemini TTS returned silent audio")
    return duration


def _encode_mp3(pcm: bytes) -> bytes:
    _require_version(CONFIG["encoder"], CONFIG["encoder_version"])
    try:
        import lameenc
    except ImportError as exc:
        raise GeminiTTSError(
            f"{CONFIG['encoder']} {CONFIG['encoder_version']} is required"
        ) from exc
    try:
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(CONFIG["bitrate_kbps"])
        encoder.set_in_sample_rate(CONFIG["sample_rate_hz"])
        encoder.set_channels(CONFIG["channels"])
        encoder.set_quality(CONFIG["encoder_quality"])
        return encoder.encode(pcm) + encoder.flush()
    except Exception as exc:
        raise GeminiTTSError("failed to encode Gemini PCM as MP3") from exc


async def _transcribe_pcm(client: Any, pcm: bytes) -> str:
    try:
        from google.genai import types
    except ImportError as exc:
        raise GeminiTTSError(
            f"{CONFIG['sdk']} {CONFIG['sdk_version']} is required"
        ) from exc
    payload = io.BytesIO()
    with wave.open(payload, "wb") as stream:
        stream.setnchannels(CONFIG["channels"])
        stream.setsampwidth(CONFIG["sample_width_bytes"])
        stream.setframerate(CONFIG["sample_rate_hz"])
        stream.writeframes(pcm)
    response = await client.aio.models.generate_content(
        model=_INDEPENDENT_ASR_MODEL,
        contents=[
            types.Part.from_bytes(
                data=payload.getvalue(),
                mime_type="audio/wav",
            ),
            (
                "Transkribiere ausschließlich die gesprochenen deutschen "
                "Wörter exakt mit normaler Zeichensetzung. Keine Erklärung."
            ),
        ],
    )
    transcript = getattr(response, "text", None)
    if not isinstance(transcript, str) or not transcript.strip():
        raise _RejectedSynthesis("independent ASR returned no transcript")
    return transcript.strip()


def _validate_mp3(payload: bytes) -> None:
    if len(payload) < 128:
        raise GeminiTTSError("MP3 encoder returned an undersized file")
    if not (payload.startswith(b"ID3") or payload.startswith(b"\xff")):
        raise GeminiTTSError("MP3 encoder returned an invalid file signature")


def _normalized_spoken_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).lower()
    value = value.replace("\u2153", " ein drittel ")
    value = re.sub(r"(?<=\w)[\-\u00ad\u2010\u2011](?=\w)", "", value)
    value = "".join(
        " " if unicodedata.category(character)[0] in {"P", "Z"} else character
        for character in value
    )
    return re.sub(r"\s+", " ", value).strip()


def _status_code(exc: Exception) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(getattr(exc, "raw_response", None), "status_code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _headers(exc: Exception) -> Any:
    return (
        getattr(exc, "headers", None)
        or getattr(getattr(exc, "response", None), "headers", None)
        or getattr(getattr(exc, "raw_response", None), "headers", None)
        or {}
    )


def _retry_after(exc: Exception) -> float | None:
    headers = _headers(exc)
    try:
        value = headers.get("Retry-After")
        if value is None:
            value = headers.get("retry-after")
    except AttributeError:
        return None
    if value is not None:
        try:
            return max(0.0, min(float(value), _MAX_RETRY_DELAY_SECONDS))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(value))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return max(0.0, min(seconds, _MAX_RETRY_DELAY_SECONDS))
            except (TypeError, ValueError, OverflowError):
                pass
    match = re.search(
        r"(?:please\s+)?retry\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s",
        str(exc),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return min(float(match.group(1)) + 1.0, _MAX_RETRY_DELAY_SECONDS)


def _is_transient(exc: Exception) -> bool:
    status = _status_code(exc)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    if status is not None:
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__.casefold()
    module = type(exc).__module__.casefold()
    network_error = any(
        marker in name
        for marker in (
            "timeout",
            "connection",
            "connecterror",
            "networkerror",
            "noresponse",
            "remoteprotocolerror",
            "readerror",
            "writeerror",
        )
    )
    return network_error and module.startswith(("httpx", "httpcore", "google."))


def _safe_api_error(exc: Exception) -> GeminiTTSError:
    status = _status_code(exc)
    suffix = f", HTTP {status}" if status is not None else ""
    return GeminiTTSError(
        f"Gemini API request failed ({type(exc).__name__}{suffix})"
    )


_sleep = asyncio.sleep


async def _retry_api(
    operation: Callable[..., Awaitable[_T]], *args: Any
) -> _T:
    for attempt in range(_API_ATTEMPTS):
        try:
            return await operation(*args)
        except GeminiTTSError:
            raise
        except Exception as exc:
            transient = _is_transient(exc)
            if not transient:
                raise _safe_api_error(exc) from exc
            if attempt == _API_ATTEMPTS - 1:
                raise _RejectedSynthesis(str(_safe_api_error(exc))) from exc
            delay = _retry_after(exc)
            if delay is None:
                delay = _BACKOFF_SECONDS[attempt]
            await _sleep(delay)
    raise AssertionError("unreachable")


def _validate_inputs(text: str, voice: str, purpose: str, target: Path) -> Path:
    if not isinstance(text, str) or not text.strip():
        raise GeminiTTSError("text must be a non-blank string")
    if not isinstance(voice, str) or voice not in VOICES:
        raise GeminiTTSError(f"voice must be one of {VOICES}")
    if not isinstance(purpose, str) or purpose not in _PURPOSES:
        raise GeminiTTSError("purpose must be 'example' or 'word'")
    try:
        path = Path(target)
    except TypeError as exc:
        raise GeminiTTSError("target must be a filesystem path") from exc
    if not path.name or path.suffix.casefold() != ".mp3":
        raise GeminiTTSError("target must name an MP3 file")
    return path


async def generate_verified_mp3(
    text: str, voice: str, purpose: str, target: Path
) -> dict[str, Any]:
    """Generate an atomic MP3 after exact Gemini Live output transcription QA."""
    target = _validate_inputs(text, voice, purpose, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    last_transcript = ""

    for attempt in range(_SYNTHESIS_ATTEMPTS):
        client = _create_client()
        temporary: Path | None = None
        try:
            try:
                pcm, transcript = await _retry_api(
                    _synthesize_pcm, client, text, voice, purpose
                )
                duration = _pcm_duration(pcm)
            except _RejectedSynthesis:
                if attempt < len(_QA_BACKOFF_SECONDS):
                    await _sleep(_QA_BACKOFF_SECONDS[attempt])
                continue
            last_transcript = transcript
            verified_transcript = transcript
            qa_source = CONFIG["transcription"]
            if (
                _normalized_spoken_text(transcript)
                != _normalized_spoken_text(text)
            ):
                try:
                    independent_transcript = await _retry_api(
                        _transcribe_pcm, client, pcm
                    )
                except _RejectedSynthesis:
                    if attempt < len(_QA_BACKOFF_SECONDS):
                        await _sleep(_QA_BACKOFF_SECONDS[attempt])
                    continue
                if (
                    _normalized_spoken_text(independent_transcript)
                    != _normalized_spoken_text(text)
                ):
                    last_transcript = independent_transcript
                    if attempt < len(_QA_BACKOFF_SECONDS):
                        await _sleep(_QA_BACKOFF_SECONDS[attempt])
                    continue
                verified_transcript = independent_transcript
                qa_source = _INDEPENDENT_ASR_MODEL
            mp3 = _encode_mp3(pcm)
            _validate_mp3(mp3)

            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".mp3.tmp", dir=target.parent
            )
            temporary = Path(temporary_name)
            try:
                handle = os.fdopen(descriptor, "wb")
            except Exception:
                os.close(descriptor)
                raise
            with handle:
                handle.write(mp3)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary, target)
            temporary = None
            digest = hashlib.sha256(mp3).hexdigest()
            return {
                "status": "ok",
                "path": str(target),
                "size": len(mp3),
                "sha256": digest,
                "duration_seconds": round(duration, 6),
                "qa_status": "exact",
                "asr_transcript": verified_transcript,
                "live_transcript": transcript,
                "qa_source": qa_source,
                "voice": voice,
                "created_utc": _now_utc(),
            }
        except GeminiTTSError:
            raise
        except Exception as exc:
            raise GeminiTTSError(
                f"failed to write verified MP3 ({type(exc).__name__})"
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    transcript_suffix = (
        f"; last Live transcript={last_transcript!r}" if last_transcript else ""
    )
    raise GeminiTTSError(
        f"Gemini TTS failed QA after {_SYNTHESIS_ATTEMPTS} synthesis attempts"
        f"{transcript_suffix}"
    )
