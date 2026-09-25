"""
Main FastAPI application entry point for LogShed.
Configures lifespan events, CORS middleware, background ingestion workers, and API routes.
"""

import asyncio
import datetime
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional, Union

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import ai, alerts, aliases, auth, drop_rules, logs, notifications, saved_views, settings, system
from app.api.deps import run_db_query
from app.collectors.docker_collector import DockerTailer
from app.collectors.syslog import SyslogServer
from app.core.config import (
    get_all_system_settings,
    get_cors_origins,
    get_data_dir,
    get_db_path,
    get_syslog_port,
    get_syslog_max_tcp_connections,
    get_syslog_tcp_inactivity_timeout,
    get_internal_log_level,
)
from app.core.migrations import run_migrations
from app.core.pipeline import KeyedMultilineAssembler, QueueConsumer, InternalLogHandler
from app.core.security import get_or_create_master_key
from app.services.fts_indexer import FTSIndexWorker
from app.services.retention import PruneWorker
from app.services.storage_metrics import StorageMetricsWorker
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

# Silence uvicorn HTTP request access logger to prevent self-logging feedback loops
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# Module-level worker references for lifespan management
_queue_consumer: Optional[QueueConsumer] = None
_fts_worker: Optional[FTSIndexWorker] = None
_metrics_worker: Optional[StorageMetricsWorker] = None
_prune_worker: Optional[PruneWorker] = None
_syslog_server: Optional[SyslogServer] = None
_docker_tailer: Optional[DockerTailer] = None
_assembler: Optional[KeyedMultilineAssembler] = None
_internal_log_handler: Optional[InternalLogHandler] = None
_alert_evaluator: Optional[Any] = None
_background_tasks: list[asyncio.Task] = []


def get_internal_log_handler() -> Optional[InternalLogHandler]:
    """Returns the active InternalLogHandler instance, or None if uninitialized or disabled."""
    return _internal_log_handler


def configure_internal_log_handler(
    level: Optional[Union[int, str]],
    alias_cache: Optional[Any] = None,
) -> Optional[InternalLogHandler]:
    """
    Dynamically configure or disable the active internal log handler on logger 'app'.
    Accepts integer logging levels, string level names ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
    or 'DISABLED' / 'OFF' / 'NONE' / None to disable internal logging.
    """
    global _internal_log_handler
    from app.core.config import parse_internal_log_level
    parsed = None if level is None else parse_internal_log_level(level)

    app_logger = logging.getLogger("app")
    if parsed is None:
        if _internal_log_handler is not None:
            _internal_log_handler.set_internal_level(None)
            try:
                app_logger.removeHandler(_internal_log_handler)
            except Exception:
                pass
            _internal_log_handler = None
        app_logger.setLevel(logging.NOTSET)
        return None

    if _internal_log_handler is None:
        _internal_log_handler = InternalLogHandler(level=parsed, alias_cache=alias_cache)
        if _internal_log_handler not in app_logger.handlers:
            app_logger.addHandler(_internal_log_handler)
    else:
        _internal_log_handler.set_internal_level(parsed)
        if alias_cache is not None:
            _internal_log_handler.set_alias_cache(alias_cache)
        if _internal_log_handler not in app_logger.handlers:
            app_logger.addHandler(_internal_log_handler)

    # Permit DEBUG or INFO records to propagate through logger 'app' to handlers
    # if a lower threshold is explicitly requested. Never raise app_logger's level to
    # WARNING or ERROR as that would suppress normal informational console logs from LogShed.
    if parsed < logging.WARNING:
        app_logger.setLevel(parsed)
    else:
        app_logger.setLevel(logging.NOTSET)

    return _internal_log_handler



