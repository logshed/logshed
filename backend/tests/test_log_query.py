"""
Unit tests for the shared log query helpers in app.services.log_query.
"""

import datetime
import sqlite3
from unittest.mock import MagicMock

import pytest

from app.services.fts_indexer import set_fts_worker
from app.services.log_query import (
    execute_with_fts_fallback,
    is_fts_syntax_error,
    to_utc_bound,
)


class TestToUtcBound:

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2026-10-15T12:00:00.000Z", "2026-10-15T12:00:00+00:00"),
            ("2026-10-15T12:00:00Z", "2026-10-15T12:00:00+00:00"),
            ("2026-10-15T12:00:00+02:00", "2026-10-15T10:00:00+00:00"),
            ("2026-10-15T12:00:00.250-01:30", "2026-10-15T13:30:00.250000+00:00"),
            ("2026-10-15T12:00:00", "2026-10-15T12:00:00+00:00"),
        ],
    )
    def test_iso_values_use_stored_utc_layout(self, value: str, expected: str):
        assert to_utc_bound(value) == expected

    def test_relative_values_are_accepted(self):
        before = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        bound = datetime.datetime.fromisoformat(to_utc_bound("1h"))
        after = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        assert before <= bound <= after
        assert bound.utcoffset() == datetime.timedelta(0)

    def test_empty_values_give_none(self):
        assert to_utc_bound(None) is None
        assert to_utc_bound("") is None

    @pytest.mark.parametrize("value", ["not-a-date", "2026-13-45T00:00:00", "the day before"])
    def test_invalid_values_raise_value_error(self, value: str):
        with pytest.raises(ValueError):
            to_utc_bound(value)


class TestExecuteWithFtsFallback:

    def test_syntax_error_retries_once_with_escaped_tokens(self):
        seen: list[str] = []

        def run(params):
            seen.append(params["fts_term"])
            if len(seen) == 1:
                raise sqlite3.OperationalError('fts5: syntax error near "="')
            return "ok"

        assert execute_with_fts_fallback(run, {"fts_term": "key=value*"}, "key=value", empty=None) == "ok"
        assert seen == ["key=value*", '"key=value"*']

    def test_lock_error_is_not_retried(self):
        calls = []

        def run(params):
            calls.append(params)
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            execute_with_fts_fallback(run, {"fts_term": "foo*"}, "foo", empty=None)
        assert len(calls) == 1

    def test_second_syntax_error_returns_empty(self):
        def run(params):
            raise sqlite3.OperationalError("fts5: syntax error near x")

        assert execute_with_fts_fallback(run, {"fts_term": "x*"}, "x y", empty=[]) == []

    def test_non_fts_queries_do_not_wake_the_indexer(self):
        worker = MagicMock()
        set_fts_worker(worker)
        try:
            execute_with_fts_fallback(lambda p: None, {}, None, empty=None)
            assert not worker.notify_new_logs.called
            execute_with_fts_fallback(lambda p: None, {"fts_term": "foo*"}, "foo", empty=None)
            assert worker.notify_new_logs.call_count == 1
        finally:
            set_fts_worker(None)

    @pytest.mark.parametrize(
        "message, expected",
        [
            ('fts5: syntax error near "@"', True),
            ("no such column: bar", True),
            ("unterminated string", True),
            ("database is locked", False),
            ("database table is locked", False),
            ("disk I/O error", False),
        ],
    )
    def test_syntax_error_detection(self, message: str, expected: bool):
        assert is_fts_syntax_error(sqlite3.OperationalError(message)) is expected
