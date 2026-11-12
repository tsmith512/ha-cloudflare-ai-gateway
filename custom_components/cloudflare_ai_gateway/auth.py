"""Cloudflare user-owned and account-owned API token verification."""

import httpx


class InvalidAuth(Exception):
    """Cloudflare rejected the token or reported it disabled/expired."""


class TokenValidationError(Exception):
    """Token validity could not be determined due to a service failure."""


async def async_validate_token(client: httpx.AsyncClient, *, account_id: str, api_token: str) -> None:
    """Verify either token ownership type without mistaking outages for expiry."""
    unavailable = False
    for path in ("user/tokens/verify", f"accounts/{account_id}/tokens/verify"):
        try:
            response = await client.get(
                f"https://api.cloudflare.com/client/v4/{path}",
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=10.0,
            )
        except httpx.RequestError:
            unavailable = True
            continue

        # A token of the other ownership type can be rejected by this endpoint.
        if response.status_code in (400, 401, 403, 404):
            continue
        if response.status_code != 200:
            unavailable = True
            continue
        try:
            payload = response.json()
        except ValueError:
            unavailable = True
            continue
        if isinstance(payload, dict) and payload.get("success") is False:
            continue
        if not isinstance(payload, dict) or payload.get("success") is not True:
            unavailable = True
            continue
        result = payload.get("result")
        status = result.get("status") if isinstance(result, dict) else None
        if status == "active":
            return
        if status in ("disabled", "expired"):
            raise InvalidAuth("Cloudflare API token is disabled or expired")
        unavailable = True

    if unavailable:
        raise TokenValidationError("Cloudflare token verification is temporarily unavailable; retry later")
    raise InvalidAuth("Cloudflare could not verify this token at either the user or account endpoint")
