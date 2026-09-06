"""Auth0 resource-server validation and backend-only Yahoo token exchange."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import jwt
from fastmcp.server.auth import AccessToken, TokenVerifier

SCOPE = "fantasy:read"


@dataclass(frozen=True)
class PilotSettings:
    base_url: str
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    connection: str
    allowed_subjects: frozenset[str]
    allowed_client_ids: frozenset[str]

    def __post_init__(self):
        for url in (self.base_url, self.issuer):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in ("", "/")
            ):
                raise ValueError("Pilot URLs must be HTTPS origins without paths or credentials")
        if not self.issuer.endswith("/") or self.base_url.endswith("/"):
            raise ValueError("Issuer must end in /; base URL must not")
        if not 1 <= len(self.allowed_subjects) <= 2 or not all(self.allowed_subjects):
            raise ValueError("Configure one or two exact Auth0 subject identifiers")
        if not self.allowed_client_ids or not all(self.allowed_client_ids) or "*" in self.allowed_client_ids:
            raise ValueError("Configure exact allowed OAuth client identifiers")
        if not all((self.client_id, self.client_secret, self.connection)):
            raise ValueError("Missing Auth0 vault configuration")

    @property
    def audience(self):
        return self.base_url + "/mcp"

    @classmethod
    def from_env(cls):
        return cls(
            base_url=os.environ["PILOT_BASE_URL"].rstrip("/"),
            issuer=os.environ["PILOT_AUTH0_ISSUER"].rstrip("/") + "/",
            client_id=os.environ["PILOT_AUTH0_API_CLIENT_ID"],
            client_secret=os.environ["PILOT_AUTH0_API_CLIENT_SECRET"],
            connection=os.environ["PILOT_YAHOO_CONNECTION"],
            allowed_client_ids=frozenset(
                x.strip() for x in os.environ["PILOT_ALLOWED_CLIENT_IDS"].split(",") if x.strip()
            ),
            allowed_subjects=frozenset(
                x.strip() for x in os.environ["PILOT_ALLOWED_SUBJECTS"].split(",") if x.strip()
            ),
        )


class PilotVerifier(TokenVerifier):
    """Only signed, scoped, audience-bound tokens for invited users are accepted."""

    def __init__(self, settings: PilotSettings, client: httpx.AsyncClient):
        super().__init__(required_scopes=[SCOPE])
        self.settings = settings
        self.client = client
        self._keys = {}
        self._keys_until = 0.0
        self._lock = asyncio.Lock()
        self._unknown_refresh_after = 0.0

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                return None
            async with self._lock:
                now = time.monotonic()
                expired = now >= self._keys_until
                unknown = header["kid"] not in self._keys
                if expired and now < self._unknown_refresh_after:
                    return None
                if expired or (unknown and now >= self._unknown_refresh_after):
                    # Bound attacker-driven refetches, including failed requests.
                    self._unknown_refresh_after = now + 5
                    response = await self.client.get(self.settings.issuer + ".well-known/jwks.json")
                    response.raise_for_status()
                    self._keys = {
                        key["kid"]: jwt.PyJWK.from_dict(key).key
                        for key in response.json()["keys"]
                        if key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
                    }
                    self._keys_until = time.monotonic() + 60
                    if expired:
                        self._unknown_refresh_after = 0.0
            key = self._keys.get(header["kid"])
            if key is None:
                return None
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.settings.issuer,
                audience=self.settings.audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            if claims["sub"] not in self.settings.allowed_subjects:
                return None
            if claims.get("azp") not in self.settings.allowed_client_ids:
                return None
            scope = claims.get("scope", "")
            if not isinstance(scope, str) or SCOPE not in scope.split():
                return None
            return AccessToken(
                token=token,
                client_id=claims["azp"],
                scopes=scope.split(),
                expires_at=int(claims["exp"]),
                claims=claims,
            )
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, KeyError):
            # Never include supplied tokens or provider response bodies in logs.
            return None


class ConnectionError(Exception):
    """Safe, user-facing failure; no upstream payloads or credentials."""


class TokenVault:
    def __init__(self, settings: PilotSettings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def access_token(self, subject_token: str) -> str:
        try:
            response = await self.client.post(
                self.settings.issuer + "oauth/token",
                json={
                    "client_id": self.settings.client_id,
                    "client_secret": self.settings.client_secret,
                    "subject_token": subject_token,
                    "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "requested_token_type": "http://auth0.com/oauth/token-type/federated-connection-access-token",
                    "connection": self.settings.connection,
                },
            )
            if response.status_code in (400, 401, 403):
                raise ConnectionError(
                    "Yahoo connection unavailable. Reconnect Yahoo; contact the pilot operator if it persists."
                )
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            if (
                not isinstance(token, str)
                or not token
                or data.get("token_type", "").lower() != "bearer"
            ):
                raise ValueError("Invalid vault token")
            if float(data.get("expires_in", 0)) <= 0:
                raise ValueError("Expired vault token")
            return token
        except ConnectionError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise ConnectionError(
                "Yahoo connection service unavailable. Try again later."
            ) from None

    async def yahoo_identity(self, access_token: str) -> str:
        try:
            response = await self.client.get(
                "https://api.login.yahoo.com/openid/v1/userinfo",
                headers={"Authorization": "Bearer " + access_token},
            )
            response.raise_for_status()
            subject = response.json().get("sub")
            if not isinstance(subject, str) or not subject:
                raise ValueError("Missing verified Yahoo identity")
            return subject
        except (httpx.HTTPError, ValueError, TypeError):
            raise ConnectionError("Could not verify your Yahoo account. Reconnect Yahoo.") from None
