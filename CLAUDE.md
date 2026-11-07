# CLAUDE.md

## Project

Home Assistant custom integration for Cloudflare AI Gateway. Routes conversation agents through Cloudflare's unified AI gateway endpoint, supporting 15+ providers with a single config.

## Dev Setup

- `mise install` — install Python 3.13 and uv
- `uv sync` — install dependencies
- `mise run validate` — **run this before every commit** (lint + format + tests)

## Commands

- `mise run validate` — lint, format check, and tests (same as CI)
- `mise run test` — run tests only
- `mise run lint` — ruff check + format check
- `mise run format` — auto-format code
- `mise run develop` — start HA in Docker with integration loaded (http://localhost:8123)

## Architecture

- **Parent config entry** = Cloudflare gateway (account ID, gateway ID, CF API token)
- **Subentries** = conversation agents, AI data/image tasks, or fixed English STT/TTS engines
- **Universal/BYOK auth** — CF token in Authorization header, Cloudflare injects stored provider keys
- Base LLM logic in `entity.py`, platform-specific code in `conversation.py`
- Speech uses Workers AI's account-scoped `/ai/run/{model}` endpoint with the parent token and `cf-aig-gateway-id`; requests and HTTP error translation are shared in `speech.py`
- STT buffers HA's raw PCM and wraps it in WAV (30-second limit); TTS returns Aura-2 Draco MP3 bytes (4096-character limit). Neither creates usage sensors
- TTS must set an explicit engine name from the subentry title: HA's TTS manager rejects a device-derived display name alone. Test through the HA TTS manager as well as the entity

## Key Files

- `custom_components/cloudflare_ai_gateway/entity.py` — base entity with Chat Completions streaming + tool calling
- `custom_components/cloudflare_ai_gateway/conversation.py` — conversation platform
- `custom_components/cloudflare_ai_gateway/speech.py` — shared Workers AI speech requests and errors
- `custom_components/cloudflare_ai_gateway/stt.py` — English Whisper transcription and PCM-to-WAV preparation
- `custom_components/cloudflare_ai_gateway/tts.py` — English Aura-2 Draco speech generation
- `custom_components/cloudflare_ai_gateway/config_flow.py` — setup flow + subentry flow
- `tests/components/cloudflare_ai_gateway/conftest.py` — test fixtures
- `tests/components/cloudflare_ai_gateway/test_speech.py` — speech contracts, HA TTS manager regression, and English Assist pipeline tests
