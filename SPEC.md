# Spec: STT and TTS for `ha-cloudflare-ai-gateway` (fork)

## Goal
Add HA speech-to-text and text-to-speech entities that run Workers AI models through the user's existing gateway. They use the same credentials and subentry structure as the existing models, so Assist can run fully through Cloudflare.

## Scope
**In:**
- Manifest fix.
- `stt` and `tts` platforms as subentry types.
- Config flow, strings and translations.
- Tests and README.

**Out:**
- Usage sensors. Don't add the new types to `TRACKED_SUBENTRY_TYPES`, and don't call `record_success` or `record_error`.
- Streaming STT/TTS.
- Nova-3 and MeloTTS (optional later).
- Any change to existing conversation, AI task, image or sensor behavior.
- Migrating the `/compat` chat path.

## Task 0: Manifest
`manifest.json` requires only `openai>=2.21.0`, but `entity.py` imports `voluptuous_openapi` [1]. Add `voluptuous-openapi` to `requirements`, using the version constraint from HA core's `openai_conversation` manifest. Add nothing else.

## Architecture (decided)

**Transport and auth.** Copy the image entity's approach [2].
- Use `httpx` via `homeassistant.helpers.httpx_client.get_async_client(hass)`.
- POST to `https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/workers-ai/{model}`.
- Send headers `cf-aig-authorization: Bearer <cf_api_token>` and `Authorization: Bearer <cf_api_token>`.
- Read `account_id`, `gateway_id` and `cf_api_token` from `entry.data`.
- Don't add the openai SDK, the `cloudflare` SDK, or `aiohttp`.

**Shared helper.** Add one small module, e.g. `gateway.py`, so STT and TTS don't duplicate the URL and header logic:
- `workers_ai_url(entry, model) -> str`
- `gateway_headers(entry) -> dict`
- `async_post(hass, entry, model, *, json=None, timeout) -> httpx.Response`, which raises `HomeAssistantError` on connection failures and non-2xx responses and starts reauth on 401.

Leave the existing image code alone unless refactoring it is trivial.

**Client object.** Don't add one. Entities build requests from `entry.data` at call time. `entry.runtime_data` is `CloudflareAIGatewayRuntimeData` (stats, store, analytics coordinator), not a client.

**Error handling.** Mirror `ai_task.py` [2]:
- 401: `self.entry.async_start_reauth(self.hass)`, then fail.
- 403: permission message.
- 404: model-not-found message.
- `httpx.ConnectError` and `TimeoutException`: "Cannot reach Cloudflare AI Gateway".
- Parse `errors[].message` from the response body for detail.

**Response envelope.** The native route wraps JSON as `{"result": {...}, "success": true}` [2]. Unwrap it, and accept an unwrapped body too.

**Entities.** Each gets its own small base:
- `_attr_has_entity_name = True`, `_attr_name = None`, `unique_id = subentry.subentry_id`.
- Device info as in the image entity [2]: `identifiers={(DOMAIN, subentry_id)}`, `name=subentry.title`, `manufacturer="Cloudflare"`, `model=f"workers-ai/{model}"`, `entry_type=SERVICE`.
- Don't inherit `CloudflareAIGatewayBaseLLMEntity`, which carries chat logic [1].

## Model contracts

**Whisper** (`@cf/openai/whisper-large-v3-turbo`) [5]:
- Input: `audio` (required, "one of", exact shape not shown on the page), optional `language`, `task`, `vad_filter`, `initial_prompt`.
- Output: `text`, `word_count`, `segments`, `vtt`.
- The agent must open `schema-input.json` (URL below) to confirm the `audio` type. My earlier reference code sent a base64 string in JSON, which I haven't confirmed.

**Aura-2 EN** (`@cf/deepgram/aura-2-en`) [6]:
- Input: `text`, `speaker` (default `luna`), `encoding` (`linear16`, `flac`, `mulaw`, `alaw`, `mp3`, `opus`, `aac`), optional `container`, `sample_rate`, `bit_rate`.
- Valid speakers: amalthea, andromeda, apollo, arcas, aries, asteria, athena, atlas, aurora, callista, cora, cordelia, delia, draco, electra, harmonia, helena, hera, hermes, hyperion, iris, janus, juno, jupiter, luna, mars, minerva, neptune, odysseus, ophelia, orion, orpheus, pandora, phoebe, pluto, saturn, thalia, theia, vesta, zeus.
- Request `encoding: "mp3"`. The response is raw audio; the page says the binding returns MPEG [6].
- Sniff the actual bytes for the extension (ID3 or 0xFF sync means mp3, RIFF means wav, OggS means ogg, fLaC means flac). If the response isn't `audio/*`, try JSON with a base64 `audio` field.

## Implementation tasks

**`const.py`**
- Add `SUBENTRY_TYPE_STT = "stt"` and `SUBENTRY_TYPE_TTS = "tts"`.
- Add `CONF_STT_MODEL`, `CONF_TTS_MODEL`, `CONF_VOICE`.
- Defaults: Whisper and Aura-2 EN above, voice `luna`.
- Add `SUPPORTED_STT_MODELS`, `SUPPORTED_TTS_MODELS` and `AURA2_VOICES`.

