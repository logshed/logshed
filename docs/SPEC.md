# Technical Specification: LogShed

LogShed is a lightweight, single-container, host-independent log aggregation, search, and AI-assisted troubleshooting application designed for home servers and self-hosted environments.

To provide clear domain boundaries and keep token consumption manageable for AI coding assistants and developers, the complete technical specification is partitioned into modular domain documents under [`docs/spec/`](docs/spec/).

---

## Specification Directory & Navigation

| Section | Domain Module | Key Technical Topics Covered |
| :--- | :--- | :--- |
| **1. Architecture & Workers** | [`docs/spec/architecture.md`](docs/spec/architecture.md) | Single-process model, 9 supervised background tasks, failure isolation, privilege reduction (`gosu`), configuration primitives, three-tier resolution hierarchy, and 12 advanced runtime settings. |
| **2. Database & Storage** | [`docs/spec/database.md`](docs/spec/database.md) | SQLite WAL mode, relational schema DDL, FTS5 external content table, migrations v1 to v3 (`PRAGMA user_version`), conditional FTS sync triggers, thread-local connection model, retention pruning, and storage metrics. |
| **3. Ingestion Pipeline** | [`docs/spec/pipeline.md`](docs/spec/pipeline.md) | Syslog listener (RFC 3164, 5424, 6587 TCP/UDP), Docker tailing via `httpx`, multiline assembler, bounded in-memory buffer (`QueueConsumer`), batch persistence, live SSE dispatch, and drop rule evaluation order. |
| **4. AI Analysis Engine** | [`docs/spec/ai-engine.md`](docs/spec/ai-engine.md) | On-demand diagnosis and automated alert enrichment, provider integration (Gemini, Claude, OpenAI), dynamic model discovery (`ModelRefreshWorker`), prompt templates, thinking token budgets, and credential redaction (`redactor.py`). |
| **5. Security & Auth** | [`docs/spec/security.md`](docs/spec/security.md) | Native password authentication (Argon2id), signed HTTP-only `SameSite=Lax` session cookies, Bearer API tokens, scope-based permissions, CSRF protection (`X-Requested-With`), Fernet secrets encryption at rest, sliding rate limiters, and CLI admin rescue command. |
| **6. API Contracts** | [`docs/spec/api.md`](docs/spec/api.md) | Route versioning (`/api/*` and `/api/v1/*`), pagination contracts, internal web UI endpoints, and scoped external programmatic v1 routes for automation, maintenance control, and AI tools. |
| **7. Frontend Architecture** | [`docs/spec/frontend.md`](docs/spec/frontend.md) | React 19 SPA, navigation routes, TanStack Virtual (50k+ lines), real-time SSE streaming with scroll pause detection, RFC severity tinting, FTS5 query builder, facet bars, slide-over drawers, modals, and dark theme styling. |
| **8. Deployment & Container** | [`docs/spec/deployment.md`](docs/spec/deployment.md) | Multi-stage Docker build, non-root `appuser` with dynamic `PUID`/`PGID` and Docker socket GID resolution via `entrypoint.sh`, volume mounts, network ports, health checks, Unraid templates, and multi-host topologies. |

---

## AI Assistant & Developer Guidelines

When working on specific LogShed tasks, avoid loading the entire specification corpus into conversation context. Instead, consult the targeted module:

- **Database schema, migrations, or FTS5 indexing:** Read [`docs/spec/database.md`](docs/spec/database.md).
- **Background workers or configuration parameters:** Read [`docs/spec/architecture.md`](docs/spec/architecture.md).
- **Log parsing, syslog framing, or Docker ingestion:** Read [`docs/spec/pipeline.md`](docs/spec/pipeline.md).
- **AI prompts, LLM providers, or thinking budgets:** Read [`docs/spec/ai-engine.md`](docs/spec/ai-engine.md).
- **Authentication, cookies, or encryption:** Read [`docs/spec/security.md`](docs/spec/security.md).
- **REST endpoints, query parameters, or response models:** Read [`docs/spec/api.md`](docs/spec/api.md).
- **UI components, virtual lists, or client state:** Read [`docs/spec/frontend.md`](docs/spec/frontend.md).
- **Dockerfile, entrypoint permissions, or container deployment:** Read [`docs/spec/deployment.md`](docs/spec/deployment.md).
