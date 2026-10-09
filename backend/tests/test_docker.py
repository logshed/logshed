"""
Tests for Docker collector, framing, container filtering, and socket fallback.
"""

import asyncio
import datetime
import logging
import os
from pathlib import Path
from unittest.mock import patch
import socket
import pytest
import httpx

from app.core.migrations import get_connection, run_migrations
from app.collectors.syslog import AliasCache, reload_active_alias_caches
from app.core.pipeline import KeyedMultilineAssembler
from app.collectors.docker_collector import (
    MAX_TTY_BUFFER,
    DockerTailer,
    _batch_update_app_aliases,
    _build_client,
    _demux_stream,
    _detect_severity,
    _extract_docker_timestamp,
    _make_log_entry,
    _parse_docker_host,
    _parse_docker_log_line,
    _parse_docker_message_content,
    _should_ignore_container,
    _tail_container_logs,
    resolve_container_display_name,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "docker_test.db"
    run_migrations(p)
    return p


# ===================================================================
# 1. Docker Host Parsing
# ===================================================================

class TestDockerHostParsing:

    def test_unix_socket_default(self):
        """Default DOCKER_HOST should parse to unix socket."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DOCKER_HOST", None)
            base_url, uds_path = _parse_docker_host()
            assert uds_path == "/var/run/docker.sock"
            assert "localhost" in base_url

    def test_unix_socket_explicit(self):
        """Explicit unix:// DOCKER_HOST should parse correctly."""
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///var/run/docker.sock"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path == "/var/run/docker.sock"
            assert "localhost" in base_url

    def test_tcp_proxy(self):
        """tcp:// DOCKER_HOST should parse to HTTP endpoint with no UDS."""
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://proxy:2375"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert "proxy" in base_url
            assert "2375" in base_url

    def test_tcp_custom_port(self):
        """tcp:// with custom port should be preserved."""
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://192.168.1.100:2376"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert "192.168.1.100" in base_url
            assert "2376" in base_url

    def test_http_scheme(self):
        """http:// DOCKER_HOST should parse correctly."""
        with patch.dict(os.environ, {"DOCKER_HOST": "http://192.168.1.50:2375"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert base_url == "http://192.168.1.50:2375/v1.43"

    def test_https_scheme(self):
        """https:// DOCKER_HOST should parse correctly."""
        with patch.dict(os.environ, {"DOCKER_HOST": "https://docker.lan:2376"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert base_url == "https://docker.lan:2376/v1.43"


# ===================================================================
# 2. Docker Log Framing & Parsing
# ===================================================================

class TestDockerLogParsing:

    def test_multiplexed_stdout_frame(self):
        """Parse a Docker multiplexed stdout frame (stream_type=1)."""
        payload = b"Hello World!"
        header = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big")
        frame = header + payload
        result = _parse_docker_log_line(frame)
        assert result == "Hello World!"

    def test_multiplexed_stderr_frame(self):
        """Parse a Docker multiplexed stderr frame (stream_type=2)."""
        payload = b"Error occurred"
        header = bytes([2, 0, 0, 0]) + len(payload).to_bytes(4, "big")
        frame = header + payload
        result = _parse_docker_log_line(frame)
        assert result == "Error occurred"

    def test_raw_tty_mode_fallback(self):
        """Non-framed text should be returned as-is (TTY mode)."""
        raw = b"Just a plain log line\n"
        result = _parse_docker_log_line(raw)
        assert result == "Just a plain log line"

    def test_make_log_entry_fields(self):
        """_make_log_entry should produce correct dict shape defaulting to 'docker'."""
        entry = _make_log_entry("my-app", "abc123def", "Server started")
        assert entry["source_alias"] == "docker"
        assert entry["app_name"] == "my-app"
        assert entry["source_ip"] == "docker"
        assert entry["message"] == "Server started"
        assert entry["severity"] == 6
        assert entry["facility"] == 1
        datetime.datetime.fromisoformat(entry["timestamp"])
        datetime.datetime.fromisoformat(entry["received_at"])

    def test_make_log_entry_source_alias_override(self, monkeypatch):
        """_make_log_entry should respect DOCKER_SOURCE_ALIAS environment variable."""
        monkeypatch.setenv("DOCKER_SOURCE_ALIAS", "pve-node1-docker")
        entry = _make_log_entry("redis-cache", "xyz789", "Ready to accept connections")
        assert entry["source_alias"] == "pve-node1-docker"
        assert entry["app_name"] == "redis-cache"

    def test_make_log_entry_strips_ansi_and_control_chars(self):
        """_make_log_entry should strip ANSI escape sequences and non-printable control characters."""
        raw_msg = "2026-09-11T19:40:00.041Z [\x1b[32minfo\x1b[39m][Plex Scan]: Beginning scan \x1b[1;31merr\x1b[0m \x07"
        entry = _make_log_entry("seerr", "c123", raw_msg)
        assert entry["message"] == "2026-09-11T19:40:00.041Z [info][Plex Scan]: Beginning scan err "
        assert entry["raw"] == "2026-09-11T19:40:00.041Z [info][Plex Scan]: Beginning scan err "

    def test_should_ignore_container(self, monkeypatch):
        """Self-containers and explicitly excluded containers should be ignored."""
        # Default self-container names
        assert _should_ignore_container("abc123456789", "logshed") is True
        assert _should_ignore_container("abc123456789", "/log_shed") is True
        assert _should_ignore_container("abc123456789", "log-shed") is True

        # Non-self container
        assert _should_ignore_container("def987654321", "nginx") is False
        assert _should_ignore_container("def987654321", "/nextcloud") is False

        # Match container short ID against HOSTNAME env var
        monkeypatch.setenv("HOSTNAME", "abc123456789")
        assert _should_ignore_container("abc123456789def012345678", "custom-app-name") is True

        # Match custom DOCKER_EXCLUDE_CONTAINERS
        monkeypatch.setenv("DOCKER_EXCLUDE_CONTAINERS", "my-db,custom_redis")
        assert _should_ignore_container("111222333444", "my-db") is True
        assert _should_ignore_container("111222333444", "/custom_redis") is True
        assert _should_ignore_container("111222333444", "plex") is False

        # Match resolved display_name against exclusion list
        assert _should_ignore_container("111222333444", "raw_service_1", display_name="my-db") is True
        assert _should_ignore_container("111222333444", "logshed", display_name="some_service") is True
        assert _should_ignore_container("111222333444", "raw_service_2", display_name="logshed") is True
        assert _should_ignore_container("111222333444", "plex_raw", display_name="plex_alias") is False


# ===================================================================
# 3. Graceful Docker Socket Fallback
# ===================================================================

class TestDockerSocketFallback:

    @pytest.mark.asyncio
    async def test_socket_not_found_fallback(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

        orig_exists = os.path.exists

        def _mock_exists(path):
            if path == "/var/run/docker.sock":
                return False
            return orig_exists(path)

        monkeypatch.setattr(os.path, "exists", _mock_exists)

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler, socket_poll_interval=0.01, socket_poll_max=0.02)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.06)

        assert "Polling for socket before fallback" in caplog.text
        expected_msg = (
            "Docker socket not found at /var/run/docker.sock. "
            "Docker container tailing disabled; operating in syslog-only mode."
        )
        assert expected_msg in caplog.text
        assert not task.done()

        await tailer.stop()
        await task
        assert task.done()

    @pytest.mark.asyncio
    async def test_socket_polling_attaches_when_socket_appears(self, monkeypatch, caplog):
        """If docker.sock is missing at first but appears during polling, DockerTailer connects."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

        exists_calls = 0
        def _mock_exists(path):
            nonlocal exists_calls
            if path == "/var/run/docker.sock":
                exists_calls += 1
                return exists_calls >= 2  # becomes True on 2nd check
            return True

        monkeypatch.setattr(os.path, "exists", _mock_exists)

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler, socket_poll_interval=0.02, socket_poll_max=0.5)

        async def _mock_attach(client):
            pass

        async def _mock_watch(client):
            await tailer._cancel_event.wait()

        monkeypatch.setattr(tailer, "_attach_running_containers", _mock_attach)
        monkeypatch.setattr(tailer, "_watch_events", _mock_watch)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.08)

        assert "Docker socket found at /var/run/docker.sock" in caplog.text
        await tailer.stop()
        await task

    @pytest.mark.asyncio
    async def test_docker_disabled_by_config(self, monkeypatch, caplog):
        """Setting DOCKER_HOST=none immediately operates in syslog-only mode without polling delay."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("DOCKER_HOST", "none")

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.02)

        assert "Docker container tailing disabled by configuration; operating in syslog-only mode." in caplog.text
        await tailer.stop()
        await task
        assert task.done()

    @pytest.mark.asyncio
    async def test_remote_tcp_connection_failure_logging(self, monkeypatch, caplog):
        """Remote TCP connection failures should log descriptive error with target URL."""
        caplog.set_level(logging.WARNING)
        # Point to an unallocated port on localhost to trigger connection failure
        monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:59999")

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.15)

        await tailer.stop()
        await task
        assert "Docker connection error at http://127.0.0.1:59999" in caplog.text


# ===================================================================
# 4. Tailer Task Map Cleanup & Reconnection
# ===================================================================

class TestDockerTailerTaskCleanup:

    @pytest.mark.asyncio
    async def test_tailer_reconnects_on_transient_disconnect(self):
        """Container tailer task auto-reconnects on transient disconnect/EOF and stays in _tailers."""
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        container_id = "test_container_123456"
        container_name = "test_app"

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                if False:
                    yield b""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                return MockResponse()

        mock_client = MockClient()

        tailer._start_tailer(mock_client, container_id, container_name)
        assert container_id in tailer._tailers
        task, _ = tailer._tailers[container_id]

        # After EOF, the task does not die; it remains running and in _tailers
        await asyncio.sleep(0.05)
        assert not task.done()
        assert container_id in tailer._tailers

        # It terminates permanently when container is stopped
        await tailer._stop_tailer(container_id)
        assert task.done()
        assert container_id not in tailer._tailers

        await tailer.stop()

    @pytest.mark.asyncio
    async def test_tailer_task_permanent_termination_on_die_and_shutdown(self):
        """Tailers only terminate permanently when _stop_tailer() or stop() is called."""
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        container_id = "test_container_die"
        container_name = "test_die_app"

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                await asyncio.sleep(10)
                if False:
                    yield b""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                return MockResponse()

        mock_client = MockClient()
        tailer._start_tailer(mock_client, container_id, container_name)
        assert container_id in tailer._tailers
        task, _ = tailer._tailers[container_id]
        assert not task.done()

        # Stop tailer (simulate container die event)
        await tailer._stop_tailer(container_id)
        assert task.done()
        assert container_id not in tailer._tailers

        await tailer.stop()


# ===================================================================
# 5. Log Stream Framing & Severity Mapping
# ===================================================================

class TestDockerLogStreamSeverityAndReconnect:

    @pytest.mark.asyncio
    async def test_multiplexed_stderr_and_stdout_severity(self):
        """Multiplexed stream frames parse severity from content, not falsely marking stderr as Error."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed

        # Frame 1: stdout with info (stream_type = 1)
        stdout_msg = b"System initialized successfully\n"
        stdout_frame = bytes([1, 0, 0, 0]) + len(stdout_msg).to_bytes(4, "big") + stdout_msg

        # Frame 2: stderr with explicit ERROR (stream_type = 2)
        stderr_err_msg = b"ERROR: database connection failure\n"
        stderr_err_frame = bytes([2, 0, 0, 0]) + len(stderr_err_msg).to_bytes(4, "big") + stderr_err_msg

        # Frame 3: stderr with normal INFO (stream_type = 2)
        stderr_info_msg = b"[INFO] [celery.app.trace] Task succeeded in 0.05s\n"
        stderr_info_frame = bytes([2, 0, 0, 0]) + len(stderr_info_msg).to_bytes(4, "big") + stderr_info_msg

        combined_payload = stdout_frame + stderr_err_frame + stderr_info_frame

        cancel_event = asyncio.Event()

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                yield combined_payload
                cancel_event.set()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                return MockResponse()

        mock_client = MockClient()
        await _tail_container_logs(
            mock_client,
            "cid123",
            "test_app",
            assembler,
            cancel_event,
        )

        assert len(entries) == 3
        # stdout without explicit level defaults to INFO
        assert entries[0]["message"] == "System initialized successfully"
        assert entries[0]["severity"] == 6  # RFC Info

        # stderr with ERROR gets Error
        assert entries[1]["message"] == "ERROR: database connection failure"
        assert entries[1]["severity"] == 3  # RFC Error

        # stderr with INFO stays INFO (does NOT become a false error)
        assert entries[2]["message"] == "[INFO] [celery.app.trace] Task succeeded in 0.05s"
        assert entries[2]["severity"] == 6  # RFC Info

    @pytest.mark.asyncio
    async def test_auto_reconnect_on_remote_protocol_error(self, monkeypatch):
        """Transient RemoteProtocolError triggers retry without terminating the stream task."""
        monkeypatch.setattr("app.collectors.docker_collector._CONTAINER_INITIAL_BACKOFF", 0.01)

        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed

        cancel_event = asyncio.Event()
        attempts = 0

        class MockResponse1:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                if False:
                    yield b""
                raise httpx.RemoteProtocolError("Connection reset by peer")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockResponse2:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                msg = b"Reconnected and alive\n"
                frame = bytes([1, 0, 0, 0]) + len(msg).to_bytes(4, "big") + msg
                yield frame
                cancel_event.set()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return MockResponse1()
                return MockResponse2()

        mock_client = MockClient()
        await _tail_container_logs(
            mock_client,
            "cid123",
            "test_app",
            assembler,
            cancel_event,
        )

        assert attempts == 2
        assert len(entries) == 1
        assert entries[0]["message"] == "Reconnected and alive"
        assert entries[0]["severity"] == 6


# ===================================================================
# 6. Content-Aware Severity Detection
# ===================================================================

class TestDetectSeverity:

    def test_logfmt_severity(self):
        assert _detect_severity('time="2026-09-06T10:28:14Z" level=info msg="cache refreshed"') == 6
        assert _detect_severity('time="2026-09-06T10:28:14Z" level=error msg="failed to connect"') == 3
        assert _detect_severity('time="2026-09-06T10:28:14Z" level=warn msg="high memory"') == 4
        assert _detect_severity('level=warning msg="deprecated API"') == 4
        assert _detect_severity('lvl=debug msg="trace context"') == 7

    def test_bracketed_severity(self):
        assert _detect_severity("[2026-09-06 11:27:00,104] [INFO] [celery.app.trace] Task succeeded") == 6
        assert _detect_severity("[ERROR] database pool exhausted") == 3
        assert _detect_severity("[WARN] slow disk response") == 4
        assert _detect_severity("[DEBUG] user authenticated") == 7
        assert _detect_severity("[FATAL] out of memory") == 2

    def test_colon_prefix_severity(self):
        assert _detect_severity("INFO: 127.0.0.1:40676 - 'GET /api/healthz HTTP/1.1' 200 OK") == 6
        assert _detect_severity("ERROR: 127.0.0.1:40676 - 'GET /api/data HTTP/1.1' 500 Internal Server Error") == 3
        assert _detect_severity("WARNING: configuration file missing key, using default") == 4
        assert _detect_severity("LOG: checkpoint complete: wrote 25 buffers") == 6

    def test_timestamp_delimited_severity(self):
        assert _detect_severity("2026-09-06 10:20:04 INFO All scopes processed") == 6
        assert _detect_severity("2026-09-06 10:20:04 ERROR Sync failed for target host") == 3
        assert _detect_severity("2026-09-06 10:20:04 WARN Retry attempt 1") == 4

    def test_ansi_colored_severity(self):
        assert _detect_severity("2026-09-06T10:27:00.012Z \x1b[32minfo\x1b[39m|Jobs|: Starting scheduled job") == 6
        assert _detect_severity("2026-09-06T10:27:00.012Z \x1b[31merror\x1b[39m|Jobs|: Job failed") == 3

    def test_json_severity(self):
        assert _detect_severity('{"level": "info", "message": "server listening"}') == 6
        assert _detect_severity('{"level": "error", "message": "unhandled rejection"}') == 3

    def test_exceptions_and_panics(self):
        assert _detect_severity("Traceback (most recent call last):") == 3
        assert _detect_severity("Exception: failed to parse config") == 3
        assert _detect_severity("panic: runtime error: invalid memory address") == 2

    def test_fallback_unstructured_text(self):
        # Normal lines with no explicit log level default to RFC Info (6)
        assert _detect_severity("Server configuration was transferred successfully.") == 6
        assert _detect_severity("DHCP Server leased IP 172.22.2.161 to Wii") == 6
        assert _detect_severity("") == 6


# ===================================================================
# 7. Accurate Resume on Docker Reconnect (Item 2)
# ===================================================================

class TestDockerTimestampAndAccurateResume:

    def test_extract_docker_timestamp_rfc3339_nanoseconds(self):
        """Extract RFC3339 nano timestamp and strip message cleanly."""
        raw = "2026-09-07T10:15:30.123456789Z Service started on port 8080"
        ts, msg = _extract_docker_timestamp(raw)
        assert ts == "2026-09-07T10:15:30.123456789Z"
        assert msg == "Service started on port 8080"

    def test_extract_docker_timestamp_offset_and_empty_msg(self):
        """Extract timestamp with timezone offset and handle empty message."""
        raw = "2026-09-07T10:15:30.500+02:00 "
        ts, msg = _extract_docker_timestamp(raw)
        assert ts == "2026-09-07T10:15:30.500+02:00"
        assert msg == ""

        # Timestamp only, no trailing space
        raw2 = "2026-09-07T10:15:30Z"
        ts2, msg2 = _extract_docker_timestamp(raw2)
        assert ts2 == "2026-09-07T10:15:30Z"
        assert msg2 == ""

    def test_extract_docker_timestamp_non_matching(self):
        """Lines without Docker timestamp prefix are returned intact with ts=None."""
        raw = "Ordinary log line without timestamp"
        ts, msg = _extract_docker_timestamp(raw)
        assert ts is None
        assert msg == "Ordinary log line without timestamp"

    def test_make_log_entry_custom_timestamp(self):
        """_make_log_entry should preserve provided timestamp."""
        ts = "2026-09-07T10:15:30.123456789Z"
        entry = _make_log_entry("nginx", "c123", "Worker process started", severity=6, timestamp=ts)
        assert entry["timestamp"] == ts
        assert entry["message"] == "Worker process started"
        assert entry["raw"] == "Worker process started"

    def test_make_log_entry_custom_raw(self):
        """_make_log_entry preserves custom raw string while storing parsed message."""
        raw_line = 'time="2026-10-03T15:56:31Z" level=info msg="Refreshed cache."'
        entry = _make_log_entry("paperless", "c123", "Refreshed cache.", severity=6, raw=raw_line)
        assert entry["message"] == "Refreshed cache."
        assert entry["raw"] == raw_line

    def test_parse_docker_message_content_logfmt(self):
        """_parse_docker_message_content extracts msg="..." and explicit level."""
        line = 'time="2026-10-03T15:56:31Z" level=info msg="Successfully refreshed custom fields cache with 0 fields."'
        msg, sev = _parse_docker_message_content(line, 6)
        assert msg == "Successfully refreshed custom fields cache with 0 fields."
        assert sev == 6

    def test_parse_docker_message_content_valkey(self):
        """_parse_docker_message_content extracts Valkey warning line."""
        line = "1:M 02 Oct 2026 11:47:57.745 # Warning: No config file specified, using the default config. In order to specify a config file use valkey-server /path/to/valkey.conf"
        msg, sev = _parse_docker_message_content(line, 6)
        assert msg == "Warning: No config file specified, using the default config. In order to specify a config file use valkey-server /path/to/valkey.conf"
        assert sev == 4

    def test_parse_docker_message_content_maintainerr(self):
        """_parse_docker_message_content extracts Maintainerr pipe line."""
        line = "[maintainerr] | 03/10/2026 16:00:33  [INFO] [RuleExecutorService] Execution of rules for 'Never Watched by Anyone' done."
        msg, sev = _parse_docker_message_content(line, 6)
        assert msg == "[RuleExecutorService] Execution of rules for 'Never Watched by Anyone' done."
        assert sev == 6

    @pytest.mark.asyncio
    async def test_initial_attach_uses_tail_0_and_timestamps_true(self):
        """Initial attach queries /logs with timestamps=true and tail=0, without since."""
        assembler = KeyedMultilineAssembler()
        cancel_event = asyncio.Event()
        captured_params = []

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                cancel_event.set()
                if False:
                    yield b""
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                captured_params.append(kwargs.get("params", {}))
                return MockResp()

        client = MockClient()
        await _tail_container_logs(
            client,
            "cid_init",
            "app_init",
            assembler,
            cancel_event,
        )

        assert len(captured_params) >= 1
        params = captured_params[0]
        assert params.get("timestamps") == "true"
        assert params.get("tail") == "0"
        assert "since" not in params

    @pytest.mark.asyncio
    async def test_reconnect_resumes_with_since_last_seen_timestamp(self, monkeypatch):
        """When reconnecting after disconnect, queries with since=<last_seen> instead of tail=0."""
        monkeypatch.setattr("app.collectors.docker_collector._CONTAINER_INITIAL_BACKOFF", 0.01)
        assembler = KeyedMultilineAssembler()
        entries = []
        captured_params = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()
        call_count = 0

        ts1 = "2026-09-07T10:15:30.100000000Z"
        msg1 = f"{ts1} First line before drop\n".encode("utf-8")
        frame1 = bytes([1, 0, 0, 0]) + len(msg1).to_bytes(4, "big") + msg1

        ts2 = "2026-09-07T10:15:32.200000000Z"
        msg2 = f"{ts2} Second line after resume\n".encode("utf-8")
        frame2 = bytes([1, 0, 0, 0]) + len(msg2).to_bytes(4, "big") + msg2

        class MockResp1:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield frame1
                # Transient network reset
                raise httpx.RemoteProtocolError("Connection reset")
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockResp2:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield frame2
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                nonlocal call_count
                call_count += 1
                captured_params.append(kwargs.get("params", {}).copy())
                if call_count == 1:
                    return MockResp1()
                return MockResp2()

        last_seen_map = {}
        client = MockClient()
        await _tail_container_logs(
            client,
            "cid_resume",
            "app_resume",
            assembler,
            cancel_event,
            container_last_seen=last_seen_map,
        )

        assert call_count == 2
        # First call: initial attach with tail=0
        assert captured_params[0]["tail"] == "0"
        assert "since" not in captured_params[0]

        # Second call: reconnected with since as Unix epoch seconds and NO tail=0
        dt1 = datetime.datetime.fromisoformat(ts1.replace("Z", "+00:00"))
        assert captured_params[1]["since"] == str(int(dt1.timestamp()))
        assert "tail" not in captured_params[1]
        assert captured_params[1]["timestamps"] == "true"

        # Both entries parsed, messages stripped cleanly
        assert len(entries) == 2
        assert entries[0]["message"] == "First line before drop"
        assert entries[0]["timestamp"] == ts1
        assert entries[1]["message"] == "Second line after resume"
        assert entries[1]["timestamp"] == ts2

        # Memory tracker updated
        assert last_seen_map["cid_resume"] == ts2

    @pytest.mark.asyncio
    async def test_reconnect_since_http_400_resets_and_falls_back_to_tail_0(self, monkeypatch):
        """If Docker daemon rejects since parameter with 400, falls back to tail=0 and clears timestamp."""
        monkeypatch.setattr("app.collectors.docker_collector._CONTAINER_INITIAL_BACKOFF", 0.01)
        assembler = KeyedMultilineAssembler()
        cancel_event = asyncio.Event()
        captured_params = []
        call_count = 0

        ts1 = "2026-09-07T10:15:32.200000000Z"
        msg1 = f"{ts1} Line after fallback\n".encode("utf-8")
        frame1 = bytes([1, 0, 0, 0]) + len(msg1).to_bytes(4, "big") + msg1

        class MockResp400:
            status_code = 400
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockResp200:
            status_code = 200
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield frame1
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                nonlocal call_count
                call_count += 1
                captured_params.append(kwargs.get("params", {}))
                if call_count == 1:
                    return MockResp400()
                return MockResp200()

        last_seen_map = {"cid_400": "2026-09-07T10:15:30.100000000Z"}
        client = MockClient()
        await _tail_container_logs(
            client,
            "cid_400",
            "app_400",
            assembler,
            cancel_event,
            container_last_seen=last_seen_map,
        )

        assert call_count == 2
        # First call: attempted since
        assert "since" in captured_params[0]
        # Second call: fell back to tail=0
        assert captured_params[1]["tail"] == "0"
        assert "since" not in captured_params[1]
        assert "cid_400" not in last_seen_map or last_seen_map["cid_400"] == ts1

    @pytest.mark.asyncio
    async def test_multiline_continuation_with_docker_timestamps(self):
        """Multi-line exceptions with Docker timestamps on each line assemble correctly."""
        assembler = KeyedMultilineAssembler()
        queued_entries = []

        with patch("app.core.pipeline.get_queue") as mock_get_q:
            class DummyQ:
                def put_nowait(self, item):
                    queued_entries.append(item)
            mock_get_q.return_value = DummyQ()

            ts1 = "2026-09-07T10:15:30.100000000Z"
            ts2 = "2026-09-07T10:15:30.101000000Z"
            line1 = f"{ts1} Exception: connection failure\n".encode("utf-8")
            line2 = f"{ts2}     at com.example.Db.connect(Db.java:10)\n".encode("utf-8")
            frame = bytes([2, 0, 0, 0]) + len(line1 + line2).to_bytes(4, "big") + line1 + line2

            cancel_event = asyncio.Event()

            class MockResp:
                def raise_for_status(self):
                    pass
                async def aiter_bytes(self):
                    yield frame
                    cancel_event.set()
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass

            class MockClient:
                async def get(self, url, **kwargs):
                    class InspectResp:
                        status_code = 200
                        def json(self):
                            return {"Config": {"Tty": False}}
                    return InspectResp()
                def stream(self, method, url, **kwargs):
                    return MockResp()

            await _tail_container_logs(
                MockClient(),
                "cid_trace",
                "app_trace",
                assembler,
                cancel_event,
            )

            # Flush the assembler
            await asyncio.sleep(0.2)
            assert len(queued_entries) == 1
            entry = queued_entries[0]
            assert "Exception: connection failure" in entry["message"]
            assert "    at com.example.Db.connect(Db.java:10)" in entry["message"]
            # Timestamp prefix should NOT be present in the message
            assert ts1 not in entry["message"]
            assert ts2 not in entry["message"]


# ===================================================================
# 8. Remote TCP Keepalive & Read Timeout / Liveness (Item 5)
# ===================================================================

class TestDockerTcpKeepaliveAndLiveness:

    def test_build_client_tcp_keepalive_socket_options(self):
        """_build_client configures SO_KEEPALIVE on remote TCP hosts without UDS."""
        client = _build_client("http://docker-proxy:2375/v1.43", uds_path=None)
        assert client._transport is not None
        socket_opts = getattr(client._transport._pool, "_socket_options", None)
        assert socket_opts is not None
        # Check that SOL_SOCKET SO_KEEPALIVE is present in socket_opts
        has_keepalive = any(
            opt[0] == socket.SOL_SOCKET and opt[1] == socket.SO_KEEPALIVE and opt[2] == 1
            for opt in socket_opts
        )
        assert has_keepalive is True

    def test_build_client_uds_does_not_set_tcp_options(self):
        """_build_client for Unix domain socket does not set TCP keepalives."""
        client = _build_client("http://localhost/v1.43", uds_path="/var/run/docker.sock")
        socket_opts = getattr(client._transport._pool, "_socket_options", None)
        assert socket_opts is None

    def test_build_client_read_timeout_and_env(self, monkeypatch):
        """_build_client respects explicit read_timeout and DOCKER_READ_TIMEOUT env var."""
        # Explicit read_timeout
        client1 = _build_client("http://proxy:2375/v1.43", uds_path=None, read_timeout=12.5)
        assert client1.timeout.read == 12.5

        # From env var
        monkeypatch.setenv("DOCKER_READ_TIMEOUT", "45.0")
        client2 = _build_client("http://proxy:2375/v1.43", uds_path=None)
        assert client2.timeout.read == 45.0

    @pytest.mark.asyncio
    async def test_heartbeat_detects_half_open_connection_and_reconnects(self, monkeypatch, caplog):
        """When no bytes arrive and Docker ping fails, heartbeat raises ReadTimeout and reconnects."""
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr("app.collectors.docker_collector._CONTAINER_INITIAL_BACKOFF", 0.01)
        assembler = KeyedMultilineAssembler()
        cancel_event = asyncio.Event()
        attempts = 0

        class MockHungResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                # Hangs indefinitely simulating half-open TCP socket
                await asyncio.sleep(10)
                if False:
                    yield b""
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockNormalResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                msg = b"Recovered after dead connection\n"
                frame = bytes([1, 0, 0, 0]) + len(msg).to_bytes(4, "big") + msg
                yield frame
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                if url == "/_ping":
                    # Simulate proxy unreachable
                    raise httpx.ConnectError("Connection to docker-socket-proxy timed out")
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return MockHungResp()
                return MockNormalResp()

        # Run with short heartbeat interval of 0.05s
        await _tail_container_logs(
            MockClient(),
            "cid_dead",
            "app_dead",
            assembler,
            cancel_event,
            heartbeat_interval=0.05,
        )

        assert attempts == 2
        assert "Docker keepalive heartbeat failed" in caplog.text

    @pytest.mark.asyncio
    async def test_heartbeat_idle_container_with_healthy_ping_remains_connected(self):
        """When container is quiet but ping succeeds, heartbeat does not disconnect."""
        assembler = KeyedMultilineAssembler()
        cancel_event = asyncio.Event()
        ping_count = 0

        class MockQuietResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                # Idle for 0.12s, then yields 1 log and completes
                await asyncio.sleep(0.12)
                msg = b"Log after silence\n"
                frame = bytes([1, 0, 0, 0]) + len(msg).to_bytes(4, "big") + msg
                yield frame
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                nonlocal ping_count
                if url == "/_ping":
                    ping_count += 1
                    class PingResp:
                        status_code = 200
                    return PingResp()
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                return MockQuietResp()

        # Heartbeat every 0.04s, so ~2 pings occur during 0.12s silence
        await _tail_container_logs(
            MockClient(),
            "cid_quiet",
            "app_quiet",
            assembler,
            cancel_event,
            heartbeat_interval=0.04,
        )

        assert ping_count >= 1
        assert cancel_event.is_set()


# ===================================================================
# 9. Multiplexed Stream Frame Resync (Item 13)
# ===================================================================

class TestDockerDemuxFrameResync:

    def test_demux_stream_valid_frames(self):
        """Parse sequential stdout and stderr frames cleanly."""
        msg1 = b"stdout message"
        frame1 = bytes([1, 0, 0, 0]) + len(msg1).to_bytes(4, "big") + msg1
        msg2 = b"stderr error details"
        frame2 = bytes([2, 0, 0, 0]) + len(msg2).to_bytes(4, "big") + msg2

        frames, remaining = _demux_stream(frame1 + frame2)
        assert len(frames) == 2
        assert frames[0] == (1, msg1)
        assert frames[1] == (2, msg2)
        assert remaining == b""

    def test_demux_stream_corrupted_stream_type_resyncs(self, caplog):
        """Corrupted stream type byte is skipped with warning, recovering valid frame."""
        caplog.set_level(logging.WARNING)
        valid_msg = b"recovered frame"
        valid_frame = bytes([1, 0, 0, 0]) + len(valid_msg).to_bytes(4, "big") + valid_msg

        corrupted_buffer = b"\x99\xff\xee" + valid_frame
        frames, remaining = _demux_stream(corrupted_buffer)

        assert len(frames) == 1
        assert frames[0] == (1, valid_msg)
        assert remaining == b""
        assert "unexpected header (stream_type=153)" in caplog.text

    def test_demux_stream_corrupted_padding_resyncs(self, caplog):
        """Invalid padding bytes (non-zero) are skipped to resync."""
        caplog.set_level(logging.WARNING)
        valid_msg = b"clean frame"
        valid_frame = bytes([2, 0, 0, 0]) + len(valid_msg).to_bytes(4, "big") + valid_msg

        # stream_type is 1, but padding is 0x05, 0x00, 0x00
        bad_header = b"\x01\x05\x00\x00\x00\x00\x00\x04test"
        corrupted_buffer = bad_header + valid_frame

        frames, remaining = _demux_stream(corrupted_buffer)
        assert len(frames) == 1
        assert frames[0] == (2, valid_msg)
        assert remaining == b""

    def test_demux_stream_invalid_huge_payload_resyncs(self, caplog):
        """Payload claims > 16MB; discarded to avoid OOM and resyncs to next frame."""
        caplog.set_level(logging.WARNING)
        valid_msg = b"normal frame"
        valid_frame = bytes([1, 0, 0, 0]) + len(valid_msg).to_bytes(4, "big") + valid_msg

        # Frame claiming 50 MB
        huge_header = bytes([1, 0, 0, 0]) + (50 * 1024 * 1024).to_bytes(4, "big")
        corrupted_buffer = huge_header + valid_frame

        frames, remaining = _demux_stream(corrupted_buffer)
        assert len(frames) == 1
        assert frames[0] == (1, valid_msg)
        assert remaining == b""
        assert "invalid payload size 52428800" in caplog.text

    def test_demux_stream_partial_frame_buffering(self):
        """Partial frame header or payload is buffered until complete."""
        msg = b"buffered payload message"
        full_frame = bytes([1, 0, 0, 0]) + len(msg).to_bytes(4, "big") + msg

        # Feed first 4 bytes (partial header)
        frames1, rem1 = _demux_stream(full_frame[:4])
        assert len(frames1) == 0
        assert rem1 == full_frame[:4]

        # Feed remaining bytes
        frames2, rem2 = _demux_stream(rem1 + full_frame[4:])
        assert len(frames2) == 1
        assert frames2[0] == (1, msg)
        assert rem2 == b""

    @pytest.mark.asyncio
    async def test_corrupted_frame_does_not_mutate_is_tty(self):
        """Corrupt frame header in _tail_container_logs does not permanently mutate is_tty to True."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()

        # Corrupted byte followed by valid frame 1, then another valid frame 2
        msg1 = b"Frame 1 after corruption\n"
        frame1 = bytes([1, 0, 0, 0]) + len(msg1).to_bytes(4, "big") + msg1
        msg2 = b"Frame 2 multiplexed intact\n"
        frame2 = bytes([2, 0, 0, 0]) + len(msg2).to_bytes(4, "big") + msg2

        payload = b"\xde\xad\xbe\xef" + frame1 + frame2

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield payload
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        await _tail_container_logs(
            MockClient(),
            "cid_resync",
            "app_resync",
            assembler,
            cancel_event,
        )

        # Both frames should be successfully extracted despite corruption at byte 0
        assert len(entries) == 2
        assert entries[0]["message"] == "Frame 1 after corruption"
        assert entries[1]["message"] == "Frame 2 multiplexed intact"

    def test_demux_stream_multiple_corruptions_between_valid_frames(self):
        """Corrupted bytes between two valid frames are cleanly skipped."""
        msg1 = b"First valid"
        frame1 = bytes([1, 0, 0, 0]) + len(msg1).to_bytes(4, "big") + msg1
        msg2 = b"Second valid"
        frame2 = bytes([2, 0, 0, 0]) + len(msg2).to_bytes(4, "big") + msg2

        garbage = b"\x00\x03\x99\xff\xee\x12\x34\x56\x78\x9a"
        frames, remaining = _demux_stream(frame1 + garbage + frame2)

        assert len(frames) == 2
        assert frames[0] == (1, msg1)
        assert frames[1] == (2, msg2)
        assert remaining == b""

    def test_extract_docker_timestamp_subsecond_variations(self):
        """Handle 0, 3, 6, and 9 subsecond decimal variations."""
        for ts_in in [
            "2026-09-07T10:15:30Z",
            "2026-09-07T10:15:30.123Z",
            "2026-09-07T10:15:30.123456Z",
            "2026-09-07T10:15:30.123456789Z",
        ]:
            line = f"{ts_in} test payload"
            ts, msg = _extract_docker_timestamp(line)
            assert ts == ts_in
            assert msg == "test payload"

    @pytest.mark.asyncio
    async def test_tty_mode_with_timestamps_and_reconnect(self, monkeypatch):
        """TTY container mode extracts timestamps and accurately resumes with since."""
        monkeypatch.setattr("app.collectors.docker_collector._CONTAINER_INITIAL_BACKOFF", 0.01)
        assembler = KeyedMultilineAssembler()
        entries = []
        captured_params = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()
        attempts = 0

        ts1 = "2026-09-07T10:15:30.100Z"
        raw1 = f"{ts1} TTY First line\r\n".encode("utf-8")
        ts2 = "2026-09-07T10:15:32.200Z"
        raw2 = f"{ts2} TTY Second line\r\n".encode("utf-8")

        class MockTtyResp1:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield raw1
                raise httpx.RemoteProtocolError("TTY drop")
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockTtyResp2:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield raw2
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockTtyClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": True}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                nonlocal attempts
                attempts += 1
                captured_params.append(kwargs.get("params", {}).copy())
                if attempts == 1:
                    return MockTtyResp1()
                return MockTtyResp2()

        last_seen = {}
        await _tail_container_logs(
            MockTtyClient(),
            "cid_tty",
            "app_tty",
            assembler,
            cancel_event,
            container_last_seen=last_seen,
        )

        assert attempts == 2
        dt1 = datetime.datetime.fromisoformat(ts1.replace("Z", "+00:00"))
        assert captured_params[1]["since"] == str(int(dt1.timestamp()))
        assert "tail" not in captured_params[1]

        assert len(entries) == 2
        assert entries[0]["message"] == "TTY First line"
        assert entries[0]["timestamp"] == ts1
        assert entries[1]["message"] == "TTY Second line"
        assert entries[1]["timestamp"] == ts2

    @pytest.mark.asyncio
    async def test_heartbeat_disabled_when_zero_or_negative(self):
        """Setting heartbeat_interval <= 0 disables background heartbeat monitor without error."""
        assembler = KeyedMultilineAssembler()
        cancel_event = asyncio.Event()
        ping_called = False

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                await asyncio.sleep(0.05)
                yield b"Plain message\n"
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                nonlocal ping_called
                if url == "/_ping":
                    ping_called = True
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": True}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        await _tail_container_logs(
            MockClient(),
            "cid_nohb",
            "app_nohb",
            assembler,
            cancel_event,
            heartbeat_interval=0,
        )

        assert ping_called is False
        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_watch_events_heartbeat_failure_raises_read_timeout(self, monkeypatch):
        """_watch_events detects silent events stream stall via ping failure and raises ReadTimeout."""
        monkeypatch.setenv("DOCKER_HEARTBEAT_INTERVAL", "0.04")
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        class MockHungEventsResp:
            def raise_for_status(self):
                pass
            async def aiter_lines(self):
                await asyncio.sleep(10)
                if False:
                    yield ""
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                if url == "/_ping":
                    raise httpx.ConnectError("Docker socket proxy hung")
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockHungEventsResp()

        with pytest.raises(httpx.ReadTimeout, match="Docker events keepalive heartbeat failed"):
            await tailer._watch_events(MockClient())

    @pytest.mark.asyncio
    async def test_supervisor_stop_gracefully_terminates_idle_events_stream(self):
        """When Docker tailer is stopped while waiting on quiet events, it terminates without hanging."""
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        class MockHungEventsResp:
            def raise_for_status(self):
                pass
            async def aiter_lines(self):
                await asyncio.sleep(100)
                yield ""
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockHungEventsResp()

        watch_task = asyncio.create_task(tailer._watch_events(MockClient()))
        await asyncio.sleep(0.05)

        # Signal stop
        await tailer.stop()

        # Must exit within 1.0s without deadlocking
        await asyncio.wait_for(watch_task, timeout=1.0)
        assert watch_task.done()

    @pytest.mark.asyncio
    async def test_container_tailer_cancel_event_gracefully_terminates_idle_container(self):
        """When container tailer is cancelled while stream is idle, it exits immediately without hanging."""
        assembler = KeyedMultilineAssembler()
        cancel_event = asyncio.Event()

        class MockIdleResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                await asyncio.sleep(100)
                yield b""
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockIdleResp()

        tail_task = asyncio.create_task(
            _tail_container_logs(
                MockClient(),
                "cid_idle_stop",
                "app_idle_stop",
                assembler,
                cancel_event,
                heartbeat_interval=30.0,
            )
        )
        await asyncio.sleep(0.05)

        # Trigger cancellation
        cancel_event.set()

        # Must terminate promptly without waiting for socket read or heartbeat
        await asyncio.wait_for(tail_task, timeout=1.0)
        assert tail_task.done()

    @pytest.mark.asyncio
    async def test_reconnect_deduplicates_replayed_lines_with_identical_timestamp(self, monkeypatch):
        """Docker daemon's inclusive 'since' replays the last seen log line; verify it is deduplicated."""
        monkeypatch.setattr("app.collectors.docker_collector._CONTAINER_INITIAL_BACKOFF", 0.01)
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()
        attempts = 0

        ts1 = "2026-09-07T10:15:30.100000000Z"
        msg1 = f"{ts1} Line at boundary\n".encode("utf-8")
        frame1 = bytes([1, 0, 0, 0]) + len(msg1).to_bytes(4, "big") + msg1

        ts2 = "2026-09-07T10:15:32.200000000Z"
        msg2 = f"{ts2} New line after resume\n".encode("utf-8")
        frame2 = bytes([1, 0, 0, 0]) + len(msg2).to_bytes(4, "big") + msg2

        class MockResp1:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield frame1
                raise httpx.RemoteProtocolError("Connection dropped")
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockResp2:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                # Real Docker daemon replays frame1 because since is inclusive (>= ts1)
                yield frame1
                yield frame2
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return MockResp1()
                return MockResp2()

        last_seen = {}
        last_msgs = {}
        await _tail_container_logs(
            MockClient(),
            "cid_dedup",
            "app_dedup",
            assembler,
            cancel_event,
            container_last_seen=last_seen,
            container_last_messages=last_msgs,
        )

        assert attempts == 2
        # Exactly 2 entries emitted: frame1 is NOT duplicated despite being replayed by Docker
        assert len(entries) == 2
        assert entries[0]["message"] == "Line at boundary"
        assert entries[0]["timestamp"] == ts1
        assert entries[1]["message"] == "New line after resume"
        assert entries[1]["timestamp"] == ts2

    @pytest.mark.asyncio
    async def test_empty_log_line_with_timestamp_preserved(self):
        """An empty log line emitted with a Docker timestamp is preserved as an entry with message=''."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()

        ts = "2026-09-07T10:15:30.123456789Z"
        msg = f"{ts}\n".encode("utf-8")
        frame = bytes([1, 0, 0, 0]) + len(msg).to_bytes(4, "big") + msg

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield frame
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        await _tail_container_logs(
            MockClient(),
            "cid_empty",
            "app_empty",
            assembler,
            cancel_event,
        )

        assert len(entries) == 1
        assert entries[0]["message"] == ""
        assert entries[0]["timestamp"] == ts
        assert entries[0]["severity"] == 6

    @pytest.mark.asyncio
    async def test_tty_trailing_buffer_without_newline_emitted_at_eof(self):
        """In TTY mode, trailing buffer without trailing newline is emitted when stream terminates."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()

        ts = "2026-09-07T10:15:30.500Z"
        raw = f"{ts} Final unbuffered status".encode("utf-8")

        class MockTtyResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                # Ends without trailing newline
                yield raw
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": True}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockTtyResp()

        task = asyncio.create_task(
            _tail_container_logs(
                MockClient(),
                "cid_tty_eof",
                "app_tty_eof",
                assembler,
                cancel_event,
                heartbeat_interval=0,
            )
        )
        await asyncio.sleep(0.05)
        cancel_event.set()
        await task

        assert len(entries) == 1
        assert entries[0]["message"] == "Final unbuffered status"
        assert entries[0]["timestamp"] == ts

    @pytest.mark.asyncio
    async def test_multiplexed_stream_chunk_line_buffering(self):
        """In multiplexed mode, line buffer retains incomplete line fragments across frames and chunks."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()

        # Frame 1: stdout with incomplete first line fragment
        msg1 = b"Part 1 of line 1, "
        frame1 = bytes([1, 0, 0, 0]) + len(msg1).to_bytes(4, "big") + msg1

        # Frame 2: stdout with remainder of line 1, complete line 2, and partial line 3
        msg2 = b"part 2 completed\nLine 2 complete\nLine 3 starts"
        frame2 = bytes([1, 0, 0, 0]) + len(msg2).to_bytes(4, "big") + msg2

        # Frame 3: stderr with remainder of line 3
        msg3 = b" and finished\n"
        frame3 = bytes([2, 0, 0, 0]) + len(msg3).to_bytes(4, "big") + msg3

        class MockMultiplexedResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                # Yield frame1 first
                yield frame1
                # Yield frame2 in two chunks to test chunk-level and frame-level buffering
                mid = len(frame2) // 2
                yield frame2[:mid]
                yield frame2[mid:]
                # Yield frame3
                yield frame3
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockMultiplexedResp()

        task = asyncio.create_task(
            _tail_container_logs(
                MockClient(),
                "cid_mux_buf",
                "app_mux_buf",
                assembler,
                cancel_event,
                heartbeat_interval=0,
            )
        )
        await asyncio.sleep(0.08)
        cancel_event.set()
        await task

        assert len(entries) == 3
        assert entries[0]["message"] == "Part 1 of line 1, part 2 completed"
        assert entries[1]["message"] == "Line 2 complete"
        assert entries[2]["message"] == "Line 3 starts and finished"

    @pytest.mark.asyncio
    async def test_multiplexed_trailing_buffer_without_newline_emitted_at_eof(self):
        """In multiplexed mode, trailing line fragment without newline is emitted when stream terminates."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()

        msg = b"Final multiplexed line without newline"
        frame = bytes([1, 0, 0, 0]) + len(msg).to_bytes(4, "big") + msg

        class MockMuxResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield frame
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockMuxResp()

        task = asyncio.create_task(
            _tail_container_logs(
                MockClient(),
                "cid_mux_eof",
                "app_mux_eof",
                assembler,
                cancel_event,
                heartbeat_interval=0,
            )
        )
        await asyncio.sleep(0.05)
        cancel_event.set()
        await task

        assert len(entries) == 1
        assert entries[0]["message"] == "Final multiplexed line without newline"

    def test_max_tty_buffer_constant(self):
        assert MAX_TTY_BUFFER == 65536

    @pytest.mark.asyncio
    async def test_tty_buffer_cap_splits_without_newline(self, caplog):
        """When TTY buffer exceeds MAX_TTY_BUFFER without newline, it splits the first 64KB and logs warning."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()

        # Create 70,000 bytes with no newline
        raw_payload = b"X" * 70000

        class MockLargeTtyResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield raw_payload
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockLargeClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": True}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockLargeTtyResp()

        with caplog.at_level(logging.WARNING):
            task = asyncio.create_task(
                _tail_container_logs(
                    MockLargeClient(),
                    "cid_tty_large",
                    "app_tty_large",
                    assembler,
                    cancel_event,
                    heartbeat_interval=0,
                )
            )
            await asyncio.sleep(0.08)
            cancel_event.set()
            await task

        # Should split into two entries: 65536 bytes chunk, then remainder of 4464 bytes
        assert len(entries) == 2
        assert len(entries[0]["message"]) == 65536
        assert len(entries[1]["message"]) == 70000 - 65536
        assert any("exceeded 65536 bytes without newline" in record.message for record in caplog.records)

    def test_build_client_connection_pool_limits(self):
        """_build_client configures higher pool limits to avoid starvation across containers."""
        client_uds = _build_client("http://localhost/v1.43", "/var/run/docker.sock")
        assert client_uds._transport._pool._max_connections == 500
        assert client_uds._transport._pool._max_keepalive_connections == 100

        client_tcp = _build_client("http://proxy:2375/v1.43", None)
        assert client_tcp._transport._pool._max_connections == 500
        assert client_tcp._transport._pool._max_keepalive_connections == 100


# ===================================================================
# 11. Docker Host Alias Resolution & Lifecycle
# ===================================================================

class TestDockerHostAliases:

    def test_make_log_entry_resolves_alias_from_cache(self, db_path: Path):
        """_make_log_entry resolves source_alias to configured alias from AliasCache."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'docker-unraid', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        entry = _make_log_entry("my-app", "c123", "Started container", alias_cache=cache)
        assert entry["source_ip"] == "docker"
        assert entry["source_alias"] == "docker-unraid"

    def test_make_log_entry_unmapped_defaults_to_docker(self, db_path: Path):
        """_make_log_entry defaults source_alias to 'docker' when no alias is configured."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.1', 'router', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        entry = _make_log_entry("my-app", "c123", "Started container", alias_cache=cache)
        assert entry["source_ip"] == "docker"
        assert entry["source_alias"] == "docker"

    def test_make_log_entry_env_override_with_cache_resolution(self, db_path: Path, monkeypatch):
        """_make_log_entry resolves DOCKER_SOURCE_ALIAS with alias cache fallback."""
        monkeypatch.setenv("DOCKER_SOURCE_ALIAS", "unraid-tower")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        cache = AliasCache(db_path)
        # Without alias in DB, uses env override
        entry = _make_log_entry("nginx", "c1", "test 1", alias_cache=cache)
        assert entry["source_alias"] == "unraid-tower"

        # When 'docker' has an alias, it overrides the env default
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'docker-primary', ?)",
                (now,),
            )
            conn.commit()
        cache.load_aliases()
        entry2 = _make_log_entry("nginx", "c1", "test 2", alias_cache=cache)
        assert entry2["source_alias"] == "docker-primary"

    def test_make_log_entry_custom_env_alias_precedence(self, db_path: Path, monkeypatch):
        """When both customized DOCKER_SOURCE_ALIAS and 'docker' have aliases, the specific custom alias takes precedence."""
        monkeypatch.setenv("DOCKER_SOURCE_ALIAS", "unraid-tower")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'generic-docker', ?)",
                (now,),
            )
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('unraid-tower', 'specific-unraid', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        entry = _make_log_entry("nginx", "c1", "test precedence", alias_cache=cache)
        assert entry["source_alias"] == "specific-unraid"

    def test_docker_tailer_initializes_with_alias_cache(self, db_path: Path):
        """DockerTailer initializes and preloads AliasCache from db_path."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'docker-unraid', ?)",
                (now,),
            )
            conn.commit()

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler, db_path=db_path)
        assert tailer.alias_cache.resolve("docker") == "docker-unraid"

    @pytest.mark.asyncio
    async def test_docker_tailer_resolves_alias_on_container_log_ingestion(self, db_path: Path):
        """_tail_container_logs sets resolved source_alias on all assembled entries."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'docker-unraid', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        cancel_event = asyncio.Event()

        # Build mock multiplexed log frame
        ts = "2026-09-08T08:52:30.123456789Z"
        msg = f"{ts} Container application healthy\n".encode("utf-8")
        payload = b"\x01\x00\x00\x00" + len(msg).to_bytes(4, "big") + msg

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield payload
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        await _tail_container_logs(
            MockClient(), "cid1", "web-app",
            assembler, cancel_event,
            alias_cache=cache,
            heartbeat_interval=0,
        )

        assert len(entries) == 1
        assert entries[0]["source_ip"] == "docker"
        assert entries[0]["source_alias"] == "docker-unraid"
        assert entries[0]["app_name"] == "web-app"
        assert entries[0]["message"] == "Container application healthy"

    @pytest.mark.asyncio
    async def test_docker_tailer_runtime_alias_update_and_reload(self, db_path: Path):
        """When host alias is updated and caches reloaded, new logs adopt the new alias immediately."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        tailer = DockerTailer(assembler, db_path=db_path)

        # Initially no alias mapped for 'docker'
        assert tailer.alias_cache.resolve("docker") == "docker"

        stream_queue = asyncio.Queue()
        cancel_event = asyncio.Event()

        class MockStreamingResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                while not cancel_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                        yield chunk
                    except asyncio.TimeoutError:
                        continue
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockStreamingResp()

        tailer_task = asyncio.create_task(
            _tail_container_logs(
                MockClient(), "cid_stream", "app_stream",
                assembler, cancel_event,
                alias_cache=tailer.alias_cache,
                heartbeat_interval=0,
            )
        )

        def _make_frame(text: str) -> bytes:
            payload = text.encode("utf-8")
            return b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload

        # 1. Send first log line before alias update
        await stream_queue.put(_make_frame("2026-09-08T08:50:00.000000000Z Line 1 unaliased\n"))
        await asyncio.sleep(0.05)
        assert len(entries) == 1
        assert entries[0]["source_alias"] == "docker"

        # 2. Update alias to 'docker-unraid' and trigger reload_active_alias_caches
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'docker-unraid', ?)",
                (now,),
            )
            conn.commit()
        reload_active_alias_caches()

        # 3. Send second log line after alias update
        await stream_queue.put(_make_frame("2026-09-08T08:52:30.000000000Z Line 2 aliased to unraid\n"))
        await asyncio.sleep(0.05)
        assert len(entries) == 2
        assert entries[1]["source_alias"] == "docker-unraid"

        # 4. Update alias again to 'unraid-main' and trigger reload
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE host_aliases SET alias = 'unraid-main' WHERE ip = 'docker'"
            )
            conn.commit()
        reload_active_alias_caches()

        # 5. Send third log line
        await stream_queue.put(_make_frame("2026-09-08T08:55:00.000000000Z Line 3 second alias\n"))
        await asyncio.sleep(0.05)
        assert len(entries) == 3
        assert entries[2]["source_alias"] == "unraid-main"

        # Cleanup
        cancel_event.set()
        await tailer_task
        await tailer.stop()

    @pytest.mark.asyncio
    async def test_docker_tailer_runtime_alias_delete(self, db_path: Path):
        """Deleting an alias reverts incoming Docker logs back to 'docker'."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'docker-unraid', ?)",
                (now,),
            )
            conn.commit()

        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed
        tailer = DockerTailer(assembler, db_path=db_path)
        assert tailer.alias_cache.resolve("docker") == "docker-unraid"

        stream_queue = asyncio.Queue()
        cancel_event = asyncio.Event()

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                while not cancel_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
                        yield chunk
                    except asyncio.TimeoutError:
                        continue
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        tailer_task = asyncio.create_task(
            _tail_container_logs(
                MockClient(), "cid_del", "app_del",
                assembler, cancel_event,
                alias_cache=tailer.alias_cache,
                heartbeat_interval=0,
            )
        )

        def _make_frame(text: str) -> bytes:
            payload = text.encode("utf-8")
            return b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload

        # Line 1: aliased
        await stream_queue.put(_make_frame("2026-09-08T08:50:00.000000000Z Line 1 aliased\n"))
        await asyncio.sleep(0.05)
        assert len(entries) == 1
        assert entries[0]["source_alias"] == "docker-unraid"

        # Delete alias and reload active caches
        with get_connection(db_path) as conn:
            conn.execute("DELETE FROM host_aliases WHERE ip = 'docker'")
            conn.commit()
        reload_active_alias_caches()

        # Line 2: reverted to 'docker'
        await stream_queue.put(_make_frame("2026-09-08T08:51:00.000000000Z Line 2 unaliased\n"))
        await asyncio.sleep(0.05)
        assert len(entries) == 2
        assert entries[1]["source_alias"] == "docker"

        cancel_event.set()
        await tailer_task
        await tailer.stop()

    @pytest.mark.asyncio
    async def test_docker_tailer_persists_across_restart(self, db_path: Path):
        """Simulates container restart: alias in host_aliases persists and applies to new DockerTailer."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('docker', 'docker-unraid', ?)",
                (now,),
            )
            conn.commit()

        assembler1 = KeyedMultilineAssembler()
        tailer1 = DockerTailer(assembler1, db_path=db_path)
        assert tailer1.alias_cache.resolve("docker") == "docker-unraid"
        await tailer1.stop()

        # Restart LogShed container: new DockerTailer instantiated from persisted db_path
        assembler2 = KeyedMultilineAssembler()
        tailer2 = DockerTailer(assembler2, db_path=db_path)
        assert tailer2.alias_cache.resolve("docker") == "docker-unraid"

        entries = []
        async def capture_feed(key, entry):
            entries.append(entry)
        assembler2.feed = capture_feed

        cancel_event = asyncio.Event()
        ts = "2026-09-08T09:00:00.000000000Z"
        msg = f"{ts} Post-restart log entry\n".encode("utf-8")
        payload = b"\x01\x00\x00\x00" + len(msg).to_bytes(4, "big") + msg

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield payload
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        await _tail_container_logs(
            MockClient(), "cid_restart", "app_restart",
            assembler2, cancel_event,
            alias_cache=tailer2.alias_cache,
            heartbeat_interval=0,
        )

        assert len(entries) == 1
        assert entries[0]["source_ip"] == "docker"
        assert entries[0]["source_alias"] == "docker-unraid"
        assert entries[0]["message"] == "Post-restart log entry"

        await tailer2.stop()


