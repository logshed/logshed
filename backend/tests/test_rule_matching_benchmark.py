"""
Benchmark of the per-log rule checks: the drop filter and the alert evaluator with 20 drop
rules and 20 alert rules (half regex, half for apps that never send), over a mixed workload of
eight apps. Excluded from the default run; run it with:

    .venv/bin/pytest backend/tests/test_rule_matching_benchmark.py -m benchmark -s

It prints entries per second for each part, so figures from before and after a change can be
compared on the same machine.
"""

import asyncio
import datetime
import random
import time
from pathlib import Path

import pytest

from app.core.migrations import get_connection, run_migrations
from app.services.alert_evaluator import AlertEvaluator
from app.services.drop_filter import DropFilter

pytestmark = pytest.mark.benchmark

ENTRY_COUNT = 20000
BATCH_SIZE = 500
# The queue consumer's largest batch, as stored when ingestion is saturated
FULL_BATCH_SIZE = 5000
APPS = ["nginx", "sshd", "kernel", "dockerd", "postgres", "valkey", "systemd", "envoy"]
HOSTS = ["edge-gw-01", "k8s-worker-01", "db-primary", "nas-storage-01"]
MESSAGES = [
    'GET /api/v1/resources/items HTTP/1.1 200 412 "client-req-id={n}" 41ms',
    "Accepted publickey for admin from 192.168.1.{n} port 50122 ssh2: RSA SHA256:d8b2e1f4",
    "Connection closed by authenticating user root 192.168.1.{n} port 40022 [preauth]",
    'pool "tank" scrub completed: 0 errors found in 4h 12m (3.8 TB scanned, 0B repaired)',
    "Disk temperature threshold normal: nvme0n1 is 38 C (warning threshold 70 C)",
    "Container worker-prod-{n} healthy: healthcheck probe succeeded in 1.8ms",
    "BGP session established with peer 10.0.0.1 (AS 64512), received 142 prefixes",
    "DNS query handled: query.local A record forwarded to upstream 1.1.1.1 in 3.4ms",
    '{{"level":"warn","event":"rate_limit_approaching","client_ip":"192.168.1.{n}","remaining":12}}',
    '{{"level":"error","event":"db_pool_timeout","pool":"writer","wait_ms":{n},"context":"order_checkout"}}',
    '{{"level":"debug","event":"task_dispatched","task_id":"task-{n}","priority":"normal"}}',
    "Traceback (most recent call last):\n  File \"/app/worker.py\", line 142, in run\nConnectionResetError: [Errno 104]",
]

# (app_pattern, message_pattern, is_regex)
DROP_RULES = [
    ("nginx", "TLS handshake negotiated", False),
    ("sshd", r"Connection closed by authenticating user \w+ [\d.]+ port \d+ \[preauth\]", True),
    ("kernel", "Disk temperature*normal", False),
    ("dockerd", r'"event":"task_dispatched","task_id":"task-\d+","priority":"normal"', True),
    ("systemd", "cache_refresh", False),
    ("envoy", r"^DNS query handled: \S+ A record forwarded", True),
    ("postgres", "checkpoint starting", False),
    ("valkey", "Background saving (started|terminated)", True),
    ("post*", r"FATAL:\s+password authentication failed", True),
    ("valkey", "*OOM command not allowed*", False),
    ("haproxy", r"Health check for server \S+ succeeded", True),
    ("unbound", "query: ", False),
    ("zigbee2mqtt", r"MQTT publish: topic 'zigbee2mqtt/[^']+'", True),
    ("frigate", "*debug*", False),
    ("mosquitto", r"Received PINGREQ from \S+", True),
    ("pihole-FTL", "cached", False),
    ("smartd", r"Self-Test (Log|Result).*(passed|without error)", True),
    ("crond", "pam_unix(crond:session)", False),
    ("traefik", r'"DownstreamStatus":2\d\d', True),
    ("home*", "homeassistant.components.recorder", False),
]

# (filter_app, match_pattern); thresholds are out of reach so the benchmark measures matching only
ALERT_RULES = [
    ("nginx", "BGP session established"),
    ("sshd", r'"event":"api_call","method":"(GET|POST)"'),
    ("kernel", "scrub completed"),
    ("postgres", r'"event":"db_pool_timeout".*"wait_ms":\d{3}'),
    ("dockerd", r"Container \S+ (unhealthy|exited)"),
    ("valkey,systemd", "healthcheck probe succeeded"),
    ("envoy", r"Traceback \(most recent call last\)"),
    ("sshd", "Accepted publickey for admin"),
    ("ngin*", r'NullPointerException: .*"token" is null'),
    (None, "rate_limit_approaching"),
    ("haproxy", r"Server \S+ is DOWN"),
    ("unbound", "SERVFAIL"),
    ("zigbee2mqtt", r"Device '[^']+' left the network"),
    ("frigate", "Unable to read frames"),
    ("mosquitto", r"Client \S+ disconnected due to (protocol error|exceeded timeout)"),
    ("pihole-FTL", "limit exceeded"),
    ("smartd", r"Currently unreadable \(pending\) sectors"),
    ("crond", "ORPHAN"),
    ("traefik", r'"DownstreamStatus":5\d\d'),
    ("homeassistant,hass", "Error doing job"),
]


