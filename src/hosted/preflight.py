"""Read-only discovery check. Does not prove tenant entitlements or Yahoo approval."""

import asyncio
import httpx
from src.hosted.auth import PilotSettings


async def check():
    settings = PilotSettings.from_env()
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
        response = await client.get(settings.issuer + ".well-known/openid-configuration")
        response.raise_for_status()
        data = response.json()
        checks = {
            "exact_issuer": data.get("issuer") == settings.issuer,
            "pkce_s256": "S256" in data.get("code_challenge_methods_supported", []),
            "authorization_endpoint": data.get("authorization_endpoint")
            == settings.issuer + "authorize",
            "token_endpoint": data.get("token_endpoint") == settings.issuer + "oauth/token",
            "jwks_endpoint": data.get("jwks_uri") == settings.issuer + ".well-known/jwks.json",
        }
        for name, passed in checks.items():
            print(f"{name}: {'PASS' if passed else 'FAIL'}")
        print(
            "PENDING: Free entitlements, Yahoo scopes/approval, vault renewal, exact ChatGPT callback, two-account test."
        )
        return all(checks.values())


if __name__ == "__main__":
    try:
        passed = asyncio.run(check())
    except Exception:
        print(
            "Preflight failed: check configured origins, required settings, and provider connectivity. No credentials printed."
        )
        passed = False
    raise SystemExit(0 if passed else 1)
