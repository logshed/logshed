"""
Unit tests for the shared sliding window limiter, the login limiter and IP bucket keys.
"""

import pytest

from app.core.rate_limiter import (
    AiRateLimiter,
    LoginRateLimiter,
    SlidingWindowLimiter,
    ip_bucket_key,
)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestSlidingWindowLimiter:

    def test_check_and_record_and_expiry(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(3, 10.0, clock=clock)

        assert [limiter.check_and_record("a") for _ in range(4)] == [True, True, True, False]
        assert limiter.is_rate_limited("a")
        assert limiter.count("b") == 0

        clock.advance(5)
        assert limiter.check_and_record("a") is False

        # The first three events leave the window together
        clock.advance(5)
        assert limiter.count("a") == 0
        assert "a" not in limiter
        assert limiter.check_and_record("a") is True

    def test_window_slides_one_event_at_a_time(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(2, 10.0, clock=clock)
        assert limiter.check_and_record("k")
        clock.advance(4)
        assert limiter.check_and_record("k")
        assert not limiter.check_and_record("k")
        clock.advance(6)  # first event expires
        assert limiter.check_and_record("k")
        assert not limiter.check_and_record("k")

    def test_forget_and_reset(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(5, 10.0, clock=clock)
        limiter.record("k")
        clock.advance(1)
        limiter.record("k")
        assert limiter.count("k") == 2
        limiter.forget("k")
        assert "k" not in limiter
        limiter.record("x")
        limiter.reset()
        assert len(limiter) == 0

    def test_idle_sweep_bounds_tracked_keys(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(5, 10.0, sweep_threshold=100, clock=clock)

        peak = 0
        for i in range(5000):
            limiter.check_and_record(f"key-{i}")
            clock.advance(1)
            peak = max(peak, len(limiter))

        # Only about 10 keys are active at any moment; idle ones are swept past the threshold
        assert peak <= 101
        clock.advance(11)
        limiter.check_and_record("late")
        assert len(limiter) <= 101

    def test_sweep_is_deferred_while_keys_stay_active(self, monkeypatch):
        """Many active keys do not trigger a full sweep on every new key."""
        clock = FakeClock()
        limiter = SlidingWindowLimiter(5, 60.0, sweep_threshold=100, clock=clock)
        sweeps = []
        original = limiter._sweep

        def _counting_sweep(now):
            sweeps.append(len(limiter))
            original(now)

        monkeypatch.setattr(limiter, "_sweep", _counting_sweep)
        for i in range(20_000):
            limiter.check_and_record(f"key-{i}")

        assert len(limiter) == 20_000
        # Threshold doubles after each unproductive sweep: 100, 200, 400, ... 12800
        assert len(sweeps) <= 9

    def test_default_sweep_threshold_is_5000(self):
        assert SlidingWindowLimiter(1, 1.0).sweep_threshold == 5000

    def test_ai_rate_limiter_is_a_sliding_window_limiter(self):
        limiter = AiRateLimiter(max_requests=2, window_seconds=60.0)
        assert isinstance(limiter, SlidingWindowLimiter)
        assert limiter.check_and_record("s") and limiter.check_and_record("s")
        assert limiter.check_and_record("s") is False


class TestLoginRateLimiter:

    def test_attempts_are_reserved_before_completion(self):
        limiter = LoginRateLimiter(max_attempts=5, max_in_flight=10, clock=FakeClock())
        attempts = [limiter.begin("ip") for _ in range(6)]
        assert all(a is not None for a in attempts[:5])
        assert attempts[5] is None

    def test_in_flight_cap(self):
        limiter = LoginRateLimiter(max_attempts=5, max_in_flight=2, clock=FakeClock())
        first = limiter.begin("ip")
        second = limiter.begin("ip")
        assert first is not None and second is not None
        assert limiter.begin("ip") is None
        # Other keys are unaffected
        assert limiter.begin("other") is not None

        limiter.finish(first, success=False)
        third = limiter.begin("ip")
        assert third is not None
        limiter.finish(second, success=False)
        limiter.finish(third, success=False)
        assert limiter._in_flight == {"other": 1}

    def test_failures_count_and_expire(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(max_attempts=5, window_seconds=60.0, clock=clock)
        for _ in range(5):
            limiter.finish(limiter.begin("ip"), success=False)
        assert limiter.begin("ip") is None
        clock.advance(60)
        assert limiter.begin("ip") is not None

    def test_success_clears_key_history(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(max_attempts=5, clock=clock)
        for _ in range(4):
            limiter.finish(limiter.begin("ip"), success=False)
        ok = limiter.begin("ip")
        limiter.finish(ok, success=True)

        assert "ip" not in limiter._per_key
        # The key starts over with a full budget
        for _ in range(5):
            limiter.finish(limiter.begin("ip"), success=False)
        assert limiter.begin("ip") is None

    def test_rejected_attempts_record_nothing(self):
        limiter = LoginRateLimiter(max_attempts=1, clock=FakeClock())
        limiter.finish(limiter.begin("ip"), success=False)
        for _ in range(10):
            assert limiter.begin("ip") is None
        assert limiter._per_key.count("ip") == 1

    def test_per_key_sweep_bounds_tracked_ips(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(
            max_attempts=5, window_seconds=10.0, sweep_threshold=100, clock=clock
        )
        for i in range(2000):
            limiter.finish(limiter.begin(f"ip-{i}"), success=False)
            clock.advance(1)
        assert len(limiter) <= 101

    def test_reset(self):
        limiter = LoginRateLimiter(clock=FakeClock())
        limiter.begin("ip")
        limiter.reset()
        assert len(limiter) == 0
        assert limiter._in_flight == {}


class TestIpBucketKey:

    @pytest.mark.parametrize(
        ("ip", "expected"),
        [
            ("203.0.113.5", "203.0.113.5"),
            ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
            ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
            ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
            ("::ffff:198.51.100.7", "198.51.100.7"),
            ("::1", "::/64"),
            ("not-an-ip", "not-an-ip"),
        ],
    )
    def test_keys(self, ip, expected):
        assert ip_bucket_key(ip) == expected


class TestLoginLimiterHasNoGlobalLimit:

    def test_failures_spread_over_many_keys_are_never_limited(self):
        limiter = LoginRateLimiter(max_attempts=5, clock=FakeClock())
        for i in range(200):
            attempt = limiter.begin(f"10.0.{i // 250}.{i}")
            assert attempt is not None
            limiter.finish(attempt, success=False)

        # A brand new key is still allowed, and the per-key limit still applies to a busy key
        assert limiter.begin("192.0.2.1") is not None
        for _ in range(5):
            limiter.finish(limiter.begin("busy"), success=False)
        assert limiter.begin("busy") is None


class TestRetryAfter:

    def test_retry_after_is_whole_seconds_until_oldest_event_expires(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(2, 60.0, clock=clock)
        limiter.record("k")
        clock.advance(10.5)
        limiter.record("k")

        # The oldest event leaves the window 49.5 s from now, rounded up
        assert limiter.retry_after("k") == 50
        clock.advance(20)
        assert limiter.retry_after("k") == 30

    def test_retry_after_is_at_least_one_second(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(1, 60.0, clock=clock)
        assert limiter.retry_after("unknown") == 1
        limiter.record("k")
        clock.advance(59.9)
        assert limiter.retry_after("k") == 1

    def test_retry_after_does_not_record_anything(self):
        limiter = SlidingWindowLimiter(1, 60.0, clock=FakeClock())
        limiter.retry_after("k")
        assert "k" not in limiter

    def test_login_retry_after_uses_the_per_key_window(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60.0, max_in_flight=10, clock=clock)
        for _ in range(2):
            limiter.finish(limiter.begin("ip"), success=False)
        clock.advance(15)
        assert limiter.begin("ip") is None
        assert limiter.retry_after("ip") == 45

    def test_login_retry_after_for_in_flight_rejection_is_one_second(self):
        limiter = LoginRateLimiter(max_attempts=5, max_in_flight=1, clock=FakeClock())
        assert limiter.begin("ip") is not None
        assert limiter.begin("ip") is None
        assert limiter.retry_after("ip") == 1
