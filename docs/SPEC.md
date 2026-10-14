# Technical Specification: LogShed

LogShed is a lightweight, single-container, host-independent log aggregation, search, and AI-assisted troubleshooting application designed for home servers and self-hosted environments.

To provide clear domain boundaries and keep token consumption manageable for AI coding assistants and developers, the complete technical specification is partitioned into modular domain documents under [`docs/spec/`](docs/spec/).

---

## Specification Directory & Navigation

| Section | Domain Module | Key Technical Topics Covered |
| :--- | :--- | :--- |
| **1. Architecture & Workers** | [`docs/spec/architecture.md`](docs/spec/architecture.md) | Single-process model, 9 supervised background tasks, failure isolation, privilege reduction (`gosu`), configuration primitives, three-tier resolution hierarchy, and 12 advanced runtime settings. |
| **2. Database & Storage** | [`docs/spec/database.md`](docs/spec/database.md) | SQLite WAL mode, relational schema DDL, FTS5 external content table, migrations v1 to v3 (`PRAGMA user_version`), conditional FTS sync triggers, thread-local connection model, retention pruning, and storage metrics. |
| **3. Ingestion Pipeline** | [`docs/spec/pipeline.md`](docs/spec/pipeline.md) | Syslog listener (RFC 3164, 5424, 6587 TCP/UDP), Docker tailing via `httpx`, multiline assembler, bounded in-memory buffer (`QueueConsumer`), batch persistence, live SSE dispatch, drop rule evaluation order, and internal application log capture (`InternalLogHandler`). |
| **4. AI Analysis Engine** | [`docs/spec/ai-engine.md`](docs/spec/ai-engine.md) | On-demand diagnosis, automated alert enrichment and follow-up conversations, provider integration (Gemini, Claude, OpenAI), dynamic model discovery (`ModelRefreshWorker`), prompt templates, thinking token budgets, and credential redaction (`redactor.py`). |
| **5. Security & Auth** | [`docs/spec/security.md`](docs/spec/security.md) | Native password authentication (Argon2id), signed HTTP-only `SameSite=Lax` session cookies, Bearer API tokens, scope-based permissions, Model Context Protocol (MCP) authentication, CSRF protection (`X-Requested-With`), Fernet secrets encryption at rest, sliding rate limiters, and CLI admin rescue command. |
| **6. API Contracts** | [`docs/spec/api.md`](docs/spec/api.md) | Route versioning (`/api/*` and `/api/v1/*`), pagination contracts, internal web UI endpoints, scoped external programmatic v1 routes for automation, and Model Context Protocol (MCP) SSE and JSON-RPC endpoints. |
| **7. Frontend Architecture** | [`docs/spec/frontend.md`](docs/spec/frontend.md) | React 19 SPA, navigation routes, TanStack Virtual (50k+ lines), real-time SSE streaming with scroll pause detection, RFC severity tinting, FTS5 query builder, facet bars, slide-over drawers, modals, API & MCP Access management with collapsible client configurations, and dark theme styling. |
| **8. Deployment & Container** | [`docs/spec/deployment.md`](docs/spec/deployment.md) | Multi-stage Docker build, non-root `appuser` with dynamic `PUID`/`PGID` and Docker socket GID resolution via `entrypoint.sh`, volume mounts, network ports, health checks, Unraid templates, and multi-host topologies. |

---

## Developer Workflow & Test Conventions

This specification describes what LogShed does. How to work on it lives in [`AGENTS.md`](../AGENTS.md) at the repository root: build and test commands, coding standards, dependency guardrails, and the backend test suite conventions (shared app fixture, low-cost Argon2id hashing, the `e2e` marker). Read it before changing code or tests.
