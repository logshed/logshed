"""
Backups and restores with day files: "Create backup now" (and the backup command) archives every
day file the catalogue lists beside the database, copied while logging carries on; a restore makes
the data folder match the archive, keeping everything it replaced in one before-restore folder.
"""

import collections
import datetime
import hashlib
import io
import json
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path
from unittest import mock

import pytest

import app.core.migrations as migrations_mod
from app import cli
from app.core import backup as db_backup
from app.core import day_files
from app.core import restore as restore_mod
from app.core.migrations import MIGRATIONS, MigrationNeeds, run_migrations
from app.services import day_store, log_store
from app.services.fts_indexer import index_pending_logs
from app.services.log_query import LogFilter
from tests.db_helpers import open_db

LATEST = MIGRATIONS[-1][0]
DiskUsage = collections.namedtuple("DiskUsage", "total used free")
GiB = 1024**3
_UTC = datetime.timezone.utc
D1, D2, D3 = "2026-03-27", "2026-03-28", "2026-03-29"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_BACKUP_ON_MIGRATION", raising=False)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(100 * GiB, 10 * GiB, 90 * GiB))
    day_store.forget_id_blocks()
    day_store.forget_connections()
    path = tmp_path / "data" / "logs.db"
    path.parent.mkdir()
    run_migrations(path)
    yield path
    day_store.forget_connections()


def _row(day: str, hour: int, message: str) -> dict:
    at = datetime.datetime.fromisoformat(day).replace(hour=hour, tzinfo=_UTC).isoformat()
    return {"timestamp": at, "received_at": at, "source_ip": "10.0.0.1", "source_alias": "nas",
            "app_name": "kernel", "facility": 1, "severity": 6, "message": message, "raw": message}


def _write(path: Path, *rows: dict) -> None:
    with open_db(path) as conn:
        day_store.write_rows(conn, [dict(r) for r in rows])
    index_pending_logs(path)


def _seal(path: Path) -> None:
    with open_db(path) as conn:
        day_store.seal_finished_days(conn, now_us=day_files.day_start_us("2026-04-01"))


def _messages(path: Path) -> list[str]:
    with open_db(path, sqlite3.Row) as conn:
        return sorted(r["message"] for r in log_store.search(conn, LogFilter(), fetch=100, count_limit=100)[1])


def _backup(path: Path) -> Path:
    return db_backup.create_backup(path, kind=f"manual-v{LATEST}", schema_version=LATEST, day_files=True).path


def _members(archive: Path) -> list[str]:
    with tarfile.open(archive) as tar:
        return tar.getnames()


