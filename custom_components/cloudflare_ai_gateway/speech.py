"""Small Workers AI request helper shared by the speech platforms."""

from typing import Any

import httpx

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.httpx_client import get_async_client

from . import CloudflareAIGatewayConfigEntry
from .const import CONF_ACCOUNT_ID, CONF_CF_API_TOKEN, CONF_GATEWAY_ID

SPEECH_TIMEOUT = 60.0


def cloudflare_error_detail(response: httpx.Response) -> str:
    """Extract only Cloudflare error messages, never the entire response body."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict) or not isinstance(payload.get("errors"), list):
        return ""
    return "; ".join(
        error["message"]
        for error in payload["errors"]
        if isinstance(error, dict) and isinstance(error.get("message"), str)
    )


async def async_run_speech_model(
    hass: HomeAssistant, entry: CloudflareAIGatewayConfigEntry, model: str, payload: dict[str, Any]
) -> httpx.Response:
    """Call Workers AI through the gateway and translate request failures."""
    data = entry.data
    try:
        response = await get_async_client(hass).post(
            f"https://api.cloudflare.com/client/v4/accounts/{data[CONF_ACCOUNT_ID]}/ai/run/{model}",
            headers={
                "Authorization": f"Bearer {data[CONF_CF_API_TOKEN]}",
                "cf-aig-gateway-id": data[CONF_GATEWAY_ID],
            },
            json=payload,
            timeout=SPEECH_TIMEOUT,
        )
    except httpx.TimeoutException as err:
        raise HomeAssistantError("Cloudflare speech request timed out") from err
    except httpx.RequestError as err:
        raise HomeAssistantError("Cannot connect to Cloudflare speech service") from err

    if response.is_success:
        return response

    detail = cloudflare_error_detail(response)
    match response.status_code:
        case 401:
            entry.async_start_reauth(hass)
            message = "Invalid Cloudflare API token"
        case 403:
            message = "Cloudflare speech access denied; check Account > Workers AI > Read permission and billing"
        case 404:
            message = "Cloudflare speech model or account not found"
        case _:
            message = "Cloudflare speech request failed"
    raise HomeAssistantError(f"{message} (HTTP {response.status_code}){': ' + detail if detail else ''}")
