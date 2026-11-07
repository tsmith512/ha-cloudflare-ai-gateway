"""Tests of the speech entities and their Workers AI gateway contract."""

import base64
from collections.abc import AsyncGenerator
from io import BytesIO
import sys
from unittest.mock import AsyncMock, Mock, patch
import wave

import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ai_gateway.const import DOMAIN, STT_MODEL, TTS_MODEL
from custom_components.cloudflare_ai_gateway.speech import SPEECH_TIMEOUT
from custom_components.cloudflare_ai_gateway.stt import MAX_PCM_BYTES, CloudflareSpeechToTextEntity
from custom_components.cloudflare_ai_gateway.tts import CloudflareTextToSpeechEntity
from homeassistant.components import stt, tts
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

pytestmark = pytest.mark.usefixtures("mock_init_component_with_speech")

PCM_AUDIO = b"\x01\x00\x02\x00" * 800
MP3_AUDIO = b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00"


def make_wav(pcm: bytes = PCM_AUDIO, sample_rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    """Build real WAV input with customizable audio settings."""
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


async def audio_stream(*chunks: bytes) -> AsyncGenerator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.fixture
def metadata() -> stt.SpeechMetadata:
    return stt.SpeechMetadata(
        language="en",
        format=stt.AudioFormats.WAV,
        codec=stt.AudioCodecs.PCM,
        bit_rate=stt.AudioBitRates.BITRATE_16,
        sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
        channel=stt.AudioChannels.CHANNEL_MONO,
    )


@pytest.fixture
def stt_entity(hass: HomeAssistant) -> CloudflareSpeechToTextEntity:
    entity = stt.async_get_speech_to_text_entity(hass, "stt.test_whisper")
    assert isinstance(entity, CloudflareSpeechToTextEntity)
    return entity


@pytest.fixture
def tts_entity(hass: HomeAssistant) -> CloudflareTextToSpeechEntity:
    entity = tts.get_engine_instance(hass, "tts.test_draco")
    assert isinstance(entity, CloudflareTextToSpeechEntity)
    return entity


@pytest.fixture
def mock_speech_http() -> AsyncGenerator[AsyncMock]:
    with patch("custom_components.cloudflare_ai_gateway.speech.get_async_client") as get_client:
        client = get_client.return_value = AsyncMock()
        client.post.return_value = httpx.Response(200, json={"success": True, "result": {"text": "Turn on the light"}})
        yield client


async def test_registration(
    hass: HomeAssistant, mock_config_entry_with_speech: MockConfigEntry, stt_entity, tts_entity
) -> None:
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    entry = mock_config_entry_with_speech
    for entity, model in ((stt_entity, STT_MODEL), (tts_entity, TTS_MODEL)):
        registered = registry.async_get(entity.entity_id)
        assert registered is not None
        subentry = entry.subentries[registered.config_subentry_id]
        assert registered.unique_id == subentry.subentry_id
        assert registered.config_entry_id == entry.entry_id
        device = devices.async_get(registered.device_id)
        assert device.identifiers == {(DOMAIN, subentry.subentry_id)}
        assert device.name == subentry.title
        assert device.manufacturer == "Cloudflare"
        assert device.model == f"workers-ai/{model}"
        assert device.entry_type == dr.DeviceEntryType.SERVICE
        assert entity.name is None
    assert not hass.states.async_entity_ids("sensor")
    assert entry.runtime_data.model_stats == {}
    assert tts_entity.supported_options is None
    assert not tts_entity.async_supports_streaming_input()
    assert tts_entity.default_language == "en"


@pytest.mark.parametrize("wrapped", [False, True])
async def test_stt_request(stt_entity, metadata, mock_speech_http, wrapped) -> None:
    audio = make_wav() if wrapped else PCM_AUDIO
    result = await stt_entity.async_process_audio_stream(metadata, audio_stream(audio[:17], audio[17:]))
    assert result == stt.SpeechResult("Turn on the light", stt.SpeechResultState.SUCCESS)
    mock_speech_http.post.assert_awaited_once_with(
        f"https://api.cloudflare.com/client/v4/accounts/test-account-id/ai/run/{STT_MODEL}",
        headers={"Authorization": "Bearer test-cf-token", "cf-aig-gateway-id": "test-gateway"},
        json={"audio": base64.b64encode(make_wav()).decode("ascii"), "language": "en", "task": "transcribe"},
        timeout=SPEECH_TIMEOUT,
    )


@pytest.mark.parametrize(
    "audio",
    [
        b"",
        b"\x01",
        b"RIFFbroken",
        make_wav(b""),
        make_wav(sample_rate=8000),
        make_wav(channels=2),
        make_wav(width=1),
        make_wav()[:-2],
        b"\x00" * (MAX_PCM_BYTES + 2),
        make_wav(b"\x00" * (MAX_PCM_BYTES + 2)),
    ],
    ids=[
        "empty",
        "partial-sample",
        "bad-wav",
        "empty-wav",
        "sample-rate",
        "stereo",
        "width",
        "truncated",
        "long-pcm",
        "long-wav",
    ],
)
async def test_invalid_audio(stt_entity, metadata, mock_speech_http, audio) -> None:
    result = await stt_entity.async_process_audio_stream(metadata, audio_stream(audio))
    assert result == stt.SpeechResult(None, stt.SpeechResultState.ERROR)
    mock_speech_http.post.assert_not_called()


async def test_audio_cap_stops_reading_before_encoding(stt_entity, metadata, mock_speech_http) -> None:
    async def too_long():
        yield b"\x00" * MAX_PCM_BYTES
        yield b"\x00" * 46
        pytest.fail("Oversize input should stop being read immediately")

    with patch("custom_components.cloudflare_ai_gateway.stt.base64.b64encode") as encode:
        result = await stt_entity.async_process_audio_stream(metadata, too_long())
    assert result.result == stt.SpeechResultState.ERROR
    encode.assert_not_called()
    mock_speech_http.post.assert_not_called()


@pytest.mark.parametrize("wrapped", [False, True])
async def test_stt_exact_limit(stt_entity, metadata, mock_speech_http, wrapped) -> None:
    pcm = b"\x00" * MAX_PCM_BYTES
    result = await stt_entity.async_process_audio_stream(metadata, audio_stream(make_wav(pcm) if wrapped else pcm))
    assert result.result == stt.SpeechResultState.SUCCESS
    audio = base64.b64decode(mock_speech_http.post.call_args.kwargs["json"]["audio"])
    assert audio == make_wav(pcm)


async def test_unsupported_metadata(stt_entity, metadata, mock_speech_http) -> None:
    metadata.sample_rate = stt.AudioSampleRates.SAMPLERATE_48000
    result = await stt_entity.async_process_audio_stream(metadata, audio_stream(PCM_AUDIO))
    assert result.result == stt.SpeechResultState.ERROR
    mock_speech_http.post.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "success": False,
            "result": {"text": "not a successful transcript"},
            "errors": [{"message": "Inference failed"}],
        },
        {"success": True},
        {"success": True, "result": None},
        {"success": True, "result": {}},
        {"success": True, "result": {"text": ""}},
        {"success": True, "result": {"text": "  "}},
        {"success": True, "result": {"text": 123}},
        [],
    ],
)
async def test_bad_transcript(stt_entity, metadata, mock_speech_http, payload) -> None:
    mock_speech_http.post.return_value = httpx.Response(200, json=payload)
    result = await stt_entity.async_process_audio_stream(metadata, audio_stream(PCM_AUDIO))
    assert result == stt.SpeechResult(None, stt.SpeechResultState.ERROR)


