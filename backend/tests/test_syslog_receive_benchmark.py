"""
Benchmark of the syslog receive path on the event loop: parsing alone, then the whole dispatch
(parse, alias lookup, multiline assembly, drop checks and enqueue) for UDP datagrams and for TCP
lines. The traffic mixes 70% single lines, 20% JSON and 10% multi-line tracebacks, half RFC 3164
and half RFC 5424; over TCP each traceback line arrives as its own frame, so the multiline
assembler joins them. Excluded from the default run; run it with:

    .venv/bin/pytest backend/tests/test_syslog_receive_benchmark.py -m benchmark -s

It prints messages per second for each part, so figures from before and after a change can be
compared on the same machine.
"""

import asyncio
import datetime
import random
import time
from pathlib import Path

import pytest

import app.core.pipeline as pipeline_mod
from app.collectors.syslog import AliasCache, dispatch_syslog_message, parse_syslog_message
from app.core.migrations import run_migrations
from app.core.pipeline import KeyedMultilineAssembler
from app.services.drop_filter import init_drop_filter

pytestmark = pytest.mark.benchmark

MESSAGE_COUNT = 20000
HOSTS = ["edge-gw-01", "k8s-worker-01", "db-primary", "nas-storage-01"]
APPS = [("nginx", 134), ("sshd", 131), ("kernel", 132), ("dockerd", 134), ("postgres", 133), ("valkey", 134)]
SINGLE_LINES = [
    'GET /api/v1/resources/items HTTP/1.1 200 412 "client-req-id={n}" 41ms',
    "Accepted publickey for admin from 192.168.1.{n} port 50122 ssh2: RSA SHA256:d8b2e1f4",
    "Connection closed by authenticating user root 192.168.1.{n} port 40022 [preauth]",
    'pool "tank" scrub completed: 0 errors found in 4h 12m (3.8 TB scanned, 0B repaired)',
    "Disk temperature threshold normal: nvme0n1 is 38 C (warning threshold 70 C)",
    "Container worker-prod-{n} healthy: healthcheck probe succeeded in 1.8ms",
    "TLS handshake negotiated: TLSv1.3 / TLS_AES_256_GCM_SHA384 cipher suite",
]
JSON_LINES = [
    '{{"level":"info","event":"api_call","method":"GET","path":"/api/v1/logs","status":200,"duration_ms":{n}}}',
    '{{"level":"warn","event":"rate_limit_approaching","client_ip":"192.168.1.{n}","remaining":12}}',
    '{{"level":"error","event":"db_pool_timeout","pool":"writer","wait_ms":{n},"context":"order_checkout"}}',
]
TRACEBACKS = [
    "Traceback (most recent call last):\n"
    '  File "/app/backend/services/worker.py", line 142, in process_transaction\n'
    '    result = await client.post("https://payment.gateway.internal/charge", timeout=2.0)\n'
    '  File "/app/backend/core/http.py", line 88, in post\n'
    '    raise ConnectionResetError(104, "Connection reset by peer")\n'
    "ConnectionResetError: [Errno 104] Connection reset by peer",
    'java.lang.NullPointerException: Cannot invoke "String.length()" because "token" is null\n'
    "\tat com.auth.jwt.TokenValidator.verifyClaims(TokenValidator.java:78)\n"
    "\tat com.auth.jwt.SecurityFilter.doFilterInternal(SecurityFilter.java:42)\n"
    "\tat org.springframework.web.filter.OncePerRequestFilter.doFilter(OncePerRequestFilter.java:117)",
    "goroutine 42 [running]:\n"
    "net/http.(*conn).serve(0xc00010e000, {0x7f4b80, 0xc0000a6050})\n"
    "\t/usr/local/go/src/net/http/server.go:1925 +0xb25\n"
    "created by net/http.(*Server).Serve\n"
    "\t/usr/local/go/src/net/http/server.go:3086 +0x4eb",
]


def _make_messages() -> list[str]:
    """Syslog messages as a sender writes them, a traceback still holding its newlines."""
    rng = random.Random(42)
    now = datetime.datetime.now(datetime.timezone.utc)
    messages = []
    for _ in range(MESSAGE_COUNT):
        roll = rng.random()
        if roll < 0.7:
            body = rng.choice(SINGLE_LINES).format(n=rng.randint(10, 250))
        elif roll < 0.9:
            body = rng.choice(JSON_LINES).format(n=rng.randint(10, 450))
        else:
            body = rng.choice(TRACEBACKS)
        host = rng.choice(HOSTS)
        app, pri = rng.choice(APPS)
        pid = rng.randint(1000, 9999)
        if rng.random() < 0.5:
            messages.append(f"<{pri}>{now.strftime('%b %d %H:%M:%S')} {host} {app}[{pid}]: {body}")
        else:
            messages.append(f"<{pri}>1 {now.isoformat()} {host} {app} {pid} ID{rng.randint(10, 99)} - {body}")
    return messages


async def _dispatch_all(frames: list[bytes], alias_cache: AliasCache) -> tuple[float, int]:
    """Seconds to dispatch every frame from one source, and the number of entries queued."""
    assembler = KeyedMultilineAssembler()
    begin = time.perf_counter()
    for frame in frames:
        await dispatch_syslog_message(frame, "172.22.2.10", alias_cache, assembler)
    await assembler.flush_all()
    seconds = time.perf_counter() - begin
    queue = pipeline_mod.get_queue()
    queued = queue.qsize()
    while not queue.empty():
        queue.get_nowait()
    return seconds, queued


def test_syslog_receive_throughput(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    init_drop_filter(db_file)
    alias_cache = AliasCache(db_file)
    # Unbounded, so no entry is counted as lost while the benchmark fills it
    monkeypatch.setattr(pipeline_mod, "_log_queue", asyncio.Queue())

    messages = _make_messages()
    datagrams = [m.encode() for m in messages]
    tcp_lines = [line.encode() for m in messages for line in m.split("\n")]

    start = time.perf_counter()
    for datagram in datagrams:
        parse_syslog_message(datagram, "172.22.2.10")
    parse_seconds = time.perf_counter() - start

    async def _run() -> tuple[tuple[float, int], tuple[float, int]]:
        return await _dispatch_all(datagrams, alias_cache), await _dispatch_all(tcp_lines, alias_cache)

    (udp_seconds, udp_queued), (tcp_seconds, tcp_queued) = asyncio.run(_run())
    monkeypatch.setattr(pipeline_mod, "_log_queue", None)

    print(
        f"\nSyslog receive benchmark ({len(messages):,} messages):\n"
        f"  parse only     : {len(datagrams):,} datagrams in {parse_seconds * 1000:.0f} ms "
        f"({len(datagrams) / parse_seconds:,.0f} messages/s)\n"
        f"  UDP dispatch   : {len(datagrams):,} datagrams in {udp_seconds * 1000:.0f} ms "
        f"({len(datagrams) / udp_seconds:,.0f} messages/s), {udp_queued:,} entries\n"
        f"  TCP dispatch   : {len(tcp_lines):,} lines in {tcp_seconds * 1000:.0f} ms "
        f"({len(tcp_lines) / tcp_seconds:,.0f} lines/s), {tcp_queued:,} entries"
    )
    # An exception line counts as a continuation, so it can join the entry before it
    assert 0 < tcp_queued <= len(messages)
    assert 0 < udp_queued <= len(messages)