**`config_flow.py`**
- Add `CloudflareSTTSubentryFlowHandler` and `CloudflareTTSSubentryFlowHandler`, subclassing `CloudflareBaseSubentryFlowHandler`.
- Model them on `CloudflareImageSubentryFlowHandler`: name field when new, a `SelectSelector` dropdown with `custom_value=True`, the `entry_not_loaded` abort, and `_validate_workers_ai_model` returning `model_not_found`.
- The TTS flow adds a voice selector.
- Register both in `async_get_supported_subentry_types`.
- Don't add them to the default subentries created at setup.

**`__init__.py`**
- Extend `PLATFORMS` with `Platform.STT` and `Platform.TTS`.
- The startup model-validation loop skips unrecognized subentry types, so leave it alone.

**`stt.py`**
- Subclass `SpeechToTextEntity`, one entity per STT subentry (`config_subentry_id=...`).
- Declare supported languages (Whisper's list), formats (WAV), codecs (PCM), bit rate 16, sample rate 16000, and mono.
- In `async_process_audio_stream`:
  - Buffer the stream.
  - Wrap raw PCM in a WAV header if there's no `RIFF` prefix.
  - Send the audio in the shape the model schema requires, with the base language code.
  - Return `SpeechResult(text, SUCCESS)`. Return `ERROR` on empty audio, empty text or failure.

**`tts.py`**
- Subclass `TextToSpeechEntity`.
- Declare `default_language`, `supported_languages` (`["en"]` for aura-2-en), `supported_options` (`["voice"]`), `default_options`, and `async_get_supported_voices`.
- `async_get_tts_audio` returns `(extension, bytes)`. Check the HA base class for the expected failure convention.

**`strings.json` and `translations/en.json`**
- Add `config_subentries.stt` and `config_subentries.tts` blocks mirroring `ai_task_image` [3]: `abort.entry_not_loaded`, `abort.reconfigure_successful`, `error.model_not_found`, `entry_type`, `initiate_flow.user` and `.reconfigure`, and `step.user.data` and `data_description`.
- Match key structure exactly so hassfest passes.
- I haven't seen `translations/en.json`. Check whether it's generated from `strings.json` or maintained by hand.

**README**
- Add STT and TTS under "What it does" and "Adding a model".
- Add an example pipeline: Cloudflare STT, a gateway chat model, Cloudflare TTS.

## Tests
I haven't read `tests/`, so the agent should mirror its fixtures and harness. Cover:
- STT success, empty audio, and WAV wrapping.
- TTS audio-bytes and base64-JSON responses, plus format sniffing.
- 401 triggering reauth.
- Subentry create and reconfigure flows, including `model_not_found`.

Mock at the HTTP transport layer. Run `mise run validate`.

## Acceptance criteria
1. Clean install loads with no missing-module errors.
2. Adding an STT and a TTS model creates `stt.*` and `tts.*` entities.
3. Both appear as engines in Settings → Voice assistants.
4. Gateway logs show Whisper and Aura-2 requests.
5. No change to existing entities.
6. No new `requirements` beyond Task 0.

## Open items for the agent to verify
1. Whether the native `gateway.ai.cloudflare.com/.../workers-ai/{model}` route works for the user's gateway with both headers. The AI Gateway Workers AI page doesn't document it [4]. If it fails, check the `api.cloudflare.com` route with `cf-aig-gateway-id` shown there, and report which one works.
2. The `audio` input type in Whisper's `schema-input.json`.
3. The failure convention in HA's `tts` base class.
4. How `translations/en.json` is generated.
5. The `tests/` conventions.

## References

**Cloudflare** (the first four I fetched; the rest are links from those pages' nav)
- AI Gateway, Workers AI: https://developers.cloudflare.com/ai-gateway/usage/providers/workersai/
- Whisper large-v3-turbo: https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/
- Whisper input schema: https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/schema-input.json
- Aura-2 EN: https://developers.cloudflare.com/workers-ai/models/aura-2-en/
- Aura-2 schemas: https://developers.cloudflare.com/workers-ai/models/aura-2-en/schema-input.json and `schema-output.json`
- Authenticated gateway: https://developers.cloudflare.com/ai-gateway/configuration/authentication/
- AI Gateway REST API: https://developers.cloudflare.com/ai-gateway/usage/rest-api/
- Workers AI OpenAI-compatible endpoints: https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
- Model catalog: https://developers.cloudflare.com/workers-ai/models/
- Machine-readable docs: https://developers.cloudflare.com/ai-gateway/llms-full.txt and https://developers.cloudflare.com/workers-ai/llms-full.txt

**Home Assistant** (from memory, not fetched, so have the agent confirm they resolve)
- Manifest: https://developers.home-assistant.io/docs/creating_integration_manifest/
- Config flow and subentries: https://developers.home-assistant.io/docs/config_entries_config_flow_handler/
- Entities: https://developers.home-assistant.io/docs/core/entity/
- STT base class: https://github.com/home-assistant/core/blob/dev/homeassistant/components/stt/__init__.py
- TTS base class: https://github.com/home-assistant/core/blob/dev/homeassistant/components/tts/__init__.py
- Reference integration: https://github.com/home-assistant/core/tree/dev/homeassistant/components/openai_conversation. I believe it has STT/TTS with subentries, but I haven't confirmed.
- Quality scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/

**Repos**
- Fork base (MIT): https://github.com/michaeldwan/ha-cloudflare-ai-gateway
- Reference for STT/TTS logic (Apache-2.0, keep the notice on ported code;
