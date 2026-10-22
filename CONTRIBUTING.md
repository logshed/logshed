# Contributing to LogShed

Thank you for your interest in contributing to LogShed! Whether you are fixing a bug, improving documentation, or proposing a feature, these guidelines will help you get started.

## Project Scope

LogShed is a self-hosted log aggregator and syslog server built for home labs and personal servers. It runs as a single container with zero external database dependencies.

Before starting work on a large new feature, please open a [Feature Request](https://github.com/logshed/logshed/issues/new?template=feature_request.yml) first so we can confirm it fits the project scope.

## Local Development Setup

Prerequisites: **Python 3.12+** and **Node.js 20+**.

```bash
# 1. Clone the repository
git clone https://github.com/logshed/logshed.git
cd logshed

# 2. Set up the Python virtual environment and install backend dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt   # app and test dependencies

# 3. Install frontend dependencies, run tests, and build static assets
cd frontend
npm install
npm test
npm run build
cd ..

# 4. Run the backend test suite
.venv/bin/pytest backend/tests/ -v

# 5. Start the local development server
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8080 --reload
```

## Architecture & Coding Guidelines

- **Single-Process Model:** LogShed runs a single OS process (`--workers 1`) with supervised `asyncio` background tasks. In-memory ingestion queues, rate limiters, and drop counters are process-local.
- **Standard Library First:** Database access uses Python's built-in `sqlite3` (WAL mode + FTS5) with `asyncio.to_thread()`. Do not introduce ORMs (`sqlalchemy`), async SQLite wrappers (`aiosqlite`), or Docker SDK packages (`docker` / `aiodocker`).
- **Dependencies:** Do not add new third-party Python or npm dependencies without discussing them in an issue first.
- **Testing:** New or changed backend code should include unit or integration tests in `backend/tests/`. Tests must run offline and deterministically without real sleep delays; tests that require live Docker builds or external services must be marked with `@pytest.mark.e2e`.
- **Style & Copy:** Keep UI text and documentation clear and concise. Avoid em dashes (` - ` with spaces instead) and prefer words with identical US and British English spellings in user-facing text.

## Submitting a Pull Request

1. Create a new branch from `main`.
2. Make your changes and add tests that cover what you added or changed.
3. Run both test suites (`.venv/bin/pytest backend/tests/ -v` and `cd frontend && npm test`) and build the frontend (`cd frontend && npm run build`).
4. Open a Pull Request against `main` and fill out the PR template.
