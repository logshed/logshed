"""
Benchmark of live stream delivery: entries broadcast in batches reach one browser stream
(`GET /api/logs/stream`) through the whole web app, its middleware included. Excluded from the
default run; run it with:

    .venv/bin/pytest backend/tests/test_live_stream_benchmark.py -m benchmark -s

It prints events per second, so figures from before and after a change can be compared on the
same machine.
"""

import asyncio
import datetime
import time

import pytest

from app.core.security import SESSION_COOKIE_NAME
from app.core.sse import sse_manager
from tests.test_logs_api import auth_cookie, client, reset_logs_env  # noqa: F401

pytestmark = pytest.mark.benchmark

EVENT_COUNT = 20000
BROADCAST_SIZE = 400


@pytest.mark.asyncio
async def test_live_stream_throughput(client, auth_cookie):  # noqa: F811 (pytest fixtures imported above)
    client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entries = [
        {
            "id": n,
            "timestamp": now_iso,
            "received_at": now_iso,
            "source_ip": "172.22.2.10",
            "source_alias": "edge-gw-01",
            "app_name": "nginx",
            "facility": 16,
            "severity": 6,
            "message": f'GET /api/v1/resources/items HTTP/1.1 200 412 "client-req-id={n}" 41ms',
            "raw": f'<134>1 {now_iso} edge-gw-01 nginx 4242 ID47 - GET /api/v1/resources/items {n}',
        }
        for n in range(EVENT_COUNT)
    ]

    async def _broadcast() -> None:
        while sse_manager.subscriber_count() == 0:
            await asyncio.sleep(0.001)
        (queue,) = list(sse_manager._subscribers)
        for offset in range(0, EVENT_COUNT, BROADCAST_SIZE):
            # Never more than the subscriber queue holds, so no event is dropped
            while queue.qsize() > queue.maxsize - BROADCAST_SIZE:
                await asyncio.sleep(0)
            await sse_manager.broadcast_batch(entries[offset:offset + BROADCAST_SIZE])

    producer = asyncio.create_task(_broadcast())
    begin = time.perf_counter()
    received = 0
    async with client.stream("GET", "/api/logs/stream", params={"max_events": EVENT_COUNT}) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                received += 1
    seconds = time.perf_counter() - begin
    await producer

    print(
        f"\nLive stream benchmark ({EVENT_COUNT:,} events, broadcast {BROADCAST_SIZE} at a time, one stream):\n"
        f"  delivered : {received:,} events in {seconds * 1000:.0f} ms ({received / seconds:,.0f} events/s)"
    )
    assert received == EVENT_COUNT
