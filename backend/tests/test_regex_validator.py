"""
Tests for regex validator and ReDoS protection utilities.
"""

import logging
import re
import time
import pytest
from app.core.regex_validator import (
    check_regex_safety,
    compile_user_pattern,
    is_catastrophic_backtracking_pattern,
    safe_regex_search,
    validate_pattern_complexity,
    validate_regex_pattern,
    get_user_regex_timeout_counts,
    reset_user_regex_timeout_stats,
    user_pattern_search,
)


class TestRegexValidator:
    """Test suite for regex pattern validation and safe bounded execution."""

    @pytest.mark.parametrize(
        "safe_pattern",
        [
            r"(GET|POST)+",
            r"(a|b)+",
            r"(info|warn|error)+",
            r"(foo|bar|baz)*",
            r"^\[(INFO|WARN|ERROR)\]",
            r"status=(200|201|204)",
            r"user_\d+",
            r".*",
            r"[a-zA-Z0-9_-]+",
        ],
    )
    def test_safe_patterns_pass_validation(self, safe_pattern: str):
        """Verify safe patterns (including common alternations) pass validation."""
        assert is_catastrophic_backtracking_pattern(safe_pattern) is False
        is_safe, err = check_regex_safety(safe_pattern)
        assert is_safe is True
        assert err is None
        validate_pattern_complexity(safe_pattern)
        validate_regex_pattern(safe_pattern)
        compiled = compile_user_pattern(safe_pattern)
        assert compiled is not None

    @pytest.mark.parametrize(
        "dangerous_pattern",
        [
            r"(a+)+",
            r"(a+)+$",
            r"([a-z]+)*",
            r"(a*)+",
            r"((a)+)+",
            r"([a-z]+)+",
            r"(a|b+)+",
            r"(a+|b)+",
            r"(a|aa)+",
            r"([a-z]|[a-z][a-z])+",
            r"(foo|foobar)+",
            r"(foo|foobar)*",
        ],
    )
    def test_dangerous_patterns_rejected(self, dangerous_pattern: str):
        """Verify catastrophic backtracking patterns are rejected."""
        assert is_catastrophic_backtracking_pattern(dangerous_pattern) is True
        is_safe, err = check_regex_safety(dangerous_pattern)
        assert is_safe is False
        assert "catastrophic backtracking" in (err or "").lower()
        with pytest.raises(ValueError):
            validate_pattern_complexity(dangerous_pattern)

    def test_safe_regex_search_matches_and_non_matches(self):
        """Verify safe_regex_search returns accurate boolean match results."""
        assert safe_regex_search(r"(GET|POST)+", "GET /api/v1 200") is True
        assert safe_regex_search(r"(GET|POST)+", "PUT /api/v1 200") is False
        assert safe_regex_search(r"(info|warn|error)+", "warn: disk full") is True
        assert safe_regex_search(r"(a|b)+", "cccaabbbccc") is True

    def test_safe_regex_search_precompiled_pattern(self):
        """Verify precompiled patterns match directly without string validation."""
        pat = re.compile(r"error:\s*\d+", re.IGNORECASE)
        assert safe_regex_search(pat, "System error: 500 occurred") is True
        assert safe_regex_search(pat, "All systems operational") is False

    def test_safe_regex_search_rejects_dangerous_string_pattern(self):
        """Verify a catastrophic backtracking string pattern is rejected and returns False."""
        assert safe_regex_search(r"(a+)+$", "a" * 21 + "!") is False

    def test_safe_regex_search_empty_input(self):
        """Verify safe_regex_search handles empty string inputs safely."""
        assert safe_regex_search(r"abc", "") is False
        assert safe_regex_search(re.compile(r"abc"), "") is False

    def test_safe_regex_search_bounded_length(self):
        """Verify candidate strings are truncated to 16,384 characters."""
        long_prefix = "x" * 16384
        long_msg = long_prefix + "target_word"
        assert safe_regex_search(r"target_word", long_msg) is False
        assert safe_regex_search(r"^x+", long_msg) is True

    def test_safe_regex_search_bounded_timeout(self):
        """Verify execution timeout safely bounds backtracking searches."""
        # Precompiled pattern bypasses initial string complexity checks
        backtracking_pat = re.compile(r"(a+)+$")
        candidate = "a" * 22 + "!"
        # Short timeout should terminate or abandon search and return False
        assert safe_regex_search(backtracking_pat, candidate, timeout=0.01) is False


# Patterns that slipped past the static detector before 1.3.0 (S-11)
BYPASS_PATTERNS = [
    r"(a|a)+$",
    r"(\w|\d)+$",
    r"(\s|\W)+$",
    r"(.*a){20}",
    r".*.*.*.*=",
]


