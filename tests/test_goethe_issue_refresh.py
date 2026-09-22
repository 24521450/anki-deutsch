from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_issue_refresh as refresh  # noqa: E402


def test_variant_registry_preserves_visible_articles_and_bound_form_markers() -> None:
    entries = refresh.load_registry()

    def labels(source_id: str) -> list[str]:
        return [item["label"] for item in entries[source_id]["variants"]]

    assert labels("A1-84887177245") == ["der Frühling", "das Frühjahr"]
    assert labels("A2-0459") == ["hin", "hin-", "-hin"]
    assert labels("B1-MAIN-1010") == ["gesamt-", "Gesamt-"]
    assert labels("B1-MAIN-1742") == ["die Nord-", "Ostsee"]
    assert labels("B1-MAIN-2245") == ["die Speise-", "-speise"]
    assert labels("B1-WG-0123") == ["die Allgemeinbildende Höhere Schule", "AHS"]
    assert all(len(labels(source_id)) == len(set(labels(source_id))) for source_id in entries)


def test_variant_renderer_inserts_one_separator_between_each_button() -> None:
    variants = [
        {"label": "hin", "spoken_text": "hin", "primary": True},
        {"label": "hin-", "spoken_text": "hin", "primary": False},
        {"label": "-hin", "spoken_text": "hin", "primary": False},
    ]
    filenames = [f"_goethe_variant_{digit * 64}.mp3" for digit in "123"]

    markup = refresh.render_variant_html(variants, filenames)

    assert markup.count('class="gw-word-variant"') == 3
    assert markup.count('class="gw-word-variant-separator"') == 2
    assert markup.count('aria-hidden="true">/</span>') == 2
    assert refresh.extract_live_variant_filenames(markup) == filenames


def test_variant_markup_refresh_reuses_live_media_without_audio_preparation(monkeypatch) -> None:
    filename = f"_goethe_variant_{'a' * 64}.mp3"
    old_variants = [
        {"label": "hin", "spoken_text": "hin", "primary": True},
        {"label": "hin second", "spoken_text": "hin", "primary": False},
        {"label": "hin third", "spoken_text": "hin", "primary": False},
    ]
    old_markup = refresh.render_variant_html(old_variants, [filename, filename, filename])
    fields = {
        "SourceID": {"value": "A2-0459"},
        "SourceRefs": {"value": "A2-0459|A2-MAIN-0464"},
        "Lemma": {"value": "hin/hin-/-hin"},
        "WordAudio": {"value": f"[sound:{filename}]"},
        "WordAudioVariantsHTML": {"value": old_markup},
    }
    note = {"noteId": 1497484861176, "fields": fields}
    writes: dict[int, dict[str, str]] = {}
    verified: list[str] = []
    monkeypatch.setattr(refresh, "all_notes", lambda: {1497484861176: note})
    monkeypatch.setattr(refresh, "verify_live_variant_media", lambda name: verified.append(name))
    monkeypatch.setattr(refresh, "export_backup", lambda label: Path("variant-markup.apkg"))
    monkeypatch.setattr(refresh, "update_fields", lambda updates: writes.update(updates))
    monkeypatch.setattr(
        refresh,
        "prepare_variant_audio",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("audio preparation used")),
    )

    refresh.apply_variant_markup({"A2-0459"})

    assert verified == [filename]
    assert list(writes) == [1497484861176]
    updated = writes[1497484861176]["WordAudioVariantsHTML"]
    assert ["hin", "hin-", "-hin"] == [
        item.split('>')[1].split('<')[0]
        for item in updated.split('class="gw-word-variant-label"')[1:]
    ]
    assert updated.count('class="gw-word-variant-separator"') == 2


def test_variant_registry_contains_reviewed_commons_pins() -> None:
    entries = refresh.load_registry()
    variants = {item["spoken_text"]: item for item in entries["A2-WG-0071"]["variants"]}
    assert variants["getrennt"]["audio_source"] == {
        "provider": "commons",
        "title": "File:De-getrennt.ogg",
        "source_url": "https://commons.wikimedia.org/wiki/File:De-getrennt.ogg",
        "audio_url": "https://upload.wikimedia.org/wikipedia/commons/4/45/De-getrennt.ogg",
        "original_sha1": "c3763fd4dfe71c0e65b9e65d9a43a5015a628b5e",
        "license_short_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    }
    assert variants["geschieden"]["audio_source"]["original_sha1"] == "a062832ac4eb9ad3501dfee70f8e0bd3eebbfa1f"


def test_pinned_source_precedes_duden_and_tts(monkeypatch) -> None:
    pin = {
        "provider": "commons",
        "title": "File:De-getrennt.ogg",
        "source_url": "https://commons.wikimedia.org/wiki/File:De-getrennt.ogg",
        "audio_url": "https://upload.wikimedia.org/wikipedia/commons/4/45/De-getrennt.ogg",
        "original_sha1": "c3763fd4dfe71c0e65b9e65d9a43a5015a628b5e",
        "license_short_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    }
    monkeypatch.setattr(refresh, "local_duden_assets", lambda: {})
    monkeypatch.setattr(refresh, "download_commons_pin", lambda text, source: {"provider": "commons", "path": Path("pinned.mp3")})
    monkeypatch.setattr(refresh, "download_duden", lambda *_: (_ for _ in ()).throw(AssertionError("Duden fallback used")))
    monkeypatch.setattr(refresh, "generate_edge", lambda *_: (_ for _ in ()).throw(AssertionError("TTS fallback used")))

    result = refresh.prepare_variant_audio({"getrennt"}, {"getrennt": pin})
    assert result["getrennt"]["provider"] == "commons"


def test_commons_pin_download_validates_metadata_and_records_mp3(monkeypatch, tmp_path) -> None:
    pin = {
        "provider": "commons",
        "title": "File:De-getrennt.ogg",
        "source_url": "https://commons.wikimedia.org/wiki/File:De-getrennt.ogg",
        "audio_url": "https://upload.wikimedia.org/wikipedia/commons/4/45/De-getrennt.ogg",
        "original_sha1": "c3763fd4dfe71c0e65b9e65d9a43a5015a628b5e",
        "license_short_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    }
    api_payload = {
        "query": {
            "pages": [{
                "title": "File:De-getrennt.ogg",
                "videoinfo": [{
                    "mediatype": "AUDIO",
                    "duration": 1.9,
                    "url": pin["audio_url"] + "?utm_source=commons.wikimedia.org",
                    "sha1": pin["original_sha1"],
                    "descriptionurl": pin["source_url"],
                    "derivatives": [{"transcodekey": "mp3", "src": "https://upload.wikimedia.org/commons/getrennt.mp3"}],
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "LicenseUrl": {"value": pin["license_url"]},
                        "Artist": {"value": '<a href="/wiki/User:Jeuwre">Jeuwre</a>'},
                    },
                }],
                "categories": [{"title": "Category:German pronunciation of adjectives"}],
            }]
        }
    }

    class Response:
        status_code = 200
        headers = {}
        content = b"ID3" + b"\x00" * 256

        def json(self):
            return api_payload

    responses = iter([Response(), Response()])
    monkeypatch.setattr(refresh.requests, "get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(refresh, "VARIANT_AUDIO", tmp_path)

    result = refresh.download_commons_pin("getrennt", pin)
    assert result["provider"] == "commons"
    assert result["title"] == "File:De-getrennt.ogg"
    assert result["path"].name.startswith("commons_")
    assert result["path"].suffix == ".mp3"
    assert result["path"].read_bytes().startswith(b"ID3")
