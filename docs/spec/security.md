# Specification: Security & Authentication

Parent document: [docs/SPEC.md](../../docs/SPEC.md)

## 1. Overview
LogShed provides self-contained native authentication and protection controls without external authentication providers or identity brokers. All security primitives execute locally within the application container.

## 2. Password Hashing & Verification
* **Algorithm & Library:** `argon2id` via `argon2-cffi` (single library - do not also add `pwdlib`).
* **Argon2id Parameters:**
  * Algorithm variant: Argon2id (RFC 9106)
  * Version: `v=19` (`0x13`)
  * Memory cost: `m=65536` (64 MiB)
  * Time cost: `t=3` iterations
  * Parallelism: `p=4` threads / lanes
  * Hash format string: `$argon2id$v=19$m=65536,t=3,p=4$...`
* **Non-Blocking Execution:** Verification is offloaded to worker threads via `asyncio.to_thread` to keep authentication from blocking the event loop.
* **Timing-Attack Resistance:** Constant-time comparison path using dummy hash verification (`_DUMMY_PASSWORD_HASH`) on non-existent records or invalid lookup attempts.

## 3. First-Run Setup Lockout
* The setup endpoint `/api/auth/setup` is accessible only when the `admin_auth` database table is empty (`COUNT(*) == 0`).
* Once an admin record exists, `/api/auth/setup` immediately returns `403 Forbidden` (`detail: "Admin account has already been set up."`).
* Subsequent credential updates require authenticated password rotation (`POST /api/auth/password`) or the CLI rescue utility.

## 4. Session Security & Cookie Handling
* **Session Cookie Architecture:** Cryptographically signed, HTTP-only, `SameSite=Lax` session cookie named `session`, set at login and at first-run setup.
* **Browser Storage Baseline:** No JWTs or sensitive authentication tokens stored in browser `localStorage` or `sessionStorage`.
* **Payload Structure & Encryption:** Session tokens contain user ID, issue timestamp (`iat`), expiration timestamp (`exp`), and issuer claim (`iss="logshed"`). Tokens are encrypted using Fernet (AES-128-CBC + HMAC-SHA256).
* **Session Lifetime:** Default duration is 7 days (604,800 seconds).
* **Cookie Flags:**
  * `HttpOnly`: Always enabled (`True`) to block client-side JavaScript access.
  * `SameSite`: `Lax` to prevent cross-site leaks while supporting top-level navigation.
  * `Path`: `/`
  * `Secure`: Automatically set when requests arrive over HTTPS, when forwarded through trusted reverse proxies (`X-Forwarded-Proto: https`), or when enforced via the `cookie_secure` configuration setting (`COOKIE_SECURE=true`).

