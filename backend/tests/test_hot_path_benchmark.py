"""
Benchmarks of small per-line and per-request costs: TCP syslog framing, Docker stream demux and
resync, Docker line parsing up to the stored timestamp, the settings cache, the database
connection lookup, FTS query formatting, an idle FTS indexing pass, the HTTP middleware, one
page of redaction and the facets query for logs without a host alias. Excluded from the default
run; run it with:

    .venv/bin/pytest backend/tests/test_hot_path_benchmark.py -m benchmark -s

Each test prints its figures, so results from before and after a change can be compared on the
same machine.
"""

import asyncio
import datetime
import logging
import random
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.benchmark

SYSLOG_LINES = [
    "<134>1 {ts} edge-gw-01 nginx - - - GET /api/v1/resources/items HTTP/1.1 200 412 \"client-req-id={n}\" 41ms",
    "<38>{bsd} k8s-worker-01 sshd[2211]: Accepted publickey for admin from 192.168.1.{n} port 50122 ssh2",
    "<133>1 {ts} db-primary postgres - - - checkpoint complete: wrote {n} buffers (0.4%); 0 WAL file(s) added",
    "<134>{bsd} nas-storage-01 kernel: Disk temperature threshold normal: nvme0n1 is 38 C (warning threshold 70 C)",
]
DOCKER_LINES = [
    "GET /api/v1/resources/items HTTP/1.1 200 412 \"client-req-id={n}\" 41ms",
    'time="2026-11-09T12:00:00Z" level=info msg="Container worker-prod-{n} healthy" component=health',
    "1:M 09 Nov 2026 12:00:00.123 * Background saving started by pid {n}",
    '{{"level":"warn","event":"rate_limit_approaching","client_ip":"192.168.1.{n}","remaining":12}}',
    "\x1b[32mINFO\x1b[0m  [scheduler] job {n} finished in 1.8ms",
    "[Nest] 42  - 11/09/2026, 12:00:00 PM     LOG [RouterExplorer] Mapped {{/api/items/{n}, GET}} route",
]


def _rate(count: int, seconds: float) -> str:
    return f"{count / seconds:,.0f}/s, {seconds / count * 1e6:.2f} us each"


def _syslog_stream(count: int, octet_counted: bool) -> bytes:
    rng = random.Random(7)
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.isoformat()
    bsd = now.strftime("%b %d %H:%M:%S")
    out = bytearray()
    for _ in range(count):
        line = rng.choice(SYSLOG_LINES).format(ts=ts, bsd=bsd, n=rng.randint(10, 250)).encode()
        out += (str(len(line)).encode() + b" " + line) if octet_counted else line + b"\n"
    return bytes(out)


class _Sink:
    """Stands in for the protocol's parse queue: counts frames without parsing them."""

    def __init__(self) -> None:
        self.count = 0

    def put_tcp(self, frame, source_ip) -> bool:
        self.count += 1
        return False


def test_tcp_framing_throughput():
    from app.collectors.syslog import SyslogTCPProtocol

    count = 40_000
    results = []
    for octet_counted in (False, True):
        data = _syslog_stream(count, octet_counted)
        for chunk_size in (4096, 65536):
            proto = SyslogTCPProtocol(assembler=None, alias_cache=None, inactivity_timeout=0)
            proto.queue = _Sink()
            chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]
            start = time.perf_counter()
            for chunk in chunks:
                proto.data_received(chunk)
            seconds = time.perf_counter() - start
            assert proto.queue.count == count
            label = "octet-counted" if octet_counted else "newline"
            results.append(f"  {label:13} {chunk_size // 1024:>2} KB chunks: {_rate(count, seconds)}")
    print("\nTCP syslog framing (frames to the queue, no parsing):\n" + "\n".join(results))


def _docker_frames(count: int) -> bytes:
    rng = random.Random(11)
    out = bytearray()
    for i in range(count):
        ts = f"2026-11-09T12:00:{i % 60:02d}.{rng.randint(0, 999_999_999):09d}Z"
        payload = (ts + " " + rng.choice(DOCKER_LINES).format(n=rng.randint(10, 250)) + "\n").encode()
        out += bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload
    return bytes(out)


def _demux_all(demux, data: bytes, chunk_size: int) -> int:
    buffer = b""
    frames = 0
    for i in range(0, len(data), chunk_size):
        buffer += data[i:i + chunk_size]
        got, buffer = demux(buffer)
        frames += len(got)
    return frames


