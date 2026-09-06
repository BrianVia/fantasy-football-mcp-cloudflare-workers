# Private Yahoo pilot runbook

## Delivery status

Code and mocked-provider tests are implemented on `multi-user-app`. **No live Auth0 tenant or Render pilot is configured.** Yahoo provisioning and permission for the hosted use case are unverified; the last checked email was an application acknowledgment, not approval. Real Yahoo refresh, ChatGPT linking, and the two-account acceptance test remain pending. Do not advertise this branch as a working public service.

`main`, its existing deployment, its dependency manifest, and personal setup remain separate. This branch exposes only leagues, own current roster, and standings through a new hosted entry point. There is no billing, public signup, invitation sender, app-store submission, or database.

## 1. Provider feasibility gate

Before connecting real users, create/select a **Free** Auth0 tenant and verify all of the following in that tenant (a time-limited paid trial does not satisfy the gate):

- MCP OAuth authorization-code flow with S256 PKCE, resource/audience binding, and client discovery or predefined ChatGPT client registration.
- A custom Yahoo social connection enabled for both authentication and Connected Accounts / Token Vault. The documented custom connection capability is not proof that Yahoo refresh works in this tenant.
- Backend **access token exchange** through a Custom API Client linked to the MCP API; token-vault grant enabled and restricted to the Yahoo connection.
- Yahoo permits the hosted two-user use case. The app has approved Fantasy access and the identity scopes needed by Yahoo UserInfo. Check actual allowed scopes; do not assume creating a Yahoo developer app provisions Fantasy access.
- An actual Yahoo login yields an account identity, a vaulted renewable connection, and a token that can read Fantasy data. Verify renewal after expiry, not just the first successful login.

If any capability is unavailable on Free, stop provider integration and record the blocker. Do not enable a paid add-on or replace it with raw personal credentials. Mocked development may continue.

