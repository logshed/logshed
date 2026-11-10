# LogShed - Instructions for AI Coding Agents

These are the project rules for any AI coding agent working in this repository (and they are a fair summary for human contributors too). Each rule says why it exists, so apply the reason to cases the rule does not name.

## Project Map
- `backend/app/api`: HTTP routes (web UI routes under `/api`, the external REST API under `/api/v1`).
- `backend/app/core`: ingest pipeline, configuration, security, redaction, outbound address checks, database migrations.
- `backend/app/services`: background workers and domain logic (alerts, AI engine, notifications, storage).
- `backend/app/collectors`: syslog server and Docker log collectors.
- `backend/app/mcp`: the MCP server.
- `frontend/src/components`, `frontend/src/api` (typed API client), `frontend/src/utils`; tests in `frontend/src/test`.
- `docs/spec/`: the technical specification, indexed by `docs/SPEC.md`. Read only the modules your task touches.

## Commands
- Python: always the project venv (`.venv/bin/python`, `.venv/bin/pytest`), never the system Python. Inspect installed packages with `.venv/bin/pip show <pkg>`, or `uv pip show <pkg>` in a venv built with `uv`.
- Backend tests: `.venv/bin/pytest backend/tests/` (one file: `.venv/bin/pytest backend/tests/test_schema.py`).
- Backend lint: `.venv/bin/ruff check backend` (rules in `ruff.toml`; CI runs it).
- Frontend tests: `cd frontend && npm test`. Build: `cd frontend && npm run build` (runs `tsc`, then bundles to `backend/app/static`).
- `test_spa_serves_index_html_for_frontend_routes` needs the frontend built first.
- Timing benchmarks (excluded by default; compare figures before and after a change on the same machine): `.venv/bin/pytest backend/tests/ -m benchmark -s`.
- End-to-end tests: `.venv/bin/pytest backend/tests/ -m e2e`. They cost money or build Docker images; read "Paid API Calls and Secrets" before running them.

## Definition of Done
- Both test suites pass, `ruff check backend` is clean and the frontend builds (commands above).
- `docs/spec/` and the user docs (`docs/*.md`, `README.md`) describe any change to what LogShed does, its API or its configuration.
- Anything users will notice has an entry in `CHANGELOG.md` under the unreleased version, in its section (`Added`, `Changed`, `Fixed`, `Security`). A change that needs action when upgrading also gets an `Upgrade Notes` entry.
- New or changed backend code has tests in `backend/tests/`; a bug fix has a test that fails without the fix.

## Paid API Calls and Secrets
- **The default test run makes no outbound calls.** `pytest.ini` excludes the `e2e` and `benchmark` markers, and the unit tests replace the AI SDKs and Docker with mocks. Keep it that way: a test that needs a live provider, Docker build or other external service is marked `@pytest.mark.e2e`.
- **`-m e2e` spends the developer's money and time.** `backend/tests/test_live_ai_models.py` calls OpenAI, Anthropic and Google Gemini with the keys in `.env` or the environment, and `TestDockerLiveContainer` in `test_deployment.py` builds a Docker image. Ask the developer before running any of them.
- **Do not assume the developer can pay for every provider.** Many contributors have keys for one provider, free tiers only, or no keys at all. Ask which providers they can use, run only those tests (`-k`), and keep to the few calls that the check needs.
- **A test without its key skips; a key without credit or quota fails** with the provider's error (for example `429`, "insufficient quota" or a billing message). That is the developer's plan, not a LogShed bug: report it, and do not change code or tests to make it pass. Overloaded providers (`503`, `504`, "high demand") are also not LogShed faults; retry later.
- **Before choosing model names for a live check or a test,** list the provider's current models (free: OpenAI `GET /v1/models` shows each model's `shutdown_date`, Anthropic `GET /v1/models`, Gemini `GET /v1beta/models`). Pick models with no shutdown date.
- **Never print, log or commit keys.** `.env` is gitignored; refer to keys by variable name only (`OPENAI_API_KEY`, `CLAUDE_API_KEY`, `GEMINI_API_KEY`).