def test_docker_demux_throughput(caplog):
    from app.collectors.docker_collector import _demux_stream

    count = 40_000
    data = _docker_frames(count)
    lines = []
    for chunk_size in (4096, 65536):
        start = time.perf_counter()
        frames = _demux_all(_demux_stream, data, chunk_size)
        seconds = time.perf_counter() - start
        assert frames == count
        lines.append(f"  framed stream, {chunk_size // 1024:>2} KB chunks: {_rate(count, seconds)}")

    # Raw text read as a framed stream (the container inspect failed for a TTY container)
    text = ("plain text written by a TTY container, no frame headers here\n" * 1100).encode()[:65536]
    caplog.set_level(logging.WARNING, logger="app.collectors.docker_collector")
    caplog.clear()
    start = time.perf_counter()
    _demux_stream(text)
    seconds = time.perf_counter() - start
    warnings = sum(1 for r in caplog.records if r.levelno == logging.WARNING)
    lines.append(f"  64 KB of unframed text: {seconds * 1000:.1f} ms, {warnings:,} warnings")
    print("\nDocker stream demux:\n" + "\n".join(lines))


def test_docker_line_to_stored_timestamp():
    """A Docker line as the tailer handles it, up to the timestamp the queue consumer stores."""
    from app.collectors.docker_collector import (
        _detect_severity,
        _extract_docker_timestamp,
        _make_log_entry,
        _parse_docker_message_content,
    )
    from app.core.pipeline import QueueConsumer

    rng = random.Random(5)
    count = 40_000
    lines = [
        f"2026-11-09T12:00:{i % 60:02d}.{rng.randint(0, 999_999_999):09d}Z "
        + rng.choice(DOCKER_LINES).format(n=rng.randint(10, 250))
        for i in range(count)
    ]
    start = time.perf_counter()
    entries = []
    for line in lines:
        ts, message = _extract_docker_timestamp(line)
        severity = _detect_severity(message)
        parsed, sev = _parse_docker_message_content(message, severity)
        entries.append(_make_log_entry(
            "worker", "abc123", parsed, severity=sev, timestamp=ts, source_alias="docker", raw=message,
        ))
    build_seconds = time.perf_counter() - start
    start = time.perf_counter()
    QueueConsumer._normalize_timestamps(entries)
    normalize_seconds = time.perf_counter() - start
    assert all(e["timestamp"].endswith("+00:00") for e in entries)
    print(
        "\nDocker line to entry:\n"
        f"  timestamp, severity, content, entry : {_rate(count, build_seconds)}\n"
        f"  stored timestamp form (consumer)    : {_rate(count, normalize_seconds)}\n"
        f"  together                            : {_rate(count, build_seconds + normalize_seconds)}"
    )


def test_settings_cache_cost(tmp_path: Path, monkeypatch):
    import app.core.config as config
    from app.core.migrations import run_migrations
    from app.core.security import encrypt_value

    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    monkeypatch.setenv("DB_PATH", str(db_file))
    conn = sqlite3.connect(db_file)
    for key, value, encrypted in [
        ("ai_api_key_openai", encrypt_value("sk-test-openai"), 1),
        ("ai_api_key_anthropic", encrypt_value("sk-ant-test"), 1),
        ("ai_api_key_gemini", encrypt_value("gem-test"), 1),
        ("docker_source_alias", "docker", 0),
        ("app_url", "http://logshed.local:8080", 0),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES (?, ?, datetime('now'), ?)",
            (key, value, encrypted),
        )
    conn.commit()
    conn.close()
    config.invalidate_settings_cache()
    config.get_cached_setting("docker_source_alias")

    count = 200_000
    start = time.perf_counter()
    for _ in range(count):
        config.get_cached_setting("docker_source_alias", "docker")
    hit_seconds = time.perf_counter() - start

    reloads = 200
    start = time.perf_counter()
    for _ in range(reloads):
        config.invalidate_settings_cache()
        config.get_cached_setting("docker_source_alias", "docker")
    reload_seconds = time.perf_counter() - start
    config.invalidate_settings_cache()
    print(
        "\nSettings cache:\n"
        f"  cached read : {_rate(count, hit_seconds)}\n"
        f"  reload      : {reload_seconds / reloads * 1000:.2f} ms each (on the event loop when it happens there)"
    )


def test_small_per_call_costs(tmp_path: Path):
    from app.api.deps import close_thread_local_connections, get_thread_read_connection
    from app.core.migrations import run_migrations
    from app.core.utils import format_fts_query

    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    count = 50_000
    get_thread_read_connection(db_file)
    start = time.perf_counter()
    for _ in range(count):
        get_thread_read_connection(db_file)
    conn_seconds = time.perf_counter() - start
    close_thread_local_connections()

    queries = ['nginx 499', '"timed out" app:sshd', 'error OR warn*', '(disk AND temp) NOT nvme0n1']
    start = time.perf_counter()
    for i in range(count):
        format_fts_query(queries[i % len(queries)])
    fts_seconds = time.perf_counter() - start
    print(
        "\nPer-call costs:\n"
        f"  thread read connection lookup : {_rate(count, conn_seconds)}\n"
        f"  format_fts_query              : {_rate(count, fts_seconds)}"
    )


