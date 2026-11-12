"""English text to speech using Cloudflare Workers AI Aura-2."""

from typing import Any

from homeassistant.components import tts
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CloudflareAIGatewayConfigEntry
from .const import DOMAIN, LOGGER, SUBENTRY_TYPE_TTS, TTS_MODEL, TTS_SPEAKER
from .speech import async_run_speech_model, cloudflare_error_detail

MAX_MESSAGE_LENGTH = 4096


async def async_setup_entry(
    hass: HomeAssistant, entry: CloudflareAIGatewayConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up one speech entity per TTS subentry."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_TTS:
            async_add_entities([CloudflareTextToSpeechEntity(entry, subentry)], config_subentry_id=subentry.subentry_id)


class CloudflareTextToSpeechEntity(tts.TextToSpeechEntity):
    """Fixed English Aura-2 engine with the Draco voice."""

    _attr_has_entity_name = False
    _attr_default_language = "en"
    _attr_supported_languages = ["en"]

    def __init__(self, entry: CloudflareAIGatewayConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the speech service device."""
        self.entry = entry
        # HA's TTS manager requires engine.name, not just a device-derived
        # friendly name. Use the full subentry title without a device prefix.
        self._attr_name = subentry.title
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Cloudflare",
            model=f"workers-ai/{TTS_MODEL}",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    async def async_get_tts_audio(self, message: str, language: str, options: dict[str, Any]) -> tuple[str, bytes]:
        """Get MP3 bytes directly from a single model request."""
        if not message.strip():
            raise HomeAssistantError("Speech text must not be empty")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise HomeAssistantError(f"Speech text exceeds the {MAX_MESSAGE_LENGTH}-character integration limit")
        if language != "en":
            raise HomeAssistantError("Cloudflare speech supports English only")
        if options:
            raise HomeAssistantError("Cloudflare speech does not support voice or other option overrides")
        response = await async_run_speech_model(
            self.hass, self.entry, TTS_MODEL, {"text": message, "speaker": TTS_SPEAKER, "encoding": "mp3"}
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        LOGGER.debug("Cloudflare TTS response: HTTP %s, content type %s", response.status_code, content_type)
        # Require the model's documented media type and an MP3 ID3/frame header.
        audio = response.content
        is_mp3 = audio.startswith(b"ID3") or (len(audio) >= 2 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0)
        if content_type not in ("audio/mpeg", "audio/mp3") or not is_mp3:
            detail = cloudflare_error_detail(response)
            raise HomeAssistantError(
                f"Cloudflare returned empty or non-MP3 speech audio{': ' + detail if detail else ''}"
            )
        return "mp3", audio
