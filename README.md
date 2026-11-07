# Cloudflare AI Gateway for Home Assistant

[![Validate](https://github.com/michaeldwan/ha-cloudflare-ai-gateway/actions/workflows/validate.yml/badge.svg)](https://github.com/michaeldwan/ha-cloudflare-ai-gateway/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

Connect your Home Assistant to hundreds of AI models from every major provider using [Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/). Out of the box support for 50+ high-performance, cost-effective ([or free!](https://developers.cloudflare.com/workers-ai/platform/pricing/)) open source models running on [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/).

<img src="https://raw.githubusercontent.com/michaeldwan/ha-cloudflare-ai-gateway/main/images/demo.png" alt="Conversation UI demo" width="500">

## What it does

- **Mix and match models** -- add a fast Llama for everyday conversation, a reasoning model for complex automations, an image model for generation. Each becomes its own HA entity.
- **LLM models** become conversation agents (with tool calling) and AI task entities for data extraction and summarization
- **Image models** become AI task entities for on-demand image generation (Flux, Stable Diffusion, etc.)
- **English voice pipelines** -- transcribe with Whisper Large v3 Turbo and speak with Aura-2's Draco voice through Workers AI
- **Streaming** -- real-time token delivery in the conversation UI
- **Swap providers without reconfiguring** -- switch from Llama to Sonnet to Kimi by adding a new model. Your gateway config, system prompts, and automations stay put. You can also route to different models on the gateway itself.
- **Usage tracking** -- chat and image models get diagnostic sensors for requests, input/output tokens, cache hits, and errors. Pair with HA's [utility meter](https://www.home-assistant.io/integrations/utility_meter/) for daily/monthly breakdowns
- **Caching** -- per-model TTL to skip redundant API calls
- **Spend limits, rate limiting, and guardrails** -- set daily/weekly/monthly cost caps, request rate limits, and content safety rules in your [AI Gateway dashboard](https://developers.cloudflare.com/ai-gateway/features/). Configured in Cloudflare, enforced before requests hit providers.
- **Analytics and logging** -- every request is logged with status, latency, and token counts in the gateway dashboard, with the option for zero log retention if you prefer

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=michaeldwan&repository=ha-cloudflare-ai-gateway&category=integration)

Or manually: open HACS, click the three-dot menu > **Custom repositories**, add `michaeldwan/ha-cloudflare-ai-gateway` as an **Integration**, search for "Cloudflare AI Gateway" and install it, then restart Home Assistant.

### Manual

Copy `custom_components/cloudflare_ai_gateway/` to your Home Assistant `config/custom_components/` directory and restart.

## Cloudflare setup

You need a free Cloudflare account and three things from the dashboard: your Account ID, a gateway, and an API token. If you already have a Cloudflare account and an AI Gateway, skip to [step 3](#3-create-an-api-token).

### 1. Get your Account ID

Sign up or log in at [dash.cloudflare.com](https://dash.cloudflare.com/). You can find your Account ID by clicking on "Quick search" in the sidebar or the "cmd+k" hotkeys, then select "Copy account ID". It's also in every dashboard URL: `dash.cloudflare.com/<account-id>/...`.

### 2. Get your AI Gateway ID

Either [create a new gateway](https://dash.cloudflare.com/?to=/:account/ai/ai-gateway/create-gateway) or [choose from your existing ones](https://dash.cloudflare.com/?to=/:account/ai/ai-gateway). The ID of the gateway is what you'll enter in HA to connect to it.

There's no cost to creating multiple gateways, so I recommend a new one dedicated to Home Assistant for isolation and cost control, but you can reuse an existing gateway if you prefer.

### 3. Create an API token

On your gateway page, click the "Create token" button on the right sidebar. The default permissions are:

<img src="https://raw.githubusercontent.com/michaeldwan/ha-cloudflare-ai-gateway/main/images/ai-gateway-token.png" alt="AI Gateway token permissions" width="200">

> [!NOTE]
> To enable cost tracking, add **Account > Account Analytics > Read** to the token (see [Usage sensors](#cost-tracking)).

This is the token you'll use to authenticate the Home Assistant integration to this gateway.

For Workers AI models, this is the only credential you need.

For speech engines, also add **Account > Workers AI > Read** to this token, keeping its existing AI Gateway permissions. No provider key is needed for Whisper or Aura-2. If you use prepaid credits, enable **Workers AI Billing: Unified billing** in your gateway settings and load credits. See [Unified Billing](https://developers.cloudflare.com/ai-gateway/features/unified-billing/).

If you're using third-party providers (OpenAI, Anthropic, Google, etc.) and don't want consolidated billing, you'll need to add API keys in the "Provider Keys" section of your gateway settings.

### 4. Add the integration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cloudflare_ai_gateway)

Or manually: **Settings > Devices & Services > Add Integration > Cloudflare AI Gateway**. 

Enter your Account ID, gateway name, and API token.

<img src="https://raw.githubusercontent.com/michaeldwan/ha-cloudflare-ai-gateway/main/images/config-dialog.png" alt="Config dialog" width="500">

This will create 3 Workers AI models to get you started -- a general LLM model for conversation, a more capable reasoning model for specialized tasks, and an image generation model. You can customize or delete these and add your own models in the integration config page.

<img src="https://raw.githubusercontent.com/michaeldwan/ha-cloudflare-ai-gateway/main/images/integration-page.png" alt="Integration page with models" width="700">

## Adding a model

From the integration page, add a subentry for the capability you need:

- **Conversation agent** -- handles conversation and device control. Pick a provider and model ID.
- **AI data task** -- extracts or generates structured data using a chat model.
- **AI image task** -- generates images via Workers AI.
- **Speech-to-text engine** or **Text-to-speech engine** -- adds one of the fixed English speech engines described below.

For a quick start, use `workers-ai` as the provider and one of the models below. Conversation agents have a system prompt, an [LLM API](https://www.home-assistant.io/integrations/conversation/) selection for tool calling, and optional tuning (temperature, top_p, max tokens, cache TTL).

### Setting up a conversation agent

1. Choose an **instruction-following chat model with tool/function calling and streaming Chat Completions support** through your selected gateway provider. Tool calling lets the model invoke HA's device-control tools. User-tested examples include **GLM** and **Gemma**; check the exact model variant and provider for these capabilities. The conversation model does not need audio support because STT and TTS handle speech separately.
2. In the agent's LLM API settings, enable **Device Control / Assist** (`assist`) and leave **Home Assistant admin tasks** (`homeassistant`) disabled. Device Control exposes HA tools while still allowing general questions. Admin tasks require an authenticated HA administrator; a hardware voice device does not normally carry that browser-user identity.
3. Expose the devices you want Assist to control in HA's voice-assistant settings, then select this conversation agent in your assistant/pipeline. Assign that same assistant to your hardware voice device.
4. Test both a device command (for example, “turn on the big lamp”) and a general question (for example, “Who is Stephen Graham Jones?”). Recognized device commands can run locally in HA; a general question checks the model's conversation path as well.

## English Assist voice pipeline

1. On the Cloudflare AI Gateway integration page, add a **Speech-to-text engine**, and give it a name (for example, “Cloudflare Whisper”).
2. Add a **Text-to-speech engine**, and give it a name (for example, “Cloudflare Draco”). Speech engines are added explicitly, not created during initial setup.
3. Open **Settings > Voice assistants** and add or edit an assistant. Choose **English** (English locales such as `en-US` select the engines' `en` language).
4. Select a conversation agent configured as [described above](#setting-up-a-conversation-agent), **Cloudflare Whisper** for speech-to-text, and **Cloudflare Draco** for text-to-speech. Enable **Device Control / Assist** and leave **Home Assistant admin tasks** disabled on that agent.
5. Assign this assistant to your hardware voice device. Test a device command and a general question through browser text, browser voice, and the hardware device.
6. Confirm that speech is transcribed and the reply plays, and that STT/TTS requests and model conversation requests appear in your Cloudflare gateway logs. If using prepaid billing, verify the charges there as well.

The speech models are fixed: `@cf/openai/whisper-large-v3-turbo` transcribes English, and `@cf/deepgram/aura-2-en` returns MP3 speech with speaker `draco`. There are no model, voice, speed, or pipeline voice overrides. Each short reply uses one TTS request.

STT accepts HA's 16-kHz, 16-bit mono PCM stream and prepares a WAV upload; complete WAV input with those same audio settings is also accepted. The integration caps each recording at **30 seconds** and each spoken reply at **4096 characters**. It does not resample audio or split long replies. Speech engines do not create usage sensors.

Speech requests use Cloudflare's account-scoped Workers AI REST endpoint with your gateway ID. A 401 starts token reauthentication; for 403 errors, check the Workers AI permission and billing prerequisites above. Empty transcripts, non-audio replies, timeouts, and connection errors are reported as failures. Debug logging for this integration records the TTS HTTP status and content type, without audio or response bodies.

### Live verification and TTS compatibility

Live testing has confirmed device control and general-question answers through browser text, browser voice, and a hardware Assist device. User-tested conversation models include GLM and Gemma. The working hardware configuration enables Device Control (`assist`) and disables Home Assistant admin tasks (`homeassistant`).

Initial live testing exposed `TTS engine name is not set.` before any Cloudflare request was made. HA's TTS manager requires an explicit engine name, even when the entity already has a device-derived display name. The TTS engine now uses the name chosen during setup, without duplicating the device name. A regression test exercises HA's TTS manager and verifies that the model's MP3 bytes reach the output stream.

If you installed an earlier development copy and see that error, replace `custom_components/cloudflare_ai_gateway/tts.py` with the updated file and restart Home Assistant. Existing speech subentries can be reused.

### Future speech customization

Planned follow-up work is to let each speech subentry select and customize its STT/TTS model and supported voice settings. The current release hardcodes Whisper Large v3 Turbo, Aura-2, the Draco speaker, English, and MP3 output; speech creation currently asks only for a name. Model and voice customization is not available yet.

## Usage sensors

Each chat or image model automatically gets diagnostic sensors that track usage from requests made through Home Assistant:

- **Requests** -- total API calls
- **Input tokens** -- tokens sent to the model
- **Output tokens** -- tokens received from the model
- **Cache hits** -- responses served from Cloudflare's cache
- **Errors** -- failed requests

Image models don't have tokens, so they get requests and errors only.

All sensors reset daily at midnight and HA records long-term statistics automatically, so you can view daily, weekly, or monthly breakdowns in the history UI.

### Cost tracking

If your API token has the **Account Analytics: Read** permission, a **Cost today** sensor appears on a gateway-level device. It polls Cloudflare's GraphQL analytics API every 5 minutes and resets daily at midnight. This is the same cost data shown in the AI Gateway dashboard.

To enable it, edit your API token in the Cloudflare dashboard and add **Account > Account Analytics > Read**. The integration detects this automatically on startup -- no reconfiguration needed, just restart HA.

## Recommended models

These are good starting points on Workers AI. The integration creates the first two and an image model by default. Workers AI has a [free tier](https://developers.cloudflare.com/workers-ai/platform/pricing/) (10,000 neurons/day) -- cheaper models stretch it further.

### Chat

For conversation agents that control devices, choose a model with tool/function calling and streaming Chat Completions support. GLM and Gemma have been tested by the user; the table below lists model IDs for additional starting points. Check the capabilities of the exact variant at your provider.

| Model | Model ID | Cost | Notes |
|---|---|---|---|
| GLM-4.7 Flash | `@cf/zai-org/glm-4.7-flash` | Low | Fast and cheap. Default conversation model. Good everyday option. |
| Qwen3 30B | `@cf/qwen/qwen3-30b-a3b-fp8` | Low | Cheapest input cost of any function-calling model. Reasoning capable. |
| GPT-OSS-20B | `@cf/openai/gpt-oss-20b` | Low | Solid reasoning at low cost. Default AI task model. |
| Mistral Small 3.1 | `@cf/mistralai/mistral-small-3.1-24b-instruct` | Medium | Good balance of capability and cost. |
| Kimi K2.5 | `@cf/moonshotai/kimi-k2.5` | High | Most capable reasoning model, but burns through the free tier fast. |
| Llama 3.3 70B | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | High | Strong function calling. Similar cost tier to Kimi. |

### Image

| Model | Model ID | Notes |
|---|---|---|
| Flux Schnell | `@cf/black-forest-labs/flux-1-schnell` | Fast, good quality. Default image model. |
| Stable Diffusion XL | `@cf/stabilityai/stable-diffusion-xl-base-1.0` | Solid all-rounder. |

Browse the full [Workers AI model catalog](https://developers.cloudflare.com/workers-ai/models/) for more, and check the [pricing page](https://developers.cloudflare.com/workers-ai/platform/pricing/) for neuron costs per model.

### Using third-party providers

If you have keys for OpenAI, Anthropic, Google, or [other providers](https://developers.cloudflare.com/ai-gateway/providers/), add them to your [gateway settings](https://dash.cloudflare.com/?to=/:account/ai/ai-gateway/general) in Cloudflare. Then pick the provider and model ID when adding a model in HA (e.g., `openai` / `gpt-4o`). Cloudflare injects the right API key automatically -- nothing is stored in HA.

## Troubleshooting

**"Authentication error" or 403:** Your API token doesn't have the right permissions. Create a new one from your gateway page using the "Create token" button.

**Third-party model returns an error:** The provider's API key isn't configured in your [AI Gateway settings](https://dash.cloudflare.com/?to=/:account/ai/ai-gateway/general). Add it there, not in HA.

**Model chats but won't control devices:** Choose a model supporting tool/function calling, enable **Device Control / Assist** (`assist`) in the agent's LLM API settings, and expose the target devices to Assist.

**Hardware says “Error preparing LLM API,” but browser conversations work:** If the log says `Error getting LLM API ['assist', 'homeassistant'] ... Unauthorized`, deselect **Home Assistant admin tasks** (`homeassistant`) and keep **Device Control / Assist** (`assist`) enabled. The admin API requires an authenticated administrator; hardware requests generally lack that user identity. Start a fresh conversation after saving. This is HA authorization, not a Cloudflare token error.

**Device commands work, but general questions fail with `Object of type _Unsupported is not JSON serializable`:** Newer HA versions use Probatio tool schemas. The integration must use Probatio's converter for those schemas rather than the older Voluptuous converter. Update `custom_components/cloudflare_ai_gateway/entity.py` and restart HA if using a development copy. This failure occurs before the request reaches Cloudflare; the selected conversation agent should handle general questions as well as device control.

**Debugging requests:** Your AI Gateway dashboard shows every request with status codes, latency, and token counts. Check there first for provider-side issues.

## Requirements

- Home Assistant 2025.7.0 or later
- A Cloudflare account (free tier works)

## Contributing

```bash
mise install          # Python 3.13 + uv
uv sync               # Install dependencies
mise run validate     # Lint + format + tests (run before committing)
mise run develop      # Start HA in Docker at http://localhost:8123
```

## License

MIT