def _manifest(archive: Path) -> dict:
    with tarfile.open(archive) as tar:
        return json.load(tar.extractfile(db_backup.MANIFEST_NAME))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestBackupWithDayFiles:
    def test_the_archive_holds_every_listed_day_file_then_the_database(self, db):
        _write(db, _row(D1, 1, "one"), _row(D2, 1, "two"))
        _seal(db)
        _write(db, _row(D3, 1, "three"))  # D3 stays open
        archive = _backup(db)
        assert _members(archive) == [day_files.file_name(D1), day_files.file_name(D2), day_files.file_name(D3), "logs.db", "manifest.json"]
        files = _manifest(archive)["files"]
        assert [(f["role"], f.get("day")) for f in files] == [("day", D1), ("day", D2), ("day", D3), ("main", None)]
        assert all(f["size_bytes"] > 0 for f in files)
        # A sealed file is archived as it is on disk (read snapshot); the open one is a VACUUM INTO copy
        with tarfile.open(archive) as tar:
            sealed = tar.extractfile(day_files.file_name(D1)).read()
        assert _sha(sealed) == _sha(day_files.day_path(db, D1).read_bytes())

    def test_a_manual_backup_without_day_files_is_unchanged(self, db):
        _write(db, _row(D1, 1, "one"))
        archive = db_backup.create_backup(db, kind=f"manual-v{LATEST}", schema_version=LATEST)
        assert _members(archive.path) == ["logs.db", "manifest.json"]

    def test_ids_given_out_after_the_database_was_copied_are_not_given_out_again(self, db, tmp_path):
        _write(db, _row(D1, 1, "before"))
        real = db_backup._vacuum_into

        def copy_then_log_more(src, dst, cancel=None):
            real(src, dst, cancel)
            # Logging carries on after the database copy: a new block of ids, into a day file archived later
            day_store.forget_id_blocks()
            _write(db, _row(D1, 2, "after the copy"))

        with mock.patch.object(db_backup, "_vacuum_into", copy_then_log_more):
            archive = _backup(db)
        with open_db(db) as conn:
            highest = log_store.max_id(conn)
        restored = tmp_path / "restored" / "logs.db"
        restored.parent.mkdir()
        restore_mod.restore_backup(archive, restored, LATEST, say=lambda _t: None)
        assert _messages(restored) == ["after the copy", "before"]
        with open_db(restored) as conn:
            assert conn.execute("SELECT next_id FROM log_id_state").fetchone()[0] > highest

    def test_a_listed_file_that_is_gone_is_left_out_and_unlisted_in_the_archive(self, db, tmp_path):
        _write(db, _row(D1, 1, "one"), _row(D2, 1, "two"))
        day_store.forget_connections()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{day_files.day_path(db, D1)}{suffix}").unlink(missing_ok=True)
        archive = _backup(db)
        assert day_files.file_name(D1) not in _members(archive)
        with tarfile.open(archive) as tar:
            (tmp_path / "x.db").write_bytes(tar.extractfile("logs.db").read())
        with closing(sqlite3.connect(tmp_path / "x.db")) as conn:
            assert {r[0] for r in conn.execute("SELECT day FROM log_files")} == {"legacy", D2}

    def test_wal_files_a_backup_run_as_root_makes_go_to_the_owner(self, db):
        _write(db, _row(D1, 1, "one"))
        _seal(db)
        day_store.forget_connections()  # the app has the sealed file closed: no -wal or -shm
        path = day_files.day_path(db, D1)
        for suffix in ("-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        given = []
        with mock.patch.object(db_backup, "_owner_to_apply", return_value=(1000, 1000)), \
                mock.patch.object(db_backup, "_give_to", side_effect=lambda p, owner: given.append((Path(p).name, owner))):
            _backup(db)
        assert (f"{path.name}-wal", (1000, 1000)) in given and (f"{path.name}-shm", (1000, 1000)) in given

    def test_the_space_check_counts_the_day_files(self, db):
        _write(db, *[_row(D1, h % 24, "x" * 2000) for h in range(300)])
        plain = db_backup.backup_space_needed(db, db_backup.METHOD_VACUUM_INTO)
        total, largest = db_backup.day_files_bytes(db)
        assert total > 0 and largest == total
        assert db_backup.backup_space_needed(db, db_backup.METHOD_VACUUM_INTO, day_files=True) > plain + largest

    def test_a_read_snapshot_keeps_a_file_still_while_it_is_written_and_checkpointed(self, db, tmp_path):
        _write(db, *[_row(D1, 1, f"m{i}") for i in range(200)])
        _seal(db)  # checkpointed: its WAL is empty
        path = day_files.day_path(db, D1)
        before = _sha(path.read_bytes())
        with db_backup._day_file_copy(path, tmp_path / "scratch.db", None) as (source, top):
            assert source == path and top == 200
            with open_db(db) as conn:
                log_store.delete(conn, LogFilter(query="m1"), 100, lambda: None)
            with closing(sqlite3.connect(path)) as other:
                assert other.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 1  # busy: not backfilled
            assert _sha(path.read_bytes()) == before
        with closing(sqlite3.connect(path)) as other:
            other.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert _sha(path.read_bytes()) != before

    def test_a_step_that_changes_day_files_backs_them_up_first(self, db):
        _write(db, _row(D1, 1, "one"))
        with mock.patch.dict(migrations_mod.MIGRATION_NEEDS, {LATEST: MigrationNeeds(touches_day_files=True)}):
            result = migrations_mod._backup_before_migrations(db, LATEST - 1, [LATEST])
        assert day_files.file_name(D1) in _members(result.path)
        # v4 itself does not: its backup holds logs.db only
        result = migrations_mod._backup_before_migrations(db, LATEST - 1, [LATEST])
        assert _members(result.path) == ["logs.db", "manifest.json"]


class TestRestoreWithDayFiles:
    def test_the_data_folder_matches_the_archive_and_everything_replaced_is_kept(self, db, capsys):
        _write(db, _row(D1, 1, "one"), _row(D2, 1, "two"))
        archive = _backup(db)
        _write(db, _row(D2, 2, "two later"), _row(D3, 1, "three"))
        before = {p.name: _sha(p.read_bytes()) for p in db.parent.glob("logs-*-UTC.db")}

        assert cli.restore_database(archive.name, str(db)) == 0

        assert sorted(p.name for p in db.parent.glob("logs-*-UTC.db")) == [day_files.file_name(D1), day_files.file_name(D2)]
        assert _messages(db) == ["one", "two"]
        (folder,) = db.parent.glob("before-restore-*")
        kept = sorted(p.name for p in folder.iterdir() if not p.name.endswith(("-wal", "-shm")))
        assert kept == sorted([*before, "logs.db"])
        assert set(before) == {day_files.file_name(d) for d in (D1, D2, D3)}
        assert not [p for p in db.parent.iterdir() if p.name.startswith(".restore-")]
        out = capsys.readouterr().out
        assert "It holds 2 day files of logs." in out and "and 3 day files it replaced are kept in" in out
        # Logging carries on above every id in the restored files, and start-up finds nothing unlisted
        with open_db(db) as conn:
            highest = log_store.max_id(conn)
            assert day_store.check_files(conn).unlisted == []
        _write(db, _row(D3, 5, "new"))
        with open_db(db) as conn:
            assert log_store.max_id(conn) > highest

    def test_an_archive_without_day_files_moves_every_day_file_aside(self, db):
        archive = _backup(db)  # no logs yet: no day files
        _write(db, _row(D1, 1, "one"))
        assert cli.restore_database(archive.name, str(db)) == 0
        assert list(db.parent.glob("logs-*-UTC.db")) == []
        (folder,) = db.parent.glob("before-restore-*")
        assert (folder / day_files.file_name(D1)).exists()
        assert _messages(db) == []

    @pytest.mark.parametrize("damage", ["missing_from_manifest", "missing_from_archive", "wrong_size"])
    def test_a_manifest_that_does_not_match_the_day_files_changes_nothing(self, db, tmp_path, damage):
        _write(db, _row(D1, 1, "one"), _row(D2, 1, "two"))
        archive = _backup(db)
        broken = tmp_path / "broken.tar.gz"
        with tarfile.open(archive) as src, tarfile.open(broken, "w:gz") as out:
            manifest = json.load(src.extractfile(db_backup.MANIFEST_NAME))
            for member in src.getmembers():
                if member.name == db_backup.MANIFEST_NAME:
                    continue
                if damage == "missing_from_archive" and member.name == day_files.file_name(D1):
                    continue
                out.addfile(member, src.extractfile(member))
            if damage == "missing_from_manifest":
                manifest["files"] = [f for f in manifest["files"] if f.get("day") != D1]
            if damage == "wrong_size":
                manifest["files"][0]["size_bytes"] += 1
            body = json.dumps(manifest).encode()
            info = tarfile.TarInfo(db_backup.MANIFEST_NAME)
            info.size = len(body)
            out.addfile(info, io.BytesIO(body))
        before = sorted(p.name for p in db.parent.glob("logs*.db"))
        with pytest.raises(restore_mod.RestoreError, match="damaged"):
            restore_mod.restore_backup(broken, db, LATEST, say=lambda _t: None)
        assert sorted(p.name for p in db.parent.glob("logs*.db")) == before
        assert not [p for p in db.parent.iterdir() if p.name.startswith((".restore-", "before-restore-"))]
        assert _messages(db) == ["one", "two"]

    def test_a_day_file_in_a_newer_layout_is_refused(self, db, tmp_path):
        _write(db, _row(D1, 1, "one"))
        day_store.forget_connections()
        with closing(sqlite3.connect(day_files.day_path(db, D1))) as conn:
            conn.execute(f"PRAGMA user_version = {day_files.LAYOUT_VERSION + 1}")
        with mock.patch.object(day_files, "check_layout", lambda conn, path: 1):
            archive = _backup(db)
        with pytest.raises(restore_mod.RestoreError, match="layout"):
            restore_mod.restore_backup(archive, db, LATEST, say=lambda _t: None)


def test_the_copied_file_list_keeps_no_counts_for_files_left_out(db, tmp_path):
    """
    The backup's copy of the file list is cut to the archived day files, and their counts go with
    them: the foreign keys are switched on before the transaction, where the pragma takes effect.
    """
    _write(db, _row(D1, 3, "one"), _row(D2, 3, "two"))
    _seal(db)
    with open_db(db) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert {r[0] for r in conn.execute("SELECT DISTINCT day FROM log_file_counts")} == {D1, D2}
    copy = tmp_path / "copy.db"
    copy.write_bytes(db.read_bytes())

    db_backup._match_catalogue(copy, [D1], None)

    with closing(sqlite3.connect(copy)) as conn:
        assert [r[0] for r in conn.execute("SELECT day FROM log_files WHERE day != 'legacy'")] == [D1]
        assert {r[0] for r in conn.execute("SELECT DISTINCT day FROM log_file_counts")} == {D1}
