"""
Tests for RFC 3164 and RFC 5424 parsing, timestamp handling, network listeners, and alias resolution.
"""

import asyncio
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

from app.core.config import get_syslog_port
from app.core.migrations import get_connection, run_migrations
from app.core.pipeline import KeyedMultilineAssembler, get_queue
from app.collectors.syslog import (
    AliasCache,
    SyslogServer,
    SyslogTCPProtocol,
    SyslogUDPProtocol,
    MAX_TCP_CONNECTIONS,
    TCP_INACTIVITY_TIMEOUT,
    dispatch_syslog_message,
    parse_syslog_message,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "syslog_test.db"
    run_migrations(p)
    return p


# ===================================================================
# 1. RFC 3164 / RFC 5424 Syslog Parsing
# ===================================================================

class TestSyslogParsing:

    def test_rfc3164_basic(self):
        raw = b"<14>Jan  5 10:30:00 myhost myapp[123]: Something happened"
        result = parse_syslog_message(raw, "192.168.1.1")

        assert result["facility"] == 1      # 14 // 8
        assert result["severity"] == 6      # 14 % 8
        assert result["app_name"] == "myapp"
        assert result["source_ip"] == "192.168.1.1"
        assert "Something happened" in result["message"]

    def test_rfc3164_no_pid(self):
        raw = b"<38>Feb 12 08:15:30 router sshd: Bad password attempt"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 4      # 38 // 8
        assert result["severity"] == 6      # 38 % 8
        assert result["app_name"] == "sshd"
        assert "Bad password" in result["message"]

    def test_rfc5424_basic(self):
        raw = b"<134>1 2024-01-15T10:30:00.000Z myhost myapp 1234 ID47 - Application started"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 16     # 134 // 8
        assert result["severity"] == 6      # 134 % 8
        assert result["app_name"] == "myapp"
        assert result["timestamp"] == "2024-01-15T10:30:00+00:00"
        assert "Application started" in result["message"]

    def test_rfc5424_nil_fields(self):
        raw = b"<165>1 2024-03-01T12:00:00Z - - - - - Just a message"
        result = parse_syslog_message(raw, "10.1.1.1")

        assert result["severity"] == 5      # 165 % 8
        assert result["app_name"] == "unknown"  # '-' maps to unknown
        assert "Just a message" in result["message"]

    def test_rfc5424_structured_data_with_spaces(self):
        raw = b'<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 [meta key="value with spaces" tag="audit"] Actual message text'
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 16
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result.get("hostname") == "srv01"
        assert result["timestamp"] == "2024-01-15T10:30:00+00:00"
        assert result["message"] == "Actual message text"

    def test_rfc5424_multiple_structured_data_elements(self):
        raw = b'<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 [sd1 a="1"][sd2 b="2"] Multiblock message'
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "myapp"
        assert result["message"] == "Multiblock message"

    def test_rfc5424_multiple_structured_data_elements_with_spaces(self):
        """RFC 5424 SD elements separated by spaces should all be parsed without polluting message text."""
        raw = b'<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 [sd1 a="1"] [sd2 b="2"] [sd3 c="3"] Clean message text'
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "myapp"
        assert result["hostname"] == "srv01"
        assert result["message"] == "Clean message text"

    def test_rfc3164_sets_hostname(self):
        """RFC 3164 messages should extract and set result['hostname']."""
        raw = b"<14>Jan  5 10:30:00 webserver01 nginx[123]: Request processed"
        result = parse_syslog_message(raw, "192.168.1.100")

        assert result["hostname"] == "webserver01"
        assert result["app_name"] == "nginx"
        assert result["message"] == "Request processed"

    def test_rfc3164_message_starting_with_number_not_misidentified_as_5424(self):
        raw = b"<134>Jan 15 10:30:00 srv01 myapp: 42 connections opened"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 16
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result["message"] == "42 connections opened"

    def test_unparseable_fallback(self):
        raw = b"This is not a syslog message at all"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["severity"] == 6
        assert result["facility"] == 1
        assert result["app_name"] == "unknown"
        assert result["raw"] == "This is not a syslog message at all"

    def test_source_ip_passthrough(self):
        raw = b"<14>Jan  1 00:00:00 h app: msg"
        result = parse_syslog_message(raw, "172.16.0.99")
        assert result["source_ip"] == "172.16.0.99"

    def test_received_at_populated(self):
        raw = b"<14>Jan  1 00:00:00 h app: msg"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert result["received_at"] is not None
        datetime.datetime.fromisoformat(result["received_at"])

    def test_raw_preserved(self):
        raw = b"<14>Jan  1 00:00:00 h app: msg"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert result["raw"] == raw.decode("utf-8").strip()

    def test_utf8_with_errors(self):
        raw = b"<14>Jan  1 00:00:00 h app: hello \xff world"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert "\ufffd" in result["raw"] or "world" in result["raw"]

    def test_rfc3164_local_timezone_ahead_of_utc(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        future_hour = now + datetime.timedelta(hours=1)
        month_str = future_hour.strftime("%b")
        day_str = f"{future_hour.day:2d}"
        time_str = future_hour.strftime("%H:%M:%S")
        raw = f"<14>{month_str} {day_str} {time_str} myhost myapp: test timezone".encode()
        result = parse_syslog_message(raw, "10.0.0.1")
        parsed_dt = datetime.datetime.fromisoformat(result["timestamp"])
        assert (parsed_dt - now).total_seconds() <= 60

    def test_rfc3164_candidate_proximity_npm_utc_in_bst(self):
        """User Issue 7: NPM in UTC emitting Sep 7 15:00:00 received at 15:00:01 UTC in Europe/London (BST, UTC+1).
        Candidate B (UTC) is 1s away, whereas Candidate A (BST -> UTC) is 3601s away.
        Candidate B must be chosen, resulting in 15:00:00 UTC."""
        arrival_now = datetime.datetime(2026, 9, 7, 15, 0, 1, 8765, tzinfo=datetime.timezone.utc)
        bst_tz = ZoneInfo("Europe/London")
        raw = b"<78>Sep  7 15:00:00 crond[330276]: USER root pid 720626 cmd run-parts /etc/periodic/hourly"
        result = parse_syslog_message(raw, "192.168.1.100", now=arrival_now, local_tz=bst_tz)

        assert result["timestamp"] == "2026-09-07T15:00:00.008765+00:00"
        assert result["received_at"] == "2026-09-07T15:00:01.008765+00:00"

    def test_rfc3164_candidate_proximity_local_sender_in_bst(self):
        """Sender emitting in container's local BST time (16:00:00) received at 15:00:01 UTC (16:00:01 BST).
        Candidate A (BST -> UTC) is 1s away, whereas Candidate B (UTC) is 3599s in the future.
        Candidate A must be chosen, resulting in 15:00:00 UTC."""
        arrival_now = datetime.datetime(2026, 9, 7, 15, 0, 1, 8765, tzinfo=datetime.timezone.utc)
        bst_tz = ZoneInfo("Europe/London")
        raw = b"<14>Sep  7 16:00:00 myhost myapp: local time message"
        result = parse_syslog_message(raw, "192.168.1.100", now=arrival_now, local_tz=bst_tz)

        assert result["timestamp"] == "2026-09-07T15:00:00.008765+00:00"

    def test_rfc3164_candidate_proximity_utc_sender_in_edt(self):
        """Receiver in America/New_York (EDT, UTC-4). Sender in UTC sends 15:00:00.
        Arrival at 15:00:01 UTC.
        Candidate B (UTC) is 1s away; Candidate A (EDT -> UTC) is ~4 hours away.
        Candidate B must be chosen."""
        arrival_now = datetime.datetime(2026, 9, 7, 15, 0, 1, 8765, tzinfo=datetime.timezone.utc)
        edt_tz = ZoneInfo("America/New_York")
        raw = b"<14>Sep  7 15:00:00 myhost myapp: utc message"
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=edt_tz)

        assert result["timestamp"] == "2026-09-07T15:00:00.008765+00:00"

    def test_rfc3164_candidate_proximity_local_sender_in_edt(self):
        """Receiver in America/New_York (EDT, UTC-4). Sender in EDT sends 11:00:00.
        Arrival at 15:00:01 UTC (11:00:01 EDT).
        Candidate A (EDT -> UTC) is 1s away; Candidate B (UTC) is ~4 hours away.
        Candidate A must be chosen."""
        arrival_now = datetime.datetime(2026, 9, 7, 15, 0, 1, 8765, tzinfo=datetime.timezone.utc)
        edt_tz = ZoneInfo("America/New_York")
        raw = b"<14>Sep  7 11:00:00 myhost myapp: edt message"
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=edt_tz)

        assert result["timestamp"] == "2026-09-07T15:00:00.008765+00:00"

    def test_rfc3164_sender_in_positive_timezone_offset_compensation(self):
        """Sender in Tokyo (UTC+9) sends 19:00:00. Receiver in UTC receives at 10:00:01 UTC.
        Positive offset heuristic compensates 9 hours to 10:00:00 UTC."""
        arrival_now = datetime.datetime(2026, 9, 7, 10, 0, 1, 0, tzinfo=datetime.timezone.utc)
        raw = b"<14>Sep  7 19:00:00 myhost myapp: tokyo message"
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=datetime.timezone.utc)

        assert result["timestamp"] == "2026-09-07T10:00:00+00:00"

    def test_rfc5424_naive_timestamp_candidate_proximity(self):
        """RFC 5424 naive timestamp without timezone offset evaluated with candidate proximity."""
        arrival_now = datetime.datetime(2026, 9, 7, 15, 0, 1, 0, tzinfo=datetime.timezone.utc)
        bst_tz = ZoneInfo("Europe/London")
        raw = b"<134>1 2026-09-07T15:00:00 myhost myapp 1234 ID47 - Application started"
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=bst_tz)

        assert result["timestamp"] == "2026-09-07T15:00:00+00:00"

    def test_rfc3164_naive_now_does_not_raise(self):
        """Passing a naive datetime for `now` should not raise TypeError and parse successfully."""
        naive_now = datetime.datetime(2026, 9, 7, 15, 0, 1)
        raw = b"<14>Sep  7 15:00:00 myhost myapp: test naive now"
        result = parse_syslog_message(raw, "10.0.0.1", now=naive_now)

        assert result["timestamp"] == "2026-09-07T15:00:00+00:00"
        assert result["received_at"] == "2026-09-07T15:00:01+00:00"

    def test_rfc3164_non_utc_now_normalizes_to_utc(self):
        """Passing an offset-aware non-UTC datetime for `now` should normalize received_at and timestamp to UTC."""
        bst_tz = ZoneInfo("Europe/London")
        local_now = datetime.datetime(2026, 9, 7, 16, 0, 1, tzinfo=bst_tz)  # 15:00:01 UTC
        raw = b"<14>Sep  7 16:00:00 myhost myapp: test bst message"
        result = parse_syslog_message(raw, "10.0.0.1", now=local_now, local_tz=bst_tz)

        assert result["timestamp"] == "2026-09-07T15:00:00+00:00"
        assert result["received_at"] == "2026-09-07T15:00:01+00:00"

    def test_rfc3164_year_boundary_sender_in_new_year(self):
        """When receiver is in December and sender in positive timezone emitted January,
        timestamp year must roll forward to the next year."""
        arrival_now = datetime.datetime(2026, 12, 31, 23, 59, 59, 0, tzinfo=datetime.timezone.utc)
        raw = b"<14>Jan  1 00:59:59 myhost myapp: happy new year"
        # local_tz is UTC, sender in UTC+1 emits Jan 1 00:59:59 (which is Dec 31 23:59:59 UTC)
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=datetime.timezone.utc)

        assert result["timestamp"] == "2026-12-31T23:59:59+00:00"
        assert result["received_at"] == "2026-12-31T23:59:59+00:00"

    def test_rfc5424_future_zulu_timestamp_clamped_to_arrival_time(self):
        """RFC 5424 timestamp ending in 'Z' that is far in the future must be clamped to arrival time."""
        now = datetime.datetime.now(datetime.timezone.utc)
        future_time = now + datetime.timedelta(hours=5)
        future_iso = future_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = f"<134>1 {future_iso} myhost myapp 1234 ID47 - Future time message".encode()
        result = parse_syslog_message(raw, "10.0.0.1")
        parsed_dt = datetime.datetime.fromisoformat(result["timestamp"])
        diff = abs((parsed_dt - now).total_seconds())
        assert diff <= 60
        assert result["timestamp"] != future_iso

    def test_rfc3164_omitted_hostname_with_pid(self):
        """RFC 3164 messages where hostname is omitted and MSG (TAG[PID]:) directly follows timestamp."""
        raw = b"<78>Sep  7 14:15:00 crond[330276]: USER root pid 718623 cmd run-parts /etc/periodic/15min"
        result = parse_syslog_message(raw, "192.168.1.50")

        assert result["facility"] == 9       # 78 // 8 (cron)
        assert result["severity"] == 6       # 78 % 8 (info)
        assert result["app_name"] == "crond"
        assert result["message"] == "USER root pid 718623 cmd run-parts /etc/periodic/15min"
        assert "hostname" not in result
        assert result["source_ip"] == "192.168.1.50"

    def test_rfc3164_omitted_hostname_without_pid(self):
        """RFC 3164 message where hostname is omitted and MSG (TAG:) directly follows timestamp."""
        raw = b"<14>Jan  5 10:30:00 myapp: Something happened"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 1
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result["message"] == "Something happened"
        assert "hostname" not in result

    def test_rfc3164_omitted_hostname_no_colon_with_pid(self):
        """RFC 3164 message where hostname is omitted and MSG has PID but no colon."""
        raw = b"<14>Jan  5 10:30:00 myapp[123] Something happened"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 1
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result["message"] == "Something happened"
        assert "hostname" not in result

    def test_rfc3164_bracketed_tag_without_hostname(self):
        """RFC 3164 message with bracketed process name (e.g. [kernel]) must not discard the bracketed token."""
        raw = b"<14>Sep  7 14:15:00 [kernel] USB disconnected"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "kernel"
        assert result["message"] == "USB disconnected"
        assert "hostname" not in result

    def test_rfc3164_bracketed_tag_with_colon(self):
        """RFC 3164 message with bracketed process name and colon (e.g. [kernel]:)."""
        raw = b"<14>Sep  7 14:15:00 [kernel]: USB disconnected"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "kernel"
        assert result["message"] == "USB disconnected"
        assert "hostname" not in result

    def test_rfc3164_hostname_with_pid_no_colon(self):
        """RFC 3164 message with hostname and process PID without colon must cleanly strip PID from app_name."""
        raw = b"<14>Sep  7 14:15:00 myhost crond[330276] USER root pid 718623"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["hostname"] == "myhost"
        assert result["app_name"] == "crond"
        assert result["message"] == "USER root pid 718623"

    def test_rfc3164_nil_hostname(self):
        """RFC 3164 message with '-' as NIL hostname should not treat '-' as hostname."""
        raw = b"<14>Sep  7 14:15:00 - myapp: message content"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert "hostname" not in result
        assert result["app_name"] == "myapp"
        assert result["message"] == "message content"

    def test_rfc3164_single_word_message(self):
        """RFC 3164 message with single word after timestamp."""
        raw = b"<14>Sep  7 14:15:00 reboot"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert "hostname" not in result
        assert result["message"] == "reboot"

    def test_rfc3164_ipv6_hostname(self):
        """RFC 3164 message with IPv6 hostname."""
        raw = b"<14>Sep  7 14:15:00 2001:db8::1 crond[123]: test message"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["hostname"] == "2001:db8::1"
        assert result["app_name"] == "crond"
        assert result["message"] == "test message"

    def test_iso_datetime_unbracketed_without_rfc5424_prefix(self):
        """Standard ISO/SQL datetime without RFC 5424 '1 ' prefix should parse timestamp and content."""
        arrival_now = datetime.datetime(2026, 9, 12, 6, 35, 3, 0, tzinfo=datetime.timezone.utc)
        raw = b"2026-09-12 06:35:02  INFO      All scopes processed"
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=datetime.timezone.utc)

        assert result["timestamp"] == "2026-09-12T06:35:02+00:00"
        assert result["severity"] == 6
        assert result["app_name"] == "unknown"
        assert "All scopes processed" in result["message"]

    def test_bracketed_timestamp_host_app_envelope(self):
        """Bracketed timestamp envelope [TIMESTAMP] [HOST] [APP] MSG should parse all structured fields."""
        arrival_now = datetime.datetime(2026, 9, 12, 4, 3, 50, 0, tzinfo=datetime.timezone.utc)
        raw = (
            b"[2026-09-12T04:03:48.907889+00:00] [docker-unraid] [UptimeKuma] "
            b"2026-09-12T05:03:48+01:00 [MONITOR] WARN: Monitor #16 'SABnzbd': Pending: connect failed"
        )
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=datetime.timezone.utc)

        assert result["timestamp"] == "2026-09-12T04:03:48.907889+00:00"
        assert result["hostname"] == "docker-unraid"
        assert result["app_name"] == "UptimeKuma"
        assert result["severity"] == 4  # Upgraded to Warning from WARN: in message
        assert "Pending: connect failed" in result["message"]

    def test_syslog_severity_promotion_from_notice_to_error(self):
        """Syslog message sent with notice/info PRI promoted to error when message contains [error]."""
        arrival_now = datetime.datetime(2026, 9, 12, 7, 43, 1, 0, tzinfo=datetime.timezone.utc)
        # PRI 14 is user.info (facility 1, severity 6)
        raw = b'<14>Sep 12 07:43:00 unraid nginx: 2026/09/12 07:43:00 [error] 2360609#2360609: *268403 open() failed'
        result = parse_syslog_message(raw, "192.168.1.5", now=arrival_now, local_tz=datetime.timezone.utc)

        assert result["hostname"] == "unraid"
        assert result["app_name"] == "nginx"
        assert result["severity"] == 3  # Promoted from 6 (info) to 3 (error)
        assert "[error]" in result["message"]

    def test_syslog_slash_datetime_parsing(self):
        """Syslog message beginning with slash datetime format YYYY/MM/DD HH:MM:SS."""
        arrival_now = datetime.datetime(2026, 9, 12, 7, 43, 1, 0, tzinfo=datetime.timezone.utc)
        raw = b'2026/09/12 07:43:00 srv01 nginx: [error] request failed'
        result = parse_syslog_message(raw, "10.0.0.1", now=arrival_now, local_tz=datetime.timezone.utc)

        assert result["timestamp"] == "2026-09-12T07:43:00+00:00"
        assert result["hostname"] == "srv01"
        assert result["app_name"] == "nginx"
        assert result["severity"] == 3  # Promoted to Error

    def test_syslog_unclosed_structured_data_does_not_hang(self):
        """RFC 5424 message with unclosed structured data bracket parses without infinite loop."""
        raw = b"<1>1 2026-09-14T00:00:00Z host app 1 - [malformed_sd"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert result["hostname"] == "host"
        assert result["app_name"] == "app"
        assert result["message"] == "[malformed_sd"

    def test_syslog_logfmt_parsing(self):
        """Logfmt format with time, level, and msg parsed correctly."""
        raw = b'time="2026-10-03T14:56:31Z" level=info msg="Successfully refreshed custom fields cache with 0 fields."'
        result = parse_syslog_message(raw, "10.0.0.1")
        assert result["timestamp"] == "2026-10-03T14:56:31+00:00"
        assert result["severity"] == 6
        assert result["message"] == "Successfully refreshed custom fields cache with 0 fields."

    def test_syslog_maintainerr_app_pipe_parsing(self):
        """Maintainerr application-prefixed pipe format with DD/MM/YYYY timestamp and bracketed level."""
        raw = b"[maintainerr] | 03/10/2026 16:00:33  [INFO] [RuleExecutorService] Execution of rules for 'Never Watched by Anyone' done."
        result = parse_syslog_message(raw, "10.0.0.1", local_tz=datetime.timezone.utc)
        assert result["app_name"] == "maintainerr"
        assert result["timestamp"] == "2026-10-03T16:00:33+00:00"
        assert result["severity"] == 6
        assert result["message"] == "[RuleExecutorService] Execution of rules for 'Never Watched by Anyone' done."

    def test_syslog_valkey_parsing(self):
        """Valkey / Redis server line with pid:role, day mon year, and warning marker."""
        raw = b"1:M 02 Oct 2026 11:47:57.745 # Warning: No config file specified, using the default config. In order to specify a config file use valkey-server /path/to/valkey.conf"
        result = parse_syslog_message(raw, "10.0.0.1", local_tz=datetime.timezone.utc)
        assert result["timestamp"] == "2026-10-02T11:47:57.745000+00:00"
        assert result["severity"] == 4  # '#' maps to warning / 4
        assert "Warning: No config file specified, using the default config." in result["message"]

    _LINES = 'Traceback (most recent call last):\n  File "/app/run.py", line 1, in <module>\nRuntimeError: disk failed'

    @pytest.mark.parametrize(
        "prefix, app, host",
        [
            ("<134>Nov  1 12:00:00 host1 app[1]: ", "app", "host1"),
            ("<134>Nov  1 12:00:00 host1 app[1] ", "app", "host1"),
            ("<134>Nov  1 12:00:00 host1 app ", "app", "host1"),
            ("<134>Nov  1 12:00:00 host1 [app] ", "app", "host1"),
            ("<134>Nov  1 12:00:00 app: ", "app", None),
            ("<134>Nov  1 12:00:00 - app: ", "app", None),
            ("<134>2026-11-01T12:00:00Z host1 app[1]: ", "app", "host1"),
            ("<134>[2026-11-01 12:00:00] [host1] [app] ", "app", "host1"),
            ("<134>[2026-11-01 12:00:00] [host1] app: ", "app", "host1"),
            ("<134>[2026-11-01 12:00:00] app: ", "app", None),
            ("<134>[app] | 01/11/2026 12:00:00 [INFO] ", "app", None),
            ("<134>[app] | 01/11/2026 12:00:00 ", "app", None),
        ],
    )
    def test_multiline_datagram_keeps_every_line_in_message(self, prefix, app, host):
        """A datagram with several lines keeps all of them in message, not only the first."""
        result = parse_syslog_message((prefix + self._LINES).encode(), "10.0.0.1", local_tz=datetime.timezone.utc)
        assert result["message"] == self._LINES
        assert result["app_name"] == app
        assert result.get("hostname") == host
        assert result["raw"].endswith("RuntimeError: disk failed")

    def test_multiline_datagram_severity_reads_every_line(self):
        """Severity detection sees the later lines of a multi-line datagram too."""
        result = parse_syslog_message(
            b"<134>Nov  1 12:00:00 host1 app[1]: starting job\n  step 2\nERROR: disk failed",
            "10.0.0.1",
            local_tz=datetime.timezone.utc,
        )
        assert result["message"].endswith("ERROR: disk failed")
        assert result["severity"] == 3

    def test_multiline_valkey_line_keeps_every_line(self):
        raw = b"1:M 02 Oct 2026 11:47:57.745 # first line\nsecond line"
        result = parse_syslog_message(raw, "10.0.0.1", local_tz=datetime.timezone.utc)
        assert result["message"] == "first line\nsecond line"


