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
    SyslogTCPServerProtocol,
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
        server = SyslogServer(asm, db_path, host="127.0.0.1", port=11514)

        await server.start()
        assert server.udp_transport is not None
        assert server.tcp_server is not None

        await server.stop()
        assert server.alias_cache._refresh_task is None

    @pytest.mark.asyncio
    async def test_udp_syslog_bounded_queue_saturation_drops(self, db_path: Path):
        """When UDP protocol queue fills up, extra packets are dropped and counted."""
        alias_cache = AliasCache(db_path)
        asm = KeyedMultilineAssembler()
        # Initialize with max_queue_size=2 and num_workers=0 so queue fills
        proto = SyslogUDPProtocol(asm, alias_cache, max_queue_size=2, num_workers=0)
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
    async def test_tcp_syslog_bounded_queue_saturation_drops(self, db_path: Path):
        """When TCP protocol queue fills up, extra lines are dropped and counted."""
        alias_cache = AliasCache(db_path)
        asm = KeyedMultilineAssembler()
        proto = SyslogTCPProtocol(asm, alias_cache, max_queue_size=2, num_workers=0)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.2", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())
        from app.core.pipeline import get_dropped_count
        initial_drops = get_dropped_count()

        proto.data_received(b"<14>Jan  1 10:00:00 host1 app1: msg 1\n<14>Jan  1 10:00:00 host1 app1: msg 2\n")
        assert proto.queue.qsize() == 2

        proto.data_received(b"<14>Jan  1 10:00:00 host1 app1: msg 3\n")
        assert get_dropped_count() == initial_drops + 1
        await proto.stop()

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

    def test_alias_cache_get_alias_interface(self, db_path: Path):
        """get_alias returns resolved alias identically to resolve()."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.75', 'switch-core', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        assert cache.get_alias("192.168.1.75") == "switch-core"
        assert cache.get_alias("192.168.1.99") == "192.168.1.99"

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

        # Wait for assembler flush timeout (150ms)
        await asyncio.sleep(0.25)

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

        await asyncio.sleep(0.25)

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

        await asyncio.sleep(0.25)

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
        await asyncio.sleep(0.25)

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
        assert SyslogTCPServerProtocol is SyslogTCPProtocol

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
        await asyncio.sleep(0.25)

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




