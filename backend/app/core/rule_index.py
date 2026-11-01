"""
Lookup of the rules that could match a log entry.

Alert and drop rules that name an exact app (or source) can only match entries with that value,
so checking them against every other entry is wasted work. RuleIndex groups such rules by the
lowercased value and keeps the others in an "always check" list. The candidates for an entry are
returned in the original rule order, so the first matching rule is the same as with a full scan.
"""

from typing import Callable, Generic, Iterable, Optional, Sequence, TypeVar

T = TypeVar("T")

# Distinct lookup combinations kept in the candidate cache before it starts again
_CANDIDATE_CACHE_LIMIT = 4096


def exact_match_key(pattern: Optional[str]) -> Optional[str]:
    """
    The lowercased value a pattern matches exactly under match_wildcard, or None when it has
    wildcards or is empty (such a pattern must be checked against every entry).
    """
    if not pattern:
        return None
    key = pattern.lower().strip()
    if not key or "*" in key or "?" in key:
        return None
    return key


def entry_key(value: Optional[str]) -> str:
    """Normalise an entry's field the way match_wildcard compares it."""
    return value.lower().strip() if value else ""


class RuleIndex(Generic[T]):
    """
    Rules grouped by the exact values they require, keeping their order.

    `exact_keys(rule)` returns (field, values) when the rule can only match entries whose field
    equals one of the values (already normalised with exact_match_key), or None when the rule must
    be checked against every entry. `source` is the list the index was built from; owners compare
    it by identity to know when to build a new index.
    """

    def __init__(self, rules: Sequence[T], exact_keys: Callable[[T], Optional[tuple[str, Iterable[str]]]]):
        self.source = rules
        self._rules = list(rules)
        always: list[int] = []
        buckets: dict[tuple[str, str], list[int]] = {}
        for position, rule in enumerate(self._rules):
            keyed = exact_keys(rule)
            if keyed is None:
                always.append(position)
                continue
            field, values = keyed
            for value in set(values):
                buckets.setdefault((field, value), []).append(position)
        self._always_positions = always
        self._buckets = buckets
        self.always = [self._rules[p] for p in always]
        self._always_ids = {id(rule) for rule in self.always}
        self._candidate_cache: dict[tuple[tuple[str, str], ...], list[T]] = {}
        self._keyed_cache: dict[tuple[tuple[str, str], ...], list[T]] = {}

    def is_always(self, rule: T) -> bool:
        """True when the rule is checked against every entry."""
        return id(rule) in self._always_ids

    def candidates(self, lookups: tuple[tuple[str, str], ...]) -> list[T]:
        """Rules that could match an entry with these (field, normalised value) pairs, in rule order."""
        cached = self._candidate_cache.get(lookups)
        if cached is None:
            cached = self._collect(lookups, include_always=True)
            self._store(self._candidate_cache, lookups, cached)
        return cached

    def keyed_candidates(self, lookups: tuple[tuple[str, str], ...]) -> list[T]:
        """Like candidates, without the rules that are checked against every entry."""
        cached = self._keyed_cache.get(lookups)
        if cached is None:
            cached = self._collect(lookups, include_always=False)
            self._store(self._keyed_cache, lookups, cached)
        return cached

    def _collect(self, lookups: tuple[tuple[str, str], ...], include_always: bool) -> list[T]:
        hits = [bucket for key in set(lookups) if (bucket := self._buckets.get(key))]
        if not hits:
            return self.always if include_always else []
        positions: set[int] = set(self._always_positions) if include_always else set()
        for bucket in hits:
            positions.update(bucket)
        return [self._rules[p] for p in sorted(positions)]

    @staticmethod
    def _store(cache: dict, lookups: tuple[tuple[str, str], ...], value: list[T]) -> None:
        if len(cache) >= _CANDIDATE_CACHE_LIMIT:
            cache.clear()
        cache[lookups] = value