# ===================================================================
# 2. Network Listeners & Protocols
# ===================================================================

class TestSyslogNetworkAndProtocol:

    @pytest.mark.asyncio
    async def test_tcp_syslog_buffer_limit_disconnects(self, db_path: Path):
        """TCP protocol should discard buffer and close connection if buffer exceeds 64KB without newline."""
        asm = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(asm, alias_cache)

        class MockTransport:
            def __init__(self):
                self.closed = False
            def get_extra_info(self, name):
                return ("192.168.1.100", 514)
            def close(self):
                self.closed = True

        transport = MockTransport()
        proto.connection_made(transport)

        oversized_chunk = b"A" * (70 * 1024)
        proto.data_received(oversized_chunk)

        assert transport.closed is True
        assert proto.buffer == b""

    @pytest.mark.asyncio
    async def test_tcp_syslog_newline_framing(self, db_path: Path):
        """TCP protocol splits incoming chunks on newline and feeds assembler."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.5", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        data = b"<14>Jan  1 10:00:00 host1 app1: line 1\n<14>Jan  1 10:00:01 host1 app1: line 2\n"
        proto.data_received(data)
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["message"] == "line 1"
        assert received[1][1]["message"] == "line 2"

    @pytest.mark.asyncio
    async def test_udp_syslog_packet_handling(self, db_path: Path):
        """UDP protocol receives datagram and feeds assembler with resolved alias."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('10.0.0.9', 'nas-box', ?)",
                (now,),
            )
            conn.commit()

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)
        proto.datagram_received(
            b"<14>Jan  1 10:00:00 host1 app1: hello udp",
            ("10.0.0.9", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][0] == "10.0.0.9:app1"
        assert received[0][1]["source_alias"] == "nas-box"
        assert received[0][1]["message"] == "hello udp"

    @pytest.mark.asyncio
    async def test_udp_syslog_unaliased_ip_adopts_parsed_hostname(self, db_path: Path):
        """UDP message from unaliased IP should use parsed hostname as source_alias."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)

        # RFC 3164 with hostname 'gateway01'
        proto.datagram_received(
            b"<14>Jan  1 10:00:00 gateway01 dhcpd: DHCPACK on 192.168.1.50",
            ("192.168.1.1", 514),
        )
        # RFC 5424 with hostname 'pve-node2'
        proto.datagram_received(
            b"<134>1 2024-01-15T10:30:00.000Z pve-node2 qemu-server 1234 ID47 - VM 100 started",
            ("192.168.1.2", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["source_alias"] == "gateway01"
        assert received[0][0] == "192.168.1.1:dhcpd"
        assert received[1][1]["source_alias"] == "pve-node2"
        assert received[1][0] == "192.168.1.2:qemu-server"

    @pytest.mark.asyncio
    async def test_tcp_syslog_unaliased_ip_adopts_parsed_hostname(self, db_path: Path):
        """TCP message from unaliased IP should use parsed hostname as source_alias."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("192.168.1.3", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        proto.data_received(b"<14>Jan  1 10:00:00 pihole-dns dnsmasq: query[A] example.com\n")
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][1]["source_alias"] == "pihole-dns"
        assert received[0][1]["source_ip"] == "192.168.1.3"

    @pytest.mark.asyncio
    async def test_syslog_unaliased_ip_invalid_or_nil_hostname_retains_ip(self, db_path: Path):
        """When hostname is nil ('-') or unknown, source_alias should remain source_ip."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)

        proto.datagram_received(
            b"<165>1 2024-03-01T12:00:00Z - app 1234 - - Message with nil hostname",
            ("192.168.1.4", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][1]["source_alias"] == "192.168.1.4"

    @pytest.mark.asyncio
    async def test_syslog_hostname_matching_docker_alias_retains_ip(self, db_path: Path):
        """A hostname equal to a Docker host alias (any letter case) is not adopted, so the rows stay syslog rows."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        with get_connection(db_path) as conn:
            conn.execute("UPDATE docker_endpoints SET source_alias = 'tower' WHERE id = 1")
            conn.commit()

        alias_cache = AliasCache(db_path)
        assert alias_cache.is_docker_alias("Tower")
        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)

        proto.datagram_received(b"<14>Jan 15 10:00:00 Tower kernel: disk spun down", ("192.168.1.50", 514))
        proto.datagram_received(b"<14>Jan 15 10:00:01 nas01 kernel: disk spun up", ("192.168.1.51", 514))
        await asyncio.sleep(0.05)

        aliases = {entry["source_ip"]: entry["source_alias"] for _, entry in received}
        assert aliases == {"192.168.1.50": "192.168.1.50", "192.168.1.51": "nas01"}

    @pytest.mark.asyncio
    async def test_syslog_omitted_hostname_uses_configured_alias_or_ip(self, db_path: Path):
        """Messages omitting hostname (e.g. crond[330276]:) should resolve via host_aliases or retain source_ip."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.75', 'npm-server', ?)",
                (now,),
            )
            conn.commit()

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)

        # 1. From IP with alias configured: adopts 'npm-server'
        proto.datagram_received(
            b"<78>Sep  7 14:15:00 crond[330276]: USER root pid 718623 cmd run-parts /etc/periodic/15min",
            ("192.168.1.75", 514),
        )
        # 2. From unaliased IP: retains source_ip ('192.168.1.76'), does NOT become 'crond[330276]'
        proto.datagram_received(
            b"<78>Sep  7 14:15:00 crond[330276]: USER root pid 718623 cmd run-parts /etc/periodic/15min",
            ("192.168.1.76", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 2
        # Message 1:
        assert received[0][1]["source_alias"] == "npm-server"
        assert received[0][1]["app_name"] == "crond"
        assert received[0][1]["message"] == "USER root pid 718623 cmd run-parts /etc/periodic/15min"
        assert "hostname" not in received[0][1]

        # Message 2:
        assert received[1][1]["source_alias"] == "192.168.1.76"
        assert received[1][1]["app_name"] == "crond"
        assert received[1][1]["message"] == "USER root pid 718623 cmd run-parts /etc/periodic/15min"
        assert "hostname" not in received[1][1]

    @pytest.mark.asyncio
    async def test_syslog_server_start_and_stop(self, db_path: Path):
        """SyslogServer manages UDP and TCP sockets and shuts down cleanly."""
        asm = KeyedMultilineAssembler()
        server = SyslogServer(asm, db_path, host="127.0.0.1", port=0)

        # start() runs until stop(), so it goes in a task
        task = asyncio.create_task(server.start())
        await _wait_until(lambda: _listening(server))

        await server.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert server.alias_cache._refresh_task is None

    @pytest.mark.asyncio
    async def test_udp_syslog_bounded_queue_saturation_drops(self, db_path: Path, monkeypatch):
        """When UDP protocol queue fills up, extra packets are dropped and counted."""
        import app.core.pipeline as pipeline_mod

        # stop() parses the two queued datagrams
        monkeypatch.setattr(pipeline_mod, "_log_queue", asyncio.Queue())
        alias_cache = AliasCache(db_path)
        asm = KeyedMultilineAssembler()
        # The worker cannot run before the test awaits, so the queue of 2 fills
        proto = SyslogUDPProtocol(asm, alias_cache, max_queue_size=2)
        from app.core.pipeline import get_dropped_count
        initial_drops = get_dropped_count()

        proto.datagram_received(b"<14>Jan  1 10:00:00 host1 app1: msg 1", ("10.0.0.1", 514))
        proto.datagram_received(b"<14>Jan  1 10:00:00 host1 app1: msg 2", ("10.0.0.1", 514))
        assert proto.queue.qsize() == 2

        # 3rd packet exceeds maxsize=2 and is dropped
        proto.datagram_received(b"<14>Jan  1 10:00:00 host1 app1: msg 3", ("10.0.0.1", 514))
        assert get_dropped_count() == initial_drops + 1
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_frames_past_the_pause_mark_pause_the_connection_instead_of_dropping(
        self, db_path: Path, monkeypatch
    ):
        """A full parse queue stops the TCP connection reading; no line is lost, and reading resumes once it drains."""
        from unittest.mock import MagicMock
        import app.collectors.syslog as syslog_mod
        from app.core.pipeline import get_dropped_count

        dispatched = []

        async def record(data, source_ip, alias_cache, assembler):
            dispatched.append(data)

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", record)
        monkeypatch.setattr(syslog_mod, "TCP_PAUSE_ITEMS", 4)
        monkeypatch.setattr(syslog_mod, "TCP_RESUME_ITEMS", 1)
        proto = SyslogTCPProtocol(KeyedMultilineAssembler(), AliasCache(db_path), inactivity_timeout=5.0)
        transport = MagicMock()
        transport.get_extra_info.return_value = ("10.0.0.2", 54321)
        transport.is_closing.return_value = False
        proto.connection_made(transport)
        initial_drops = get_dropped_count()

        proto.data_received(b"<14>msg 1\n<14>msg 2\n")
        assert proto.queue.qsize() == 2
        assert not transport.pause_reading.called

        # Framing stops at the mark: the rest of the read waits as bytes, the connection stops
        # reading and its idle timer stops
        proto.data_received(b"".join(b"<14>msg %d\n" % i for i in range(3, 7)))
        assert proto.queue.qsize() == 4
        assert proto.buffer == b"<14>msg 5\n<14>msg 6\n"
        assert transport.pause_reading.call_count == 1
        assert proto.paused and proto._inactivity_handle is None

        await _wait_until(lambda: len(dispatched) == 6)
        assert dispatched == [b"<14>msg %d" % i for i in range(1, 7)]
        assert transport.resume_reading.call_count == 1
        assert not proto.paused and proto._inactivity_handle is not None
        assert proto.buffer == b""
        await proto.stop()
        assert get_dropped_count() == initial_drops

    @pytest.mark.asyncio
    async def test_paused_connections_resume_in_the_order_they_paused(self, db_path: Path, monkeypatch):
        """The first to pause queues the rest of its read first, so a sender read late each time still gets its turn."""
        from unittest.mock import MagicMock
        import app.collectors.syslog as syslog_mod
        from app.collectors.syslog import SyslogParseQueue

        dispatched = []

        async def record(data, source_ip, alias_cache, assembler):
            dispatched.append(data)

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", record)
        monkeypatch.setattr(syslog_mod, "TCP_PAUSE_ITEMS", 2)
        monkeypatch.setattr(syslog_mod, "TCP_RESUME_ITEMS", 1)
        shared = SyslogParseQueue(KeyedMultilineAssembler(), AliasCache(db_path, preload=False))
        protos = {}
        for name in ("c", "a", "b"):
            proto = SyslogTCPProtocol(shared.assembler, shared.alias_cache, parse_queue=shared)
            transport = MagicMock()
            transport.get_extra_info.return_value = (f"10.0.0.{len(protos) + 1}", 5000)
            transport.is_closing.return_value = False
            proto.connection_made(transport)
            protos[name] = proto
        # One read each, in this order: every connection pauses with part of its read left
        for name in ("c", "a", "b"):
            protos[name].data_received(b"".join(b"<14>%s%d\n" % (name.encode(), i) for i in range(3)))
        assert all(p.paused and p.buffer for p in protos.values())

        await _wait_until(lambda: len(dispatched) == 9)
        rest = [d.decode()[4:] for d in dispatched[4:]]
        # a filled the queue again and went to the back, behind b
        assert rest == ["c2", "a1", "b1", "b2", "a2"]
        for proto in protos.values():
            await proto.stop()
        await shared.close()

    @pytest.mark.asyncio
    async def test_paused_connection_closing_still_queues_the_rest_of_its_read(self, db_path: Path, monkeypatch):
        from unittest.mock import MagicMock
        import app.collectors.syslog as syslog_mod

        dispatched = []

        async def record(data, source_ip, alias_cache, assembler):
            dispatched.append(data)

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", record)
        monkeypatch.setattr(syslog_mod, "TCP_PAUSE_ITEMS", 2)
        proto = SyslogTCPProtocol(KeyedMultilineAssembler(), AliasCache(db_path))
        transport = MagicMock()
        transport.get_extra_info.return_value = ("10.0.0.2", 54321)
        transport.is_closing.return_value = False
        proto.connection_made(transport)
        proto.data_received(b"".join(b"<14>msg %d\n" % i for i in range(1, 6)))
        assert proto.paused and proto.queue.qsize() == 2

        proto.connection_lost(None)
        await proto.drain_task
        assert dispatched == [b"<14>msg %d" % i for i in range(1, 6)]

    @pytest.mark.asyncio
    async def test_udp_keeps_its_room_while_tcp_is_paused(self, db_path: Path, monkeypatch):
        """TCP pauses at its mark, so datagrams still fit up to the full queue size."""
        from unittest.mock import MagicMock
        import app.collectors.syslog as syslog_mod
        from app.collectors.syslog import SyslogParseQueue
        import app.core.pipeline as pipeline_mod
        from app.core.pipeline import get_dropped_count

        monkeypatch.setattr(pipeline_mod, "_log_queue", asyncio.Queue())
        monkeypatch.setattr(syslog_mod, "TCP_PAUSE_ITEMS", 2)
        shared = SyslogParseQueue(KeyedMultilineAssembler(), AliasCache(db_path), max_items=5)
        tcp = SyslogTCPProtocol(shared.assembler, shared.alias_cache, parse_queue=shared)
        transport = MagicMock()
        transport.get_extra_info.return_value = ("10.0.0.3", 5000)
        transport.is_closing.return_value = False
        tcp.connection_made(transport)
        udp = SyslogUDPProtocol(shared.assembler, shared.alias_cache, parse_queue=shared)
        initial_drops = get_dropped_count()

        tcp.data_received(b"<14>t1\n<14>t2\n")
        assert transport.pause_reading.called
        for i in range(4):
            udp.datagram_received(b"<14>u%d" % i, ("10.0.0.4", 514))
        assert shared.qsize() == 5
        assert get_dropped_count() == initial_drops + 1

        await tcp.stop()
        await udp.stop()
        await shared.close()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_single_message(self, db_path: Path):
        """TCP protocol parses single RFC 6587 octet-counted frame."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        msg = b"<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 - Octet-counted payload"
        frame = f"{len(msg)} ".encode("ascii") + msg

        proto.data_received(frame)
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][0] == "10.0.0.8:myapp"
        assert received[0][1]["message"] == "Octet-counted payload"
        assert received[0][1]["hostname"] == "srv01"
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_batched_messages(self, db_path: Path):
        """TCP protocol parses multiple RFC 6587 octet-counted frames in a single chunk (with and without newlines)."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        msg1 = b"<14>Jan  1 10:00:00 host1 app1: msg 1"
        msg2 = b"<14>Jan  1 10:00:01 host1 app1: msg 2"
        # Back-to-back without newlines
        batch1 = f"{len(msg1)} ".encode("ascii") + msg1 + f"{len(msg2)} ".encode("ascii") + msg2
        proto.data_received(batch1)
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["message"] == "msg 1"
        assert received[1][1]["message"] == "msg 2"

        # Trailing / interleaved newlines
        msg3 = b"<14>Jan  1 10:00:02 host1 app1: msg 3"
        batch2 = b"\n" + f"{len(msg3)} ".encode("ascii") + msg3 + b"\r\n"
        proto.data_received(batch2)
        await asyncio.sleep(0.05)

        assert len(received) == 3
        assert received[2][1]["message"] == "msg 3"
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_fragmented_frames(self, db_path: Path):
        """TCP protocol properly handles octet-counted frames split across multiple network packets."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        msg = b"<14>Jan  1 10:00:00 host1 app1: long message arriving in fragments"
        frame = f"{len(msg)} ".encode("ascii") + msg

        # Split 1: length prefix split
        proto.data_received(frame[:2])
        await asyncio.sleep(0.02)
        assert len(received) == 0

        # Split 2: payload body split
        proto.data_received(frame[2:20])
        await asyncio.sleep(0.02)
        assert len(received) == 0

        # Split 3: remainder
        proto.data_received(frame[20:])
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][1]["message"] == "long message arriving in fragments"
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_absurd_length_disconnects(self, db_path: Path):
        """TCP protocol disconnects and clears buffer if octet-counted frame length exceeds MAX_TCP_BUFFER."""
        asm = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(asm, alias_cache)

        class MockTransport:
            def __init__(self):
                self.closed = False
            def get_extra_info(self, name):
                return ("192.168.1.100", 514)
            def close(self):
                self.closed = True

        transport = MockTransport()
        proto.connection_made(transport)

        # Length 99999 exceeds 65536
        proto.data_received(b"99999 <14>message...")

        assert transport.closed is True
        assert proto.buffer == b""
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_mixed_framing_modes(self, db_path: Path):
        """TCP protocol dynamically handles both octet-counted and newline-delimited frames."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        # First send an octet-counted frame
        msg1 = b"<14>Jan  1 10:00:00 host1 app1: octet message"
        frame1 = f"{len(msg1)} ".encode("ascii") + msg1
        proto.data_received(frame1)

        # Next send a newline-delimited frame
        frame2 = b"<14>Jan  1 10:00:01 host1 app1: newline message\n"
        proto.data_received(frame2)

        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["message"] == "octet message"
        assert received[1][1]["message"] == "newline message"
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_logs_starting_with_numbers_not_misidentified_as_octet_counted(self, db_path: Path):
        """TCP protocol treats logs starting with numbers as newline-delimited frames rather than octet-counted frames."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))
            def get_active_stream_key_for_source(self, ip: str):
                return None

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def __init__(self):
                self.closed = False
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                self.closed = True

        transport = MockTransport()
        proto.connection_made(transport)

        # Send logs starting with numbers such as HTTP status codes
        frame1 = b"200 OK - HTTP GET /health\n"
        proto.data_received(frame1)

        frame2 = b"404 Not Found - GET /missing\n"
        proto.data_received(frame2)

        await asyncio.sleep(0.05)

        assert transport.closed is False
        assert proto.buffer == b""
        assert len(received) == 2
        assert "200 OK - HTTP GET /health" in received[0][1]["message"]
        assert "404 Not Found - GET /missing" in received[1][1]["message"]
        await proto.stop()


