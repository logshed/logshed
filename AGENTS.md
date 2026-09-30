# LogShed - LLM Instructions

## Build & Test Commands
- Run backend tests: `.venv/bin/pytest backend/tests/ -v`
- Run single test file: `.venv/bin/pytest backend/tests/test_schema.py -v`
- Run frontend tests: `cd frontend && npm test`
- Build frontend: `cd frontend && npm run build`
- Python Environment: Always execute Python tools using `.venv/bin/python` or `.venv/bin/pytest` (or prefix shell commands with `source .venv/bin/activate && ...`). Never use the global system Python.
- **CLI & Standard Tools First (Strictly No Ad-Hoc Scripting):**
  * **STRICT PROHIBITION on `python -c '...'` or Ad-Hoc Scripts:** Never write or execute ad-hoc scripts (inline `python -c`, Node, or temporary Python scripts in scratch/workspace) for tasks solvable with standard CLI utilities (`grep`, `git`, `find`, `sed`, `head`, `tail`, `wc`, `jq`) or agent read tools (`view_file`).
  * **File & Text Inspection:** Always use standard CLI tools or file tools:
    - Text/pattern/Unicode checks: `grep -rn <pattern>` or `git grep <pattern>` (e.g. for em dashes: `grep -rn $'\u2014' backend/ frontend/src/`).
    - Repository state: `git status`, `git diff`, `git log`.
    - Package inspection: `.venv/bin/pip show <pkg>` or `.venv/bin/pip list`.
  * **Python Execution Scope:** Python execution is strictly reserved for running existing project test suites (`.venv/bin/pytest`) and starting the project application.

## Architecture & Code Standards
- **Runtime:** Python 3.12 (`asyncio`) + FastAPI + SQLite (WAL mode + FTS5).
- **Frontend:** React + Vite + Tailwind CSS (bundled to `backend/app/static`).
- **Process Model:** Single container, **single OS process**, running multiple concurrent `asyncio` tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`) under supervisor isolation. Never run multiple uvicorn/gunicorn worker processes (`--workers 1` only) - the in-memory ingestion queue, rate limiter, and drop counters are process-local and would silently desync across separate OS processes.
- **Docker Access:** Respect `DOCKER_HOST` (supports socket or `tecnativa/docker-socket-proxy`).
- **Security Baseline:**
  * App drops privileges via `gosu` to run as a non-root user defined by `PUID` and `PGID` environment variables (defaults to 1000:1000).
  * On-demand redaction of all sensitive tokens/passwords via `redactor.py` before LLM dispatch.
  * Native Auth: Argon2id password hashing + HTTP-only SameSite=Lax session cookies.
  * Database & Migrations: SQLite (WAL mode + FTS5 external content table) versioned via `PRAGMA user_version = 2`. Asynchronous FTS5 indexing decoupled from raw ingestion via supervised `FTSIndexWorker` (target catch-up latency <= 1000ms), durable state tracking in `fts_index_state` (last_indexed_id), conditional triggers guarding against unindexed row deletions, and thread-local read connection reuse via `run_db_query` in `backend/app/api/deps.py`. No external database servers.

## Workflow Protocol
1. Consult `docs/SPEC.md` for technical schemas, endpoints, and exact trigger definitions.
2. At the end of every phase, run the phase verification test suite.

## Dependency & Performance Guardrails
- **Zero Unapproved Dependencies:** Any package explicitly required by the core backend/frontend specs (`fastapi`, `uvicorn`, `httpx`, `argon2-cffi`, `cryptography`, `google-genai`, `openai`, `pydantic`, `@tanstack/react-virtual`, `recharts`, `apprise`) is pre-approved. Do not add `aiosqlite`. Do not add the `docker` or `aiodocker` SDKs - talk to the Docker Engine API (both `unix:///var/run/docker.sock` and `tcp://proxy:2375`) using `httpx`, with `httpx.HTTPTransport(uds=...)` for the Unix socket case.
- **Standard Library First:** For anything not already dictated by `docs/SPEC.md`, default to Python standard library modules (`sqlite3`, `json`, `dataclasses`, `pathlib`, `logging`, `typing`) before reaching for external packages. Use stdlib `sqlite3` + `asyncio.to_thread()` for all database operations.
- **No Heavyweight Tooling:** Strictly forbid data-science or heavy ORM libraries (e.g., `pandas`, `numpy`, `scipy`, `sqlalchemy`) - these are never approved, regardless of `docs/SPEC.md`.

## Typography & Formatting Guardrails
- **No Em Dashes:** Never use em dashes (the character \u2014) anywhere in the codebase, UI text, error messages, test descriptions, or documentation markdown files. Always use standard hyphens (` - `) or clean commas/parentheses instead. Avoid fancy curly quotes or typographer symbols in code strings.
- **Dialect-Neutral English (UI & Documentation):** All user-facing text (UI elements, modals, button labels, toast/error messages, tooltips, and documentation markdown files) must avoid words with divergent US and British English spellings (e.g. avoid *-ize/-ise*, *-ization/-isation*, *-or/-our*, *license/licence*, *defense/defence*).
  * Use words with invariant spellings across both dialects (e.g., use *redaction* instead of *sanitization*, *validation* instead of *standardization*, *streamlined/efficiency* instead of *optimized/optimization*, *rely on* instead of *in favor of*, *copyright terms* instead of *license details*).
  * **Exemption for Code & Technical Primitives:** This rule applies strictly to human-facing copy. Do NOT alter standard programming language keywords, SQL commands (such as SQLite `OPTIMIZE`), third-party library identifiers, HTTP headers, or protocol specs that require specific spellings.