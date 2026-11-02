"""
Sliding window for rate alert rules that counts matches instead of storing them.

A rate rule fires at threshold_count * window_seconds matches inside its window, and the
form allows up to 100,000 logs/s over 86,400 s, far more matches than can be kept in memory.
So the window counts: the matches in fine time buckets (a thousandth of the window each),
the culprit figures (app, host and message start) in coarser spans (a sixtieth of the window
each), and only the newest matches are kept, for the notification and the AI prompt.
Memory stays the same whatever the threshold and window.
"""

from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

# Buckets per window for the match count; a bucket leaves the window once its end is older
# than the window start, so the count may include up to a thousandth of a window extra
COUNT_BUCKETS = 1000
# Spans per window for the culprit counters
CULPRIT_SPANS = 60
# Distinct apps, hosts or messages counted per span; further ones in that span are not counted
MAX_CULPRIT_KEYS = 100
# Newest matches kept for the notification sample and the AI context
SAMPLE_SIZE = 50
# The message start that groups matches into one culprit pattern
MESSAGE_KEY_LENGTH = 150

_T = TypeVar("_T")


def culprit_values(entry: dict[str, Any]) -> tuple[str, str, str]:
    """The app, host and message start a matched log counts under in the culprit analysis."""
    app = entry.get("app_name") or entry.get("container_name") or entry.get("tag") or "unknown"
    host = entry.get("source_alias") or entry.get("source_ip") or entry.get("host") or "unknown"
    message = str(entry.get("message") or entry.get("raw") or "")[:MESSAGE_KEY_LENGTH].strip()
    return str(app), str(host), message


def _count_capped(counter: Counter[str], key: str) -> bool:
    """Count key unless the counter already holds MAX_CULPRIT_KEYS other keys; True when counted."""
    if key in counter or len(counter) < MAX_CULPRIT_KEYS:
        counter[key] += 1
        return True
    return False


def _bucket_at(items: deque[_T], index: int, index_of: Callable[[_T], int], make: Callable[[], _T]) -> _T:
    """The item for index in a deque sorted by index, inserted in order when missing (late logs are rare)."""
    if not items or index_of(items[-1]) < index:
        items.append(make())
        return items[-1]
    position = len(items)
    while position > 0 and index_of(items[position - 1]) > index:
        position -= 1
    if position > 0 and index_of(items[position - 1]) == index:
        return items[position - 1]
    item = make()
    items.insert(position, item)
    return item


class _CulpritSpan:
    """Culprit counters for one span of the window, with the newest match of each counted app."""

    __slots__ = ("index", "apps", "hosts", "messages", "newest_by_app")

    def __init__(self, index: int):
        self.index = index
        self.apps: Counter[str] = Counter()
        self.hosts: Counter[str] = Counter()
        self.messages: Counter[str] = Counter()
        self.newest_by_app: dict[str, tuple[float, dict[str, Any]]] = {}

    def add(self, epoch: float, payload: dict[str, Any]) -> None:
        app, host, message = culprit_values(payload)
        if _count_capped(self.apps, app):
            newest = self.newest_by_app.get(app)
            if newest is None or epoch >= newest[0]:
                self.newest_by_app[app] = (epoch, payload)
        _count_capped(self.hosts, host)
        if message:
            _count_capped(self.messages, message)


@dataclass
class RateSnapshot:
    """What a rate rule's window held when it fired."""

    total: int
    apps: Counter[str]
    hosts: Counter[str]
    messages: Counter[str]
    # Newest matches, oldest first
    samples: list[dict[str, Any]]
    newest_by_app: dict[str, dict[str, Any]]


class RateWindow:
    """Counts a rate rule's matches over its sliding window (see the module docstring)."""

    def __init__(self, window_seconds: int):
        self.window_seconds = window_seconds
        self._count_width = window_seconds / COUNT_BUCKETS
        self._span_width = window_seconds / CULPRIT_SPANS
        # [bucket index, matches], oldest first
        self._counts: deque[list[int]] = deque()
        self._spans: deque[_CulpritSpan] = deque()
        self._samples: deque[tuple[float, dict[str, Any]]] = deque()
        self.total = 0

    def __len__(self) -> int:
        return self.total

    @property
    def samples(self) -> list[dict[str, Any]]:
        return [payload for _, payload in self._samples]

    def add(self, epoch: float, payload: dict[str, Any]) -> None:
        """Count one match; payload is the trimmed record kept as a sample and for the culprits."""
        index = int(epoch // self._count_width)
        _bucket_at(self._counts, index, lambda bucket: bucket[0], lambda: [index, 0])[1] += 1
        self.total += 1

        span_index = int(epoch // self._span_width)
        _bucket_at(self._spans, span_index, lambda span: span.index, lambda: _CulpritSpan(span_index)).add(epoch, payload)

        samples = self._samples
        if not samples or samples[-1][0] <= epoch:
            samples.append((epoch, payload))
        else:
            position = len(samples)
            while position > 0 and samples[position - 1][0] > epoch:
                position -= 1
            samples.insert(position, (epoch, payload))
        if len(samples) > SAMPLE_SIZE:
            samples.popleft()

    def prune(self, cutoff: float) -> None:
        """Drop the counts, spans and samples that lie wholly before cutoff (the window start)."""
        counts = self._counts
        while counts and (counts[0][0] + 1) * self._count_width <= cutoff:
            self.total -= counts.popleft()[1]
        spans = self._spans
        while spans and (spans[0].index + 1) * self._span_width <= cutoff:
            spans.popleft()
        samples = self._samples
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    def snapshot(self) -> RateSnapshot:
        apps: Counter[str] = Counter()
        hosts: Counter[str] = Counter()
        messages: Counter[str] = Counter()
        newest: dict[str, tuple[float, dict[str, Any]]] = {}
        for span in self._spans:
            apps.update(span.apps)
            hosts.update(span.hosts)
            messages.update(span.messages)
            for app, (epoch, payload) in span.newest_by_app.items():
                if app not in newest or epoch >= newest[app][0]:
                    newest[app] = (epoch, payload)
        return RateSnapshot(
            total=self.total,
            apps=apps,
            hosts=hosts,
            messages=messages,
            samples=self.samples,
            newest_by_app={app: payload for app, (_, payload) in newest.items()},
        )

    def clear(self) -> None:
        self._counts.clear()
        self._spans.clear()
        self._samples.clear()
        self.total = 0
