# Specification: Security & Authentication

Parent document: [docs/SPEC.md](file:///home/ben/workspace/logshed/docs/SPEC.md)

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
* **Session Cookie Architecture:** Cryptographically signed, HTTP-only, `SameSite=Lax` session cookies (`logshed_session` / `session`).
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
* **Exemptions:** Safe read-only methods (`GET`, `HEAD`, `OPTIONS`), container health check endpoints (`/api/health`), and Server-Sent Events streams (`/api/logs/stream`).

## 6. Rate Limiting
* **Authentication Endpoint:** In-memory sliding window on `/api/auth/login` (5 failed attempts per IP per minute). Exceeding this threshold returns `429 Too Many Requests`.
* **AI Diagnosis Endpoints:** In-memory sliding window on `/api/ai/diagnose` and `/api/ai/diagnose/stream` (10 requests per minute per user/session) to prevent runaway LLM consumption.

## 7. Secret Key Generation & Key Derivation
* **Secrets at Rest:** API keys and sensitive settings stored at rest are encrypted with `cryptography.fernet`.
* **Master Encryption Key Generation:**
  * Master encryption key stored at `/data/.secret_key` (generated automatically on first boot with `0600` permissions).
  * Persisted using direct file descriptors and restrictive Unix file creation masks.
* **Environment Override & Derivation:**
  * Master key can be supplied via the `LOGSHED_SECRET_KEY` environment variable.
  * If an arbitrary string is supplied instead of a 32-byte urlsafe-base64 key, key derivation computes a 32-byte SHA-256 digest and base64-urlsafe encodes the result (`_derive_fernet_key`).

## 8. CLI Password Recovery
* Single-command rescue executable inside container (accepts `--password` or prompts securely via terminal):
```bash
python -m app.cli reset-admin [--password <new_password>]
```