async def _supervise_worker(coro_fn, name: str, *args, **kwargs) -> None:
    """
    Supervisor wrapper running a worker coroutine with exception isolation
    and exponential backoff restart without crashing the event loop (docs/SPEC.md §1).
    """
    backoff = 1.0
    while True:
        try:
            await coro_fn(*args, **kwargs)
            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker '{name}' crashed with error: {e}. Restarting in {backoff:.1f}s...")
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * 2.0, 60.0)


async def _model_refresh_worker(db_path) -> None:
    """
    Background worker that periodically refreshes available AI models
    every 12 hours if an API key is configured.
    """
    from app.services.ai_engine import fetch_available_models

    while True:
        try:
            # Wait 60 seconds after startup before initial discovery check
            await asyncio.sleep(60)

            settings = await run_db_query(get_all_system_settings, custom_db_path=Path(db_path))
            provider = (settings.get("ai_provider") or "gemini").lower()
            api_key = settings.get("ai_api_key", "").strip()
            base_url = settings.get("ai_base_url")

            if (provider == "openai_compatible") or api_key:
                try:
                    discovered = await fetch_available_models(provider, api_key=api_key or None, base_url=base_url)
                    if discovered:
                        def _save(conn):
                            cursor = conn.cursor()
                            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                            cursor.execute(
                                """
                                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                                VALUES (?, ?, ?, 0)
                                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                                """,
                                (f"ai_models_cache_{provider}", json.dumps(discovered), now_iso),
                            )

                        await run_db_query(_save, custom_db_path=Path(db_path))
                except Exception as e:
                    logger.debug(f"Background model refresh for {provider} skipped or failed: {e}")

            # Sleep 12 hours before next background refresh check
            await asyncio.sleep(12 * 3600)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Error in model_refresh_worker: {e}")
            await asyncio.sleep(3600)


