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


class TestExpandSourceAliases:
    """Tests for expand_source_aliases helper function."""

    def test_empty_or_none_sources(self):
        from app.core.utils import expand_source_aliases
        assert expand_source_aliases(None) == []
        assert expand_source_aliases([]) == []
        assert expand_source_aliases([""]) == []

    def test_no_connection_returns_parsed_sources(self):
        from app.core.utils import expand_source_aliases
        assert expand_source_aliases(["host1,host2", "host3"]) == ["host1", "host2", "host3"]

    def test_symmetric_expansion_from_alias_and_ip(self):
        import sqlite3
        from app.core.utils import expand_source_aliases

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE host_aliases (ip TEXT, alias TEXT)")
            conn.execute("INSERT INTO host_aliases VALUES ('127.0.0.1', 'LogShed')")
            conn.execute("INSERT INTO host_aliases VALUES ('docker', 'Docker Server')")
            conn.execute("INSERT INTO host_aliases VALUES ('172.22.2.11', 'Proxmox')")

            # Query by alias -> expands to alias + ip
            res1 = expand_source_aliases(["LogShed"], conn=conn)
            assert sorted(res1) == ["127.0.0.1", "LogShed"]

            # Query by lowercase alias -> expands to lowercase + original alias + ip
            res2 = expand_source_aliases(["logshed"], conn=conn)
            assert "127.0.0.1" in res2
            assert "LogShed" in res2
            assert "logshed" in res2

            # Query by IP -> expands to IP + alias
            res3 = expand_source_aliases(["127.0.0.1"], conn=conn)
            assert sorted(res3) == ["127.0.0.1", "LogShed"]

            # Query by multi-word alias
            res4 = expand_source_aliases(["Docker Server"], conn=conn)
            assert sorted(res4) == ["Docker Server", "docker"]

            # Query by unaliased host -> preserves host without error
            res5 = expand_source_aliases(["unknown-host"], conn=conn)
            assert res5 == ["unknown-host"]

            # Combined list
            res6 = expand_source_aliases(["LogShed", "Proxmox"], conn=conn)
            assert sorted(res6) == ["127.0.0.1", "172.22.2.11", "LogShed", "Proxmox"]
        finally:
            conn.close()

