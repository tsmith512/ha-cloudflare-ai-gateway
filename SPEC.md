# Spec: English Assist speech via Cloudflare AI Gateway

## Goal

Add speech-to-text (STT) and text-to-speech (TTS) engines to the existing integration so an English Assist pipeline can use Cloudflare for STT, an existing conversation agent, and TTS. Each engine is a separate config subentry and entity. Target the current Home Assistant release; publish through the repository's existing GitHub/HACS release workflow.

## Fixed v1 choices

| Engine | Workers AI model | Request settings | Response |
| --- | --- | --- | --- |
| STT | `@cf/openai/whisper-large-v3-turbo` | `language: "en"`, `task: "transcribe"` | JSON transcript |
| TTS | `@cf/deepgram/aura-2-en` | `speaker: "draco"`, `encoding: "mp3"` | MP3 bytes |

English only. Model, voice and format are fixed; subentry creation asks only for a name. No model/voice/speed selectors or Assist pipeline voice overrides. No streaming, resampling, usage sensors for these subentries, new pip requirements, or changes to existing conversation, AI task, image and sensor behavior.

## Gateway contract (verified on the user's gateway)

Use HA's shared `httpx` client (`homeassistant.helpers.httpx_client.get_async_client(hass)`) to POST to:

`https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}`

Read `account_id`, `gateway_id` and `cf_api_token` from the parent config entry. Send `Authorization: Bearer <cf_api_token>`, `cf-aig-gateway-id: <gateway_id>`, and JSON model input directly in the body (no `{model, input}` wrapper). The user will update the existing token with **Account > Workers AI > Read** permission; retain its existing AI Gateway permissions. Do not add permission probes, alternative authentication, SDKs or a new client in `entry.runtime_data`. For prepaid credits, the user configures **Workers AI Billing: Unified billing** on the gateway and loads credits. No provider key is needed for these Workers AI models. Leave the existing `/compat` chat and image paths alone.

- **STT request:** `{"audio": "<base64-encoded WAV bytes>", "language": "en", "task": "transcribe"}`. The model's input schema explicitly supports base64 audio. A live request returned **HTTP 200**, `application/json`; top-level keys `errors`, `messages`, `result`, `success`; and `result` keys `text`, `segments`, `transcription_info`, `usage`, `vtt`, `word_count`. Parse `result.text` on success; reject missing/empty text or an unsuccessful envelope.
- **TTS request:** `{"text": "<message>", "speaker": "draco", "encoding": "mp3"}`. The user verified that this route returns directly playable speech in the chosen voice with **one request**. The model output schema specifies binary `audio/mpeg`; return `("mp3", response.content)` for nonempty audio. Do not make a follow-up download or assume a JSON/base64 response. The live test did not report the exact HTTP status or content type: inspect these during implementation and reject error/JSON payloads rather than returning them as audio.

Share URL/header and HTTP error translation in a small helper if that avoids duplication; keep response handling model-specific. Use the integration's existing 401 reauth pattern for invalid tokens and clear errors for 403, 404, timeouts and connection failures. Inspect Cloudflare JSON `errors[].message` for details when present; never log the token, input audio or entire response bodies. Treat HTTP success with `success: false` as an STT failure.

## Home Assistant integration

- Conversation agents used in the pipeline need an instruction-following chat model with tool/function calling and streaming Chat Completions support through the gateway provider. User-tested examples include GLM and Gemma. Enable Device Control / Assist (`assist`) and leave Home Assistant admin tasks (`homeassistant`) disabled for hardware voice use; the admin API requires an authenticated administrator.
- `const.py`: add STT/TTS subentry type constants, fixed model IDs and Draco speaker. Do **not** add these types to `TRACKED_SUBENTRY_TYPES`.
- `config_flow.py`: register STT and TTS subentry flows in `async_get_supported_subentry_types`. Use the existing `entry_not_loaded` check and name-only creation. No configurable model parameters to reconfigure; handle a reconfigure attempt with an explicit abort and matching translation rather than showing an empty form. Do not create default speech subentries or run model-catalog validation for fixed IDs.
- `__init__.py` / `manifest.json`: add `Platform.STT` and `Platform.TTS` to `PLATFORMS`, and `stt` and `tts` to manifest `dependencies`. Existing startup validation can continue to skip these types; existing tracked statistics stay unchanged.
- `stt.py`: create one `stt.SpeechToTextEntity` per STT subentry via `async_add_entities(..., config_subentry_id=subentry.subentry_id)`. Advertise `supported_languages = ["en"]`, WAV/PCM, 16-bit, 16 kHz, mono using HA's `stt.Audio*` enums. HA's English pipeline language matching selects `en` for locales such as `en-US`; verify this in entity/pipeline tests on the target HA version. Buffer incoming audio, reject empty/invalid input, and base64-encode once. Wrap HA's headerless PCM stream in WAV for Cloudflare; also accept valid complete WAV input. Do not resample audio in v1. Return `stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)` for nonempty transcript or `stt.SpeechResult(None, stt.SpeechResultState.ERROR)` on unusable audio/transcript; surface HTTP errors appropriately and trigger reauth on 401.
- `tts.py`: create one `tts.TextToSpeechEntity` per TTS subentry with `_attr_default_language = "en"`, `_attr_supported_languages = ["en"]` and `async_get_tts_audio(message, language, options)` returning `("mp3", bytes)`. Do not expose `supported_options` or implement a streaming override. Reject empty text and empty/non-audio responses; surface request failures as `HomeAssistantError`.
- Both entities: unique ID `subentry.subentry_id`; service device info with identifiers `{(DOMAIN, subentry.subentry_id)}`, title, manufacturer `Cloudflare`, and `model=f"workers-ai/{model}"`. STT uses `_attr_has_entity_name = True` and `_attr_name = None`. TTS must expose a nonempty engine name for HA's TTS manager: use `_attr_has_entity_name = False` and `_attr_name = subentry.title` to avoid duplicating the device title. Do not inherit `CloudflareAIGatewayBaseLLMEntity` or call the usage-sensor recording helpers.
- Add matching `config_subentries.stt` and `.tts` strings in `strings.json` and `translations/en.json` (both committed; currently identical). Update README with token/billing prerequisites, adding each speech engine and setting up an English Assist pipeline.