class TestHardenedStaticDetector:
    """The static detector refuses the known bypass shapes with a clear message."""

    @pytest.mark.parametrize("pattern", BYPASS_PATTERNS)
    def test_bypass_patterns_rejected(self, pattern: str):
        assert is_catastrophic_backtracking_pattern(pattern) is True
        is_safe, err = check_regex_safety(pattern)
        assert is_safe is False
        assert "catastrophic backtracking" in (err or "").lower()

    @pytest.mark.parametrize(
        "pattern",
        [
            r"(\w|-)+",
            r"(\d|\.)+",
            r"(\s|,)+",
            r"(GET|POST|PUT)+",
            r"(ab|ac)+",
            r"(.*a){3}",
            r"error.*disk.*full",
            r".*.*",
            r"(\d{1,3}\.){3}\d{1,3}",
            r"[.*][.*][.*]",
            r"\.*\.*\.*",
        ],
    )
    def test_ordinary_patterns_still_accepted(self, pattern: str):
        is_safe, err = check_regex_safety(pattern)
        assert is_safe is True, err


class TestUserPatternTimeout:
    """User patterns run on the `regex` engine with a real per-match timeout."""

    @pytest.fixture(autouse=True)
    def _reset_stats(self):
        reset_user_regex_timeout_stats()
        yield
        reset_user_regex_timeout_stats()

    def _compile_bypassing_static_check(self, monkeypatch, pattern: str):
        import app.core.regex_validator as regex_validator

        monkeypatch.setattr(regex_validator, "is_catastrophic_backtracking_pattern", lambda p: False)
        return compile_user_pattern(pattern, re.IGNORECASE)

    def test_catastrophic_pattern_returns_no_match_within_bound(self, monkeypatch, caplog):
        compiled = self._compile_bypassing_static_check(monkeypatch, r"(a|aa)+$")
        adversarial = "a" * 16383 + "!"
        assert len(adversarial) == 16384

        caplog.set_level(logging.WARNING, logger="app.core.regex_validator")
        started = time.monotonic()
        matched = user_pattern_search(compiled, adversarial, rule_label="alert rule 42")
        elapsed = time.monotonic() - started

        assert matched is False
        assert elapsed < 0.5
        assert get_user_regex_timeout_counts() == {"alert rule 42": 1}
        warnings = [r for r in caplog.records if "alert rule 42" in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING

    def test_timeout_warning_is_rate_limited_per_rule_but_counted(self, monkeypatch, caplog):
        compiled = self._compile_bypassing_static_check(monkeypatch, r"(a|aa)+$")
        monkeypatch.setattr("app.core.regex_validator.USER_REGEX_TIMEOUT", 0.005)
        adversarial = "a" * 4000 + "!"

        caplog.set_level(logging.WARNING, logger="app.core.regex_validator")
        for _ in range(3):
            assert user_pattern_search(compiled, adversarial, rule_label="drop rule 9") is False
        assert user_pattern_search(compiled, adversarial, rule_label="drop rule 10") is False

        counts = get_user_regex_timeout_counts()
        assert counts["drop rule 9"] == 3
        assert counts["drop rule 10"] == 1
        warned_9 = [r for r in caplog.records if "drop rule 9" in r.getMessage()]
        warned_10 = [r for r in caplog.records if "drop rule 10" in r.getMessage()]
        assert len(warned_9) == 1
        assert len(warned_10) == 1

    def test_safe_regex_search_has_no_thread_pool(self):
        import app.core.regex_validator as regex_validator

        assert not hasattr(regex_validator, "get_regex_executor")
        assert not hasattr(regex_validator, "ThreadPoolExecutor")

    def test_ordinary_patterns_match_case_insensitively(self):
        compiled = compile_user_pattern(r"failed password for \w+", re.IGNORECASE)
        assert user_pattern_search(compiled, "FAILED PASSWORD for root from 10.0.0.1") is True
        assert user_pattern_search(compiled, "Accepted password for root") is False
        inline = compile_user_pattern(r"(?i)disk\s+full")
        assert user_pattern_search(inline, "DISK   FULL on /var") is True

    def test_text_is_truncated_to_16k(self):
        compiled = compile_user_pattern(r"needle")
        assert user_pattern_search(compiled, "x" * 16384 + "needle") is False
        assert user_pattern_search(compiled, "x" * 16378 + "needle") is True

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (None, 0.05),
            ("", 0.05),
            ("200", 0.2),
            ("12.5", 0.0125),
            ("0", 0.05),
            ("-5", 0.05),
            ("abc", 0.05),
            ("nan", 0.05),
        ],
    )
    def test_timeout_is_read_from_environment(self, monkeypatch, raw, expected):
        from app.core.regex_validator import read_user_regex_timeout

        if raw is None:
            monkeypatch.delenv("USER_REGEX_TIMEOUT_MS", raising=False)
        else:
            monkeypatch.setenv("USER_REGEX_TIMEOUT_MS", raw)
        assert read_user_regex_timeout() == pytest.approx(expected)


class TestStaticDetectorCost:
    """The hardened detector stays cheap on the longest allowed patterns."""

    def test_long_single_atom_alternation_is_checked_quickly(self):
        letters = [chr(c) for c in range(0x4E00, 0x4E00 + 450)]
        pattern = "(" + "|".join(letters) + ")+"
        assert len(pattern) <= 1000
        started = time.monotonic()
        is_safe, err = check_regex_safety(pattern)
        assert time.monotonic() - started < 0.5
        assert is_safe is True, err
