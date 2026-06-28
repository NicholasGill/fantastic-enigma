from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: float

    def is_valid(self) -> bool:
        return time.time() < self.expires_at - 60


class BlizzardApiError(RuntimeError):
    """Raised when the Blizzard API returns an unsuccessful response."""


class BlizzardClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        region: str,
        locale: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region.lower()
        self.locale = locale
        self.api_base_url = f"https://{self.region}.api.blizzard.com"
        self.oauth_url = "https://oauth.battle.net/token"
        self._http_client = http_client or httpx.Client(timeout=30.0)
        self._owns_http_client = http_client is None
        self._token: AccessToken | None = None

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> BlizzardClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def fetch_connected_realm_auctions(self, connected_realm_id: int) -> dict[str, Any]:
        return self._get(
            f"/data/wow/connected-realm/{connected_realm_id}/auctions",
            namespace=f"dynamic-{self.region}",
        )

    def fetch_commodity_auctions(self) -> dict[str, Any]:
        return self._get(
            "/data/wow/auctions/commodities",
            namespace=f"dynamic-{self.region}",
        )

    def _get(self, path: str, *, namespace: str) -> dict[str, Any]:
        token = self._get_access_token()
        response = self._http_client.get(
            f"{self.api_base_url}{path}",
            params={
                "namespace": namespace,
                "locale": self.locale,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        return self._read_json_response(response)

    def _get_access_token(self) -> str:
        if self._token and self._token.is_valid():
            return self._token.value

        response = self._http_client.post(
            self.oauth_url,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        payload = self._read_json_response(response)
        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 0))
        if not access_token or expires_in <= 0:
            raise BlizzardApiError("OAuth response did not include a usable access token")

        self._token = AccessToken(
            value=str(access_token),
            expires_at=time.time() + expires_in,
        )
        return self._token.value

    @staticmethod
    def _read_json_response(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise BlizzardApiError(
                f"Blizzard API request failed with HTTP {response.status_code}: {response.text}"
            ) from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise BlizzardApiError("Blizzard API response was not a JSON object")
        return payload