## 5. Cross-Site Request Forgery (CSRF) Protection
* **Header Requirement:** Mutating API endpoints (`POST`, `PUT`, `DELETE`, `PATCH`) under `/api/` require the custom `X-Requested-With` header (e.g. `X-Requested-With: XMLHttpRequest`).
* **Validation Middleware:** Inbound mutating requests lacking the `X-Requested-With` header are rejected immediately with `403 Forbidden` (`detail: "Forbidden: missing required X-Requested-With header."`).
* **Exemptions:** Safe read-only methods (`GET`, `HEAD`, `OPTIONS`), container health check endpoints (`/api/health`), Server-Sent Events streams (`/api/logs/stream`), requests bearing an `Authorization: Bearer` header, and the MCP transport routes under `/api/v1/mcp` (including `/sse` and `/messages`). The MCP routes are exempt because they authenticate by API token only (Bearer or raw `Authorization` value; `?token=` only when `MCP_ALLOW_QUERY_TOKEN=true`) and never accept the session cookie, so there is no ambient browser credential for a cross-site request to abuse. Requests without a valid token are rejected with `401` (the only exception is `POST /api/v1/mcp/messages`, which also accepts the unguessable 128-bit `session_id` of an already authenticated SSE session, but only while the token that opened that session is still valid; the message counts toward that token's rate limit). There is no other path-based exemption for `/api/v1/*`: cookie-authenticated mutating calls to those paths (for example `POST /api/v1/maintenance/disable` from the Web UI) must also send `X-Requested-With`.

## 6. Rate Limiting
* **Authentication Endpoint:** In-memory limits on `/api/auth/login`: 5 failed attempts per client address per minute (IPv6 clients share one limit per /64), at most 2 attempts from one address checked at the same time, and a global limit of 30 failed attempts per minute across all addresses. Each attempt is counted before the password is checked, so parallel guesses cannot get around the limit. Exceeding a limit returns `429 Too Many Requests`. Password checks run on their own two-thread pool so a login burst does not stall database reads. The global limit is a deliberate lockout risk: an attacker using six or more addresses can keep it tripped and block new admin logins while the attack lasts (existing sessions are unaffected).
* **API Token Sliding Window:** 120 requests per minute sliding window per token for programmatic external endpoints.
* **Rate Limit Headers:** Token-authenticated REST and ingestion responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` (seconds until the oldest counted request leaves the window). A `429` response also carries `Retry-After` (whole seconds). MCP responses carry the `429` headers only.
* **Heavy Operations:** Prune and vacuum share one in-progress lock across the web UI, external API and the daily `PruneWorker`; a concurrent attempt returns `409 Conflict` (the worker retries after 60 seconds). The lock stays held until the operation actually finishes, even if the requesting client disconnects mid-run. The external API 5-minute cooldown starts only after a successful run and is measured with a monotonic clock. Operation failures are logged server-side and return a generic message.
* **High-Throughput Ingestion Endpoint:** 6,000 requests per minute sliding window per token for edge log shippers on `POST /api/v1/ingest`, backed by a 60-second in-memory token verification cache.
* **AI Diagnosis Endpoints:** In-memory sliding window on `/api/ai/diagnose` and `/api/ai/diagnose/stream` (10 requests per minute per user/session) to prevent runaway LLM consumption.

## 7. Secret Key Generation, Key Derivation & Outbound Transport Security
* **Secrets at Rest:** API keys, sensitive settings, and remote Docker mutual TLS client private keys stored at rest are encrypted with `cryptography.fernet`. Remote Docker client private keys (`client_key`) are strictly write-only in the REST API: they are never returned in plain text in GET or PUT responses (which return `has_client_key: bool`), and are stripped from all logs and external AI prompt dispatches. Ephemeral TLS contexts safely load decrypted client credentials into OpenSSL memory using temporary files with `0600` permissions and unlink them immediately.
* **SSRF Policy & Outbound Endpoint Validation:**
  * **Docker Daemon Collection:** As a self-hosted log management platform, LogShed connects to local Unix sockets (`unix://`) and private network endpoints (`tcp://`, `http://`, `https://`) to collect container logs from local or remote homelab Docker daemons. All configured daemon URLs are validated to require valid schemes (`unix://`, `tcp://`, `http://`, `https://`), and endpoint management is restricted to authenticated admin sessions with CSRF validation.
  * **Docker TLS and URL checks:** Each endpoint has a `verify_hostname` toggle (default on, including endpoints created before it existed): the TLS handshake checks that the daemon certificate matches the host name in the URL, so a host reached by an IP address missing from the certificate fails until the toggle is turned off. TLS to a host that is not on this machine requires a CA certificate. Link-local and cloud metadata addresses are refused. A `unix://` URL must end in `docker.sock`, be the `DOCKER_HOST` socket, or be listed in `DOCKER_ALLOWED_SOCKETS` (comma-separated); stored URLs are not re-checked at startup. The connection test reuses stored client keys only for the host's saved URL and returns generic error text.
  * **Notification Webhooks:** Governed by the `allow_private_notification_targets` runtime setting (default `True`), which allows dispatching notifications to local LAN IP addresses (RFC 1918) and internal domain names while permitting administrators in high-security environments to restrict outbound webhooks to public domains.  The address actually connected to is checked against the blocked ranges at connect time (stopping DNS rebinding), and self-hosted hosts of plugins such as `notica://` are validated. With `HTTP_PROXY` or `HTTPS_PROXY` set, that check sees the proxy address rather than the destination: a proxy on a blocked range (including loopback) is refused, and the destination gets only the static check.
* **Master Encryption Key Generation:**
  * Master encryption key stored at `/data/.secret_key` (generated automatically on first boot with `0600` permissions).
  * Persisted using direct file descriptors and restrictive Unix file creation masks.
* **Environment Override & Derivation:**
  * Master key can be supplied via the `LOGSHED_SECRET_KEY` environment variable.
  * If an arbitrary string is supplied instead of a 32-byte urlsafe-base64 key, key derivation computes a 32-byte SHA-256 digest and base64-urlsafe encodes the result (`_derive_fernet_key`).

## 8. External API Tokens & Permissions
* **Bearer Token Format:** Raw token string formatted as `ls_live_<40 hex characters>` (entropy 160 bits via `secrets.token_hex(20)`).
* **Storage & Hashing:** Stored exclusively as SHA-256 hash (`token_hash`) in the `api_tokens` database table with unique index. The raw secret is returned to the caller exactly once upon creation and cannot be retrieved again.
* **Masked Display Identifiers:** In UI and non-sensitive responses, tokens are identified via prefix and suffix format: `ls_live_<first 4 chars>...<last 4 chars>`.
* **Scope-Based Permissions:**
  * Supported individual scopes:
    - `logs:write`: Push log batches through the high-throughput HTTP ingestion endpoint (`POST /api/v1/ingest`).
    - `maintenance:write`: Start and terminate on-demand maintenance windows and sessions (`POST /api/v1/maintenance/enable`, `POST /api/v1/maintenance/disable`).
    - `maintenance:read`: Inspect active maintenance window status and session details (`GET /api/v1/maintenance/status`).
    - `logs:read`: Execute FTS5 log queries and counts, fetch surrounding log context, and inspect source/application facets (`GET /api/v1/logs`, `GET /api/v1/logs/count`, `GET /api/v1/logs/{id}/context`, `GET /api/v1/logs/facets`).
    - `alerts:read`: Review incident history firings and trigger audit summaries (`GET /api/v1/alerts/history`).
    - `system:read`: Read telemetry metrics, sliding alert state, and cached error distributions (`GET /api/v1/system/metrics`).
    - `system:write`: Trigger storage retention pruning and database compaction (`POST /api/v1/system/prune`, `POST /api/v1/system/vacuum`).
    - `*`: Full administrative wildcard matching all existing and future programmatic endpoints.
  * **Scope Validation on Creation:** `POST /api/tokens` requires between 1 and 20 scopes. Each entry must be `*`, one of the individual scopes above, or a namespace wildcard `<category>:*` for a known category (`logs`, `alerts`, `system`, `maintenance`). Unknown entries are rejected with `422` and duplicates are removed. A successful create returns `200` with the raw token. The allow-list is defined in `backend/app/core/api_tokens.py`.
  * **Verification Order:** All Bearer verification paths (standard routes, `POST /api/v1/ingest`, MCP) share one function: hash lookup, expiry, scope check, rate limit, throttled `last_used_at` update. Revoking a token also clears its rate limit window, `last_used_at` throttle entry, and ingestion cache entry.
  * **Preset Bundles:**
    - **Maintenance Only** (`["maintenance:write", "maintenance:read"]`): Intended for home automation runners, Proxmox update hooks, or backup scripts that silence notifications or drop errors during host restarts.
    - **Edge Shipper / Ingest** (`["logs:write"]`): Intended for external log forwarders (Vector, Fluent Bit in Kubernetes/k3s, or custom scripts) pushing log batches directly into LogShed over HTTP.
    - **Read Only** (`["logs:read", "alerts:read", "system:read"]`): Intended for external monitoring dashboards, status monitors, or telemetry aggregators that need log and metric visibility without modification rights.
    - **AI Assistant / MCP (Read Only)** (`["logs:read", "alerts:read", "system:read"]`, the default preset in the token dialog): Intended for Model Context Protocol (MCP) clients, Claude Desktop, or Cursor agents that investigate anomalies and query log context without changing anything.
    - **AI Assistant / MCP + Maintenance** (`["logs:read", "alerts:read", "system:read", "maintenance:write"]`): As above, and the agent may also start and end maintenance windows. Tokens created with the earlier four-scope bundle keep working.
    - **Full Access** (`["*"]`): Full administrative access across all endpoints, including storage vacuum and retention pruning.
* **Throttled Last Used Tracking:** Updates to `last_used_at` in SQLite are throttled to at most once per 5 minutes per token to avoid database write amplification.
* **Response Redaction:** All external API responses that carry log or AI diagnosis text (`/api/v1/logs`, `/api/v1/logs/{id}/context`, `/api/v1/alerts/history`, and every MCP tool) pass `message`, `raw`, alert samples, and AI summaries through `redactor.py` before leaving the server. Internal UI endpoints stay unredacted for authenticated sessions. Redaction is best-effort and may not catch every secret.
* **Exempt from CSRF:** Bearer token requests are cryptographically bound to HTTP Authorization headers and immune to browser CSRF.
* **Model Context Protocol (MCP) Authentication:**
  * Streamable HTTP connections (`/mcp` and `/api/v1/mcp`), network SSE connections (`GET /api/v1/mcp/sse`), and JSON-RPC message calls (`POST /api/v1/mcp/messages`) require authentication with a valid token that has `logs:read` (or a matching namespace wildcard, or `*`); other tokens get `403`. Each tool and resource then checks its own scope (`logs:read`, `alerts:read`, `system:read` or `maintenance:write`) and returns a tool error when it is missing. `drop_errors` maintenance over MCP requires a `target_app` or `target_host`. Legacy SSE sessions are bound to the token that opened them; revoking or expiring the token ends them.
  * Tokens can be supplied as `Authorization: Bearer <token>` or as a raw token in the `Authorization` header. A `?token=` query parameter is refused unless `MCP_ALLOW_QUERY_TOKEN=true`, because URLs are written to proxy access logs; when enabled, a deprecation warning is logged. `mcp-proxy` reads the token from its `API_ACCESS_TOKEN` environment variable and sends it as a Bearer header.
  * Sessions establish verified mapping between client streams and message dispatchers with automatic expiration and cleanup.

## 9. CLI Password Recovery
* Single-command rescue executable inside container. Without `--password` it prompts for the new password twice (8 to 128 characters, input hidden). `--password` still works but prints a warning, because the value is visible in shell history and process listings:
```bash
docker exec -it <container> python -m app.cli reset-admin
```

## 10. Response Headers & Cross-Origin Requests
* **Content-Security-Policy:** Every response carries one. `/docs` and `/redoc` get a separate policy that allows what the API documentation pages need.
* **Caching:** API responses are sent with `Cache-Control: no-store`. CSRF rejections carry the security headers too.
* **CORS:** Cross-origin requests are allowed only for origins listed in `CORS_ORIGINS` (empty in production; the localhost development origins are added only when `ENVIRONMENT=development`, and `DEBUG` alone does not add them). A `*` entry is ignored and logged as an error, because credentialed requests would let any website act as a logged-in user. Allowed request headers are `Content-Type`, `X-Requested-With` and `Authorization`.
* **Version endpoint:** `GET /api/system/version` returns no version string and starts no update check for anonymous callers; a failed GHCR update check is cached and retried at most once a minute.
* **API documentation:** `ENABLE_API_DOCS=false` turns off `/docs`, `/redoc` and `/openapi.json`.
* **Trusted proxies:** `trust_docker_proxies` trusts every address in `172.16.0.0/12` to set `X-Forwarded-For`. If clients can reach LogShed through the Docker userland proxy or hairpin NAT, they appear as the bridge gateway and can spoof their address to avoid per-address login limits (the global failed-login limit still applies). Prefer `TRUSTED_PROXIES=<reverse proxy container IP>`. LogShed logs a warning at startup while `trust_docker_proxies` is in effect.
* **External maintenance routes:** A request with an `Authorization` header is checked only as an API token and never falls back to the session cookie; the `Bearer` scheme is matched in any letter case.
