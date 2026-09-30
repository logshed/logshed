"""
Unit tests for shared core utility functions.
"""

import datetime
import pytest

from app.core.utils import match_wildcard, parse_iso_to_epoch, parse_iso_to_utc_datetime


class TestMatchWildcard:
    """Tests for match_wildcard helper function."""

    def test_empty_or_none_pattern(self):
        assert match_wildcard(None, "anything") is True
        assert match_wildcard("", "anything") is True
        assert match_wildcard("   ", "anything") is True

    def test_empty_or_none_text(self):
        assert match_wildcard("pattern", None) is False
        assert match_wildcard("pattern", "") is False

    def test_exact_match_case_insensitive(self):
        assert match_wildcard("nginx", "nginx") is True
        assert match_wildcard("NGINX", "nginx") is True
        assert match_wildcard("nginx", "NGINX") is True
        assert match_wildcard("nginx", "apache") is False

    def test_wildcard_glob_matching(self):
        assert match_wildcard("ssh*", "sshd") is True
        assert match_wildcard("ssh*", "ssh-agent") is True
        assert match_wildcard("ssh*", "nginx") is False
        assert match_wildcard("*error*", "critical error occurred") is True
        assert match_wildcard("app-?-prod", "app-1-prod") is True
        assert match_wildcard("app-?-prod", "app-12-prod") is False


class TestParseIsoToEpoch:
    """Tests for parse_iso_to_epoch helper function."""

    def test_valid_iso_utc_string(self):
        ts_str = "2026-09-19T12:00:00Z"
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
        assert parse_iso_to_epoch(ts_str) == expected

    def test_valid_iso_offset_string(self):
        ts_str = "2026-09-19T13:00:00+01:00"
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
        assert parse_iso_to_epoch(ts_str) == expected

    def test_valid_naive_iso_string(self):
        ts_str = "2026-09-19T12:00:00"
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
        assert parse_iso_to_epoch(ts_str) == expected

    def test_datetime_object_input(self):
        dt = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert parse_iso_to_epoch(dt) == dt.timestamp()

    def test_numeric_epoch_input(self):
        assert parse_iso_to_epoch(1726747200) == 1726747200.0
        assert parse_iso_to_epoch(1726747200.5) == 1726747200.5

    def test_none_and_empty_input_uses_fallback(self):
        assert parse_iso_to_epoch(None) == 0.0
        assert parse_iso_to_epoch(None, fallback=123.45) == 123.45
        assert parse_iso_to_epoch("", fallback=99.0) == 99.0
        assert parse_iso_to_epoch("   ", fallback=99.0) == 99.0

    def test_invalid_string_uses_fallback(self):
        assert parse_iso_to_epoch("invalid-timestamp") == 0.0
        assert parse_iso_to_epoch("invalid-timestamp", fallback=55.5) == 55.5


class TestParseIsoToUtcDatetime:
    """Tests for parse_iso_to_utc_datetime helper function."""

    def test_valid_iso_utc_string(self):
        ts_str = "2026-09-19T12:00:00Z"
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert parse_iso_to_utc_datetime(ts_str) == expected

    def test_valid_iso_offset_string(self):
        ts_str = "2026-09-19T13:00:00+01:00"
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert parse_iso_to_utc_datetime(ts_str) == expected

    def test_valid_naive_iso_string(self):
        ts_str = "2026-09-19T12:00:00"
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert parse_iso_to_utc_datetime(ts_str) == expected

    def test_datetime_object_input_naive(self):
        dt = datetime.datetime(2026, 9, 19, 12, 0, 0)
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert parse_iso_to_utc_datetime(dt) == expected

    def test_datetime_object_input_aware(self):
        tz = datetime.timezone(datetime.timedelta(hours=2))
        dt = datetime.datetime(2026, 9, 19, 14, 0, 0, tzinfo=tz)
        expected = datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert parse_iso_to_utc_datetime(dt) == expected

    def test_numeric_epoch_input(self):
        expected = datetime.datetime(2024, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert parse_iso_to_utc_datetime(1726747200) == expected
        assert parse_iso_to_utc_datetime(1726747200.0) == expected

    def test_none_and_empty_input(self):
        assert parse_iso_to_utc_datetime(None) is None
        assert parse_iso_to_utc_datetime("") is None
        assert parse_iso_to_utc_datetime("   ") is None

    def test_invalid_string(self):
        assert parse_iso_to_utc_datetime("not-a-date") is None
        assert parse_iso_to_utc_datetime("2026-99-99T99:99:99") is None

