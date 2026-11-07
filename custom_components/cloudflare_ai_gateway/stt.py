"""English speech recognition using Cloudflare Workers AI Whisper."""

import base64
from collections.abc import AsyncIterable
from io import BytesIO
import wave

from homeassistant.components import stt
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CloudflareAIGatewayConfigEntry
from .const import DOMAIN, LOGGER, STT_MODEL, SUBENTRY_TYPE_STT
from .speech import async_run_speech_model, cloudflare_error_detail

MAX_PCM_BYTES = 30 * 16000 * 2
WAV_HEADER_BYTES = 44
MAX_WAV_BYTES = MAX_PCM_BYTES + WAV_HEADER_BYTES


async def async_setup_entry(
    hass: HomeAssistant, entry: CloudflareAIGatewayConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up one speech entity per STT subentry."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_STT:
            async_add_entities([CloudflareSpeechToTextEntity(entry, subentry)], config_subentry_id=subentry.subentry_id)


def _prepare_wav(audio: bytes) -> bytes | None:
    """Validate a WAV or wrap HA's raw PCM stream without resampling."""
    if audio.startswith(b"RIFF"):
        try:
            with wave.open(BytesIO(audio), "rb") as wav:
                if (
                    wav.getnchannels() != 1
                    or wav.getsampwidth() != 2
                    or wav.getframerate() != 16000
                    or wav.getcomptype() != "NONE"
                    or not 0 < wav.getnframes() <= MAX_PCM_BYTES // 2
                ):
                    return None
                if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
                    return None
        except (wave.Error, EOFError):
            return None
        return audio
    if not audio or len(audio) > MAX_PCM_BYTES or len(audio) % 2:
        return None
    with BytesIO() as output:
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(audio)
        return output.getvalue()


class CloudflareSpeechToTextEntity(stt.SpeechToTextEntity):
    """Fixed English Whisper engine."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: CloudflareAIGatewayConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the speech service device."""
        self.entry = entry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Cloudflare",
            model=f"workers-ai/{STT_MODEL}",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str]:
        """Return the supported language."""
        return ["en"]

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        """Return the supported container."""
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        """Return the supported codec."""
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        """Return the supported sample width."""
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        """Return the supported sample rate."""
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        """Return the supported channel count."""
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        """Buffer at most 30 seconds of audio and transcribe it in one request."""
        error = stt.SpeechResult(None, stt.SpeechResultState.ERROR)
        if not self.check_metadata(metadata):
            return error
        audio = bytearray()
        async for chunk in stream:
            if len(audio) + len(chunk) > MAX_WAV_BYTES:
                LOGGER.warning("Speech input exceeds the 30-second integration limit")
                return error
            audio.extend(chunk)
        wav_audio = _prepare_wav(bytes(audio))
        if wav_audio is None:
            return error
        response = await async_run_speech_model(
            self.hass,
            self.entry,
            STT_MODEL,
            {"audio": base64.b64encode(wav_audio).decode("ascii"), "language": "en", "task": "transcribe"},
        )
        try:
            payload = response.json()
        except ValueError:
            return error
        if not isinstance(payload, dict) or payload.get("success") is not True:
            detail = cloudflare_error_detail(response)
            LOGGER.warning("Cloudflare transcription failed%s", f": {detail}" if detail else "")
            return error
        result = payload.get("result")
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str) or not text.strip():
            return error
        return stt.SpeechResult(text.strip(), stt.SpeechResultState.SUCCESS)