async def _drop_filter_flush_worker() -> None:
    """
    Background worker that periodically flushes in-memory drop counts
    to SQLite every 30 seconds to minimize write contention.
    """
    from app.services.drop_filter import get_drop_filter

    while True:
        try:
            await asyncio.sleep(30)
            await asyncio.to_thread(get_drop_filter().flush_counts)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Error flushing drop counts: {e}")
            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Sets up database schema, master encryption keys, and starts background workers.
    """
    global _queue_consumer, _fts_worker, _metrics_worker, _prune_worker, _syslog_server, _docker_tailer, _assembler, _alert_evaluator, _background_tasks

    db_path = get_db_path()
    logger.info(f"Setting up LogShed database at {db_path}...")

    # 1. Run migrations and set up master key
    await asyncio.to_thread(run_migrations, db_path)
    await asyncio.to_thread(get_or_create_master_key)

    # Ensure user presets directories exist in DATA_DIR
    for preset_sub in ("alerts", "drops"):
        (Path(get_data_dir()) / "presets" / preset_sub).mkdir(parents=True, exist_ok=True)

    # Initialize in-memory drop filter cache
    from app.services.drop_filter import init_drop_filter
    init_drop_filter(db_path)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_drop_filter_flush_worker, "DropFilterWorker")))

    # Initialize in-memory alert evaluator
    from app.services.alert_evaluator import init_alert_evaluator
    _alert_evaluator = init_alert_evaluator(db_path)

    # 2. Shared KeyedMultilineAssembler for all collectors
    _assembler = KeyedMultilineAssembler()

    # 3. Start FTSIndexWorker and QueueConsumer
    _fts_worker = FTSIndexWorker(db_path)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_fts_worker.run, "FTSIndexWorker")))

    _queue_consumer = QueueConsumer(db_path, fts_indexer=_fts_worker, alert_evaluator=_alert_evaluator)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_queue_consumer.run, "QueueConsumer")))

    # 4. Start StorageMetricsWorker
    _metrics_worker = StorageMetricsWorker(db_path)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_metrics_worker.run, "StorageMetricsWorker")))

    # 5. Start PruneWorker (runs automated daily retention pruning)
    _prune_worker = PruneWorker(db_path)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_prune_worker.run, "PruneWorker")))

    # 6. Start ModelRefreshWorker (periodically updates available AI models)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_model_refresh_worker, "ModelRefreshWorker", db_path)))

    # 7. Start Syslog Server (optional / non-fatal in dev/test)
    try:
        syslog_port = get_syslog_port()
        _syslog_server = SyslogServer(
            assembler=_assembler,
            db_path=db_path,
            host="0.0.0.0",
            port=syslog_port,
            max_tcp_connections=get_syslog_max_tcp_connections(),
            tcp_inactivity_timeout=get_syslog_tcp_inactivity_timeout(),
        )
        _background_tasks.append(asyncio.create_task(_supervise_worker(_syslog_server.start, "SyslogServer")))
        logger.info(f"SyslogServer listener started on port {syslog_port}.")
    except Exception as e:
        logger.warning(f"SyslogServer could not be started: {e}")

    # 7. Start Docker Tailer (optional / non-fatal if Docker socket is not present)
    try:
        shared_cache = _syslog_server.alias_cache if _syslog_server else None
        _docker_tailer = DockerTailer(assembler=_assembler, db_path=db_path, alias_cache=shared_cache)
        _background_tasks.append(asyncio.create_task(_supervise_worker(_docker_tailer.run, "DockerTailer")))
    except Exception as e:
        logger.warning(f"DockerTailer could not be started: {e}")

    # 8. Attach internal log handler so application warnings and errors appear in LogShed
    persisted_level = None
    try:
        import sqlite3

        def _read_persisted_level(conn):
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = 'internal_log_level'")
            row = cursor.fetchone()
            if row:
                return row["value"] if isinstance(row, sqlite3.Row) else row[0]
            return None

        persisted_level = await run_db_query(_read_persisted_level, custom_db_path=Path(db_path))
    except Exception:
        pass

    shared_cache = _syslog_server.alias_cache if _syslog_server else None
    if shared_cache is None and _docker_tailer and hasattr(_docker_tailer, "alias_cache"):
        shared_cache = _docker_tailer.alias_cache
    if shared_cache is None:
        from app.collectors.syslog import AliasCache
        shared_cache = AliasCache(db_path)

    active_level = persisted_level if persisted_level is not None else get_internal_log_level()
    _internal_log_handler = configure_internal_log_handler(active_level, alias_cache=shared_cache)
    if _internal_log_handler is not None and not _internal_log_handler.is_disabled:
        logger.info(f"InternalLogHandler attached at level {logging.getLevelName(_internal_log_handler.level)}.")
    else:
        logger.info("InternalLogHandler disabled by configuration.")

    yield

    # Shutdown sequence
    logger.info("Shutting down background workers...")
    if _internal_log_handler is not None:
        try:
            logging.getLogger("app").removeHandler(_internal_log_handler)
        except Exception:
            pass
        _internal_log_handler = None
    if _docker_tailer:
        try:
            await _docker_tailer.stop()
        except Exception as e:
            logger.warning(f"Error stopping DockerTailer: {e}")
    if _syslog_server:
        try:
            await _syslog_server.stop()
        except Exception as e:
            logger.warning(f"Error stopping SyslogServer: {e}")
    if _assembler:
        try:
            await _assembler.flush_all()
        except Exception as e:
            logger.warning(f"Error flushing multiline assembler: {e}")
    if _queue_consumer:
        try:
            await _queue_consumer.stop()
        except Exception as e:
            logger.error(f"Error stopping QueueConsumer: {e}")
    if _fts_worker:
        try:
            await _fts_worker.stop()
        except Exception as e:
            logger.error(f"Error stopping FTSIndexWorker: {e}")
    if _prune_worker:
        try:
            await _prune_worker.stop()
        except Exception as e:
            logger.warning(f"Error stopping PruneWorker: {e}")
    if _metrics_worker:
        try:
            await _metrics_worker.stop()
        except Exception as e:
            logger.warning(f"Error stopping StorageMetricsWorker: {e}")
    if _alert_evaluator:
        try:
            await _alert_evaluator.stop()
        except Exception as e:
            logger.warning(f"Error stopping AlertEvaluator: {e}")

    try:
        from app.services.notifier import shutdown_notifier_executor
        shutdown_notifier_executor(wait=True)
    except Exception as e:
        logger.warning(f"Error shutting down notification executor: {e}")

    try:
        from app.services.drop_filter import get_drop_filter
        get_drop_filter().flush_counts()
    except Exception as e:
        logger.warning(f"Error flushing drop filter counts during shutdown: {e}")

    for task in _background_tasks:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _background_tasks.clear()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Factory creating and configuring the FastAPI application instance."""
    app = FastAPI(
        title="LogShed",
        description="Unified syslog and Docker container log aggregator with on-demand AI analysis.",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Format clean, human-readable error messages without dumping massive raw input payloads
        messages = []
        for err in exc.errors():
            msg = err.get("msg", "Validation error")
            loc = " -> ".join(str(l) for l in err.get("loc", []) if l != "body")
            messages.append(f"{loc}: {msg}" if loc else msg)
        clean_detail = "; ".join(messages) if messages else "Request validation failed."
        return JSONResponse(
            status_code=422,
            content={"detail": clean_detail},
        )

    # FTS search consistency middleware: ensures pending unindexed logs are caught up
    # prior to executing full-text search queries.
    @app.middleware("http")
    async def fts_search_consistency(request: Request, call_next):
        if request.url.path == "/api/logs" and request.query_params.get("query"):
            db_path = get_db_path()
            from app.services.fts_indexer import index_pending_logs
            try:
                await asyncio.to_thread(index_pending_logs, db_path)
            except Exception:
                pass
        return await call_next(request)

    # Security headers middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # CSRF protection middleware for mutating endpoints
    @app.middleware("http")
    async def csrf_protection(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
            # Health checks and log stream (GET) are exempted
            if not request.url.path.startswith("/api/health") and request.url.path != "/api/logs/stream":
                if not request.headers.get("x-requested-with"):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Forbidden: missing required X-Requested-With header."},
                    )
        return await call_next(request)

    # CORS Middleware allowing credentials for Vite frontend development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routers
    api_router = APIRouter(prefix="/api")
    api_router.include_router(auth.router)
    api_router.include_router(logs.router)
    api_router.include_router(settings.router)
    api_router.include_router(aliases.router)
    api_router.include_router(drop_rules.router)
    api_router.include_router(saved_views.router)
    api_router.include_router(notifications.router)
    api_router.include_router(alerts.router)
    api_router.include_router(system.router)
    api_router.include_router(ai.router)

    app.include_router(api_router)

    # API v1 alias router
    api_v1_router = APIRouter(prefix="/api/v1")
    api_v1_router.include_router(auth.router)
    api_v1_router.include_router(logs.router)
    api_v1_router.include_router(settings.router)
    api_v1_router.include_router(aliases.router)
    api_v1_router.include_router(drop_rules.router)
    api_v1_router.include_router(saved_views.router)
    api_v1_router.include_router(notifications.router)
    api_v1_router.include_router(alerts.router)
    api_v1_router.include_router(system.router)
    api_v1_router.include_router(ai.router)

    app.include_router(api_v1_router)

    # Static files serving with SPA fallback
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            if full_path.startswith("api"):
                raise HTTPException(status_code=404, detail="Not Found")
            resolved_file = (static_dir / full_path).resolve()
            if not resolved_file.is_relative_to(static_dir.resolve()):
                raise HTTPException(status_code=404, detail="Not Found")
            if resolved_file.is_file():
                return FileResponse(str(resolved_file))
            index_path = static_dir / "index.html"
            if index_path.is_file():
                return FileResponse(str(index_path))
            raise HTTPException(status_code=404, detail="Static files not found")

    return app


app = create_app()
