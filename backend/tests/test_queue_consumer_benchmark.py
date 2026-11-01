"""
Benchmark of the queue consumer's cycle for full batches of 5,000 logs: the SQLite insert, the
live stream broadcast to two subscribers, and the alert checks with 20 alert rules, then the rate
the consumer stores logs at while the queue stays full. Excluded from the default run; run it with:

    .venv/bin/pytest backend/tests/test_queue_consumer_benchmark.py -m benchmark -s

It prints milliseconds per batch for each part and logs per second overall, so figures from
before and after a change can be compared on the same machine.
"""

import asyncio
import datetime
import random
import time
from pathlib import Path

import pytest

import app.core.pipeline as pipeline_mod
from app.core.pipeline import QueueConsumer
from app.core.sse import sse_manager
from app.services.alert_evaluator import AlertEvaluator
from tests.test_rule_matching_benchmark import APPS, HOSTS, MESSAGES, _seed_rules

pytestmark = pytest.mark.benchmark

BATCH_SIZE = 5000
BATCHES = 4
THROUGHPUT_ENTRIES = 60000


def _make_batch(rng: random.Random) -> list[dict]:
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    batch = []
    for _ in range(BATCH_SIZE):
        app = rng.choice(APPS)
        host = rng.choice(HOSTS)
        message = rng.choice(MESSAGES).format(n=rng.randint(10, 450))
        batch.append({
            "timestamp": now_iso,
            "received_at": now_iso,
            "source_ip": "172.22.2.10",
            "source_alias": host,
            "app_name": app,
            "facility": 16,
            "severity": 6,
            "message": message,
            "raw": f"<134>1 {now_iso} {host} {app} 4242 ID47 - {message}",
        })
    return batch


async def _drain(queue: asyncio.Queue) -> None:
    """A live stream tab reading its queue as fast as it can."""
    while True:
        await queue.get()


def test_queue_consumer_cycle(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "logs.db"
    _seed_rules(db_file)
    rng = random.Random(7)

    async def _phases() -> dict[str, float]:
        consumer = QueueConsumer(db_file, alert_evaluator=AlertEvaluator(db_file))
        subscribers = [await sse_manager.subscribe(), await sse_manager.subscribe()]
        readers = [asyncio.create_task(_drain(q)) for q in subscribers]
        totals = {"insert": 0.0, "broadcast": 0.0, "alerts": 0.0}
        try:
            for _ in range(BATCHES):
                batch = _make_batch(rng)
                begin = time.perf_counter()
                await asyncio.to_thread(consumer._insert_batch, batch)
                totals["insert"] += time.perf_counter() - begin
                begin = time.perf_counter()
                await sse_manager.broadcast_batch(batch)
                totals["broadcast"] += time.perf_counter() - begin
                begin = time.perf_counter()
                await consumer._alert_evaluator.evaluate_batch(batch)
                totals["alerts"] += time.perf_counter() - begin
                await asyncio.sleep(0.01)
        finally:
            for task in readers:
                task.cancel()
            for q in subscribers:
                await sse_manager.unsubscribe(q)
            await asyncio.to_thread(consumer._close_conn)
            pipeline_mod.set_queue_consumer(None)
        return {name: seconds / BATCHES for name, seconds in totals.items()}

    async def _throughput() -> float:
        queue: asyncio.Queue = asyncio.Queue(maxsize=pipeline_mod._QUEUE_MAXSIZE)
        monkeypatch.setattr(pipeline_mod, "_log_queue", queue)
        consumer = QueueConsumer(db_file, alert_evaluator=AlertEvaluator(db_file))
        subscribers = [await sse_manager.subscribe(), await sse_manager.subscribe()]
        readers = [asyncio.create_task(_drain(q)) for q in subscribers]
        entries = [entry for _ in range(THROUGHPUT_ENTRIES // BATCH_SIZE) for entry in _make_batch(rng)]
        for entry in entries[:queue.maxsize]:
            queue.put_nowait(entry)
        run_task = asyncio.create_task(consumer.run())
        begin = time.perf_counter()
        for entry in entries[queue.maxsize:]:
            await queue.put(entry)
        await queue.join()
        seconds = time.perf_counter() - begin
        await consumer.stop()
        await run_task
        for task in readers:
            task.cancel()
        for q in subscribers:
            await sse_manager.unsubscribe(q)
        monkeypatch.setattr(pipeline_mod, "_log_queue", None)
        return len(entries) / seconds

    per_batch = asyncio.run(_phases())
    rate = asyncio.run(_throughput())
    cycle = sum(per_batch.values())

    print(
        f"\nQueue consumer benchmark (batches of {BATCH_SIZE:,}, two live stream subscribers, 20 alert rules):\n"
        f"  insert    : {per_batch['insert'] * 1000:.0f} ms per batch\n"
        f"  broadcast : {per_batch['broadcast'] * 1000:.0f} ms per batch\n"
        f"  alerts    : {per_batch['alerts'] * 1000:.0f} ms per batch\n"
        f"  cycle     : {cycle * 1000:.0f} ms per batch ({BATCH_SIZE / cycle:,.0f} logs/s)\n"
        f"  consumer  : {rate:,.0f} logs/s stored from a full queue ({THROUGHPUT_ENTRIES:,} logs)"
    )
    assert rate > 0
