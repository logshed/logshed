"""Database backups: the archive, the backup before a migration, the backup command and expiry."""

import collections
import datetime
import errno
import json
import logging
import os
import sqlite3
import tarfile
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

import app.core.migrations as migrations_mod
from app import cli
from app.core import backup as db_backup
from app.core.migrations import (
    MIGRATIONS,
    MigrationBackupError,
    MigrationNeeds,
    get_connection,
    get_user_version,
    run_migrations,
)

DiskUsage = collections.namedtuple("DiskUsage", "total used free")
GiB = 1024**3
LATEST = MIGRATIONS[-1][0]
NEXT = LATEST + 1


def _migrate_to(path: Path, version: int, monkeypatch) -> None:
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", MIGRATIONS[:version])
    run_migrations(path)
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", MIGRATIONS)


def _add_logs(path: Path, count: int, start: int = 0) -> None:
    with closing(get_connection(path)) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
            "VALUES ('2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00', '10.0.0.1', 'nas', 'app', 1, 6, ?, ?)",
            [(f"message {i}", f"raw {i}") for i in range(start, start + count)],
        )
        conn.commit()


def _add_settings_rows(path: Path) -> None:
    with closing(get_connection(path)) as conn:
        conn.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('backup_test_key', 'kept', datetime('now'), 0)"
        )
        conn.execute("INSERT INTO alert_rules (name, rule_type, created_at) VALUES ('rule one', 'threshold', datetime('now'))")
        conn.commit()


def _counts(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM pragma_table_list WHERE schema = 'main' AND type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def _archives(db_path: Path) -> list[Path]:
    folder = db_backup.backups_dir(db_path)
    return sorted(folder.glob("*.tar.gz")) if folder.exists() else []


def _extract(archive: Path, dest: Path) -> tuple[dict, Path]:
    with tarfile.open(archive, "r:gz") as tar:
        assert tar.getnames()[-1] == "manifest.json"
        manifest = json.loads(tar.extractfile("manifest.json").read())
        tar.extractall(dest, filter="data")
    return manifest, dest / "logs.db"


def _integrity(path: Path) -> str:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]


def _schema_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return get_user_version(conn)


def _no_tmp(db_path: Path) -> bool:
    folder = db_backup.backups_dir(db_path)
    return not folder.exists() or not list(folder.glob("*.tmp"))