class TestDockerContainerMemoryPruning:
    @pytest.mark.asyncio
    async def test_dockertailer_pruning_removes_exited_container_keys(self):
        """DockerTailer discovery purges exited container IDs from _container_last_seen and _container_last_messages."""
        from unittest.mock import AsyncMock, MagicMock

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        # Seed tracking state with running and exited containers
        tailer._container_last_seen = {
            "running_container_1": "2026-09-30T12:00:00Z",
            "exited_container_2": "2026-09-30T11:59:00Z",
            "stopped_container_3": "2026-09-30T11:58:00Z",
        }
        tailer._container_last_messages = {
            "running_container_1": {"msg1", "msg2"},
            "exited_container_2": {"old_msg"},
            "stopped_container_3": {"dead_msg"},
        }

        # Mock client returning only running_container_1
        mock_containers = [
            {"Id": "running_container_1", "Names": ["/running_container_1"]},
        ]
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_containers
        mock_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_resp

        discovered = await tailer._discover_containers(mock_client)
        assert len(discovered) == 1

        # Verify running container tracking state is retained
        assert "running_container_1" in tailer._container_last_seen
        assert "running_container_1" in tailer._container_last_messages

        # Verify exited containers are purged
        assert "exited_container_2" not in tailer._container_last_seen
        assert "stopped_container_3" not in tailer._container_last_seen
        assert "exited_container_2" not in tailer._container_last_messages
        assert "stopped_container_3" not in tailer._container_last_messages