## Backend Test Conventions
`backend/tests/conftest.py` keeps the backend suite fast (about a minute). Know these before writing or debugging tests:
- **Shared FastAPI app:** In tests, `create_app()` returns a cached instance (one per CORS origin set, `ENABLE_API_DOCS` value and built-frontend presence), because building the app costs about 0.12 s and hundreds of tests use one. Never change the returned app (routes, middleware, mounts or `app.state`); call `create_app.uncached()` when a test needs its own instance. `app.dependency_overrides` is cleared after each test.
- **Low-cost Argon2id:** `hash_password()` uses minimal Argon2id cost parameters in tests. Production-cost hashes still verify, but tests must not assert the production cost parameters on hashes made by `hash_password()`.
- **No real waiting:** Tests never sit out production timeouts or backoffs (SQLite `busy_timeout`, retry delays, batch windows). Worker delays are class attributes that tests lower (for example `QueueConsumer.RETRY_BACKOFF`, `DailyDigestWorker.CHECK_INTERVAL`); patch the value down or inject the condition. Check new slow tests with `--durations=20`.
- **Lifespan tests:** a test that runs the app lifespan uses the `free_syslog_port` fixture and `ENABLE_DOCKER=false`, and stubs `DockerCollectorPool.start` when Docker hosts exist.
- **Async mocks:** Give `AsyncMock.side_effect` an async function, never a coroutine object such as `asyncio.sleep(10)` (it is never awaited and leaks a `RuntimeWarning` into unrelated tests).
- **Timing margins:** CI runners are slower than a laptop. Give clock-based tests generous margins, or use a fake clock.

## Frontend Test Conventions
- No `act(...)` warnings on stderr: await the state updates a test causes.
- Use fake timers (`vi.useFakeTimers()`, `vi.advanceTimersByTimeAsync`) instead of real waits for debounces, retries and redirect timers, and restore real timers in a `finally`.
- Replace the `src/api/*` functions (`vi.spyOn` or `vi.mock`) rather than `fetch`; stub `fetch` only in tests of the API client or the sign-in flow.
- Tests that download a file use `src/test/downloadStubs.ts`.

## Architecture
- **Runtime:** Python 3.12 (the Docker image and CI) with `asyncio`, FastAPI and SQLite (WAL mode and FTS5); React, Vite and Tailwind CSS for the frontend, bundled into `backend/app/static`.
- **Single process:** one container, one OS process, running supervised `asyncio` tasks: `SyslogServer`, `DockerCollectorPool` (one `DockerTailer` per Docker host), `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`, `DropFilterWorker`, `MaintenanceSweepWorker`, `DailyDigestWorker` and `LoopLagMonitor` (started in `backend/app/main.py`). Never run more than one uvicorn worker (`--workers 1`): the ingest queue, rate limiters and drop counters live in process memory and would silently disagree across processes.
- **Database:** stdlib `sqlite3` through `asyncio.to_thread()`, with thread-local connections (for reads and writes) reused by `run_db_query` (`backend/app/api/deps.py`). The schema version is `PRAGMA user_version`, set by the migrations in `backend/app/core/migrations.py`. Full-text indexing runs apart from ingestion in `FTSIndexWorker` (target catch-up under 1 s), with its progress in `fts_index_state`. No external database servers.
- **Docker access:** talk to the Docker Engine API with `httpx` (`httpx.HTTPTransport(uds=...)` for the Unix socket), respecting `DOCKER_HOST` (a socket or `tecnativa/docker-socket-proxy`).
- **Security baseline:** the container drops privileges with `gosu` to the user in `PUID` and `PGID` (default 1000:1000); native auth with Argon2id password hashes and HTTP-only `SameSite=Lax` session cookies.

## Public Contracts
Users and their tools rely on these, so keep changes backward compatible: `/api/v1/*`, the MCP tool names and argument schemas, the HTTP ingest format, the syslog formats, environment variable names and `unraid-template.xml`. When a path is renamed, keep the old one as a hidden alias (`include_in_schema=False`), as the alert rule export and import routes do. The web UI routes under `/api` ship with the frontend and may change together with it.