def _with_next_step(monkeypatch, needs: MigrationNeeds | None = None, func=None) -> None:
    """Add a harmless step after the latest, as the next release's migration would be."""

    def migrate_next(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS next_marker (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(migrations_mod, "MIGRATIONS", list(MIGRATIONS) + [(NEXT, func or migrate_next)])
    monkeypatch.setattr(migrations_mod, "MIGRATION_NEEDS", {NEXT: needs} if needs else {})


@pytest.fixture
def db(tmp_path):
    return tmp_path / "logs.db"


@pytest.fixture(autouse=True)
def _backup_env(monkeypatch):
    monkeypatch.delenv("AUTO_BACKUP_ON_MIGRATION", raising=False)


@pytest.fixture
def roomy_disk(monkeypatch):
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(100 * GiB, 10 * GiB, 90 * GiB))


# --- The backup before a migration ---

def test_fresh_install_makes_no_backup(db):
    run_migrations(db)
    assert not db_backup.backups_dir(db).exists()


def test_current_database_makes_no_backup(db):
    run_migrations(db)
    run_migrations(db)
    assert not db_backup.backups_dir(db).exists()


def test_upgrade_from_v2_makes_one_full_archive(db, monkeypatch, caplog):
    _migrate_to(db, 2, monkeypatch)
    _add_logs(db, 50)
    _add_settings_rows(db)
    before = _counts(db)

    with caplog.at_level(logging.INFO, logger="app.core.backup"):
        run_migrations(db)
    # Streamed under the lock, not the fallback that needs the database's size free
    assert not any("VACUUM INTO instead" in r.getMessage() for r in caplog.records)

    archives = _archives(db)
    assert len(archives) == 1
    assert db_backup.ARCHIVE_NAME_RE.match(archives[0].name)["kind"] == f"pre-v2-to-v{LATEST}"
    assert archives[0].stat().st_mode & 0o777 == 0o640
    manifest, restored = _extract(archives[0], db.parent / "x")
    assert manifest["format"] == 1
    assert manifest["kind"] == "pre-migration"
    assert (manifest["schema_version"], manifest["target_schema"]) == (2, LATEST)
    assert manifest["files"] == [{"name": "logs.db", "role": "main", "size_bytes": restored.stat().st_size}]
    assert _integrity(restored) == "ok"
    assert _schema_version(restored) == 2
    assert _counts(restored) == before
    assert _schema_version(db) == LATEST
    assert _no_tmp(db)


def test_upgrade_from_v3_backs_up_once_before_all_steps(db, monkeypatch):
    run_migrations(db)
    _add_logs(db, 10)
    _with_next_step(monkeypatch)
    run_migrations(db)
    archives = _archives(db)
    assert [db_backup.ARCHIVE_NAME_RE.match(a.name)["kind"] for a in archives] == [f"pre-v{LATEST}-to-v{NEXT}"]
    _, restored = _extract(archives[0], db.parent / "x")
    assert _schema_version(restored) == LATEST
    assert _counts(restored)["logs"] == 10


def test_step_that_keeps_log_rows_gets_a_backup_without_logs(db, monkeypatch, caplog):
    run_migrations(db)
    _add_logs(db, 30)
    _add_settings_rows(db)
    before = _counts(db)
    _with_next_step(monkeypatch, MigrationNeeds(backup_without_logs=True))

    with caplog.at_level("INFO", logger="app.core.backup"):
        run_migrations(db)
    assert "leaving out the stored logs (database " in caplog.text

    (archive,) = _archives(db)
    manifest, restored = _extract(archive, db.parent / "x")
    assert manifest["files"][0]["omitted_tables"] == ["logs", "logs_fts"]
    assert _integrity(restored) == "ok"
    after = _counts(restored)
    assert after["logs"] == 0
    assert {t: n for t, n in after.items() if not t.startswith("logs")} == {
        t: n for t, n in before.items() if not t.startswith("logs")
    }
    with closing(sqlite3.connect(restored)) as conn:
        assert get_user_version(conn) == LATEST
        # Ids carry on after the old ones, so the full-text indexer's position stays valid
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'logs'").fetchone()[0] == 30
        conn.execute("INSERT INTO logs_fts(logs_fts) VALUES ('integrity-check')")
        with closing(sqlite3.connect(db)) as src:
            source = {r for r in src.execute("SELECT type, name FROM sqlite_master")}
        assert {r for r in conn.execute("SELECT type, name FROM sqlite_master")} == source - {("table", "next_marker")}


def test_backup_without_logs_needs_every_pending_step_to_allow_it(db, monkeypatch):
    _migrate_to(db, 2, monkeypatch)
    _add_logs(db, 5)
    _with_next_step(monkeypatch, MigrationNeeds(backup_without_logs=True))
    run_migrations(db)
    (archive,) = _archives(db)
    manifest, restored = _extract(archive, db.parent / "x")
    assert "omitted_tables" not in manifest["files"][0]
    assert _counts(restored)["logs"] == 5


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_auto_backup_off_makes_none(db, monkeypatch, value):
    _migrate_to(db, 2, monkeypatch)
    monkeypatch.setenv("AUTO_BACKUP_ON_MIGRATION", value)
    run_migrations(db)
    assert _schema_version(db) == LATEST
    assert not db_backup.backups_dir(db).exists()


@pytest.mark.parametrize("value", ["", "true", "yes", "nonsense"])
def test_auto_backup_on_unless_false(db, monkeypatch, value):
    _migrate_to(db, 2, monkeypatch)
    monkeypatch.setenv("AUTO_BACKUP_ON_MIGRATION", value)
    run_migrations(db)
    assert len(_archives(db)) == 1


@pytest.mark.parametrize(
    "free",
    [
        pytest.param(1024, id="no-room-for-the-archive"),
        pytest.param(2 * GiB, id="room-for-the-archive-but-under-the-floor"),
    ],
)
def test_too_little_space_stops_the_migration(db, monkeypatch, caplog, free):
    _migrate_to(db, 2, monkeypatch)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(50 * GiB, 50 * GiB - free, free))

    with caplog.at_level(logging.WARNING, logger="app.core.migrations"):
        with pytest.raises(MigrationBackupError) as raised:
            run_migrations(db)

    assert _schema_version(db) == 2
    assert _archives(db) == [] and _no_tmp(db)
    message = str(raised.value)
    assert "AUTO_BACKUP_ON_MIGRATION=false" in message
    assert "5.0 GiB free on the disk" in message  # 10% of 50 GiB
    warning = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning) == 1 and "must leave 5.0 GiB free" in warning[0]