Sources: [Auth0 custom connections](https://auth0.com/docs/authenticate/identity-providers/social-identity-providers/oauth2), [vault configuration](https://auth0.com/docs/secure/call-apis-on-users-behalf/token-vault/configure-token-vault), [backend exchange](https://auth0.com/docs/secure/call-apis-on-users-behalf/token-vault/access-token-exchange-with-token-vault), [Yahoo identity](https://developer.yahoo.com/sign-in-with-yahoo/).

## 2. Configure identity and account linking

1. Register an Auth0 API whose identifier is exactly `https://YOUR-PILOT.onrender.com/mcp`, with RS256 signing and the `fantasy:read` permission. Use short-lived API access tokens (for example, 10 minutes). Enable refresh for the ChatGPT connection as supported by the provider; these are Auth0 tokens, not Yahoo refresh tokens.
2. Create the linked **Custom API Client** for Token Vault backend exchange. Put its credentials only in Render secrets. Restrict grants to the pilot API and the named Yahoo connection; do not give the MCP service broad Auth0 Management API access.
3. Configure the custom social connection `yahoo-pilot` with Yahoo's authorization and token endpoints. Store the Yahoo client secret in Auth0 only. Configure space-delimited approved scopes, including `openid`, and a profile fetch using Yahoo UserInfo; derive identity from `sub`, never email. Validate the profile response and reject a missing subject. Register the exact Auth0 callback `https://YOUR-TENANT/login/callback` with Yahoo.
4. Enable this connection for login and Token Vault. Configure account creation/linking so the initial Yahoo login establishes the renewable connected account. Permit **one Yahoo connected account per user**. Verify this constraint and account population before the pilot: automatic storage must not be assumed from login success alone. Do not enable additional social or password connections, automatic email-based account merging, or unrestricted pilot access.
5. Configure ChatGPT as the OAuth client using Auth0's supported MCP setup. For the private test, a predefined client is acceptable; enter its exact details in ChatGPT's developer connector setup. Allowlist the exact ChatGPT callback displayed there. Never use wildcard callbacks. Verify authorization-server metadata advertises S256, that `resource` maps to the API identifier, and that requesting `fantasy:read` yields that scope. Follow current [OpenAI authentication instructions](https://developers.openai.com/plugins/build/auth).
6. Have each tester sign in independently. Copy each verified Auth0 `sub` from the tenant into `PILOT_ALLOWED_SUBJECTS`. One or two identifiers are accepted; everyone else fails closed. No raw Yahoo token, client secret, or user selector is accepted in tool arguments.

This is a configuration recipe, **not an automated tenant setup**. Provider account creation, tenant-specific entitlement verification, and actual Yahoo permission testing are still required.

## 3. Separate free deployment

- Create a new Render Free web service from `multi-user-app` using `render-pilot.yaml`. Do not alter or import settings from the personal service. Automatic deploys are off; if using a Blueprint, also set its Auto Sync to No. Deploy an explicitly reviewed commit manually.
- Set the six variables in `config/hosted/pilot.env.example`. Use the Render-assigned HTTPS origin without a trailing slash. Auth0's issuer includes its trailing slash. The audience is always the origin plus `/mcp`.
- Do not supply `YAHOO_ACCESS_TOKEN`, `YAHOO_REFRESH_TOKEN`, or personal token files. No Yahoo refresh tokens are persisted on Render. Runtime caches are memory-only and can be discarded on restart.
- Remain on Free. If the existing Render workspace can bill usage overages, use a separate free workspace without a payment method or verified zero-spend enforcement; do not change billing settings of unrelated services. If zero spend cannot be guaranteed, stop deployment and report it.
- Single process/instance only; no workers or autoscaling. Render Free sleeps after inactivity and can take about a minute to resume. Open `/health` before a test session, wait for `{"status":"ok"}`, then connect/retry in ChatGPT. Do not schedule keepalive traffic.
- `/health` is liveness only, not a claim that Yahoo/Auth0 is working. Protected metadata is at `/.well-known/oauth-protected-resource`; MCP is at `/mcp`.

Local hosted verification in a dedicated Python 3.12 environment:

```sh
python -m pip install -r requirements-hosted.txt
python -m pytest tests/hosted tests/unit
# With real pilot settings injected by your local secret manager:
python -m src.hosted.preflight
python hosted_server.py
```

The hosted manifest retains the existing base pins and adds explicit JWT/HTTP/ASGI dependencies. Validate in a separate environment from the personal installation. Render's ephemeral disk is never used for tokens or account state. [Render Free limits](https://render.com/docs/free)

## 4. Request and failure behavior

Every tool call verifies the Auth0 token and pilot allowlist, obtains a Yahoo token from Token Vault, verifies Yahoo identity through UserInfo, then binds request-local credentials. Cache ownership includes issuer, Auth0 subject, connection name, and Yahoo identity. League discovery bypasses the personal global cache; membership checks gate roster/standings and the roster's team is resolved server-side.

The three interfaces are `ff_get_leagues()`, `ff_get_roster(league_key)`, and `ff_get_standings(league_key)`. Rosters use basic data without external enrichment. The rest of the personal server's tools are not registered.

Only `token_rejected` Yahoo 401s are retried, at most once per Yahoo API call, by asking the vault for access again. The app never exchanges Yahoo refresh tokens itself. An identity change during renewal aborts the request. Provisioning failures remain distinct; revocation prompts reconnection. Upstream error bodies and credentials are not returned. Tool work has a 45-second limit, and provider HTTP calls have a 20-second timeout.

## 5. Private operator-assisted disconnect

1. Remove the user's subject from `PILOT_ALLOWED_SUBJECTS` and redeploy/restart before proceeding. If removing the last tester, suspend the pilot service instead; an empty allowlist intentionally prevents startup.
2. Restart clears all process caches. Verify the old bearer token is denied, even if still unexpired.
3. In Auth0, remove the Yahoo connected account and revoke the user's pilot grants/refresh tokens. Delete the pilot user record if they requested account deletion. Verify the vault no longer returns Yahoo access for the removed connection.
4. Ask the user to revoke the app in Yahoo's connected-app settings if they want provider-side revocation as well, and disconnect it in ChatGPT. Do not claim that a ChatGPT disconnect alone removes the vaulted connection.

For reconnect, repeat the identity/linking verification before restoring the allowlist entry. If the Yahoo account changed, old data cannot be reused because its cache namespace differs.

## 6. Acceptance and rollback

Mocked tests must pass for token rejection, exact tool surface/metadata, concurrent identities, cached results, foreign-league denial, managed renewal, provisioning errors, revocation, restart, and personal-environment isolation. Run existing unit tests in a separate base-dependency environment. Five legacy API tests had stale credential-name/cache-call expectations on `main`; those expectations are corrected on this branch, without changing personal API behavior.

Pending live checklist:

- [ ] Free-tier tenant entitlement and Yahoo hosted-use approval verified.
- [ ] Two actual accounts connect in ChatGPT and see only their own leagues/default roster.
- [ ] Both accounts still work after token expiry and a Render restart.
- [ ] User removal denies the old token and removes the vaulted connection.
- [ ] First-request cold-start behavior documented with the testers.

Inspect status-only Render logs and provider dashboards for failures; never enable request/body/token debug logging. Check free usage limits manually during the pilot. If isolation or refresh fails, suspend only the pilot service and remove its grants; do not modify the personal service. No merge to `main` or public submission is part of this release.
