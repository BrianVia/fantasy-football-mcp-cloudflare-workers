"""Authenticated, three-tool pilot. No personal credential fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse

from src.api.yahoo_credentials import YahooCredentials, use_yahoo_credentials
from src.api.yahoo_client import NOT_PROVISIONED_ERROR
from src.hosted.auth import ConnectionError, PilotSettings, PilotVerifier, SCOPE, TokenVault


def create_server(settings: PilotSettings, *, client=None):
    owned_client = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)
    verifier = PilotVerifier(settings, client)
    vault = TokenVault(settings, client)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            if owned_client:
                await client.aclose()

    auth = RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(settings.issuer)],
        base_url=settings.base_url,
    )
    server = FastMCP(
        "Yahoo Fantasy private pilot",
        auth=auth,
        lifespan=lifespan,
        instructions="Read your connected Yahoo leagues, roster, and standings. Private pilot.",
        mask_error_details=True,
    )

    # Imported before request contexts exist: legacy helper injection runs once.
    import fantasy_football_multi_league as legacy
    from src.handlers.league_handlers import handle_ff_get_leagues, handle_ff_get_standings
    from src.handlers.roster_handlers import handle_ff_get_roster

    async def execute(name, league_key=None):
        bearer = get_access_token()
        if bearer is None:
            return {"error": "Connect your account to use this tool."}
        # Revalidate even for in-process clients and long-lived MCP sessions.
        verified = await verifier.verify_token(bearer.token)
        if verified is None:
            return {
                "error": "Your connection is unauthorized. Reconnect or contact the pilot operator."
            }
        try:
            async with asyncio.timeout(45):
                yahoo_id = None

                async def supply():
                    nonlocal yahoo_id
                    next_token = await vault.access_token(bearer.token)
                    next_identity = await vault.yahoo_identity(next_token)
                    if yahoo_id is not None and yahoo_id != next_identity:
                        raise ConnectionError(
                            "Yahoo account changed during this request. Reconnect Yahoo."
                        )
                    yahoo_id = next_identity
                    return next_token

                token = await supply()
                namespace = hashlib.sha256(
                    json.dumps(
                        [settings.issuer, verified.claims["sub"], settings.connection, yahoo_id]
                    ).encode()
                ).hexdigest()
                credentials = YahooCredentials(
                    access_token=token,
                    refresh_token="",
                    client_id="",
                    client_secret="",
                    user_id=verified.claims["sub"],
                    yahoo_user_id=yahoo_id,
                    cache_namespace=namespace,
                    access_token_supplier=supply,
                )
                with use_yahoo_credentials(credentials):
                    if name == "ff_get_leagues":
                        return await handle_ff_get_leagues({})
                    leagues = await legacy.discover_leagues()
                    if league_key not in leagues:
                        return {
                            "error": "That league is not available to your connected Yahoo account."
                        }
                    if name == "ff_get_standings":
                        return await handle_ff_get_standings({"league_key": league_key})
                    return await handle_ff_get_roster(
                        {
                            "league_key": league_key,
                            "data_level": "basic",
                            "include_projections": False,
                            "include_external_data": False,
                            "include_analysis": False,
                        }
                    )
        except ConnectionError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            if str(exc) == NOT_PROVISIONED_ERROR:
                return {
                    "error": "Yahoo has not provisioned Fantasy API access for this app. Contact the pilot operator."
                }
            if "needs reauthorization" in str(exc):
                return {"error": "Yahoo access needs renewal. Reconnect Yahoo."}
            return {"error": "Unable to retrieve Yahoo data. Try again or reconnect Yahoo."}

    meta = {"securitySchemes": [{"type": "oauth2", "scopes": [SCOPE]}]}
    annotations = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}

    @server.tool(meta=meta, annotations=annotations)
    async def ff_get_leagues() -> dict:
        """List the NFL leagues belonging to your connected Yahoo account."""
        return await execute("ff_get_leagues")

    @server.tool(meta=meta, annotations=annotations)
    async def ff_get_roster(league_key: str) -> dict:
        """Read your own current roster in one of your connected Yahoo leagues."""
        return await execute("ff_get_roster", league_key)

    @server.tool(meta=meta, annotations=annotations)
    async def ff_get_standings(league_key: str) -> dict:
        """Read standings for one of your connected Yahoo leagues."""
        return await execute("ff_get_standings", league_key)

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok"})

    return server