def test_idle_fts_pass(tmp_path: Path):
    """An indexing pass with nothing to index, as the FTS worker runs twice a second when idle."""
    from app.core.migrations import get_connection, run_migrations
    from app.services.fts_indexer import index_pending_batch

    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    conn = get_connection(db_file)
    conn.isolation_level = None
    passes = 5_000
    start = time.perf_counter()
    for _ in range(passes):
        assert index_pending_batch(conn, batch_size=1000) == 0
    seconds = time.perf_counter() - start
    conn.close()

    # Does an idle pass hold up a writer? Time a writer's commits while a thread runs idle passes.
    import threading

    stop = threading.Event()

    def _poller() -> None:
        c = get_connection(db_file)
        c.isolation_level = None
        while not stop.is_set():
            index_pending_batch(c, batch_size=1000)
        c.close()

    writer = get_connection(db_file)
    writer.execute("CREATE TABLE bench_writes (n INTEGER)")
    writer.commit()
    writes = 2_000
    waits = []
    thread = threading.Thread(target=_poller)
    thread.start()
    try:
        for i in range(writes):
            t0 = time.perf_counter()
            writer.execute("INSERT INTO bench_writes (n) VALUES (?)", (i,))
            writer.commit()
            waits.append(time.perf_counter() - t0)
    finally:
        stop.set()
        thread.join()
        writer.close()
    waits.sort()
    print(
        "\nIdle FTS pass:\n"
        f"  one pass                  : {seconds / passes * 1e6:.1f} us\n"
        f"  writer commit beside a busy poller: median {waits[len(waits) // 2] * 1e6:.0f} us, "
        f"p99 {waits[int(len(waits) * 0.99)] * 1e6:.0f} us, max {waits[-1] * 1000:.1f} ms"
    )


def test_http_middleware_overhead(tmp_path: Path, monkeypatch):
    from app.core.migrations import run_migrations
    from app.main import create_app

    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    monkeypatch.setenv("DB_PATH", str(db_file))
    app = create_app.uncached()

    async def _run() -> tuple[float, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(200):
                await client.get("/api/health")
            requests = 3_000
            start = time.perf_counter()
            for _ in range(requests):
                resp = await client.get("/api/health")
            seconds = time.perf_counter() - start
            assert resp.status_code == 200 and resp.headers["X-Frame-Options"] == "DENY"
            return seconds, requests

    seconds, requests = asyncio.run(_run())
    print(f"\nHTTP: GET /api/health through the middleware stack: {_rate(requests, seconds)}")


def test_redact_page():
    """One full page of GET /api/v1/logs: message and raw of 1,000 rows."""
    from app.core.redactor import redact

    rng = random.Random(3)
    texts = []
    for _ in range(1000):
        line = rng.choice(DOCKER_LINES + [s.split(" - - - ")[-1] for s in SYSLOG_LINES]).format(
            n=rng.randint(10, 250), ts="", bsd="",
        )
        texts.extend([line, line])
    rounds = 5
    start = time.perf_counter()
    for _ in range(rounds):
        for text in texts:
            redact(text)
    seconds = (time.perf_counter() - start) / rounds
    print(f"\nRedaction of one 1,000-row page (message and raw): {seconds * 1000:.1f} ms")


def test_facets_alias_less_hosts(tmp_path: Path):
    """Facets with logs from many hosts that have no alias (each listed with its apps)."""
    from app.api.deps import close_thread_local_connections
    from app.api.logs import get_log_facets
    from app.core.migrations import get_connection, run_migrations

    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    conn = get_connection(db_file)
    rng = random.Random(9)
    ips = [f"10.0.{i // 250}.{i % 250 + 1}" for i in range(200)]
    apps = [f"app{i}" for i in range(15)]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = [
        (now, now, rng.choice(ips), "", rng.choice(apps), 1, 6, "m", "m")
        for _ in range(300_000)
    ]
    conn.executemany(
        "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.execute("ANALYZE")
    conn.close()

    import app.api.deps as deps

    async def _run() -> float:
        original = deps.get_db_path
        deps.get_db_path = lambda: db_file
        try:
            await get_log_facets(user={"id": 1})
            start = time.perf_counter()
            for _ in range(3):
                result = await get_log_facets(user={"id": 1})
            assert set(ips) <= set(result.sources)
            return (time.perf_counter() - start) / 3
        finally:
            deps.get_db_path = original
            close_thread_local_connections()

    seconds = asyncio.run(_run())
    print(f"\nFacets, 300,000 logs from 200 hosts without an alias: {seconds * 1000:.0f} ms")