def test_disk_full_while_writing_removes_the_partial_file(db, monkeypatch, roomy_disk):
    _migrate_to(db, 2, monkeypatch)
    real_write = db_backup._write_archive

    def full_disk(tmp_path, *args, **kwargs):
        tmp_path.write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(db_backup, "_write_archive", full_disk)
    with pytest.raises(MigrationBackupError, match="No space left"):
        run_migrations(db)
    assert _schema_version(db) == 2
    assert _archives(db) == [] and _no_tmp(db)

    monkeypatch.setattr(db_backup, "_write_archive", real_write)
    run_migrations(db)
    assert _schema_version(db) == LATEST


def test_migration_extra_space_is_kept_free(db, monkeypatch):
    run_migrations(db)
    _with_next_step(monkeypatch, MigrationNeeds(extra_space=lambda size: 40 * GiB))
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(100 * GiB, 70 * GiB, 30 * GiB))
    with pytest.raises(MigrationBackupError):
        run_migrations(db)
    assert _schema_version(db) == LATEST


def test_floor_is_ten_percent_between_2_and_20_gib():
    assert db_backup.free_space_floor(10 * GiB) == 2 * GiB
    assert db_backup.free_space_floor(100 * GiB) == 10 * GiB
    assert db_backup.free_space_floor(8000 * GiB) == 20 * GiB


def test_backup_error_stops_start_up_in_the_cli(db, monkeypatch, capsys):
    _migrate_to(db, 2, monkeypatch)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(50 * GiB, 50 * GiB, 0))
    with pytest.raises(SystemExit) as raised:
        monkeypatch.setattr("sys.argv", ["app.cli", "seed-logs", "--count", "1", "--db-path", str(db)])
        cli.main()
    assert raised.value.code == 1
    assert "AUTO_BACKUP_ON_MIGRATION=false" in capsys.readouterr().err


# --- Locked stream ---

def test_locked_stream_falls_back_when_the_wal_is_not_empty_under_the_lock(db, monkeypatch, roomy_disk):
    run_migrations(db)
    _add_logs(db, 20)
    # Another process commits between the checkpoint and the lock
    monkeypatch.setattr(db_backup, "_wal_is_empty", lambda path: False)
    result = db_backup.create_backup(db, "pre-v3-to-v4", 3, 4, method=db_backup.METHOD_LOCKED_STREAM)
    assert result.method == db_backup.METHOD_VACUUM_INTO
    _, restored = _extract(result.path, db.parent / "x")
    assert _integrity(restored) == "ok" and _counts(restored)["logs"] == 20
    assert _no_tmp(db)