## Migrations
- Never change a migration that has shipped in a release; add `migrate_vN` and register it in `MIGRATIONS`. Each migration and its `user_version` bump run in one transaction.
- Add a test that upgrades a database from the previous version.
- Keep the FTS5 triggers consistent with the `logs` table.

## Security Choke Points
New code goes through these, never around them:
- Text sent to an AI provider passes through `core/redactor.py`.
- Every outbound URL (notifications, Docker hosts, AI base URLs) gets the address checks in `core/net_guard.py`, and notification URLs also the notifier's URL and redirect checks.
- Patterns written by users run on the `regex` package with its per-match timeout, through `core/regex_validator.py`. LogShed's own patterns stay on `re`.
- Stored secrets are encrypted with `encrypt_value` (`core/security.py`).
- Every new endpoint requires authentication; `/api/v1` endpoints also require an API token scope.
- Mutating requests require the `X-Requested-With` CSRF header (Bearer token requests are exempt).

## Event Loop and Ingest Hot Path
- No blocking I/O on the event loop: database work goes through `asyncio.to_thread()` or `run_db_query`.
- Code that runs for every received log line runs thousands of times per second: keep it small, and measure before and after changing it (`-m benchmark`).

## Time
- Store timestamps in UTC.
- Show and read times in the server's time zone (`TZ`), not the browser's; several past bugs came from mixing the two.

## New Settings
A new environment variable also goes into `.env.example`, `unraid-template.xml`, `docs/CONFIGURATION.md` and the settings list in `docs/spec/architecture.md`.

## Dependencies
- **No new dependencies without the maintainer's approval.** Approved: everything already in `backend/requirements*.txt` and `frontend/package.json`. In particular `fastapi`, `uvicorn`, `httpx`, `argon2-cffi`, `cryptography`, `google-genai`, `openai`, `anthropic`, `mcp`, `pydantic`, `apprise`, `regex`, `@tanstack/react-virtual`, `recharts`, `lucide-react`, and the frontend dev dependencies `swagger-ui-dist` and `redoc` (the build copies their bundles to `/docs-assets/` for the self-hosted `/docs` and `/redoc` pages). `regex` is only for user-written alert and drop rule patterns, because it supports a per-match timeout.
- **Standard library first:** for anything else, reach for `sqlite3`, `json`, `dataclasses`, `pathlib`, `logging` and `typing` before an external package.
- **Never add:** `aiosqlite`, the `docker` or `aiodocker` SDKs (use `httpx`, above), ORMs such as `sqlalchemy`, or data-science libraries such as `pandas`, `numpy` and `scipy`. They are heavy for a single small container and duplicate what the code already does.

## Code, Text and Commits
- **Match the surrounding code:** naming, comment density and idiom. Ruff lints the backend; no formatter is configured, so do not run `ruff format`.
- **No plan or backlog IDs** (such as `BL-12`, `B-23`, `S-14`, `WP3` or `Item #4`) in code, comments, docstrings, test names, `describe` blocks or commit messages. They mean nothing to future readers; say what the code does instead.
- **No em dashes** (the character U+2014) anywhere: code, UI text, error messages, tests and docs. Use ` - `, commas or parentheses. Avoid curly quotes and other typographic symbols in code strings.
- **Dialect-neutral English** in everything users read (UI text, messages, tooltips, docs): avoid words spelled differently in US and British English (*-ize/-ise*, *-ization/-isation*, *-or/-our*, *license/licence*, *defense/defence*). Use words with one spelling, such as *redaction* for *sanitization*, *validation* for *standardization*, *efficiency* for *optimization*, *rely on* for *in favor of*. Code, SQL keywords (SQLite `OPTIMIZE`), library identifiers, HTTP headers and protocol terms keep their required spelling.
- **Commit messages:** one line, `Type: what is now true, in plain words` (types: `Fix`, `Feat`, `Refactor`, `Docs`, `Chore`, `Perf`, `Security`).