class TestContainerDisplayResolution:
    """Tests for resolve_container_display_name hierarchy."""

    def test_resolve_logshed_alias_override(self):
        """logshed.alias label takes highest priority."""
        labels = {
            "logshed.alias": "Accounting Service",
            "com.docker.compose.service": "accounting",
            "com.docker.swarm.service.name": "swarm-acc",
        }
        res = resolve_container_display_name("c1234567890123", raw_name="my_acc_container", labels=labels)
        assert res == "Accounting Service"

    def test_resolve_logshed_name_override(self):
        """logshed.name label acts as explicit user override."""
        labels = {
            "logshed.name": "Billing Service",
            "com.docker.compose.service": "billing",
        }
        res = resolve_container_display_name("c1234567890123", raw_name="my_billing_container", labels=labels)
        assert res == "Billing Service"

    def test_resolve_compose_service_label(self):
        """com.docker.compose.service takes priority over swarm, raw container name, and id."""
        labels = {
            "com.docker.compose.service": "radarr",
            "com.docker.compose.project": "homelab",
            "com.docker.swarm.service.name": "swarm-radarr",
        }
        res = resolve_container_display_name("c1234567890123", raw_name="homelab_radarr_1", labels=labels)
        assert res == "radarr"

    def test_resolve_swarm_service_label(self):
        """com.docker.swarm.service.name takes priority over raw container name when no compose label."""
        labels = {
            "com.docker.swarm.service.name": "web-cluster-worker",
        }
        res = resolve_container_display_name("c1234567890123", raw_name="web-cluster-worker.1.xyz", labels=labels)
        assert res == "web-cluster-worker"

    def test_resolve_raw_container_name_fallback(self):
        """Fallback to raw container name when no labels are present."""
        res = resolve_container_display_name("c1234567890123", raw_name="/custom-unraid-app", labels={})
        assert res == "custom-unraid-app"

    def test_resolve_fallback_to_short_cid(self):
        """Fallback to 12-char container ID when no name or labels exist."""
        res = resolve_container_display_name("abcdef1234567890", raw_name="", labels={})
        assert res == "abcdef123456"

    def test_resolve_strip_slashes_and_whitespace(self):
        """Leading slashes and whitespace are cleanly stripped from resolved names."""
        labels = {"logshed.alias": "  /my-aliased-app  "}
        res = resolve_container_display_name("c123", raw_name="/raw", labels=labels)
        assert res == "my-aliased-app"