# ===================================================================
# 3. Syslog Configuration (SYSLOG_PORT)
# ===================================================================

class TestSyslogConfig:

    def test_syslog_port_default(self, monkeypatch):
        monkeypatch.delenv("SYSLOG_PORT", raising=False)
        assert get_syslog_port() == 1514

    def test_syslog_port_custom_valid(self, monkeypatch):
        monkeypatch.setenv("SYSLOG_PORT", "514")
        assert get_syslog_port() == 514

        monkeypatch.setenv("SYSLOG_PORT", "15140")
        assert get_syslog_port() == 15140

        monkeypatch.setenv("SYSLOG_PORT", " 1514 ")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "1")
        assert get_syslog_port() == 1

        monkeypatch.setenv("SYSLOG_PORT", "65535")
        assert get_syslog_port() == 65535

    def test_syslog_port_invalid_fallback(self, monkeypatch):
        monkeypatch.setenv("SYSLOG_PORT", "0")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "65536")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "-514")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "abc")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "")
        assert get_syslog_port() == 1514


# ===================================================================
# 4. Host Alias Cache & Resolution
# ===================================================================

class TestAliasCache:

    def test_alias_cache_resolves_known_ip(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.50', 'pihole', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        cache.load_aliases()
        assert cache.resolve("192.168.1.50") == "pihole"

    def test_alias_cache_falls_back_to_ip(self, db_path: Path):
        cache = AliasCache(db_path)
        cache.load_aliases()
        assert cache.resolve("10.99.99.99") == "10.99.99.99"

    def test_alias_cache_bulk_load(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            for i in range(50):
                conn.execute(
                    "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                    (f"10.0.0.{i}", f"host-{i}", now),
                )
            conn.commit()

        cache = AliasCache(db_path)
        cache.load_aliases()

        for i in range(50):
            assert cache.resolve(f"10.0.0.{i}") == f"host-{i}"
        assert cache.resolve("10.0.0.200") == "10.0.0.200"

    def test_alias_resolution_persisted(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.99', 'truenas-core', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        cache.load_aliases()
        resolved = cache.resolve("192.168.1.99")
        assert resolved == "truenas-core"

    @pytest.mark.asyncio
    async def test_alias_cache_async_lifecycle_and_refresh(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cache = AliasCache(db_path, refresh_interval=0.05)
        await cache.start()

        assert cache.resolve("192.168.1.10") == "192.168.1.10"

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.10', 'nas-primary', ?)",
                (now,),
            )
            conn.commit()

        await asyncio.sleep(0.12)
        assert cache.resolve("192.168.1.10") == "nas-primary"

        await cache.stop()
        assert cache._refresh_task is None

    def test_alias_cache_weakset_no_leak(self, db_path: Path):
        """AliasCache is registered in _active_caches via WeakSet and garbage collected without leaking."""
        import gc
        from app.collectors.syslog import _active_caches

        gc.collect()
        initial_count = len(_active_caches)
        cache = AliasCache(db_path)
        assert len(_active_caches) == initial_count + 1
        assert cache in _active_caches

        # Explicit stop removes it
        asyncio.run(cache.stop())
        assert cache not in _active_caches

        # Test garbage collection removal without explicit stop
        cache2 = AliasCache(db_path)
        assert cache2 in _active_caches
        del cache2
        gc.collect()
        # After gc, the destroyed cache is automatically pruned from WeakSet
        assert len(_active_caches) == initial_count


# ===================================================================
# 5. Multiline Traceback Assembly (Python Exceptions & Continuation)
# ===================================================================

class TestSyslogMultilineTracebackAssembly:

    @pytest.mark.asyncio
    async def test_udp_python_traceback_multiline_assembly(self, db_path: Path):
        """
        Verify that a multi-line Python traceback sent over UDP syslog as separate packets
        (including headerless continuation lines and unindented ConnectionResetError)
        assembles cleanly into a single log entry under the originating app and host.
        """
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogUDPProtocol(assembler, alias_cache)

        multiline_packets = [
            b"<131>Jan 15 10:00:00 app-server api-worker[8842]: ERROR: Unhandled exception during payment webhook processing",
            b"Traceback (most recent call last):",
            b'  File "/app/services/payment.py", line 42, in process_event',
            b"    response = await gateway.verify_signature(payload)",
            b"ConnectionResetError: [Errno 104] Connection reset by peer from remote gateway",
        ]

        for pkt in multiline_packets:
            await proto.process_message(pkt, "172.22.2.100")
            await asyncio.sleep(0.01)

        await assembler.flush_all()

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert len(items) == 1, f"Expected 1 assembled entry, got {len(items)}: {items}"
        entry = items[0]
        assert entry["app_name"] == "api-worker"
        assert entry["source_alias"] == "app-server"
        assert entry["source_ip"] == "172.22.2.100"
        assert entry["severity"] == 3  # Min severity across the batch (error)
        assert "ERROR: Unhandled exception" in entry["message"]
        assert "Traceback (most recent call last):" in entry["message"]
        assert 'File "/app/services/payment.py"' in entry["message"]
        assert "ConnectionResetError: [Errno 104]" in entry["message"]

        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_python_traceback_multiline_assembly(self, db_path: Path):
        """
        Verify that a multi-line Python traceback streamed over TCP with newline framing
        assembles cleanly into a single entry with parent app_name and source_alias.
        """
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(assembler, alias_cache)

        multiline_packets = [
            b"<131>Jan 15 10:00:00 app-server api-worker[8842]: ERROR: Unhandled exception during payment webhook processing",
            b"Traceback (most recent call last):",
            b'  File "/app/services/payment.py", line 42, in process_event',
            b"    response = await gateway.verify_signature(payload)",
            b"ConnectionResetError: [Errno 104] Connection reset by peer from remote gateway",
        ]

        for pkt in multiline_packets:
            await proto.process_message(pkt, "172.22.2.100")
            await asyncio.sleep(0.01)

        await assembler.flush_all()

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert len(items) == 1, f"Expected 1 assembled entry, got {len(items)}: {items}"
        entry = items[0]
        assert entry["app_name"] == "api-worker"
        assert entry["source_alias"] == "app-server"
        assert entry["severity"] == 3
        assert "ConnectionResetError: [Errno 104]" in entry["message"]

        await proto.stop()

    @pytest.mark.asyncio
    async def test_udp_chained_python_exception_assembly(self, db_path: Path):
        """
        Verify that Python chained exceptions (with 'During handling of the above exception...')
        assemble into a single log entry rather than fragmenting into multiple records.
        """
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogUDPProtocol(assembler, alias_cache)

        packets = [
            b"<131>Jan 15 10:00:00 srv01 webapp[1200]: Request failed",
            b"Traceback (most recent call last):",
            b'  File "/app/db.py", line 15, in get_user',
            b"TimeoutError: connection timed out",
            b"During handling of the above exception, another exception occurred:",
            b"Traceback (most recent call last):",
            b'  File "/app/views.py", line 30, in handle_request',
            b"RuntimeError: failed to render error page",
        ]

        for pkt in packets:
            await proto.process_message(pkt, "10.0.0.50")
            await asyncio.sleep(0.01)

        await assembler.flush_all()

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert len(items) == 1
        entry = items[0]
        assert entry["app_name"] == "webapp"
        assert "TimeoutError: connection timed out" in entry["message"]
        assert "During handling of the above exception" in entry["message"]
        assert "RuntimeError: failed to render error page" in entry["message"]

        await proto.stop()

    @pytest.mark.asyncio
    async def test_standalone_headerless_message_does_not_attach_without_active_stream(self, db_path: Path):
        """
        Verify that a headerless line arriving when no active stream exists from that source IP
        is safely ingested as an independent record with app_name 'unknown' and doesn't fail.
        """
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogUDPProtocol(assembler, alias_cache)

        await proto.process_message(b"Just an isolated text line from legacy script", "192.168.1.88")
        await assembler.flush_all()

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert len(items) == 1
        entry = items[0]
        assert entry["app_name"] == "unknown"
        assert entry["source_alias"] == "192.168.1.88"
        assert entry["message"] == "Just an isolated text line from legacy script"

        await proto.stop()


class TestSyslogTCPConnectionLimitsAndTimeout:
    """Unit tests for TCP connection limits, inactivity timeouts, and shared dispatch logic."""

    def test_constants_and_aliases(self):
        assert MAX_TCP_CONNECTIONS == 250
        assert TCP_INACTIVITY_TIMEOUT == 0.0

    @pytest.mark.asyncio
    async def test_tcp_connection_limit_rejection(self, db_path: Path):
        """When active TCP connections reach max_tcp_connections, new connections are rejected."""
        from unittest.mock import MagicMock
        assembler = KeyedMultilineAssembler()

        server = SyslogServer(
            assembler,
            db_path,
            max_tcp_connections=2,
        )

        def _remove_tcp_protocol(proto: SyslogTCPProtocol):
            server.tcp_protocols.discard(proto)

        def _create_protocol():
            if len(server.tcp_protocols) >= server.max_tcp_connections:
                return SyslogTCPProtocol(
                    server.assembler,
                    server.alias_cache,
                    on_close=_remove_tcp_protocol,
                    reject_on_connect=True,
                )
            proto = SyslogTCPProtocol(
                server.assembler,
                server.alias_cache,
                on_close=_remove_tcp_protocol,
                inactivity_timeout=server.tcp_inactivity_timeout,
            )
            server.tcp_protocols.add(proto)
            return proto

        assert server.active_tcp_connections == 0

        # Protocol 1 connects
        t1 = MagicMock()
        t1.get_extra_info.return_value = ("10.0.0.1", 1001)
        p1 = _create_protocol()
        p1.connection_made(t1)
        assert server.active_tcp_connections == 1
        assert not t1.close.called

        # Protocol 2 connects
        t2 = MagicMock()
        t2.get_extra_info.return_value = ("10.0.0.2", 1002)
        p2 = _create_protocol()
        p2.connection_made(t2)
        assert server.active_tcp_connections == 2
        assert not t2.close.called

        # Protocol 3 connects (exceeds limit: must be rejected on connect)
        t3 = MagicMock()
        t3.get_extra_info.return_value = ("10.0.0.3", 1003)
        p3 = _create_protocol()
        assert p3.reject_on_connect is True
        p3.connection_made(t3)
        assert t3.close.called
        assert server.active_tcp_connections == 2

        # Protocol 1 closes
        p1.connection_lost(None)
        assert server.active_tcp_connections == 1

        # Protocol 4 connects (now accepted because slot opened)
        t4 = MagicMock()
        t4.get_extra_info.return_value = ("10.0.0.4", 1004)
        p4 = _create_protocol()
        assert p4.reject_on_connect is False
        p4.connection_made(t4)
        assert not t4.close.called
        assert server.active_tcp_connections == 2

        await p1.stop()
        await p2.stop()
        await p3.stop()
        await p4.stop()

    @pytest.mark.asyncio
    async def test_tcp_inactivity_timeout(self, db_path: Path):
        """SyslogTCPProtocol closes transport if no data is received within inactivity_timeout."""
        from unittest.mock import MagicMock
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(
            assembler,
            alias_cache,
            inactivity_timeout=0.1,
        )

        transport = MagicMock()
        transport.get_extra_info.return_value = ("127.0.0.1", 12345)
        transport.is_closing.return_value = False

        proto.connection_made(transport)
        assert not transport.close.called

        # Wait for timeout to expire
        await asyncio.sleep(0.15)
        assert transport.close.called
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_inactivity_timer_reset_on_data(self, db_path: Path):
        """Inactivity timer is reset whenever data is received."""
        from unittest.mock import MagicMock
        class MockAssembler:
            async def feed(self, key, entry):
                pass
            def get_active_stream_key_for_source(self, ip):
                return None

        assembler = MockAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(
            assembler,
            alias_cache,
            inactivity_timeout=0.15,
        )

        transport = MagicMock()
        transport.get_extra_info.return_value = ("127.0.0.1", 12345)
        transport.is_closing.return_value = False

        proto.connection_made(transport)

        # Send data at 0.08s
        await asyncio.sleep(0.08)
        proto.data_received(b"<14>Jan 15 10:00:00 app-srv worker: heartbeat\n")
        assert not transport.close.called

        # After another 0.08s (total 0.16s, which would have expired without reset), still active
        await asyncio.sleep(0.08)
        assert not transport.close.called

        # Wait until remaining time expires without further data
        await asyncio.sleep(0.12)
        assert transport.close.called
        await proto.stop()

    @pytest.mark.asyncio
    async def test_dispatch_syslog_message_shared(self, db_path: Path):
        """dispatch_syslog_message parses, resolves alias, and feeds assembler correctly."""
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)

        q = get_queue()
        while not q.empty():
            q.get_nowait()

        data = b"<14>Jan 15 10:00:00 host01 my-daemon[42]: process started successfully"
        await dispatch_syslog_message(data, "10.0.0.99", alias_cache, assembler)
        await assembler.flush_all()

        assert not q.empty()
        item = q.get_nowait()
        assert item["app_name"] == "my-daemon"
        assert item["source_alias"] == "host01"
        assert item["message"] == "process started successfully"

    @pytest.mark.asyncio
    async def test_tcp_keepalive_socket_option(self, db_path: Path):
        """SyslogTCPProtocol sets SO_KEEPALIVE on accepted client socket."""
        import socket
        from unittest.mock import MagicMock
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(assembler, alias_cache)

        mock_socket = MagicMock()
        transport = MagicMock()
        def get_extra_info(name, default=None):
            if name == "socket":
                return mock_socket
            if name == "peername":
                return ("192.168.1.50", 55555)
            return default
        transport.get_extra_info.side_effect = get_extra_info

        proto.connection_made(transport)
        mock_socket.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_inactivity_disabled_by_default(self, db_path: Path):
        """SyslogTCPProtocol does not schedule an inactivity timer when inactivity_timeout is 0.0."""
        from unittest.mock import MagicMock
        assembler = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(assembler, alias_cache, inactivity_timeout=0.0)

        transport = MagicMock()
        transport.get_extra_info.return_value = ("192.168.1.50", 55555)
        proto.connection_made(transport)

        assert proto._inactivity_handle is None
        await asyncio.sleep(0.05)
        assert not transport.close.called
        await proto.stop()

    def test_config_syslog_tcp_env_vars(self, monkeypatch):
        """Config helpers parse SYSLOG_MAX_TCP_CONNECTIONS and SYSLOG_TCP_INACTIVITY_TIMEOUT."""
        from app.core.config import (
            get_syslog_max_tcp_connections,
            get_syslog_tcp_inactivity_timeout,
        )

        # Default values when env vars are unset
        monkeypatch.delenv("SYSLOG_MAX_TCP_CONNECTIONS", raising=False)
        monkeypatch.delenv("SYSLOG_TCP_INACTIVITY_TIMEOUT", raising=False)
        assert get_syslog_max_tcp_connections() == 250
        assert get_syslog_tcp_inactivity_timeout() == 0.0

        # Valid custom values
        monkeypatch.setenv("SYSLOG_MAX_TCP_CONNECTIONS", "500")
        monkeypatch.setenv("SYSLOG_TCP_INACTIVITY_TIMEOUT", "120.5")
        assert get_syslog_max_tcp_connections() == 500
        assert get_syslog_tcp_inactivity_timeout() == 120.5

        # Invalid fallback values
        monkeypatch.setenv("SYSLOG_MAX_TCP_CONNECTIONS", "0")
        monkeypatch.setenv("SYSLOG_TCP_INACTIVITY_TIMEOUT", "-10")
        assert get_syslog_max_tcp_connections() == 250
        assert get_syslog_tcp_inactivity_timeout() == 0.0

        monkeypatch.setenv("SYSLOG_MAX_TCP_CONNECTIONS", "invalid")
        monkeypatch.setenv("SYSLOG_TCP_INACTIVITY_TIMEOUT", "not_a_number")
        assert get_syslog_max_tcp_connections() == 250
        assert get_syslog_tcp_inactivity_timeout() == 0.0




# ===================================================================
# 7. Listener robustness: supervision, per-IP cap, truncation, drain
# ===================================================================

async def _wait_until(predicate, timeout: float = 2.0) -> None:
    """Poll predicate() until it is true, failing the test after timeout seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def _listening(server: SyslogServer) -> bool:
    return server.udp_transport is not None and server.tcp_server is not None


def _hold_port(kind: int):
    """Bind a socket on an OS-assigned loopback port so the syslog server cannot take it."""
    import socket as socket_mod

    sock = socket_mod.socket(socket_mod.AF_INET, kind)
    sock.bind(("127.0.0.1", 0))
    if kind == socket_mod.SOCK_STREAM:
        sock.listen()
    return sock


class TestSyslogListenerSupervision:
    """Bind failures raise so the supervisor retries, and start() runs until stop()."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("held", ["udp", "tcp"])
    async def test_start_raises_when_port_is_taken(self, db_path: Path, held: str):
        import socket as socket_mod

        sock = _hold_port(socket_mod.SOCK_DGRAM if held == "udp" else socket_mod.SOCK_STREAM)
        port = sock.getsockname()[1]
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=port)
        try:
            with pytest.raises(OSError):
                await asyncio.wait_for(server.start(), timeout=2.0)
            # A failed start leaves no half-open listener behind
            assert server.udp_transport is None
            assert server.tcp_server is None
        finally:
            sock.close()
            await server.stop()

    @pytest.mark.asyncio
    async def test_supervisor_retries_until_port_is_free(self, db_path: Path, monkeypatch):
        import socket as socket_mod
        from app.main import _supervise_worker

        real_sleep = asyncio.sleep

        async def fast_sleep(delay, *args, **kwargs):
            return await real_sleep(min(delay, 0.01), *args, **kwargs)

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)

        sock = _hold_port(socket_mod.SOCK_DGRAM)
        port = sock.getsockname()[1]
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=port)

        attempts = 0
        real_start = server.start

        async def counting_start():
            nonlocal attempts
            attempts += 1
            await real_start()

        task = asyncio.create_task(_supervise_worker(counting_start, "SyslogServer"))
        try:
            await _wait_until(lambda: attempts >= 2)
            assert not task.done()
            sock.close()
            await _wait_until(lambda: _listening(server))
            assert not task.done()
        finally:
            sock.close()
            await server.stop()
        # stop() ends the awaiting start(), so the supervisor finishes normally
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_stop_ends_running_start(self, db_path: Path):
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        await _wait_until(lambda: _listening(server))
        await asyncio.sleep(0.02)
        assert not task.done(), "start() must keep running while the listeners are up"

        await server.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert task.exception() is None
        assert server.udp_transport is None
        assert server.tcp_server is None
        assert server.alias_cache._refresh_task is None

    @pytest.mark.asyncio
    async def test_start_after_stop_does_not_bind(self, db_path: Path):
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        await server.stop()
        await asyncio.wait_for(server.start(), timeout=2.0)
        assert server.udp_transport is None
        assert server.tcp_server is None
        assert server.alias_cache._refresh_task is None

    @pytest.mark.asyncio
    async def test_stop_returns_with_connected_tcp_client(self, db_path: Path):
        """A persistent TCP sender (such as rsyslog) must not hang shutdown."""
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        await _wait_until(lambda: _listening(server))
        tcp_port = server.tcp_server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", tcp_port)
        try:
            await _wait_until(lambda: server.active_tcp_connections == 1)
            await asyncio.wait_for(server.stop(), timeout=3.0)
            await asyncio.wait_for(task, timeout=2.0)
            # The server closed the client connection
            assert await asyncio.wait_for(reader.read(), timeout=2.0) == b""
        finally:
            writer.close()

    @pytest.mark.asyncio
    async def test_unexpected_udp_listener_loss_raises_for_supervisor(self, db_path: Path):
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        try:
            await _wait_until(lambda: _listening(server))
            server.udp_transport.close()
            with pytest.raises(OSError):
                await asyncio.wait_for(task, timeout=2.0)
            # The failed run closed its other listener too, ready for a clean retry
            assert server.tcp_server is None
        finally:
            await server.stop()