## Bounds and verification

- **STT:** Cap buffered 16-kHz/16-bit/mono WAV input at **30 seconds** (960,000 PCM bytes plus a WAV header). Enforce while reading, before base64 encoding; return an STT error for oversize audio. This bounds memory/upload and is an integration limit, not a claimed model limit.
- **TTS:** Handle one short response in one request; cap message length at **4096 characters** for this release and fail clearly beyond it. No splitting or second audio fetch. Use finite HTTP timeouts for both models.
- Follow `tests/components/cloudflare_ai_gateway/conftest.py` (`MockConfigEntry`, `ConfigSubentryData`, `_mock_init`). Test subentry creation/abort, entity registration, exact gateway headers/JSON bodies, STT transcript and bad/empty/oversize responses, TTS Draco MP3 response without a second request, 401 reauth, HTTP/network failures, and no new usage sensors or regressions in existing platforms. Mock HTTP responses with realistic headers and body types.
- Run `mise run validate` for implementation; CI runs hassfest and HACS validation separately. On the remote HA installation, verify both entities appear in Settings → Voice assistants, an English Assist pipeline transcribes and plays a response, gateway logs show both requests, and prepaid billing is applied if enabled. Inspect the live TTS status/content type while implementing; the playable-audio behavior is already verified.
- To publish, the repository's `mise run release` script validates, stamps the manifest version, commits, tags, and pushes to `origin main`; the tag triggers `.github/workflows/release.yml`, which attaches `cloudflare_ai_gateway.zip` to a GitHub release. Run it only when release/push is explicitly requested.

## Implementation and live verification

- HA's Assist pipeline supplies headerless PCM despite advertising WAV/PCM metadata. Following clarification of the goal, STT wraps that 16-kHz, 16-bit mono audio in WAV before uploading it to Cloudflare. Complete WAV input is also validated; no resampling is performed.
- Initial live TTS calls failed inside HA with `TTS engine name is not set.` because the original device-derived display name did not populate `engine.name`. TTS now exposes the subentry title as its explicit engine name, without a device-name prefix. The regression test uses HA's TTS manager and output stream rather than calling only the integration entity directly.
- The user confirmed live TTS playback after the naming fix and successful device-state changes from a hardware Assist device using specific English instructions, for example “turn on the big lamp.” This supersedes the initial implementation commit's note that live testing was pending.
- Specific device instructions can be handled locally by HA and do not alone establish that the gateway conversation path works. Subsequent testing of a general question exposed a schema-engine mismatch on newer HA: Probatio's `_Unsupported` sentinel leaked through the older Voluptuous converter into `tools[].function.parameters`. Tool and structured-output conversion now select the converter matching the schema engine; a regression test serializes a general question with Probatio tools through the real OpenAI SDK and a mocked HTTP response.
- After the schema fix, the user confirmed device control and general-question responses in browser text and browser voice. Hardware requests initially failed with `Error getting LLM API ['assist', 'homeassistant'] ... Unauthorized`: selecting the administrator-only API made the combined API require an administrator identity absent from the hardware request. Keeping Device Control / Assist enabled and disabling Home Assistant admin tasks resolved the issue. The user reports the hardware pipeline is now working well, with GLM and Gemma as tested conversation-model examples.
- Exact live TTS HTTP status/content type, gateway-log inspection, and prepaid billing verification have not been reported. The implementation logs TTS status/content type at debug level to support those checks.

## Future state

Allow speech subentries to select and customize STT/TTS models and supported voice settings. These are currently hardcoded to Whisper Large v3 Turbo for transcription and Aura-2 with Draco/MP3 for speech generation. The name-only, fixed-English behavior remains the implemented v1 contract; configurable speech models and voices are follow-up work.

## References

- Cloudflare REST API (including model-in-path billing and required token permission): https://developers.cloudflare.com/ai-gateway/usage/rest-api/
- Whisper model/input schema: https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/schema-input.json
- Aura-2 model/input/output schemas: https://developers.cloudflare.com/workers-ai/models/aura-2-en/
- Unified Billing setup: https://developers.cloudflare.com/ai-gateway/features/unified-billing/
- HA STT/TTS entity bases: https://github.com/home-assistant/core/blob/dev/homeassistant/components/stt/entity.py and https://github.com/home-assistant/core/blob/dev/homeassistant/components/tts/entity.py
