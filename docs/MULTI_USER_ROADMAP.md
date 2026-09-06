# Experimental multi-user app roadmap

Status: planning only. This branch does not provide a ready-to-deploy shared service or an approved ChatGPT app.

## Branch boundaries

- `main` remains the supported personal, single-user server using the existing environment-variable setup.
- `multi-user-app` starts from `main` at `721a6e2a` and holds future hosted, multi-user work.
- Preserve existing security fixes, request-scoped credentials, and cache isolation on both branches. Shared fixes should be reviewed on `main` and brought into this branch.
- Keep hosted-only dependencies and configuration out of the single-user installation path. Do not require an account service or database to run the personal server.
- Leave `launch-hardening` untouched until its remaining differences have been reviewed separately.

## Existing groundwork

The code already supports request-scoped Yahoo credentials, isolated refresh handling, and per-user cache keys. Without a request context, it continues to use the single-user environment configuration. These helpers are groundwork, not a complete authentication or account system.

When a request refreshes Yahoo tokens, the hosting layer must persist the final `session.credentials` from `use_yahoo_credentials`; Yahoo may rotate the refresh token. Tokens must remain outside model-visible tool responses.

## Milestones

### 1. Confirm feasibility

- [ ] Obtain Yahoo Fantasy API access and confirm that approval covers the intended hosted, multi-user use.
- [ ] Verify current official ChatGPT app authentication, hosting, privacy, and submission requirements before choosing the integration design.
- [ ] Define a small read/analysis-only pilot and acceptable hosting/support costs.

### 2. Connect individual accounts

- [ ] Authenticate the connecting app user and map that identity to their Yahoo connection.
- [ ] Implement the Yahoo authorization callback with request validation and secure account binding.
- [ ] Store tokens encrypted per user, persist rotated tokens, and handle concurrent refreshes safely.
- [ ] Bind verified user credentials to every hosted MCP request; reject missing identity or credentials rather than falling back to the operator's environment credentials.
- [ ] Provide account disconnection and token/data deletion, with a reauthorization path when access is revoked.

### 3. Verify separation and operation

- [ ] Test that two users cannot receive each other's data through requests, caches, errors, logs, or token refreshes.
- [ ] Test expired/revoked tokens, Yahoo provisioning failures, rate limits, and account disconnection.
- [ ] Limit the public tool surface to the intended consumer read/analysis tools.
- [ ] Verify unchanged personal installation, environment configuration, and server entry points before proposing any shared code changes to `main`.
- [ ] Establish deployment, monitoring, and recovery procedures without logging credentials.

### 4. Pilot, then decide on submission

- [ ] Run a small pilot with a couple of consenting users after Yahoo permits the intended use.
- [ ] Address usability, reliability, privacy, and support issues discovered in the pilot.
- [ ] Recheck current official submission requirements and prepare the required materials.
- [ ] Make a separate decision to submit; this roadmap does not commit the project to a public launch.