class TestSyslogMessageCaps:
    """Oversize messages are truncated, and one IP cannot hold every TCP slot."""

    def test_oversize_message_is_truncated(self):
        from app.core.pipeline import MAX_MESSAGE_BYTES

        data = b"<13>Oct 11 22:14:15 myhost myapp: " + b"A" * (MAX_MESSAGE_BYTES * 3)
        result = parse_syslog_message(data, "10.0.0.1")
        assert len(result["raw"]) <= MAX_MESSAGE_BYTES
        assert len(result["message"]) <= MAX_MESSAGE_BYTES
        assert result["raw"].endswith("[truncated]")
        assert result["message"].endswith("[truncated]")
        assert result["app_name"] == "myapp"
        assert result["message"].startswith("AAAA")

    def test_message_at_limit_is_not_truncated(self):
        from app.core.pipeline import MAX_MESSAGE_BYTES

        data = b"A" * MAX_MESSAGE_BYTES
        result = parse_syslog_message(data, "10.0.0.1")
        assert result["raw"] == "A" * MAX_MESSAGE_BYTES
        assert "[truncated]" not in result["message"]

    def test_config_per_ip_limit(self, monkeypatch):
        from app.core.config import get_syslog_max_tcp_connections_per_ip

        monkeypatch.delenv("SYSLOG_MAX_TCP_CONNECTIONS_PER_IP", raising=False)
        assert get_syslog_max_tcp_connections_per_ip() == 20
        monkeypatch.setenv("SYSLOG_MAX_TCP_CONNECTIONS_PER_IP", "5")
        assert get_syslog_max_tcp_connections_per_ip() == 5
        for bad in ("0", "-3", "abc", ""):
            monkeypatch.setenv("SYSLOG_MAX_TCP_CONNECTIONS_PER_IP", bad)
            assert get_syslog_max_tcp_connections_per_ip() == 20

    @pytest.mark.asyncio
    async def test_per_ip_tcp_connection_cap(self, db_path: Path):
        """The 21st connection from one IP is refused while another IP still connects."""
        import socket as socket_mod

        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        writers = []
        try:
            await _wait_until(lambda: _listening(server))
            tcp_port = server.tcp_server.sockets[0].getsockname()[1]

            for _ in range(20):
                _, w = await asyncio.open_connection("127.0.0.1", tcp_port)
                writers.append(w)
            await _wait_until(lambda: server.active_tcp_connections == 20)

            reader21, w21 = await asyncio.open_connection("127.0.0.1", tcp_port)
            writers.append(w21)
            # Refused at accept time: the server closes it straight away
            assert await asyncio.wait_for(reader21.read(), timeout=2.0) == b""
            assert server.active_tcp_connections == 20

            # A different source IP (127.0.0.2 is loopback on Linux) is still accepted
            other = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
            other.bind(("127.0.0.2", 0))
            other.setblocking(False)
            await asyncio.get_running_loop().sock_connect(other, ("127.0.0.1", tcp_port))
            reader_o, w_o = await asyncio.open_connection(sock=other)
            writers.append(w_o)
            await _wait_until(lambda: server.active_tcp_connections == 21)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(reader_o.read(), timeout=0.1)
        finally:
            for w in writers:
                w.close()
            await server.stop()
            await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_per_ip_cap_slot_frees_on_disconnect(self, db_path: Path):
        server = SyslogServer(
            KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0,
            max_tcp_connections_per_ip=1,
        )
        task = asyncio.create_task(server.start())
        writers = []
        try:
            await _wait_until(lambda: _listening(server))
            tcp_port = server.tcp_server.sockets[0].getsockname()[1]
            _, w1 = await asyncio.open_connection("127.0.0.1", tcp_port)
            writers.append(w1)
            await _wait_until(lambda: server.active_tcp_connections == 1)

            w1.close()
            await _wait_until(lambda: server.active_tcp_connections == 0)

            reader2, w2 = await asyncio.open_connection("127.0.0.1", tcp_port)
            writers.append(w2)
            await _wait_until(lambda: server.active_tcp_connections == 1)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(reader2.read(), timeout=0.1)
        finally:
            for w in writers:
                w.close()
            await server.stop()
            await asyncio.wait_for(task, timeout=2.0)


