"""
Tests for downloading logs: POST /api/logs/export (the Console) and GET /api/v1/logs/export.
"""

import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.core import pipeline as pipeline_mod
from app.core.api_tokens import _last_used_timestamps, api_token_rate_limiter
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.redactor import REDACTED
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.services import log_export, log_store
from app.services.fts_indexer import index_pending_logs
from app.services.log_query import LogFilter
from tests.db_helpers import open_db, store_logs

BASE = datetime(2026, 11, 15, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    reset_crypto_cache()
    _last_used_timestamps.clear()
    db_file = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    yield
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def session_headers():
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={create_session_token(user_id=1)}",
        "X-Requested-With": "XMLHttpRequest",
    }


def entry(i: int, **fields) -> dict:
    """A log i seconds after BASE."""
    return {
        "timestamp": (BASE + timedelta(seconds=i)).isoformat(),
        "source_ip": "192.168.1.10",
        "source_alias": "server-01",
        "app_name": "app",
        "facility": 1,
        "severity": 6,
        "message": f"line {i}",
        "raw": f"<14>line {i}",
        **fields,
    }


def read_csv(text: str) -> list[dict]:
    assert text.startswith("﻿"), "the CSV starts with a byte order mark"
    return list(csv.DictReader(io.StringIO(text[1:], newline="")))


def read_ndjson(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines()]


