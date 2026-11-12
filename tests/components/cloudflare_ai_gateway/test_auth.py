"""Token ownership and temporary-failure classification tests."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.cloudflare_ai_gateway.auth import InvalidAuth, TokenValidationError, async_validate_token
from custom_components.cloudflare_ai_gateway.config_flow import validate_gateway
from custom_components.cloudflare_ai_gateway.const import CONF_ACCOUNT_ID, CONF_CF_API_TOKEN, CONF_GATEWAY_ID

USER_VERIFY = "https://api.cloudflare.com/client/v4/user/tokens/verify"
ACCOUNT_VERIFY = "https://api.cloudflare.com/client/v4/accounts/test-account/tokens/verify"


def active_token() -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": {"status": "active"}})


async def verify(client):
    await async_validate_token(client, account_id="test-account", api_token="test-token")


async def test_user_token_verification() -> None:
    client = AsyncMock()
    client.get.return_value = active_token()
    await verify(client)
    client.get.assert_awaited_once_with(USER_VERIFY, headers={"Authorization": "Bearer test-token"}, timeout=10.0)


@pytest.mark.parametrize(
    "user_response",
    [httpx.Response(status) for status in (400, 401, 403, 404)]
    + [httpx.Response(200, json={"success": False, "errors": [{"code": 1000, "message": "Invalid API Token"}]})],
)
async def test_account_token_fallback(user_response) -> None:
    client = AsyncMock()
    client.get.side_effect = [user_response, active_token()]
    await verify(client)
    assert [call.args[0] for call in client.get.await_args_list] == [USER_VERIFY, ACCOUNT_VERIFY]
    for call in client.get.await_args_list:
        assert call.kwargs == {"headers": {"Authorization": "Bearer test-token"}, "timeout": 10.0}


async def test_rejected_by_both_endpoints() -> None:
    client = AsyncMock()
    client.get.return_value = httpx.Response(401)
    with pytest.raises(InvalidAuth):
        await verify(client)
    assert client.get.await_count == 2


@pytest.mark.parametrize("status", ["disabled", "expired"])
async def test_inactive_token(status) -> None:
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json={"success": True, "result": {"status": status}})
    with pytest.raises(InvalidAuth):
        await verify(client)
    client.get.assert_awaited_once()


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(429),
        httpx.Response(500),
        httpx.Response(200, text="upstream unavailable"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"success": True, "result": {}}),
        httpx.ConnectError("connection failed"),
        httpx.TimeoutException("timeout"),
    ],
)
@pytest.mark.parametrize("failure_first", [False, True])
async def test_service_failures_are_not_invalid_tokens(failure, failure_first) -> None:
    client = AsyncMock()
    client.get.side_effect = [failure, httpx.Response(401)] if failure_first else [httpx.Response(401), failure]
    with pytest.raises(TokenValidationError, match="temporarily unavailable"):
        await verify(client)


async def test_account_token_works_even_if_user_endpoint_is_unavailable() -> None:
    client = AsyncMock()
    client.get.side_effect = [httpx.Response(503), active_token()]
    await verify(client)


async def test_gateway_validation_accepts_account_token(hass) -> None:
    client = AsyncMock()
    client.get.side_effect = [httpx.Response(401), active_token(), httpx.Response(405)]
    with patch("custom_components.cloudflare_ai_gateway.config_flow.get_async_client", return_value=client):
        await validate_gateway(
            hass,
            {CONF_ACCOUNT_ID: "test-account", CONF_GATEWAY_ID: "test-gateway", CONF_CF_API_TOKEN: "test-token"},
        )
    assert [call.args[0] for call in client.get.await_args_list] == [
        USER_VERIFY,
        ACCOUNT_VERIFY,
        "https://gateway.ai.cloudflare.com/v1/test-account/test-gateway/compat",
    ]