class TestSyslogShutdownDrain:
    """`stop()` hands every queued message to the assembler before the worker ends."""

    @pytest.mark.asyncio
    async def test_udp_stop_drains_queued_datagrams(self, db_path: Path, monkeypatch):
        import app.collectors.syslog as syslog_mod

        dispatched = []

        async def fake_dispatch(data, source_ip, alias_cache, assembler):
            await asyncio.sleep(0)
            dispatched.append(data)

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", fake_dispatch)
        proto = SyslogUDPProtocol(KeyedMultilineAssembler(), AliasCache(db_path, preload=False))
        for i in range(100):
            proto.datagram_received(f"<14>msg {i}".encode(), ("10.0.0.1", 514))

        await proto.stop()
        assert len(dispatched) == 100
        assert proto.queue.qsize() == 0 and proto.queue._worker is None

    @pytest.mark.asyncio
    async def test_tcp_stop_drains_queued_frames(self, db_path: Path, monkeypatch):
        from unittest.mock import MagicMock
        import app.collectors.syslog as syslog_mod

        dispatched = []

        async def fake_dispatch(data, source_ip, alias_cache, assembler):
            await asyncio.sleep(0)
            dispatched.append(data)

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", fake_dispatch)
        proto = SyslogTCPProtocol(KeyedMultilineAssembler(), AliasCache(db_path, preload=False))
        transport = MagicMock()
        transport.get_extra_info.return_value = ("10.0.0.2", 5000)
        proto.connection_made(transport)
        proto.data_received(b"".join(f"<14>line {i}\n".encode() for i in range(100)))

        await proto.stop()
        assert len(dispatched) == 100
        assert transport.close.called

    @pytest.mark.asyncio
    async def test_udp_stop_cancels_stuck_workers_after_timeout(self, db_path: Path, monkeypatch):
        import app.collectors.syslog as syslog_mod

        async def stuck_dispatch(data, source_ip, alias_cache, assembler):
            await asyncio.Event().wait()

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", stuck_dispatch)
        monkeypatch.setattr(syslog_mod, "WORKER_DRAIN_TIMEOUT", 0.05)
        proto = SyslogUDPProtocol(KeyedMultilineAssembler(), AliasCache(db_path, preload=False))
        proto.datagram_received(b"<14>stuck", ("10.0.0.1", 514))
        await asyncio.sleep(0)

        await asyncio.wait_for(proto.stop(), timeout=1.0)
        assert proto.queue.qsize() == 0 and proto.queue._worker is None