class TestConsoleExport:
    @pytest.mark.asyncio
    async def test_csv_has_every_field_newest_first(self, client, session_headers, tmp_path):
        store_logs(tmp_path / "logs.db", entry(1), entry(2, severity=3, app_name="caddy"))
        res = await client.post("/api/logs/export", json={"format": "csv"}, headers=session_headers)
        assert res.status_code == 200
        assert res.headers["content-type"] == "text/csv; charset=utf-8"
        assert res.headers["content-disposition"].startswith('attachment; filename="logshed-logs-')
        assert res.headers["content-disposition"].endswith('.csv"')
        assert res.headers[log_export.ROWS_HEADER] == "2"
        assert res.headers[log_export.CAPPED_HEADER] == "false"
        rows = read_csv(res.text)
        assert list(rows[0]) == list(log_export.EXPORT_FIELDS)
        assert [r["message"] for r in rows] == ["line 2", "line 1"]
        assert rows[0]["app_name"] == "caddy"
        assert rows[0]["severity"] == "3"
        assert rows[0]["raw"] == "<14>line 2"
        assert rows[0]["timestamp"].startswith("2026-11-15T10:00:02")

    @pytest.mark.asyncio
    async def test_csv_keeps_line_breaks_and_quotes_and_defuses_formulas(self, client, session_headers, tmp_path):
        messages = ["=HYPERLINK(\"http://x\")", "+1", "-rm", "@SUM(A1)", "two\nlines, \"quoted\"", "plain"]
        store_logs(tmp_path / "logs.db", *(entry(i, message=m) for i, m in enumerate(messages)))
        res = await client.post("/api/logs/export", json={"format": "csv"}, headers=session_headers)
        got = [r["message"] for r in reversed(read_csv(res.text))]
        assert got == ["'=HYPERLINK(\"http://x\")", "'+1", "'-rm", "'@SUM(A1)", "two\nlines, \"quoted\"", "plain"]

    @pytest.mark.asyncio
    async def test_ndjson_is_exact(self, client, session_headers, tmp_path):
        store_logs(tmp_path / "logs.db", entry(1, message="=1+1 password=hunter2 café"))
        res = await client.post("/api/logs/export", json={"format": "ndjson"}, headers=session_headers)
        assert res.headers["content-type"] == "application/x-ndjson"
        [record] = read_ndjson(res.text)
        assert list(record) == list(log_export.EXPORT_FIELDS)
        assert record["message"] == "=1+1 password=hunter2 café"
        assert record["severity"] == 6

    @pytest.mark.asyncio
    async def test_mask_secrets_runs_the_redactor(self, client, session_headers, tmp_path):
        store_logs(tmp_path / "logs.db", entry(1, message="login password=hunter2", raw="<14>login password=hunter2"))
        for fmt, read in (("ndjson", read_ndjson), ("csv", read_csv)):
            res = await client.post(
                "/api/logs/export", json={"format": fmt, "mask_secrets": True}, headers=session_headers
            )
            [record] = read(res.text)
            assert "hunter2" not in record["message"] and REDACTED in record["message"]
            assert "hunter2" not in record["raw"]

    @pytest.mark.asyncio
    async def test_filters_and_full_text_search(self, client, session_headers, tmp_path):
        db = tmp_path / "logs.db"
        store_logs(
            db,
            entry(1, message="disk failed", severity=3),
            entry(2, message="disk ok", severity=6),
            entry(3, message="disk failed", severity=3, source_alias="nas"),
        )
        index_pending_logs(db)
        res = await client.post(
            "/api/logs/export",
            json={"format": "ndjson", "query": "failed", "sources": ["server-01"], "severity_max": 4},
            headers=session_headers,
        )
        assert [r["id"] for r in read_ndjson(res.text)] == [1]

    @pytest.mark.asyncio
    async def test_ticked_rows_alone(self, client, session_headers, tmp_path):
        ids = store_logs(tmp_path / "logs.db", *(entry(i) for i in range(5)))
        res = await client.post(
            "/api/logs/export", json={"format": "ndjson", "log_ids": [ids[1], ids[3]]}, headers=session_headers
        )
        assert [r["id"] for r in read_ndjson(res.text)] == [ids[3], ids[1]]
        res = await client.post(
            "/api/logs/export", json={"log_ids": [ids[1]], "query": "line"}, headers=session_headers
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_cap_keeps_the_newest_and_says_so(self, client, session_headers, tmp_path, monkeypatch):
        monkeypatch.setattr(log_export, "EXPORT_ROW_CAP", 5)
        monkeypatch.setattr(log_export, "EXPORT_PAGE_SIZE", 2)
        store_logs(tmp_path / "logs.db", *(entry(i) for i in range(9)))
        res = await client.post("/api/logs/export", json={"format": "ndjson"}, headers=session_headers)
        assert res.headers[log_export.ROWS_HEADER] == "5"
        assert res.headers[log_export.CAPPED_HEADER] == "true"
        assert [r["message"] for r in read_ndjson(res.text)] == [f"line {i}" for i in (8, 7, 6, 5, 4)]

    @pytest.mark.asyncio
    async def test_exactly_the_cap_is_not_capped(self, client, session_headers, tmp_path, monkeypatch):
        monkeypatch.setattr(log_export, "EXPORT_ROW_CAP", 4)
        monkeypatch.setattr(log_export, "EXPORT_PAGE_SIZE", 2)
        store_logs(tmp_path / "logs.db", *(entry(i) for i in range(4)))
        res = await client.post("/api/logs/export", json={"format": "csv"}, headers=session_headers)
        assert res.headers[log_export.CAPPED_HEADER] == "false"
        assert len(read_csv(res.text)) == 4

    @pytest.mark.asyncio
    async def test_pages_run_across_the_legacy_table_and_day_files(self, client, session_headers, tmp_path, monkeypatch):
        monkeypatch.setattr(log_export, "EXPORT_PAGE_SIZE", 2)
        db = tmp_path / "logs.db"
        with open_db(db) as conn:
            for i in range(3):
                ts = (BASE - timedelta(days=2, seconds=-i)).isoformat()
                conn.execute(
                    "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
                    "VALUES (?, ?, '10.0.0.1', 'old', 'app', 1, 6, ?, 'raw')",
                    (ts, ts, f"legacy {i}"),
                )
        store_logs(db, *(entry(i) for i in range(3)))
        res = await client.post("/api/logs/export", json={"format": "ndjson"}, headers=session_headers)
        got = [r["message"] for r in read_ndjson(res.text)]
        assert got == ["line 2", "line 1", "line 0", "legacy 2", "legacy 1", "legacy 0"]

    @pytest.mark.asyncio
    async def test_empty_search_gives_just_the_heading(self, client, session_headers):
        res = await client.post("/api/logs/export", json={"format": "csv"}, headers=session_headers)
        assert res.headers[log_export.ROWS_HEADER] == "0"
        assert read_csv(res.text) == []

    @pytest.mark.asyncio
    async def test_needs_a_session_and_the_csrf_header(self, client, session_headers):
        csrf_only = {"X-Requested-With": "XMLHttpRequest"}
        assert (await client.post("/api/logs/export", json={}, headers=csrf_only)).status_code == 401
        cookie_only = {"Cookie": session_headers["Cookie"]}
        assert (await client.post("/api/logs/export", json={}, headers=cookie_only)).status_code == 403

    @pytest.mark.asyncio
    async def test_bad_time_bound_is_refused(self, client, session_headers):
        res = await client.post("/api/logs/export", json={"from": "yesterday-ish"}, headers=session_headers)
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_a_failure_after_the_start_cuts_the_download_off(self, client, session_headers, tmp_path, monkeypatch):
        monkeypatch.setattr(log_export, "EXPORT_PAGE_SIZE", 2)
        store_logs(tmp_path / "logs.db", *(entry(i) for i in range(5)))
        real_search = log_store.search
        calls = {"n": 0}

        def failing_second_page(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("interrupted")
            return real_search(*args, **kwargs)

        monkeypatch.setattr(log_store, "search", failing_second_page)
        with pytest.raises(sqlite3.OperationalError):
            await client.post("/api/logs/export", json={"format": "ndjson"}, headers=session_headers)


class TestStreamStops:
    @pytest.mark.asyncio
    async def test_closing_the_stream_stops_the_page_being_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr(log_export, "EXPORT_PAGE_SIZE", 2)
        store_logs(tmp_path / "logs.db", *(entry(i) for i in range(7)))
        checks = []
        real = deps.run_db_query

        async def recording(fn, custom_db_path=None, should_interrupt=None):
            if should_interrupt is not None:
                checks.append(should_interrupt)
            return await real(fn, custom_db_path=custom_db_path, should_interrupt=should_interrupt)

        monkeypatch.setattr(deps, "run_db_query", recording)
        response = await log_export.export_logs(LogFilter(), "ndjson", False)
        body = response.body_iterator
        assert "line 6" in await body.__anext__()
        assert "line 4" in await body.__anext__()
        assert checks and not checks[-1]()
        await body.aclose()
        assert checks[-1](), "a closed download interrupts its database work"


class TestApiV1Export:
    @pytest_asyncio.fixture
    async def token_headers(self, client, session_headers):
        async def make(scopes):
            res = await client.post("/api/tokens", json={"name": "t", "scopes": scopes}, headers=session_headers)
            return {"Authorization": f"Bearer {res.json()['raw_token']}"}

        return make

    @pytest.mark.asyncio
    async def test_ndjson_by_default_with_named_severity(self, client, token_headers, tmp_path):
        store_logs(tmp_path / "logs.db", entry(1, severity=3), entry(2, severity=6))
        headers = await token_headers(["logs:read"])
        res = await client.get("/api/v1/logs/export", params={"severity": "error"}, headers=headers)
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/x-ndjson"
        assert [r["message"] for r in read_ndjson(res.text)] == ["line 1"]
        res = await client.get("/api/v1/logs/export", params={"format": "csv"}, headers=headers)
        assert len(read_csv(res.text)) == 2

    @pytest.mark.asyncio
    async def test_always_masks_secrets_like_every_api_response(self, client, token_headers, tmp_path):
        store_logs(tmp_path / "logs.db", entry(1, message="login password=hunter2", raw="<14>login password=hunter2"))
        headers = await token_headers(["logs:read"])
        for params in ({}, {"mask_secrets": "false"}):
            res = await client.get("/api/v1/logs/export", params=params, headers=headers)
            [record] = read_ndjson(res.text)
            assert "hunter2" not in record["message"] and "hunter2" not in record["raw"]

    @pytest.mark.asyncio
    async def test_needs_the_logs_read_scope(self, client, token_headers):
        headers = await token_headers(["alerts:read"])
        assert (await client.get("/api/v1/logs/export", headers=headers)).status_code == 403
        assert (await client.get("/api/v1/logs/export")).status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_format_is_refused(self, client, token_headers):
        headers = await token_headers(["logs:read"])
        assert (await client.get("/api/v1/logs/export", params={"format": "xml"}, headers=headers)).status_code == 422


def test_csv_cell_leaves_numbers_and_safe_text():
    assert log_export.csv_cell(-5) == -5
    assert log_export.csv_cell("ok") == "ok"
    assert log_export.csv_cell("\tx") == "'\tx"


def test_filename_uses_the_server_time_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/London")
    when = datetime(2026, 7, 1, 12, 0, 5, tzinfo=timezone.utc)
    assert log_export.export_filename("csv", when) == "logshed-logs-20260701-130005.csv"