def test_locked_stream_fallback_without_room_fails_cleanly(db, monkeypatch):
    run_migrations(db)
    size = db.stat().st_size
    # Room for the compressed stream, not for the full copy the fallback needs
    free = 2 * GiB + size
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(10 * GiB, 10 * GiB - free, free))
    monkeypatch.setattr(db_backup, "_wal_is_empty", lambda path: False)
    with pytest.raises(db_backup.BackupSpaceError):
        db_backup.create_backup(db, "pre-v3-to-v4", 3, 4, method=db_backup.METHOD_LOCKED_STREAM)
    assert _archives(db) == [] and _no_tmp(db)


def test_locked_stream_holds_writers_off_and_reports_progress(db, roomy_disk):
    run_migrations(db)
    _add_logs(db, 200)
    seen = []
    locked = []

    def progress(done, total):
        if not locked:
            with closing(sqlite3.connect(db, timeout=0)) as other:
                try:
                    other.execute("BEGIN IMMEDIATE")
                    locked.append(False)
                except sqlite3.OperationalError:
                    locked.append(True)
        seen.append((done, total))

    db_backup.create_backup(db, "pre-v3-to-v4", 3, 4, method=db_backup.METHOD_LOCKED_STREAM, progress_cb=progress)
    assert locked == [True]
    assert seen[-1][0] == seen[-1][1] == db.stat().st_size
    with closing(sqlite3.connect(db, timeout=0)) as other:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")


# --- The backup command ---

def test_backup_command_while_another_connection_inserts(db, monkeypatch, roomy_disk, capsys):
    run_migrations(db)
    _add_logs(db, 500)
    stop = threading.Event()
    inserted = []

    def writer():
        n = 1000
        while not stop.is_set():
            _add_logs(db, 20, start=n)
            n += 20
            inserted.append(n)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while not inserted:
            time.sleep(0.001)
        status = cli.backup_database(str(db))
    finally:
        stop.set()
        thread.join()

    assert status == 0
    out = capsys.readouterr().out
    (archive,) = _archives(db)
    assert f"Backup written: {archive}" in out
    assert "Deleted automatically after" in out
    assert db_backup.ARCHIVE_NAME_RE.match(archive.name)["kind"] == f"manual-v{LATEST}"
    manifest, restored = _extract(archive, db.parent / "x")
    assert manifest["kind"] == "manual" and manifest["target_schema"] is None
    assert _integrity(restored) == "ok"
    assert _counts(restored)["logs"] >= 520


def test_backup_command_without_room_says_to_copy_the_folder(db, monkeypatch, capsys):
    run_migrations(db)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(10 * GiB, 10 * GiB, 0))
    assert cli.backup_database(str(db)) == 1
    err = capsys.readouterr().err
    assert "Stop the container and copy the data folder instead" in err
    assert not db_backup.backups_dir(db).exists()


def test_backup_command_defaults_to_the_app_database(tmp_path, monkeypatch, roomy_disk):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    run_migrations(tmp_path / "logs.db")
    assert cli.backup_database(None) == 0
    assert len(_archives(tmp_path / "logs.db")) == 1