async def test_non_json_transcript(stt_entity, metadata, mock_speech_http) -> None:
    mock_speech_http.post.return_value = httpx.Response(200, text="Bad upstream response")
    result = await stt_entity.async_process_audio_stream(metadata, audio_stream(PCM_AUDIO))
    assert result.result == stt.SpeechResultState.ERROR


async def test_tts_single_request(tts_entity, mock_speech_http) -> None:
    mock_speech_http.post.return_value = httpx.Response(200, content=MP3_AUDIO, headers={"content-type": "audio/mpeg"})
    assert await tts_entity.async_get_tts_audio("Hello there", "en", {}) == ("mp3", MP3_AUDIO)
    mock_speech_http.post.assert_awaited_once_with(
        f"https://api.cloudflare.com/client/v4/accounts/test-account-id/ai/run/{TTS_MODEL}",
        headers={"Authorization": "Bearer test-cf-token", "cf-aig-gateway-id": "test-gateway"},
        json={"text": "Hello there", "speaker": "draco", "encoding": "mp3"},
        timeout=SPEECH_TIMEOUT,
    )
    mock_speech_http.get.assert_not_called()


@pytest.mark.parametrize(
    ("message", "language", "options", "error"),
    [
        ("", "en", {}, "empty"),
        ("  ", "en", {}, "empty"),
        ("a" * 4097, "en", {}, "4096"),
        ("Hello", "fr", {}, "English"),
        ("Hello", "en", {"voice": "other"}, "overrides"),
    ],
)
async def test_tts_invalid_request(tts_entity, mock_speech_http, message, language, options, error) -> None:
    with pytest.raises(HomeAssistantError, match=error):
        await tts_entity.async_get_tts_audio(message, language, options)
    mock_speech_http.post.assert_not_called()


