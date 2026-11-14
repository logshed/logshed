"""
Benchmark of how the event loop holds up during an ingest flood: a sender in another process
writes syslog lines to a real TCP listener as fast as it can while the queue consumer stores
them, two live stream tabs read the broadcast (one keeping up, one slow) and a probe stands in
for web requests. Excluded from the default run; run it with:

    .venv/bin/pytest backend/tests/test_flood_responsiveness_benchmark.py -m benchmark -s

It prints the loop's wake-up lag (median, 95th percentile, max), the time a request that reads
the database waits for its answer, and the logs stored per second, so figures from before and
after a change can be compared on the same machine.
"""

import asyncio
import socket
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import pytest

import app.core.pipeline as pipeline_mod
from app.collectors.syslog import SyslogServer
from app.core.migrations import run_migrations
from app.core.pipeline import KeyedMultilineAssembler, QueueConsumer
from app.core.sse import sse_manager
from app.services.alert_evaluator import AlertEvaluator
from app.services import log_store
from app.services.drop_filter import init_drop_filter
from tests.test_rule_matching_benchmark import _seed_rules

pytestmark = pytest.mark.benchmark

LINES = 150_000
PROBE_INTERVAL = 0.01

SENDER = r"""
import socket, sys, datetime
port, count = int(sys.argv[1]), int(sys.argv[2])
now = datetime.datetime.now(datetime.timezone.utc).strftime("%b %d %H:%M:%S")
apps = ["nginx", "sshd", "kernel", "dockerd", "postgres"]
lines = [
    f"<134>{now} edge-gw-01 {apps[n % 5]}[4242]: GET /api/v1/resources/items HTTP/1.1 200 412 req={n} 41ms\n".encode()
    for n in range(count)
]
with socket.create_connection(("127.0.0.1", port)) as s:
    for start in range(0, count, 200):
        s.sendall(b"".join(lines[start:start + 200]))
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
        tcp.bind(("127.0.0.1", 0))
        return tcp.getsockname()[1]


async def _fast_reader(queue: asyncio.Queue) -> None:
    while True:
        await queue.get()


async def _slow_reader(queue: asyncio.Queue) -> None:
    """A tab on a slow link: takes what is queued every 100 ms."""
    while True:
        await asyncio.sleep(0.1)
        while not queue.empty():
            queue.get_nowait()


def _pct(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def test_flood_responsiveness(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    _seed_rules(db_file)
    init_drop_filter(db_file)
    port = _free_port()

    async def _run() -> dict:
        queue: asyncio.Queue = asyncio.Queue(maxsize=pipeline_mod._QUEUE_MAXSIZE)
        monkeypatch.setattr(pipeline_mod, "_log_queue", queue)
        assembler = KeyedMultilineAssembler()
        server = SyslogServer(assembler, db_file, host="127.0.0.1", port=port)
        consumer = QueueConsumer(db_file, alert_evaluator=AlertEvaluator(db_file))
        server_task = asyncio.create_task(server.start())
        consumer_task = asyncio.create_task(consumer.run())
        subscribers = [await sse_manager.subscribe(), await sse_manager.subscribe()]
        readers = [asyncio.create_task(_fast_reader(subscribers[0])), asyncio.create_task(_slow_reader(subscribers[1]))]
        while server.tcp_server is None:
            await asyncio.sleep(0.01)

        lags: list[float] = []
        waits: list[float] = []
        done = asyncio.Event()

        async def _lag_probe() -> None:
            while not done.is_set():
                expected = time.perf_counter() + PROBE_INTERVAL
                await asyncio.sleep(PROBE_INTERVAL)
                lags.append(max(0.0, time.perf_counter() - expected))

        def _read() -> int:
            conn = sqlite3.connect(db_file)
            try:
                return conn.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0]
            finally:
                conn.close()

        async def _request_probe() -> None:
            while not done.is_set():
                begin = time.perf_counter()
                await asyncio.to_thread(_read)
                waits.append(time.perf_counter() - begin)
                await asyncio.sleep(0.05)

        probes = [asyncio.create_task(_lag_probe()), asyncio.create_task(_request_probe())]
        sender = await asyncio.create_subprocess_exec(sys.executable, "-c", SENDER, str(port), str(LINES))
        begin = time.perf_counter()
        await sender.wait()
        # Wait until the listener and consumer have taken in everything sent
        while server.parse_queue.qsize() or queue.qsize():
            await asyncio.sleep(0.01)
        await queue.join()
        seconds = time.perf_counter() - begin
        done.set()
        await asyncio.gather(*probes)

        for task in readers:
            task.cancel()
        for q in subscribers:
            await sse_manager.unsubscribe(q)
        await server.stop()
        await server_task
        await consumer.stop()
        await consumer_task
        monkeypatch.setattr(pipeline_mod, "_log_queue", None)
        pipeline_mod.set_queue_consumer(None)
        return {"lags": lags, "waits": waits, "seconds": seconds}

    result = asyncio.run(_run())
    conn = sqlite3.connect(db_file)
    try:
        stored = log_store.count_rows(conn)
    finally:
        conn.close()
    lags, waits = result["lags"], result["waits"]
    print(
        f"\nFlood responsiveness benchmark ({LINES:,} TCP lines as fast as one sender writes, "
        f"two live stream tabs, 20 alert rules):\n"
        f"  loop lag     : median {statistics.median(lags) * 1000:.1f} ms, p95 {_pct(lags, 0.95) * 1000:.1f} ms, "
        f"p99 {_pct(lags, 0.99) * 1000:.1f} ms, max {max(lags) * 1000:.1f} ms ({len(lags):,} samples)\n"
        f"  request wait : median {statistics.median(waits) * 1000:.1f} ms, p95 {_pct(waits, 0.95) * 1000:.1f} ms, "
        f"max {max(waits) * 1000:.1f} ms ({len(waits):,} requests)\n"
        f"  stored       : {stored:,} logs in {result['seconds']:.1f} s ({LINES / result['seconds']:,.0f} lines/s)"
    )
    assert lags and waits
