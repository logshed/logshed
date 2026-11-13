"""
Tests for the day-file layout (app/core/day_files.py) and the catalogue that schema v4 adds:
stored forms of times and raw lines, file names, and the legacy entry of an upgraded database.
"""

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

import app.core.migrations as migrations_mod
from app.core import backup as db_backup
from app.core import day_files
from app.core.migrations import MIGRATIONS, backup_plan, run_migrations
from tests.db_helpers import open_db


def _make_v3(path: Path) -> None:
    with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
        run_migrations(path)


def _insert_legacy(path: Path, *rows: tuple[str, str]) -> None:
    """Rows (timestamp, message) in the logs table, as 1.3.x stored them."""
    with open_db(path) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
            "VALUES (?, ?, '10.0.0.1', 'nas', 'kernel', 1, 6, ?, ?)",
            [(ts, ts, msg, msg) for ts, msg in rows],
        )


def _catalogue(path: Path) -> list[dict]:
    with open_db(path, row_factory=sqlite3.Row) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM log_files ORDER BY day")]


def _next_id(path: Path) -> int:
    with open_db(path) as conn:
        return conn.execute("SELECT next_id FROM log_id_state WHERE id = 1").fetchone()[0]


class TestStoredForms:
    @pytest.mark.parametrize(
        "iso",
        [
            "2026-11-13T10:00:00+00:00",
            "2026-11-13T10:00:00.000001+00:00",
            "2026-11-13T23:59:59.999999+00:00",
            "1970-01-01T00:00:00+00:00",
            "1969-12-31T23:59:59.500000+00:00",
            "2001-02-03T04:05:06.070000+00:00",
        ],
    )
    def test_a_stored_time_comes_back_as_the_same_string_in_python_and_in_sql(self, iso):
        value = day_files.iso_to_us(iso)
        assert day_files.us_to_iso(value) == iso
        conn = sqlite3.connect(":memory:")
        try:
            assert conn.execute(f"SELECT {day_files.iso_sql(':v')}", {"v": value}).fetchone()[0] == iso
        finally:
            conn.close()

    def test_z_and_no_offset_read_as_utc(self):
        expected = day_files.iso_to_us("2026-11-13T10:00:00+00:00")
        assert day_files.iso_to_us("2026-11-13T10:00:00Z") == expected
        assert day_files.iso_to_us("2026-11-13T10:00:00") == expected
        assert day_files.iso_to_us("2026-11-13T11:00:00+01:00") == expected

    def test_days_are_utc_days(self):
        just_before = day_files.iso_to_us("2026-03-29T00:59:59.999999+01:00")
        assert day_files.day_of_us(just_before) == "2026-03-28"
        assert day_files.day_of_us(just_before + 1) == "2026-03-29"
        assert day_files.day_start_us("2026-03-29") == just_before + 1

    @pytest.mark.parametrize(
        "message, raw, kind, stored",
        [
            ("disk full", "disk full", day_files.RAW_IS_MESSAGE, None),
            ("disk full", None, day_files.RAW_IS_MESSAGE, None),
            ("disk full", "<13>1 2026-11-13T10:00:00Z nas kernel - - - disk full", day_files.RAW_IS_PREFIX, "<13>1 2026-11-13T10:00:00Z nas kernel - - - "),
            ("disk full", "disk full (repeated)", day_files.RAW_IS_WHOLE, "disk full (repeated)"),
            ("", "a packet with no message", day_files.RAW_IS_WHOLE, "a packet with no message"),
            ("", "", day_files.RAW_IS_MESSAGE, None),
            ("x", "", day_files.RAW_IS_WHOLE, ""),
        ],
    )
    def test_raw_is_stored_without_the_message_and_rebuilt_exactly(self, message, raw, kind, stored):
        assert day_files.split_raw(message, raw) == (stored, kind)
        assert day_files.join_raw(message, stored, kind) == (raw if raw is not None else message)

    def test_raw_rebuilt_in_sql_matches_python(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE logs (message TEXT, raw TEXT, raw_kind INTEGER)")
            cases = [("m", "m"), ("m", "head m"), ("m", "other"), ("", "x"), ("é", "<1>é")]
            for message, raw in cases:
                stored, kind = day_files.split_raw(message, raw)
                conn.execute("INSERT INTO logs VALUES (?, ?, ?)", (message, stored, kind))
            rebuilt = [r[0] for r in conn.execute(f"SELECT {day_files.RAW_SQL} FROM logs")]
            assert rebuilt == [raw for _m, raw in cases]
        finally:
            conn.close()

    def test_file_names_say_the_day_is_utc(self, tmp_path):
        assert day_files.file_name("2026-11-12") == "logs-2026-11-12-UTC.db"
        assert day_files.day_from_file_name("logs-2026-11-12-UTC.db") == "2026-11-12"
        assert day_files.day_from_file_name("logs.db") is None
        assert day_files.day_from_file_name("logs-2026-11-12-UTC.db-wal") is None
        assert day_files.day_path(tmp_path / "logs.db", "2026-11-12") == tmp_path / "logs-2026-11-12-UTC.db"


class TestLayout:
    def test_a_new_file_gets_the_layout_and_an_empty_index_state(self, tmp_path):
        path = tmp_path / "logs-2026-11-13-UTC.db"
        with open_db(path) as conn:
            day_files.create_layout(conn)
            assert day_files.check_layout(conn, path) == day_files.LAYOUT_VERSION
            assert conn.execute("SELECT last_indexed_id FROM fts_index_state").fetchone() == (0,)
            # A second call leaves the file as it is
            day_files.create_layout(conn)
            assert conn.execute("SELECT COUNT(*) FROM fts_index_state").fetchone() == (1,)

    def test_a_file_in_a_newer_layout_is_refused(self, tmp_path):
        path = tmp_path / "logs-2026-11-13-UTC.db"
        with open_db(path) as conn:
            conn.execute(f"PRAGMA user_version = {day_files.LAYOUT_VERSION + 1}")
            with pytest.raises(day_files.DayFileNewerThanAppError, match="newer version"):
                day_files.check_layout(conn, path)


class TestSchemaV4Catalogue:
    def test_a_new_database_lists_an_empty_legacy_table_and_starts_ids_at_1(self, tmp_path):
        path = tmp_path / "logs.db"
        run_migrations(path)
        [legacy] = _catalogue(path)
        assert legacy["day"] == day_files.LEGACY_DAY
        assert (legacy["file_name"], legacy["layout"], legacy["state"], legacy["row_count"]) == ("logs.db", 0, "legacy", 0)
        assert legacy["min_id"] is None and legacy["max_ts"] is None
        assert _next_id(path) == 1

    def test_an_upgraded_database_lists_its_logs_as_legacy_without_touching_them(self, tmp_path):
        path = tmp_path / "logs.db"
        _make_v3(path)
        _insert_legacy(
            path,
            ("2026-11-10T08:00:00.250000+00:00", "first"),
            ("2026-11-12T23:59:59+00:00", "last"),
            ("2026-11-11T12:00:00+00:00", "middle"),
        )
        with open_db(path) as conn:
            before = conn.execute("SELECT * FROM logs ORDER BY id").fetchall()
        run_migrations(path, backup=False)

        [legacy] = _catalogue(path)
        assert legacy["row_count"] == 3
        assert (legacy["min_id"], legacy["max_id"]) == (1, 3)
        assert legacy["min_ts"] == day_files.iso_to_us("2026-11-10T08:00:00.250000+00:00")
        assert legacy["max_ts"] == day_files.iso_to_us("2026-11-12T23:59:59+00:00")
        assert _next_id(path) == 4
        with open_db(path) as conn:
            assert conn.execute("SELECT * FROM logs ORDER BY id").fetchall() == before
        # No day file is made by the upgrade
        assert sorted(p.name for p in tmp_path.glob("logs-*")) == []

    def test_ids_carry_on_after_the_highest_id_ever_given(self, tmp_path):
        path = tmp_path / "logs.db"
        _make_v3(path)
        _insert_legacy(path, *[(f"2026-11-1{i}T00:00:00+00:00", f"m{i}") for i in range(5)])
        with open_db(path) as conn:
            conn.execute("DELETE FROM logs WHERE id >= 4")
        run_migrations(path, backup=False)
        assert _next_id(path) == 6
        assert _catalogue(path)[0]["max_id"] == 3

    def test_an_unreadable_legacy_time_leaves_the_range_open(self, tmp_path):
        path = tmp_path / "logs.db"
        _make_v3(path)
        _insert_legacy(path, ("not a time", "odd"))
        run_migrations(path, backup=False)
        [legacy] = _catalogue(path)
        assert legacy["row_count"] == 1
        assert legacy["min_ts"] is None and legacy["max_ts"] is None

    def test_the_backup_before_v4_leaves_the_logs_out_but_one_from_v2_keeps_them(self, tmp_path):
        path = tmp_path / "logs.db"
        _make_v3(path)
        method, extra = backup_plan(path, [4])
        assert method == db_backup.METHOD_WITHOUT_LOGS
        assert 0 < extra <= 64 * 1024**2
        assert backup_plan(path, [3, 4])[0] == db_backup.METHOD_LOCKED_STREAM
