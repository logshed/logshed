"""
Tests for the event loop lag monitor: wake-up lag measurement, the rolling window and the
figures it reports. The clock and the sleep are fakes, so no test waits in real time.
"""

import asyncio

import pytest

from app.core.loop_lag import LoopLagMonitor


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def make_monitor(clock: FakeClock, wake_delays: list[float], interval: float = 0.1, window: float = 60.0):
    """A monitor whose sleep advances the fake clock by the interval plus the next delay, then stops."""
    delays = iter(wake_delays)

    async def fake_sleep(seconds: float) -> None:
        try:
            delay = next(delays)
        except StopIteration:
            raise asyncio.CancelledError
        clock.now += seconds + delay

    return LoopLagMonitor(interval=interval, window_seconds=window, clock=clock, sleep=fake_sleep)


async def run_until_done(monitor: LoopLagMonitor) -> None:
    with pytest.raises(asyncio.CancelledError):
        await monitor.run()


class TestLoopLagMonitor:
    def test_no_samples_reports_none(self):
        monitor = LoopLagMonitor(clock=FakeClock())
        assert monitor.snapshot() == {"loop_lag_ms": None, "loop_lag_p95_ms": None, "loop_lag_max_ms": None}

    @pytest.mark.asyncio
    async def test_on_time_wake_ups_report_zero(self):
        clock = FakeClock()
        monitor = make_monitor(clock, [0.0] * 5)
        await run_until_done(monitor)
        assert monitor.snapshot() == {"loop_lag_ms": 0.0, "loop_lag_p95_ms": 0.0, "loop_lag_max_ms": 0.0}

    @pytest.mark.asyncio
    async def test_a_late_wake_up_shows_in_current_and_max(self):
        clock = FakeClock()
        monitor = make_monitor(clock, [0.0, 0.0, 0.35, 0.002])
        await run_until_done(monitor)
        snap = monitor.snapshot()
        assert snap["loop_lag_ms"] == 2.0
        assert snap["loop_lag_max_ms"] == 350.0

    @pytest.mark.asyncio
    async def test_p95_ignores_a_single_spike_but_follows_sustained_lag(self):
        clock = FakeClock()
        # 99 quick wake-ups and one 2 s stall: the 95th percentile stays low, the maximum shows the stall
        monitor = make_monitor(clock, [0.001] * 99 + [2.0])
        await run_until_done(monitor)
        snap = monitor.snapshot()
        assert snap["loop_lag_p95_ms"] == 1.0
        assert snap["loop_lag_max_ms"] == 2000.0

        clock = FakeClock()
        # A quarter of the wake-ups 300 ms late: the 95th percentile reports it
        monitor = make_monitor(clock, ([0.001] * 3 + [0.3]) * 25)
        await run_until_done(monitor)
        assert monitor.snapshot()["loop_lag_p95_ms"] == 300.0

    def test_window_drops_old_samples(self):
        clock = FakeClock()
        monitor = LoopLagMonitor(window_seconds=60.0, clock=clock)
        monitor.record(0.5, now=clock.now)
        clock.now += 30.0
        monitor.record(0.01, now=clock.now)
        assert monitor.snapshot()["loop_lag_max_ms"] == 500.0

        # 61 s after the slow sample it has left the window; the 30 s old one is still in it
        clock.now += 31.0
        snap = monitor.snapshot()
        assert snap["loop_lag_max_ms"] == 10.0
        assert snap["loop_lag_p95_ms"] == 10.0

        clock.now += 60.0
        assert monitor.snapshot()["loop_lag_ms"] is None

    def test_early_wake_up_counts_as_no_lag(self):
        clock = FakeClock()

        async def early_sleep(seconds: float) -> None:
            if clock.now > 1000.0:
                raise asyncio.CancelledError
            clock.now += seconds - 0.01

        monitor = LoopLagMonitor(clock=clock, sleep=early_sleep)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(monitor.run())
        assert monitor.snapshot()["loop_lag_max_ms"] == 0.0

    def test_reset_clears_samples(self):
        clock = FakeClock()
        monitor = LoopLagMonitor(clock=clock)
        monitor.record(0.2, now=clock.now)
        monitor.reset()
        assert monitor.snapshot()["loop_lag_ms"] is None