async def test_tts_exact_limit(tts_entity, mock_speech_http) -> None:
    mock_speech_http.post.return_value = httpx.Response(200, content=MP3_AUDIO, headers={"content-type": "audio/mpeg"})
    assert await tts_entity.async_get_tts_audio("a" * 4096, "en", {}) == ("mp3", MP3_AUDIO)


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"", "audio/mpeg"),
        (b'{"success":false}', "application/json"),
        (b'{"success":false}', "audio/mpeg"),
        (b"<html>error</html>", "text/html"),
        (MP3_AUDIO, "application/octet-stream"),
        (b"not mp3", "audio/mpeg"),
    ],
)
async def test_tts_rejects_non_audio(tts_entity, mock_speech_http, content, content_type) -> None:
    mock_speech_http.post.return_value = httpx.Response(200, content=content, headers={"content-type": content_type})
    with pytest.raises(HomeAssistantError, match="empty or non-MP3"):
        await tts_entity.async_get_tts_audio("Hello", "en", {})
    mock_speech_http.post.assert_awaited_once()


@pytest.mark.parametrize("platform", ["stt", "tts"])
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
async def test_http_errors(
    hass, mock_config_entry_with_speech, stt_entity, tts_entity, metadata, mock_speech_http, platform, status
) -> None:
    mock_speech_http.post.return_value = httpx.Response(
        status, json={"errors": [{"message": "Cloudflare error detail"}]}
    )
    request = (
        stt_entity.async_process_audio_stream(metadata, audio_stream(PCM_AUDIO))
        if platform == "stt"
        else tts_entity.async_get_tts_audio("Hello", "en", {})
    )
    with patch.object(mock_config_entry_with_speech, "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match=f"HTTP {status}.*Cloudflare error detail"):
            await request
        if status == 401:
            reauth.assert_called_once_with(hass)
        else:
            reauth.assert_not_called()


@pytest.mark.parametrize("platform", ["stt", "tts"])
@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (httpx.ReadTimeout("private details"), "timed out"),
        (httpx.ConnectError("private details"), "Cannot connect"),
        (httpx.RemoteProtocolError("private details"), "Cannot connect"),
    ],
)
async def test_network_errors(stt_entity, tts_entity, metadata, mock_speech_http, platform, exception, error) -> None:
    mock_speech_http.post.side_effect = exception
    request = (
        stt_entity.async_process_audio_stream(metadata, audio_stream(PCM_AUDIO))
        if platform == "stt"
        else tts_entity.async_get_tts_audio("Hello", "en", {})
    )
    with pytest.raises(HomeAssistantError, match=error):
        await request


@pytest.mark.parametrize("locale", ["en-US", "en-GB"])
async def test_english_assist_pipeline(hass, metadata, mock_speech_http, locale) -> None:
    """Exercise HA's locale resolution and actual PCM-to-STT pipeline path."""
    # Native VAD/noise processors are unused here (VAD is disabled), and aren't
    # integration requirements. Keep the actual pipeline and language matching.
    with patch.dict(sys.modules, {"pymicro_vad": Mock(), "pyspeex_noise": Mock()}):
        from homeassistant.components.assist_pipeline.audio_enhancer import EnhancedAudioChunk  # noqa: PLC0415
        from homeassistant.components.assist_pipeline.pipeline import (  # noqa: PLC0415
            AudioSettings,
            PipelineRun,
            PipelineStage,
            async_create_default_pipeline,
        )

        assert await async_setup_component(hass, "assist_pipeline", {})
    hass.config.language = locale
    pipeline = await async_create_default_pipeline(hass, "stt.test_whisper", "tts.test_draco", "Cloudflare English")
    assert pipeline is not None
    assert pipeline.stt_language == pipeline.tts_language == "en"
    assert pipeline.tts_voice is None
    run = PipelineRun(
        hass=hass,
        context=Context(),
        pipeline=pipeline,
        start_stage=PipelineStage.STT,
        end_stage=PipelineStage.STT,
        event_callback=Mock(),
        audio_settings=AudioSettings(is_vad_enabled=False),
    )
    await run.prepare_speech_to_text(metadata)

    async def enhanced_stream():
        yield EnhancedAudioChunk(audio=PCM_AUDIO, timestamp_ms=0, speech_probability=None)

    assert await run.speech_to_text(metadata, enhanced_stream()) == "Turn on the light"
    uploaded = base64.b64decode(mock_speech_http.post.call_args.kwargs["json"]["audio"])
    assert uploaded == make_wav()
