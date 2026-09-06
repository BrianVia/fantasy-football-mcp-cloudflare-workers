"""Real MCP/ASGI authentication and mocked provider integration tests."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.hosted.auth import PilotSettings, PilotVerifier, TokenVault, ConnectionError
from src.hosted.server import create_server
from src.api.yahoo_credentials import YahooCredentials, use_yahoo_credentials
from src.api import yahoo_client
from src.api.yahoo_utils import response_cache


@pytest.fixture
def harness(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="key-1", alg="RS256", use="sig")
    settings = PilotSettings(
        "https://pilot.example",
        "https://tenant.auth0.com/",
        "backend",
        "backend-secret",
        "yahoo-pilot",
        frozenset({"user-a", "user-b"}),
    )

    def mint(sub="user-a", **overrides):
        claims = dict(
            sub=sub,
            iss=settings.issuer,
            aud=settings.audience,
            exp=int(time.time()) + 600,
            iat=int(time.time()) - 1,
            scope="fantasy:read",
            azp="chatgpt",
        )
        claims.update(overrides)
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "key-1"})

    calls = []
    revoked = set()

    async def upstream(request):
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path == "/oauth/token":
            body = json.loads(request.content)
            assert body["client_id"] == "backend"
            assert body["connection"] == "yahoo-pilot"
            claims = jwt.decode(
                body["subject_token"],
                private.public_key(),
                algorithms=["RS256"],
                audience=settings.audience,
                issuer=settings.issuer,
            )
            sub = claims["sub"]
            calls.append(("vault", sub))
            if sub in revoked:
                return httpx.Response(403, json={"error": "secret-upstream-details"})
            return httpx.Response(
                200,
                json={"access_token": "yahoo-" + sub, "token_type": "Bearer", "expires_in": 3600},
            )
        if request.url.path == "/openid/v1/userinfo":
            sub = request.headers["authorization"].removeprefix("Bearer yahoo-")
            return httpx.Response(200, json={"sub": "guid-" + sub})
        raise AssertionError(str(request.url))

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    monkeypatch.setenv("YAHOO_ACCESS_TOKEN", "personal-token-never-use")
    monkeypatch.setenv("YAHOO_GUID", "personal-guid-never-use")
    import fantasy_football_multi_league as legacy

    monkeypatch.setattr(legacy, "LEAGUES_CACHE", {"personal-only": {"name": "PRIVATE"}})
    response_cache.cache.clear()
    monkeypatch.setattr(yahoo_client.rate_limiter, "acquire", AsyncMock())
    monkeypatch.setattr(yahoo_client.aiohttp, "TCPConnector", lambda **kw: None)

    class Response:
        status = 200

        def __init__(self, url, headers):
            self.url = url
            self.sub = headers["Authorization"].removeprefix("Bearer yahoo-")
            assert self.sub in ("user-a", "user-b")

        async def __aenter__(self):
            await asyncio.sleep(0)
            return self

        async def __aexit__(self, *args):
            pass

        async def json(self):
            sub = self.sub
            endpoint = self.url.split("/fantasy/v2/", 1)[1].split("?", 1)[0]
            calls.append((endpoint, sub))
            league_key = "461.l." + ("1" if sub == "user-a" else "2")
            if endpoint.startswith("users;"):
                return {
                    "fantasy_content": {
                        "users": {
                            "0": {
                                "user": [
                                    {},
                                    {
                                        "games": {
                                            "0": {
                                                "game": [
                                                    {},
                                                    {
                                                        "leagues": {
                                                            "0": {
                                                                "league": [
                                                                    {
                                                                        "league_key": league_key,
                                                                        "league_id": "1",
                                                                        "name": sub,
                                                                        "num_teams": 2,
                                                                        "current_week": 1,
                                                                        "scoring_type": "head",
                                                                    }
                                                                ]
                                                            },
                                                            "count": 1,
                                                        }
                                                    },
                                                ]
                                            }
                                        }
                                    },
                                ]
                            }
                        }
                    }
                }
            if endpoint.endswith("/teams"):
                return {
                    "fantasy_content": {
                        "league": [
                            {},
                            {
                                "teams": {
                                    "0": {
                                        "team": [
                                            [
                                                {"team_key": league_key + ".t.1"},
                                                {"name": sub},
                                                {
                                                    "managers": [
                                                        {"manager": {"guid": "guid-" + sub}}
                                                    ]
                                                },
                                            ]
                                        ]
                                    }
                                }
                            },
                        ]
                    }
                }
            if endpoint.endswith("/roster"):
                return {
                    "fantasy_content": {
                        "team": [
                            [],
                            {
                                "roster": {
                                    "0": {
                                        "players": {
                                            "0": {
                                                "player": [
                                                    [
                                                        {"player_key": "461.p.1"},
                                                        {"name": {"full": "Player " + sub}},
                                                        {"display_position": "QB"},
                                                    ],
                                                    {"selected_position": [{"position": "QB"}]},
                                                ]
                                            },
                                            "count": 1,
                                        }
                                    }
                                }
                            },
                        ]
                    }
                }
            if endpoint.endswith("/standings"):
                return {"fantasy_content": {"league": [{}, {"standings": {"teams": {}}}]}}
            raise AssertionError(endpoint)

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, headers):
            return Response(url, headers)

        def post(self, *a, **kw):
            raise AssertionError("Must not refresh Yahoo directly")

    monkeypatch.setattr(yahoo_client.aiohttp, "ClientSession", Session)
    return settings, client, mint, calls, revoked


@asynccontextmanager
async def transport(harness):
    settings, client, *_ = harness
    server = create_server(settings, client=client)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=settings.base_url
        ) as session:
            yield session


async def rpc(session, token, method="tools/list", params=None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    return await session.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def result(response):
    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    if "structuredContent" in payload:
        return payload["structuredContent"]
    return json.loads(payload["content"][0]["text"])


@pytest.mark.asyncio
async def test_discovery_and_exact_tool_surface(harness):
    async with transport(harness) as session:
        health = await session.get("/health")
        assert health.json() == {"status": "ok"}
        metadata = (await session.get("/.well-known/oauth-protected-resource")).json()
        assert metadata["resource"] == harness[0].audience
        assert metadata["authorization_servers"] == [harness[0].issuer]
        assert metadata["scopes_supported"] == ["fantasy:read"]
        response = await rpc(session, harness[2]())
        tools = response.json()["result"]["tools"]
        assert {t["name"] for t in tools} == {"ff_get_leagues", "ff_get_roster", "ff_get_standings"}
        for tool in tools:
            assert set(tool["inputSchema"]["properties"]) == (
                set() if tool["name"] == "ff_get_leagues" else {"league_key"}
            )
            assert tool["_meta"]["securitySchemes"][0]["scopes"] == ["fantasy:read"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "forged",
        "expired",
        "audience",
        "issuer",
        "scope",
        "uninvited",
        "missing-exp",
        "future",
    ],
)
async def test_bad_tokens_rejected_before_yahoo(harness, kind):
    mint = harness[2]
    tokens = {
        "missing": None,
        "forged": "bad-token",
        "expired": mint(exp=1),
        "audience": mint(aud="https://other.example"),
        "issuer": mint(iss="https://other.example/"),
        "scope": mint(scope="other"),
        "uninvited": mint(sub="user-c"),
        "missing-exp": mint(exp=None),
        "future": mint(nbf=int(time.time()) + 1000),
    }
    async with transport(harness) as session:
        response = await rpc(session, tokens[kind])
        assert response.status_code in (401, 403)
        assert "www-authenticate" in response.headers
    assert not harness[3]


@pytest.mark.asyncio
async def test_concurrent_accounts_cache_roster_and_foreign_league(harness):
    async with transport(harness) as session:

        async def leagues(user):
            return result(
                await rpc(
                    session,
                    harness[2](user),
                    "tools/call",
                    {"name": "ff_get_leagues", "arguments": {}},
                )
            )

        a, b = await asyncio.gather(leagues("user-a"), leagues("user-b"))
        assert a["leagues"][0]["name"] == "user-a"
        assert b["leagues"][0]["name"] == "user-b"
        assert await leagues("user-a") == a
        for user, key in (("user-a", "461.l.1"), ("user-b", "461.l.2")):
            roster = result(
                await rpc(
                    session,
                    harness[2](user),
                    "tools/call",
                    {"name": "ff_get_roster", "arguments": {"league_key": key}},
                )
            )
            assert roster["team_key"] == key + ".t.1", roster
            assert roster["team_name"] == user
            assert "Player " + user in json.dumps(roster)
            standings = result(
                await rpc(
                    session,
                    harness[2](user),
                    "tools/call",
                    {"name": "ff_get_standings", "arguments": {"league_key": key}},
                )
            )
            assert standings["league_key"] == key
        before = len(harness[3])
        denied = result(
            await rpc(
                session,
                harness[2](),
                "tools/call",
                {"name": "ff_get_roster", "arguments": {"league_key": "461.l.2"}},
            )
        )
        assert "not available" in denied["error"]
        assert not any(endpoint.startswith("team/") for endpoint, _ in harness[3][before:])
        # Every invocation still checks the vault, even when data is cached.
        assert len([c for c in harness[3] if c[0] == "vault"]) == 8


@pytest.mark.asyncio
async def test_revocation_before_cache_and_restart(harness):
    async with transport(harness) as session:
        request = {"name": "ff_get_leagues", "arguments": {}}
        first = result(await rpc(session, harness[2](), "tools/call", request))
        assert first["total_leagues"] == 1
        harness[4].add("user-a")
        denied = result(await rpc(session, harness[2](), "tools/call", request))
        assert "Reconnect" in denied["error"]
        assert "secret-upstream" not in json.dumps(denied)
    harness[4].clear()
    response_cache.cache.clear()
    async with transport(harness) as session:
        assert result(await rpc(session, harness[2](), "tools/call", request)) == first
    removed = (replace(harness[0], allowed_subjects=frozenset({"user-b"})), *harness[1:])
    async with transport(removed) as session:
        assert (await rpc(session, harness[2]())).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,count", [("token_rejected", 2), ("additional_authorization_required", 1), ("revoked", 1)]
)
async def test_yahoo_401_retry_policy(harness, monkeypatch, body, count):
    gets = []

    class Response:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def text(self):
            return body

    class Session:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        def get(self, *a, **kw):
            gets.append(kw)
            return Response()

    monkeypatch.setattr(yahoo_client.aiohttp, "ClientSession", Session)
    supplier = AsyncMock(return_value="renewed-secret")
    c = YahooCredentials("old-secret", "", "", "", user_id="a", access_token_supplier=supplier)
    with use_yahoo_credentials(c):
        with pytest.raises(Exception) as caught:
            await yahoo_client.yahoo_api_call("test", use_cache=False)
        assert "old-secret" not in str(caught.value)
        assert "renewed-secret" not in str(caught.value)
    assert len(gets) == count
    assert supplier.await_count == count - 1


@pytest.mark.asyncio
async def test_forged_signature_and_required_claims(harness):
    settings, client, mint, *_ = harness
    verifier = PilotVerifier(settings, client)
    claims = jwt.decode(mint(), options={"verify_signature": False})
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, other_key, algorithm="RS256", headers={"kid": "key-1"})
    assert await verifier.verify_token(forged) is None
    assert await verifier.verify_token(mint()) is not None


@pytest.mark.asyncio
async def test_managed_renewal_success_does_not_touch_personal_environment(harness, monkeypatch):
    import os

    responses = [(401, "token_rejected"), (200, "")]
    seen = []

    class Response:
        def __init__(self):
            self.status, self.body = responses.pop(0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def text(self):
            return self.body

        async def json(self):
            return {"roster": "ok"}

    class Session:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        def get(self, *a, **kw):
            seen.append(kw["headers"]["Authorization"])
            return Response()

    monkeypatch.setattr(yahoo_client.aiohttp, "ClientSession", Session)
    supplier = AsyncMock(return_value="new-private-token")
    credentials = YahooCredentials("old-private-token", "", "", "", access_token_supplier=supplier)
    with use_yahoo_credentials(credentials):
        assert await yahoo_client.yahoo_api_call("test", use_cache=False) == {"roster": "ok"}
    assert supplier.await_count == 1
    assert seen == ["Bearer old-private-token", "Bearer new-private-token"]
    assert os.environ["YAHOO_ACCESS_TOKEN"] == "personal-token-never-use"
    assert "old-private-token" not in repr(credentials)


@pytest.mark.asyncio
async def test_nested_league_shape_and_global_cache_bypass(
    harness, monkeypatch, mock_yahoo_league_response
):
    import fantasy_football_multi_league as legacy

    monkeypatch.setattr(
        legacy, "yahoo_api_call", AsyncMock(return_value=mock_yahoo_league_response)
    )
    c = YahooCredentials("token", "", "", "", user_id="a")
    with use_yahoo_credentials(c):
        leagues = await legacy.discover_leagues()
    assert "461.l.61410" in leagues
    assert legacy.LEAGUES_CACHE == {"personal-only": {"name": "PRIVATE"}}


@pytest.mark.asyncio
async def test_vault_errors_are_sanitized(harness):
    settings = harness[0]

    async def upstream(request):
        return httpx.Response(500, text="secret-token-in-upstream-error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        vault = TokenVault(settings, client)
        with pytest.raises(ConnectionError) as caught:
            await vault.access_token("private-subject-token")
    assert "secret-token" not in str(caught.value)
    assert "private-subject" not in str(caught.value)


def test_settings_fail_closed():
    with pytest.raises(ValueError):
        PilotSettings(
            "http://unsafe.example",
            "https://tenant.auth0.com/",
            "id",
            "secret",
            "yahoo",
            frozenset({"a"}),
        )
    with pytest.raises(ValueError):
        PilotSettings(
            "https://pilot.example",
            "https://tenant.auth0.com/",
            "id",
            "secret",
            "yahoo",
            frozenset(),
        )
    with pytest.raises(ValueError):
        PilotSettings(
            "https://pilot.example",
            "https://tenant.auth0.com/",
            "id",
            "secret",
            "yahoo",
            frozenset({"a", "b", "c"}),
        )


@pytest.mark.asyncio
async def test_account_change_during_renewal_aborts(harness, monkeypatch, caplog):
    identities = iter(["guid-user-a", "guid-user-b"])

    async def identity(self, token):
        return next(identities)

    monkeypatch.setattr(TokenVault, "yahoo_identity", identity)

    class Response:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def text(self):
            return "token_rejected"

    class Session:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        def get(self, *a, **kw):
            return Response()

    monkeypatch.setattr(yahoo_client.aiohttp, "ClientSession", Session)
    bearer = harness[2]()
    async with transport(harness) as session:
        payload = result(
            await rpc(session, bearer, "tools/call", {"name": "ff_get_leagues", "arguments": {}})
        )
    assert "Reconnect" in payload["error"]
    for secret in (bearer, "backend-secret", "yahoo-user-a", "personal-token-never-use"):
        assert secret not in json.dumps(payload) + caplog.text