class TestBatchUpdateAppAliases:
    """Tests for _batch_update_app_aliases and DockerTailer app alias scheduling."""

    def test_batch_update_app_aliases_updates_existing_docker_logs(self, db_path: Path):
        """_batch_update_app_aliases retroactively modifies app_name for existing docker logs."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            for i in range(5):
                cursor.execute(
                    """
                    INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                    VALUES (?, ?, 'docker', 'docker', 'homelab_radarr_1', 1, 6, ?, ?)
                    """,
                    (now, now, f"msg {i}", f"msg {i}"),
                )
            # Insert a non-docker log with the same app_name
            cursor.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES (?, ?, '192.168.1.10', 'server', 'homelab_radarr_1', 1, 6, 'syslog msg', 'syslog msg')
                """,
                (now, now),
            )
            conn.commit()

            _batch_update_app_aliases(conn, "homelab_radarr_1", "radarr", batch_size=2)

            cursor.execute("SELECT app_name, source_ip FROM logs ORDER BY id ASC")
            rows = cursor.fetchall()

            # First 5 docker rows should be updated to 'radarr'
            for row in rows[:5]:
                assert row[0] == "radarr"
                assert row[1] == "docker"

            # The 6th non-docker row should retain 'homelab_radarr_1'
            assert rows[5][0] == "homelab_radarr_1"
            assert rows[5][1] == "192.168.1.10"

    @pytest.mark.asyncio
    async def test_schedule_app_alias_update_deduplication(self, db_path: Path):
        """DockerTailer._schedule_app_alias_update deduplicates calls for the same pair."""
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler, db_path=db_path)

        assert len(tailer._updated_app_aliases) == 0

        # No-op when raw_name == display_name
        tailer._schedule_app_alias_update("radarr", "radarr")
        assert len(tailer._updated_app_aliases) == 0

        # No-op when empty
        tailer._schedule_app_alias_update("", "radarr")
        assert len(tailer._updated_app_aliases) == 0

        # First dispatch schedules update and registers in deduplication set
        tailer._schedule_app_alias_update("homelab_radarr_1", "radarr")
        assert ("homelab_radarr_1", "radarr") in tailer._updated_app_aliases
        assert len(tailer._updated_app_aliases) == 1

        # Second dispatch for identical pair is ignored
        tailer._schedule_app_alias_update("homelab_radarr_1", "radarr")
        assert len(tailer._updated_app_aliases) == 1

        # Give background thread brief moment to complete
        await asyncio.sleep(0.05)
        await tailer.stop()

    @pytest.mark.asyncio
    async def test_docker_tailer_attaches_and_emits_with_resolved_display_name(self, db_path: Path):
        """_attach_running_containers resolves container labels and feeds entries with resolved display_name."""
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler, db_path=db_path)

        entries = []
        async def capture_feed(key, entry):
            entries.append(entry)
        assembler.feed = capture_feed

        cid = "c_compose_12345"
        raw_name = "/prod_web_1"
        compose_service = "web"

        mock_containers = [
            {
                "Id": cid,
                "Names": [raw_name],
                "Labels": {"com.docker.compose.service": compose_service},
            }
        ]

        cancel_event = asyncio.Event()
        ts = "2026-10-09T11:00:00.000000000Z"
        msg = f"{ts} Web request completed\n".encode("utf-8")
        payload = b"\x01\x00\x00\x00" + len(msg).to_bytes(4, "big") + msg

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield payload
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            is_closed = False
            async def get(self, url, **kwargs):
                if "/containers/json" in url:
                    class ListResp:
                        status_code = 200
                        def json(self):
                            return mock_containers
                        def raise_for_status(self):
                            pass
                    return ListResp()
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        mock_client = MockClient()
        await tailer._attach_running_containers(mock_client)

        # Allow tailer to consume stream
        await asyncio.sleep(0.05)

        assert len(entries) >= 1
        assert entries[0]["app_name"] == "web"
        assert entries[0]["message"] == "Web request completed"

        # Check deduplication tracking set recorded the update
        assert ("prod_web_1", "web") in tailer._updated_app_aliases

        await tailer.stop()