class TestSyslogSharedParseQueue:
    """One parse queue and worker serve every listener and connection, without losing what the ingest queue can take."""

    @pytest.mark.asyncio
    async def test_many_busy_tcp_connections_lose_nothing_while_the_consumer_keeps_up(
        self, db_path: Path, monkeypatch
    ):
        from unittest.mock import MagicMock
        import app.core.pipeline as pipeline_mod
        from app.core.pipeline import get_dropped_count

        ingest = asyncio.Queue(maxsize=200)
        monkeypatch.setattr(pipeline_mod, "_log_queue", ingest)
        server = SyslogServer(KeyedMultilineAssembler(), db_path)
        stored = 0

        async def consume():
            nonlocal stored
            while True:
                await ingest.get()
                stored += 1

        consumer = asyncio.create_task(consume())
        initial_drops = get_dropped_count()
        protos = []
        for i in range(8):
            proto = server._create_tcp_protocol()
            transport = MagicMock()
            transport.get_extra_info.return_value = (f"10.0.1.{i}", 5000)
            transport.is_closing.return_value = False
            proto.connection_made(transport)
            protos.append(proto)
        try:
            # One pass of the event loop can hand every busy connection a full read
            for i, proto in enumerate(protos):
                proto.data_received(b"".join(
                    b"<14>Jan  1 10:00:00 host%d app%d: line %d\n" % (i, n % 3, n) for n in range(150)
                ))
            await _wait_until(lambda: all(p.queue.qsize() == 0 for p in protos))
            await server.assembler.flush_all()
            await _wait_until(lambda: ingest.qsize() == 0)
            assert get_dropped_count() == initial_drops
            assert stored == 8 * 150
        finally:
            consumer.cancel()
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_listeners_and_connections_share_one_queue(self, db_path: Path):
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        try:
            await _wait_until(lambda: _listening(server))
            first, second = server._create_tcp_protocol(), server._create_tcp_protocol()
            assert first.queue is second.queue is server.udp_protocol.queue is server.parse_queue
            # An idle connection starts no task of its own
            assert server.parse_queue._worker is None
        finally:
            await server.stop()
            await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_full_ingest_queue_waits_then_loses_until_half_empty(self, db_path: Path, monkeypatch):
        import app.collectors.syslog as syslog_mod
        import app.core.pipeline as pipeline_mod
        from app.collectors.syslog import SyslogParseQueue
        from app.core.pipeline import get_dropped_count

        ingest = pipeline_mod._IngestQueue(maxsize=4)
        monkeypatch.setattr(pipeline_mod, "_log_queue", ingest)
        monkeypatch.setattr(syslog_mod, "INGEST_ROOM_WAIT", 0.05)

        async def enqueue_only(data, source_ip, alias_cache, assembler):
            pipeline_mod.enqueue_entry({"message": data})

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", enqueue_only)
        shared = SyslogParseQueue(KeyedMultilineAssembler(), AliasCache(db_path, preload=False))
        loop = asyncio.get_running_loop()
        for i in range(4):
            ingest.put_nowait({"message": i})
        initial_drops = get_dropped_count()
        try:
            # Full: the worker waits for room, then gives up and the entry is lost
            begin = loop.time()
            shared.put_udp(b"a", "10.0.0.1")
            await _wait_until(lambda: get_dropped_count() == initial_drops + 1)
            assert loop.time() - begin >= 0.05

            # Still full: no second wait
            begin = loop.time()
            shared.put_udp(b"b", "10.0.0.1")
            await _wait_until(lambda: get_dropped_count() == initial_drops + 2)
            assert loop.time() - begin < 0.05

            # Below half full again: stored, and the next full queue is waited for again
            for _ in range(3):
                ingest.get_nowait()
            shared.put_udp(b"c", "10.0.0.1")
            await _wait_until(lambda: ingest.qsize() == 2)
            ingest.put_nowait({"message": "x"})
            ingest.put_nowait({"message": "y"})
            monkeypatch.setattr(syslog_mod, "INGEST_ROOM_WAIT", 5.0)
            shared.put_udp(b"d", "10.0.0.1")
            await asyncio.sleep(0.02)
            assert shared.qsize() == 0 and ingest.qsize() == 4
            # Woken once the consumer has taken enough for one message
            ingest.get_nowait()
            await asyncio.sleep(0.01)
            assert ingest.qsize() == 3
            ingest.get_nowait()
            await _wait_until(lambda: ingest.qsize() == 3)
            assert get_dropped_count() == initial_drops + 2
        finally:
            await shared.close()


