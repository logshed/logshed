"""
The log store (app/services/log_store.py): the only module that reads and writes stored logs.
These tests call it directly; the routes and services that use it have their own tests.
"""

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from app.core.migrations import run_migrations
from app.services import log_store
from app.services.fts_indexer import index_pending_logs
from app.services.log_query import LogFilter
from tests.db_helpers import open_db, store_logs

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Modules allowed to name the logs tables in SQL: the store, its SQL helpers (the filter builder
# and the full-text indexer), the schema migrations and the backup, which copies whole tables.
STORE_MODULES = {
    "services/log_store.py",
    "services/log_query.py",
    "services/fts_indexer.py",
    "core/migrations.py",
    "core/backup.py",
}
# The table name may be quoted or carry a schema name ("logs", [logs], main.logs)
_LOGS_SQL = re.compile(
    r"\b(FROM|INTO|UPDATE|JOIN|TABLE|ON)\s+([\w]+\.)?[\"`\[]?logs(_fts)?\b[\"`\]]?",
    re.IGNORECASE,
)


def _code_strings(tree: ast.AST) -> list[str]:
    """Every string constant in a module except docstrings (f-string parts included)."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_only_the_store_runs_sql_on_logs():
    """A module that queried the logs table itself would bypass the store and miss its later layouts."""
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR).as_posix()
        if rel in STORE_MODULES:
            continue
        for text in _code_strings(ast.parse(path.read_text(encoding="utf-8"))):
            match = _LOGS_SQL.search(text)
            if match:
                offenders.append(f"{rel}: {match.group(0)!r}")
    assert offenders == []


def test_the_guard_finds_sql_in_plain_and_f_strings():
    tree = ast.parse(
        'def f(w):\n    """Read FROM logs here."""\n    a = "SELECT 1 FROM logs"\n    b = f"UPDATE logs SET x = {w}"\n'
        '    c = "CREATE INDEX i ON logs(x)"\n    d = \'SELECT * FROM "logs_fts"\'\n    e = "DELETE FROM main.logs"\n'
    )
    found = [t for t in _code_strings(tree) if _LOGS_SQL.search(t)]
    assert sorted(found) == sorted([
        "SELECT 1 FROM logs", "UPDATE logs SET x = ", "CREATE INDEX i ON logs(x)",
        'SELECT * FROM "logs_fts"', "DELETE FROM main.logs",
    ])


@pytest.fixture
def db(tmp_path):
    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    return db_file


def _row(ts: str, *, ip="10.0.0.1", alias="", app="app", severity=6, message="m", raw=None) -> dict:
    return {
        "timestamp": ts,
        "received_at": ts,
        "source_ip": ip,
        "source_alias": alias,
        "app_name": app,
        "severity": severity,
        "message": message,
        "raw": raw if raw is not None else message,
    }


def _ts(minute: int, day: int = 1) -> str:
    return f"2026-11-{day:02d}T10:{minute:02d}:00+00:00"


def test_insert_rows_gives_increasing_ids_and_keeps_every_field(db):
    entries = [_row(_ts(i), message=f"line {i}") for i in range(3)]
    entries.append({"timestamp": _ts(9), "received_at": _ts(9)})  # every other field defaults
    with open_db(db, sqlite3.Row) as conn:
        log_store.insert_rows(conn, entries)
    ids = [e["id"] for e in entries]
    assert ids == sorted(ids) and len(set(ids)) == 4

    with open_db(db, sqlite3.Row) as conn:
        total, rows = log_store.search(conn, LogFilter(), fetch=10, count_limit=100)
    assert total == 4
    assert [r["id"] for r in rows] == list(reversed(ids))
    bare = rows[0]
    assert (bare["source_ip"], bare["source_alias"], bare["app_name"], bare["facility"], bare["severity"]) == (
        "", "", "", 1, 6
    )
    assert (bare["message"], bare["raw"]) == ("", "")


def test_insert_rows_over_one_chunk(db):
    entries = [_row(_ts(i % 60, day=1 + i // 60)) for i in range(log_store.INSERT_CHUNK_ROWS + 3)]
    with open_db(db, sqlite3.Row) as conn:
        log_store.insert_rows(conn, entries)
    with open_db(db, sqlite3.Row) as conn:
        assert log_store.count_rows(conn) == len(entries)
        assert log_store.max_id(conn) == entries[-1]["id"]
        assert log_store.count_rows(conn, after_id=entries[-4]["id"]) == 3


def test_insert_row_returns_its_id(db):
    with open_db(db, sqlite3.Row) as conn:
        first = log_store.insert_row(conn, _row(_ts(1)))
        second = log_store.insert_row(conn, _row(_ts(2)))
    assert second == first + 1


def test_search_pages_by_offset_and_by_position_agree(db):
    store_logs(db, *[_row(_ts(i)) for i in range(10)])
    with open_db(db, sqlite3.Row) as conn:
        _, everything = log_store.search(conn, LogFilter(), fetch=10, count_limit=100)
        _, by_offset = log_store.search(conn, LogFilter(), fetch=3, offset=3, count_limit=100)
        third = everything[2]
        total, by_position = log_store.search(
            conn, LogFilter(), fetch=3, before=(third["timestamp"], third["id"]), count_limit=100
        )
    assert [r["id"] for r in by_offset] == [r["id"] for r in everything[3:6]]
    assert [r["id"] for r in by_position] == [r["id"] for r in everything[3:6]]
    # The count ignores where the page starts
    assert total == 10


def test_search_count_is_capped(db):
    store_logs(db, *[_row(_ts(i)) for i in range(10)])
    with open_db(db, sqlite3.Row) as conn:
        total, rows = log_store.search(conn, LogFilter(), fetch=2, count_limit=4)
    assert (total, len(rows)) == (4, 2)


def test_search_leaves_raw_out_when_asked(db):
    store_logs(db, _row(_ts(1), raw="<14>raw line"))
    with open_db(db, sqlite3.Row) as conn:
        _, rows = log_store.search(conn, LogFilter(), fetch=1, count_limit=10, include_raw=False)
    assert "raw" not in rows[0].keys()


def test_full_text_search_and_count(db):
    store_logs(db, _row(_ts(1), message="disk failure", severity=3), _row(_ts(2), message="all good"))
    index_pending_logs(db)
    with open_db(db, sqlite3.Row) as conn:
        total, rows = log_store.search(conn, LogFilter(query="failure"), fetch=10, count_limit=100)
        assert (total, [r["message"] for r in rows]) == (1, ["disk failure"])
        assert log_store.count(conn, LogFilter(), group_by="severity") == (2, "severity", {"error": 1, "info": 1}, False)
        assert log_store.count_matching(conn, LogFilter(query="good")) == 1


def _window_spy(monkeypatch) -> list[int]:
    """Record the lowest rowid each full-text attempt of log_store.search matched from."""
    attempts: list[int] = []
    real_run = log_store.run_newest_first

    def _counting(conn, run_params, run, oldest_needed, matches, needed):
        def _run(p):
            attempts.append(p["fts_min_rowid"])
            return run(p)
        return real_run(conn, run_params, _run, oldest_needed, matches, needed)

    monkeypatch.setattr(log_store, "run_newest_first", _counting)
    return attempts


@pytest.mark.parametrize("page", [{"fetch": 3, "offset": 2}, {"fetch": 4, "before": True}])
def test_search_answered_from_the_newest_rows_matches_a_search_of_every_row(db, monkeypatch, page):
    """With a page and a capped count that fit in the newest rows, the first window is accepted."""
    store_logs(db, *[_row(_ts(i % 60, day=1 + i // 60), message=f"timeout {i}") for i in range(500)])
    index_pending_logs(db)
    log_filter = LogFilter(query="timeout")
    with open_db(db, sqlite3.Row) as conn:
        _, top = log_store.search(conn, log_filter, fetch=10, count_limit=100)
        kwargs = dict(page)
        if kwargs.pop("before", False):
            kwargs["before"] = (top[1]["timestamp"], top[1]["id"])

        monkeypatch.setattr("app.services.log_query.NEWEST_FIRST_WINDOWS", ())
        everything = log_store.search(conn, log_filter, count_limit=5, **kwargs)
        monkeypatch.setattr("app.services.log_query.NEWEST_FIRST_WINDOWS", (50, 200))
        attempts = _window_spy(monkeypatch)
        windowed = log_store.search(conn, log_filter, count_limit=5, **kwargs)

    assert [r["id"] for r in windowed[1]] == [r["id"] for r in everything[1]]
    assert windowed[0] == everything[0] == 5
    # Answered from the newest 50 rows, with no wider attempt
    assert attempts == [top[0]["id"] - 49]


def test_get_many_is_oldest_first_and_skips_missing_ids(db):
    ids = store_logs(db, _row(_ts(5)), _row(_ts(1)))
    with open_db(db, sqlite3.Row) as conn:
        rows = log_store.get_many(conn, [*ids, 999])
    assert [r["id"] for r in rows] == [ids[1], ids[0]]


def test_context_is_the_same_host_in_time_order(db):
    ids = store_logs(
        db,
        _row(_ts(1), alias="nas"),
        _row(_ts(2), alias="router"),
        _row(_ts(3), alias="nas"),
        _row(_ts(4), alias="nas"),
    )
    with open_db(db, sqlite3.Row) as conn:
        target, before, after = log_store.context(conn, ids[2], before=5, after=5)
        assert log_store.context(conn, 999, before=1, after=1) is None
    assert target["id"] == ids[2]
    assert [r["id"] for r in before] == [ids[0]]
    assert [r["id"] for r in after] == [ids[3]]


def test_relabel_host_changes_all_but_the_kept_names(db):
    ids = store_logs(
        db,
        _row(_ts(1), ip="10.0.0.5", alias=""),
        _row(_ts(2), ip="10.0.0.5", alias="old"),
        _row(_ts(3), ip="10.0.0.5", alias="dockerhost"),
        _row(_ts(4), ip="10.0.0.6", alias=""),
    )
    with open_db(db, sqlite3.Row) as conn:
        changed = log_store.relabel_host(conn, "10.0.0.5", "nas", keep_aliases=("dockerhost",), batch_size=1)
        names = {r[0]: r[1] for r in conn.execute("SELECT id, source_alias FROM logs")}
    assert changed == 2
    assert [names[i] for i in ids] == ["nas", "nas", "dockerhost", ""]


def test_relabel_host_only_one_name(db):
    ids = store_logs(db, _row(_ts(1), ip="docker", alias="a"), _row(_ts(2), ip="docker", alias="b"))
    with open_db(db, sqlite3.Row) as conn:
        assert log_store.relabel_host(conn, "docker", "c", only_alias="a") == 1
        names = [conn.execute("SELECT source_alias FROM logs WHERE id = ?", (i,)).fetchone()[0] for i in ids]
    assert names == ["c", "b"]


def test_relabel_host_every_other_name(db):
    store_logs(db, _row(_ts(1), ip="docker", alias="a"), _row(_ts(2), ip="docker", alias="b"))
    with open_db(db, sqlite3.Row) as conn:
        assert log_store.relabel_host(conn, "docker", "b") == 1
        assert {r[0] for r in conn.execute("SELECT source_alias FROM logs")} == {"b"}


def test_relabel_docker_app_spares_syslog_rows_from_the_same_address(db):
    store_logs(
        db,
        _row(_ts(1), ip="10.0.0.9", alias="docker", app="abc123"),
        _row(_ts(2), ip="10.0.0.9", alias="nas", app="abc123"),
    )
    with open_db(db, sqlite3.Row) as conn:
        log_store.relabel_docker_app(conn, "abc123", "web", source_ip="10.0.0.9", batch_size=1)
        apps = sorted((r[0], r[1]) for r in conn.execute("SELECT source_alias, app_name FROM logs"))
    assert apps == [("docker", "web"), ("nas", "abc123")]


def test_delete_by_ids_and_by_filter(db):
    ids = store_logs(db, _row(_ts(1), app="a"), _row(_ts(2), app="b"), _row(_ts(3), app="b"))
    calls = []
    with open_db(db, sqlite3.Row) as conn:
        assert log_store.delete(conn, LogFilter(log_ids=(ids[0], 999)), 10, lambda: calls.append(1)) == 1
        assert log_store.delete(conn, LogFilter(apps=("b",)), 1, lambda: calls.append(1)) == 2
        assert log_store.count_rows(conn) == 0
    assert calls


def test_delete_everything_keeps_rows_written_after_it_starts(db):
    store_logs(db, _row(_ts(1)), _row(_ts(2)))
    index_pending_logs(db)

    def _late_write():
        if not late:
            late.extend(store_logs(db, _row(_ts(3))))

    late: list[int] = []
    with open_db(db, sqlite3.Row) as conn:
        assert log_store.delete_everything(conn, 1, _late_write) == 2
        assert [r[0] for r in conn.execute("SELECT id FROM logs")] == late


def test_prune_deletes_only_indexed_rows_older_than_the_cutoff(db):
    store_logs(db, _row(_ts(1, day=1)), _row(_ts(1, day=2)))
    index_pending_logs(db)
    store_logs(db, _row(_ts(2, day=1)))  # old but not indexed yet
    cutoff = "2026-11-02T00:00:00+00:00"
    with open_db(db, sqlite3.Row) as conn:
        assert log_store.count_prunable(conn, cutoff) == 1
        assert log_store.prune(conn, cutoff, lambda: None, batch_size=1) == 1
        assert log_store.count_rows(conn) == 2


def test_seen_names(db):
    store_logs(
        db,
        _row(_ts(1), ip="10.0.0.1", alias="nas", app="smbd"),
        _row(_ts(2), ip="10.0.0.1", alias="nas", app="nfsd"),
        _row(_ts(3), ip="10.0.0.2", alias="", app="dnsmasq"),
    )
    with open_db(db, sqlite3.Row) as conn:
        seen = log_store.seen_names(conn, scan_limit=10, pair_limit=10)
    assert seen.aliases == [("nas", "10.0.0.1")]
    assert seen.bare_ips == ["10.0.0.2"]
    assert seen.apps == ["dnsmasq", "nfsd", "smbd"]
    assert seen.alias_apps == [("nas", "nfsd", "10.0.0.1"), ("nas", "smbd", "10.0.0.1")]
    assert seen.bare_ip_apps == [("dnsmasq", "10.0.0.2")]


def test_seen_names_stops_at_the_limits(db):
    store_logs(db, *[_row(_ts(i), alias=f"host{i}", app=f"app{i}") for i in range(5)])
    with open_db(db, sqlite3.Row) as conn:
        seen = log_store.seen_names(conn, scan_limit=3, pair_limit=2)
    assert (len(seen.aliases), len(seen.apps), len(seen.alias_apps)) == (3, 3, 2)


def test_error_and_digest_figures(db):
    store_logs(
        db,
        _row("2026-11-01T09:00:00+00:00", alias="nas", app="smbd", severity=3),
        _row("2026-11-01T11:30:00+00:00", alias="nas", app="smbd", severity=2),
        _row("2026-11-01T11:40:00+00:00", alias="router", app="dnsmasq", severity=4),
        _row("2026-11-01T11:50:00+00:00", alias="router", app="dnsmasq", severity=6),
    )
    with open_db(db, sqlite3.Row) as conn:
        errors = log_store.error_figures(conn, "2026-11-01T11:00:00", "2026-10-31T12:00:00")
        digest = log_store.digest_figures(conn, "2026-11-01T10:00:00+00:00")
    counts = errors.severity_counts
    assert (counts["count_24h"], counts["count_1h"], counts["count_warning"]) == (2, 1, 1)
    assert [(r["app_name"], r["cnt"]) for r in errors.top_apps] == [("smbd", 2)]
    assert [(r["host_val"], r["cnt"]) for r in errors.top_hosts] == [("nas", 2)]
    assert (digest.total_logs, digest.total_errors) == (3, 1)
    assert digest.top_errors[0]["entity"] == "nas" and digest.top_errors[0]["crit_count"] == 1
    assert digest.top_services == [{"service": "dnsmasq", "log_count": 2}, {"service": "smbd", "log_count": 1}]