def _seed_rules(db_file: Path) -> None:
    run_migrations(db_file)
    conn = get_connection(db_file)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for order, (app, pattern, is_regex) in enumerate(DROP_RULES, start=1):
        conn.execute(
            "INSERT INTO drop_rules (name, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, "
            "dropped_count, display_order, created_at) VALUES (?, NULL, ?, ?, ?, 1, 0, ?, ?)",
            (f"Drop {order}", app, pattern, int(is_regex), order, now_iso),
        )
    for order, (apps, pattern) in enumerate(ALERT_RULES, start=1):
        conn.execute(
            "INSERT INTO alert_rules (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, "
            "cooldown_seconds, is_enabled, display_order, created_at) VALUES (?, 'threshold', ?, ?, 100000, 60, 300, 1, ?, ?)",
            (f"Alert {order}", apps, pattern, order, now_iso),
        )
    conn.commit()
    conn.close()


def _make_entries() -> list[dict]:
    rng = random.Random(42)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entries = []
    for i in range(ENTRY_COUNT):
        message = rng.choice(MESSAGES).format(n=rng.randint(10, 450))
        entries.append({
            "id": i + 1,
            "timestamp": now_iso,
            "source_ip": "172.22.2.10",
            "source_alias": rng.choice(HOSTS),
            "app_name": rng.choice(APPS),
            "severity": 6,
            "message": message,
            "raw": message,
        })
    return entries


def test_rule_matching_throughput(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    _seed_rules(db_file)
    entries = _make_entries()

    drop_filter = DropFilter(db_file)
    start = time.perf_counter()
    kept = [
        e for e in entries
        if drop_filter.should_drop(e["source_alias"], e["source_ip"], e["app_name"], e["message"], e["severity"]) is None
    ]
    drop_seconds = time.perf_counter() - start

    evaluator = AlertEvaluator(db_file)

    async def _evaluate() -> float:
        begin = time.perf_counter()
        for offset in range(0, len(kept), BATCH_SIZE):
            await evaluator.evaluate_batch(kept[offset:offset + BATCH_SIZE])
        return time.perf_counter() - begin

    alert_seconds = asyncio.run(_evaluate())

    async def _evaluate_full_batches() -> tuple[float, float]:
        """Seconds for full batches, and the longest time the event loop could not run meanwhile."""
        full_evaluator = AlertEvaluator(db_file)
        longest_pause = 0.0
        running = True

        async def _ticker() -> None:
            nonlocal longest_pause
            last = time.perf_counter()
            while running:
                await asyncio.sleep(0.001)
                now = time.perf_counter()
                longest_pause = max(longest_pause, now - last)
                last = now

        ticker = asyncio.create_task(_ticker())
        await asyncio.sleep(0.01)
        begin = time.perf_counter()
        for offset in range(0, len(kept), FULL_BATCH_SIZE):
            await full_evaluator.evaluate_batch(kept[offset:offset + FULL_BATCH_SIZE])
            # The queue consumer awaits the next insert between batches, so the loop runs meanwhile
            await asyncio.sleep(0.005)
        seconds = time.perf_counter() - begin
        running = False
        await ticker
        return seconds, longest_pause

    full_seconds, longest_pause = asyncio.run(_evaluate_full_batches())

    print(
        f"\nRule matching benchmark ({len(DROP_RULES)} drop rules, {len(ALERT_RULES)} alert rules):\n"
        f"  drop filter : {len(entries):,} entries in {drop_seconds * 1000:.0f} ms "
        f"({len(entries) / drop_seconds:,.0f} entries/s), {len(entries) - len(kept):,} dropped\n"
        f"  alert rules : {len(kept):,} entries in {alert_seconds * 1000:.0f} ms "
        f"({len(kept) / alert_seconds:,.0f} entries/s)\n"
        f"  alert rules : {len(kept):,} entries in batches of {FULL_BATCH_SIZE:,} in {full_seconds * 1000:.0f} ms, "
        f"longest event loop pause {longest_pause * 1000:.0f} ms"
    )
    assert 0 < len(kept) < len(entries)