def test_backup_command_run_as_root_gives_files_to_the_database_owner(db, monkeypatch, roomy_disk):
    run_migrations(db)
    calls = []
    monkeypatch.setattr(db_backup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(db_backup, "_owner", lambda path: (1000, 1000))
    monkeypatch.setattr(db_backup.os, "chown", lambda path, uid, gid: calls.append((Path(path).name, uid, gid)))

    assert cli.backup_database(str(db)) == 0

    (archive,) = _archives(db)
    names = [c[0] for c in calls]
    assert ("backups", 1000, 1000) in calls
    assert f"{archive.name}.db.tmp" in names  # the copy, so a stale one can be expired
    assert f"{archive.name}.tmp" in names  # the archive, before its final rename
    assert all(c[1:] == (1000, 1000) for c in calls)


def test_names_never_collide(db, roomy_disk):
    run_migrations(db)
    now = datetime.datetime(2026, 11, 13, 12, 0, 0, tzinfo=datetime.timezone.utc)
    first = db_backup.create_backup(db, "manual-v3", 3, now=now)
    second = db_backup.create_backup(db, "manual-v3", 3, now=now)
    assert first.path.name == "logshed-backup-manual-v3-20261113T120000Z.tar.gz"
    assert second.path.name == "logshed-backup-manual-v3-20261113T120000Z-2.tar.gz"
    assert db_backup.ARCHIVE_NAME_RE.match(second.path.name)


# --- Expiry ---

def _touch(path: Path, age_seconds: float, now: float) -> Path:
    path.write_bytes(b"x")
    os.utime(path, (now - age_seconds, now - age_seconds))
    return path


def test_expiry_deletes_only_matching_names_older_than_30_days(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    now = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=datetime.timezone.utc).timestamp()
    old = folder / "logshed-backup-pre-v3-to-v4-20261013T115959Z.tar.gz"
    young = folder / "logshed-backup-manual-v4-20261014T120001Z-2.tar.gz"
    foreign = folder / "my-own-copy.tar.gz"
    for path in (old, young, foreign):
        _touch(path, 60 * 86400, now)  # the name's time decides, not the file's
    assert db_backup.prune_backups(folder, now=now) == (1, 0)
    assert sorted(p.name for p in folder.iterdir()) == sorted([young.name, foreign.name])


def test_expiry_keeps_a_recent_tmp_file_and_deletes_a_stale_one(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    now = time.time()
    running = _touch(folder / "logshed-backup-manual-v3-20261113T120000Z.tar.gz.db.tmp", 3600, now)
    stale = _touch(folder / "logshed-backup-manual-v3-20261112T000000Z.tar.gz.tmp", 25 * 3600, now)
    assert db_backup.prune_backups(folder, now=now) == (0, 1)
    assert running.exists() and not stale.exists()


def test_prune_expires_backups(db, roomy_disk):
    from app.services.retention import execute_prune

    run_migrations(db)
    folder = db_backup.backups_dir(db)
    folder.mkdir()
    old = folder / "logshed-backup-manual-v3-20200101T000000Z.tar.gz"
    old.write_bytes(b"x")
    execute_prune(db, retention_days=14, index_pending=False)
    assert not old.exists()


def test_backup_before_migration_expires_old_archives_first(db, monkeypatch, roomy_disk):
    _migrate_to(db, 2, monkeypatch)
    folder = db_backup.backups_dir(db)
    folder.mkdir()
    old = folder / "logshed-backup-manual-v2-20200101T000000Z.tar.gz"
    old.write_bytes(b"x")
    run_migrations(db)
    assert not old.exists()
    assert len(_archives(db)) == 1


def test_backups_folder_is_private_whatever_the_umask(db, roomy_disk):
    run_migrations(db)
    old = os.umask(0)
    try:
        assert cli.backup_database(str(db)) == 0
    finally:
        os.umask(old)
    assert db_backup.backups_dir(db).stat().st_mode & 0o777 == 0o750
    (archive,) = _archives(db)
    assert archive.stat().st_mode & 0o777 == 0o640


def test_space_error_names_unfinished_backup_files(db, monkeypatch):
    run_migrations(db)
    folder = db_backup.backups_dir(db)
    folder.mkdir()
    (folder / "logshed-backup-pre-v3-to-v4-20261113T120000Z.tar.gz.tmp").write_bytes(b"x" * 4096)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(10 * GiB, 10 * GiB, 0))
    with pytest.raises(db_backup.BackupSpaceError, match=r"Unfinished backup files \(\*\.tmp\) in that folder take 4.0 KiB"):
        db_backup.create_backup(db, "pre-v3-to-v4", 3, 4, method=db_backup.METHOD_LOCKED_STREAM)


def test_a_failure_other_than_space_does_not_ask_for_space(db, monkeypatch, roomy_disk):
    _migrate_to(db, 2, monkeypatch)

    def fail(*args, **kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(db_backup, "_write_archive", fail)
    with pytest.raises(MigrationBackupError) as raised:
        run_migrations(db)
    assert "Fix the cause" in str(raised.value) and "Free some space" not in str(raised.value)
