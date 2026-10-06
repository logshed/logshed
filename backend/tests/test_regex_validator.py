"""
Tests for regex validator and ReDoS protection utilities.
"""

import re
import pytest
from app.core.regex_validator import (
    check_regex_safety,
    compile_safe_regex,
    is_catastrophic_backtracking_pattern,
    safe_regex_search,
    validate_pattern_complexity,
    validate_regex_pattern,
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
        compiled = compile_safe_regex(safe_pattern)
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
        candidate = "a" * 30 + "!"
        # Short timeout should terminate or abandon search and return False
        assert safe_regex_search(backtracking_pat, candidate, timeout=0.01) is False