class TestSyslogFutureTimestampWindow:
    """Syslog keeps timestamps up to 5 minutes ahead, like HTTP ingest and the queue consumer."""

    NOW = datetime.datetime(2026, 10, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)

    @staticmethod
    def _messages(ts: datetime.datetime) -> list[bytes]:
        return [
            f"<14>1 {ts.strftime('%Y-%m-%dT%H:%M:%SZ')} host app - - - rfc5424".encode(),
            f"<14>{ts.strftime('%b %d %H:%M:%S')} host app: rfc3164".encode(),
            f"<14>{ts.strftime('%Y-%m-%dT%H:%M:%SZ')} host app: iso".encode(),
        ]

    def test_timestamp_inside_window_is_kept(self):
        ahead = self.NOW + datetime.timedelta(minutes=4)
        for data in self._messages(ahead):
            parsed = parse_syslog_message(data, "10.0.0.1", now=self.NOW, local_tz=datetime.timezone.utc)
            assert parsed["timestamp"] == ahead.isoformat(), data

    def test_timestamp_beyond_window_uses_received_time(self):
        ahead = self.NOW + datetime.timedelta(minutes=10)
        for data in self._messages(ahead):
            parsed = parse_syslog_message(data, "10.0.0.1", now=self.NOW, local_tz=datetime.timezone.utc)
            assert parsed["timestamp"] == self.NOW.isoformat(), data

    def test_whole_hour_offset_is_still_corrected(self):
        """A sender one hour ahead (wrong time zone) is still pulled back by whole hours."""
        ahead = self.NOW + datetime.timedelta(hours=1, minutes=2)
        data = f"<14>1 {ahead.strftime('%Y-%m-%dT%H:%M:%SZ')} host app - - - tz".encode()
        parsed = parse_syslog_message(data, "10.0.0.1", now=self.NOW, local_tz=datetime.timezone.utc)
        assert parsed["timestamp"] == (self.NOW + datetime.timedelta(minutes=2)).isoformat()


class TestSyslogTimestampWithoutZone:
    """A timestamp without an offset is read as local time or UTC, whichever is nearer the received time."""

    NOW = datetime.datetime(2026, 10, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
    LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=5))

    @staticmethod
    def _messages(day: str, month: int, clock: str) -> list[bytes]:
        mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][month - 1]
        return [
            f"<14>1 2026-{month:02d}-{day}T{clock} host app - - - rfc5424".encode(),
            f"<14>{mon} {day} {clock} host app: rfc3164".encode(),
            f"<14>2026-{month:02d}-{day} {clock} host app: iso".encode(),
            f"[2026-{month:02d}-{day} {clock}] bracketed iso".encode(),
            f"[app] | {day}/{month:02d}/2026 {clock}  [INFO] day first".encode(),
            f"1:M {day} {mon} 2026 {clock}.000 # valkey".encode(),
        ]

    def test_local_time_is_chosen_when_it_is_nearer(self):
        for data in self._messages("19", 10, "17:00:00"):
            parsed = parse_syslog_message(data, "10.0.0.1", now=self.NOW, local_tz=self.LOCAL_TZ)
            assert parsed["timestamp"] == "2026-10-19T12:00:00+00:00", data

    def test_utc_is_chosen_when_it_is_nearer(self):
        for data in self._messages("19", 10, "11:59:00"):
            parsed = parse_syslog_message(data, "10.0.0.1", now=self.NOW, local_tz=self.LOCAL_TZ)
            assert parsed["timestamp"] == "2026-10-19T11:59:00+00:00", data


class TestSyslogTimestampHelpers:
    """The parser's own date reading and its cached local time zone."""

    def test_rfc3164_date_reads_like_strptime(self):
        from app.collectors.syslog import _rfc3164_datetime

        months = ["Jan", "Feb", "Jun", "Dec", "Foo"]
        days = ["1", "01", "9", "10", "28", "29", "30", "31", "0", "00", "32", "001", "123"]
        times = [("00", "00", "00"), ("23", "59", "59"), ("24", "00", "00"), ("12", "60", "00"), ("12", "00", "60"), ("12", "00", "61")]
        checked = 0
        for year in (2024, 2026):
            for mon in months:
                for day in days:
                    for hour, minute, second in times:
                        try:
                            expected = datetime.datetime.strptime(
                                f"{year} {mon} {day} {hour}:{minute}:{second}", "%Y %b %d %H:%M:%S"
                            )
                        except ValueError:
                            expected = None
                        try:
                            actual = _rfc3164_datetime(year, mon, day, hour, minute, second)
                        except ValueError:
                            actual = None
                        assert actual == expected, (year, mon, day, hour, minute, second)
                        checked += expected is not None
        assert checked > 0

    def test_local_time_zone_follows_a_zone_change_at_once(self, monkeypatch):
        import os
        import time

        from app.collectors.syslog import _current_local_tz

        original = os.environ.get("TZ")
        try:
            monkeypatch.setenv("TZ", "UTC")
            time.tzset()
            assert datetime.datetime(2026, 1, 15, tzinfo=_current_local_tz()).utcoffset() == datetime.timedelta(0)
            monkeypatch.setenv("TZ", "Asia/Kolkata")
            time.tzset()
            assert _current_local_tz().utcoffset(None) == datetime.timedelta(hours=5, minutes=30)
            raw = b"<14>Oct 19 17:30:00 host app: local time"
            now = datetime.datetime(2026, 10, 19, 12, 0, 5, tzinfo=datetime.timezone.utc)
            assert parse_syslog_message(raw, "10.0.0.1", now=now)["timestamp"].startswith("2026-10-19T12:00:00")
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_local_time_zone_is_read_again_after_a_second(self, monkeypatch):
        import app.collectors.syslog as syslog_mod

        clock = [1000.0]
        monkeypatch.setattr(syslog_mod.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(syslog_mod, "_local_tz_cache", None)
        first = syslog_mod._current_local_tz()
        fake = datetime.timezone(datetime.timedelta(hours=3))
        monkeypatch.setattr(syslog_mod, "_local_tz_cache", (1001.0, syslog_mod.time.tzname, fake))
        clock[0] = 1000.5
        assert syslog_mod._current_local_tz() is fake
        clock[0] = 1001.0
        assert syslog_mod._current_local_tz() == first


class TestSyslogTcpDisconnectDrain:
    """Frames read before a sender disconnects are still parsed, and stop() waits for them."""

    @staticmethod
    async def _send_and_disconnect(server: SyslogServer, count: int) -> None:
        tcp_port = server.tcp_server.sockets[0].getsockname()[1]
        _, writer = await asyncio.open_connection("127.0.0.1", tcp_port)
        await _wait_until(lambda: server.active_tcp_connections == 1)
        writer.write(b"".join(f"<14>line {i}\n".encode() for i in range(count)))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await _wait_until(lambda: server.active_tcp_connections == 0)

    @pytest.mark.asyncio
    async def test_frames_queued_before_disconnect_are_all_dispatched(self, db_path: Path, monkeypatch):
        import app.collectors.syslog as syslog_mod

        release = asyncio.Event()
        dispatched = []

        async def slow_dispatch(data, source_ip, alias_cache, assembler):
            await release.wait()
            dispatched.append(data)

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", slow_dispatch)
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        try:
            await _wait_until(lambda: _listening(server))
            await self._send_and_disconnect(server, 50)
            # The parser is still blocked: the frames wait in the shared queue
            assert dispatched == []
            assert server.parse_queue.qsize() == 49
            release.set()
            await _wait_until(lambda: len(dispatched) == 50)
            assert dispatched == [f"<14>line {i}".encode() for i in range(50)]
        finally:
            release.set()
            await server.stop()
            await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_stop_waits_for_disconnect_drains(self, db_path: Path, monkeypatch):
        import app.collectors.syslog as syslog_mod

        release = asyncio.Event()
        dispatched = []

        async def slow_dispatch(data, source_ip, alias_cache, assembler):
            await release.wait()
            dispatched.append(data)

        monkeypatch.setattr(syslog_mod, "dispatch_syslog_message", slow_dispatch)
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        try:
            await _wait_until(lambda: _listening(server))
            await self._send_and_disconnect(server, 20)
            assert server.parse_queue.qsize() == 19

            stop_task = asyncio.create_task(server.stop())
            for _ in range(10):
                await asyncio.sleep(0)
            assert not stop_task.done()
            release.set()
            await asyncio.wait_for(stop_task, timeout=2.0)
            assert len(dispatched) == 20
            assert server.parse_queue.qsize() == 0
        finally:
            release.set()
            await server.stop()
            await asyncio.wait_for(task, timeout=2.0)


class _FakeUdpSocket:
    """Records setsockopt calls and reports a chosen SO_RCVBUF from getsockopt."""

    def __init__(self, granted: int, fail_set: bool = False):
        self.granted = granted
        self.fail_set = fail_set
        self.set_calls: list[tuple] = []

    def setsockopt(self, level, option, value):
        self.set_calls.append((level, option, value))
        if self.fail_set:
            raise OSError("not permitted")

    def getsockopt(self, level, option):
        import socket as socket_mod

        assert (level, option) == (socket_mod.SOL_SOCKET, socket_mod.SO_RCVBUF)
        return self.granted


class TestSyslogUdpReceiveBuffer:
    """The UDP listener asks for a large receive buffer (SYSLOG_UDP_RECEIVE_BUFFER_BYTES)."""

    @pytest.fixture(autouse=True)
    def _linux_kernel(self, monkeypatch):
        import app.collectors.syslog as syslog_mod

        monkeypatch.setattr(syslog_mod, "_KERNEL_DOUBLES_RCVBUF", True)

    def test_config_default_and_overrides(self, monkeypatch, caplog):
        from app.core.config import get_syslog_udp_receive_buffer_bytes

        monkeypatch.delenv("SYSLOG_UDP_RECEIVE_BUFFER_BYTES", raising=False)
        assert get_syslog_udp_receive_buffer_bytes() == 8 * 1024 * 1024
        monkeypatch.setenv("SYSLOG_UDP_RECEIVE_BUFFER_BYTES", "0")
        assert get_syslog_udp_receive_buffer_bytes() == 0
        monkeypatch.setenv("SYSLOG_UDP_RECEIVE_BUFFER_BYTES", " 1048576 ")
        assert get_syslog_udp_receive_buffer_bytes() == 1048576
        monkeypatch.setenv("SYSLOG_UDP_RECEIVE_BUFFER_BYTES", "")
        assert get_syslog_udp_receive_buffer_bytes() == 8 * 1024 * 1024

    @pytest.mark.parametrize("bad", ["abc", "-1", "1.5", str(2**31)])
    def test_config_invalid_value_uses_default_with_warning(self, monkeypatch, caplog, bad):
        import logging

        from app.core.config import get_syslog_udp_receive_buffer_bytes

        monkeypatch.setenv("SYSLOG_UDP_RECEIVE_BUFFER_BYTES", bad)
        with caplog.at_level(logging.WARNING, logger="app.core.config"):
            assert get_syslog_udp_receive_buffer_bytes() == 8 * 1024 * 1024
        assert "SYSLOG_UDP_RECEIVE_BUFFER_BYTES" in caplog.text

    def test_requested_size_is_set_and_granted_size_logged(self, caplog):
        import logging
        import socket as socket_mod

        from app.collectors.syslog import configure_udp_receive_buffer

        sock = _FakeUdpSocket(granted=2 * 8388608)
        with caplog.at_level(logging.INFO, logger="app.collectors.syslog"):
            assert configure_udp_receive_buffer(sock, 8388608) == 8388608
        assert sock.set_calls == [(socket_mod.SOL_SOCKET, socket_mod.SO_RCVBUF, 8388608)]
        assert "8388608 bytes" in caplog.text
        assert "16777216" not in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_zero_leaves_the_os_default(self, caplog):
        import logging

        from app.collectors.syslog import configure_udp_receive_buffer

        sock = _FakeUdpSocket(granted=212992)
        with caplog.at_level(logging.INFO, logger="app.collectors.syslog"):
            assert configure_udp_receive_buffer(sock, 0) == 106496
        assert sock.set_calls == []
        assert "106496 bytes (OS default)" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.parametrize("reported, usable", [(425984, 212992), (8388608, 4194304)])
    def test_capped_grant_logs_rmem_max_hint(self, caplog, reported, usable):
        """
        Linux reports double the usable size, so 8 MiB reported for 8 MiB requested is a 4 MiB
        buffer. The warning states the usable size, never the doubled figure next to the request.
        """
        import logging

        from app.collectors.syslog import configure_udp_receive_buffer

        sock = _FakeUdpSocket(granted=reported)
        with caplog.at_level(logging.INFO, logger="app.collectors.syslog"):
            assert configure_udp_receive_buffer(sock, 8388608) == usable
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].startswith(f"Syslog UDP receive buffer is {usable} bytes, less than the 8388608 bytes")
        assert "sysctl -w net.core.rmem_max=8388608" in warnings[0]
        assert f"SYSLOG_UDP_RECEIVE_BUFFER_BYTES={usable}" in warnings[0]
        assert "sysctls" not in warnings[0]

    def test_other_systems_report_the_size_as_is(self, caplog, monkeypatch):
        import logging

        import app.collectors.syslog as syslog_mod

        monkeypatch.setattr(syslog_mod, "_KERNEL_DOUBLES_RCVBUF", False)
        with caplog.at_level(logging.INFO, logger="app.collectors.syslog"):
            assert syslog_mod.configure_udp_receive_buffer(_FakeUdpSocket(granted=8388608), 8388608) == 8388608
            assert syslog_mod.configure_udp_receive_buffer(_FakeUdpSocket(granted=4194304), 8388608) == 4194304
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "is 4194304 bytes" in warnings[0]

    def test_setsockopt_failure_does_not_raise(self, caplog):
        import logging

        from app.collectors.syslog import configure_udp_receive_buffer

        sock = _FakeUdpSocket(granted=212992, fail_set=True)
        with caplog.at_level(logging.WARNING, logger="app.collectors.syslog"):
            assert configure_udp_receive_buffer(sock, 8388608) == 106496
        assert "SYSLOG_UDP_RECEIVE_BUFFER_BYTES" in caplog.text

    @pytest.mark.asyncio
    async def test_server_applies_the_configured_size(self, db_path: Path, monkeypatch):
        import socket as socket_mod

        import app.collectors.syslog as syslog_mod

        calls = []
        monkeypatch.setattr(
            syslog_mod, "configure_udp_receive_buffer", lambda sock, requested: calls.append((sock, requested))
        )
        monkeypatch.setenv("SYSLOG_UDP_RECEIVE_BUFFER_BYTES", "262144")
        server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.start())
        try:
            await _wait_until(lambda: _listening(server))
            assert len(calls) == 1
            sock, requested = calls[0]
            assert requested == 262144
            assert sock.type == socket_mod.SOCK_DGRAM
        finally:
            await server.stop()
            await asyncio.wait_for(task, timeout=2.0)


SAMPLE_PROC_NET_SNMP = """Ip: Forwarding DefaultTTL InReceives InHdrErrors
Ip: 1 64 1017100 0
Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors IgnoredMulti MemErrors
Udp: 1017038 0 812722 4 812722 0 0 3330 0
UdpLite: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors IgnoredMulti MemErrors
UdpLite: 0 0 0 0 0 0 0 0 0
"""


class TestUdpReceiveErrorCounter:
    """UDP datagrams the kernel dropped (RcvbufErrors) are read from /proc/net/snmp."""

    def test_parser_reads_counters_by_header_name(self, tmp_path: Path):
        from app.collectors.syslog import read_udp_receive_buffer_errors, read_udp_snmp_counters

        snmp = tmp_path / "snmp"
        snmp.write_text(SAMPLE_PROC_NET_SNMP)
        counters = read_udp_snmp_counters(snmp)
        assert counters["RcvbufErrors"] == 812722
        assert counters["InErrors"] == 812722
        assert counters["InDatagrams"] == 1017038
        assert read_udp_receive_buffer_errors(snmp) == 812722

    def test_missing_or_malformed_file_gives_none(self, tmp_path: Path):
        from app.collectors.syslog import read_udp_receive_buffer_errors

        assert read_udp_receive_buffer_errors(tmp_path / "missing") is None
        bad = tmp_path / "bad"
        bad.write_text("Udp: InDatagrams RcvbufErrors\nUdp: 12 abc\n")
        assert read_udp_receive_buffer_errors(bad) is None
        no_udp = tmp_path / "no_udp"
        no_udp.write_text("Ip: Forwarding\nIp: 1\n")
        assert read_udp_receive_buffer_errors(no_udp) is None
        old_kernel = tmp_path / "old_kernel"
        old_kernel.write_text("Udp: InDatagrams NoPorts InErrors OutDatagrams\nUdp: 1 0 0 1\n")
        assert read_udp_receive_buffer_errors(old_kernel) is None

    def test_change_since_baseline(self, monkeypatch):
        import app.collectors.syslog as syslog_mod

        current = {"value": 1500}
        monkeypatch.setattr(syslog_mod, "read_udp_receive_buffer_errors", lambda path=None: current["value"])
        monkeypatch.setattr(syslog_mod, "_udp_receive_errors_baseline", None)
        assert syslog_mod.get_udp_receive_errors() is None  # no syslog listener started yet

        syslog_mod.record_udp_receive_errors_baseline()
        assert syslog_mod.get_udp_receive_errors() == 0
        current["value"] = 1742
        assert syslog_mod.get_udp_receive_errors() == 242
        current["value"] = None
        assert syslog_mod.get_udp_receive_errors() is None

    @pytest.mark.asyncio
    async def test_server_start_records_baseline_once(self, db_path: Path, monkeypatch):
        import app.collectors.syslog as syslog_mod

        current = {"value": 400}
        monkeypatch.setattr(syslog_mod, "read_udp_receive_buffer_errors", lambda path=None: current["value"])
        monkeypatch.setattr(syslog_mod, "_udp_receive_errors_baseline", None)
        for expected_baseline in (400, 400):
            server = SyslogServer(KeyedMultilineAssembler(), db_path, host="127.0.0.1", port=0)
            task = asyncio.create_task(server.start())
            try:
                await _wait_until(lambda: _listening(server))
                assert syslog_mod._udp_receive_errors_baseline == expected_baseline
            finally:
                await server.stop()
                await asyncio.wait_for(task, timeout=2.0)
            # A listener restart in the same process keeps counting from the first start
            current["value"] = 450
        assert syslog_mod.get_udp_receive_errors() == 50
