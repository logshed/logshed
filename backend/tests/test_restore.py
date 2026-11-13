"""The restore command: an archive in place of the database, checked first, with the old one kept aside."""

import collections
import io
import json
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path

import pytest

import app.core.migrations as migrations_mod
import app.main as main_mod
from app import cli
from app.core import backup as db_backup
from app.core import instance_lock, lifecycle
from app.core import restore as restore_mod
from app.core.migrations import MIGRATIONS, get_connection, get_user_version, run_migrations

LATEST = MIGRATIONS[-1][0]
NEXT = LATEST + 1

DiskUsage = collections.namedtuple("DiskUsage", "total used free")
GiB = 1024**3


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_BACKUP_ON_MIGRATION", raising=False)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(100 * GiB, 10 * GiB, 90 * GiB))
    path = tmp_path / "data" / "logs.db"
    path.parent.mkdir()
    run_migrations(path)
    return path


def _set(db: Path, key: str, value: str) -> None:
    with closing(get_connection(db)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES (?, ?, datetime('now'), 0)",
            (key, value),
        )
        conn.commit()


def _get(db: Path, key: str):
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def _schema(db: Path) -> int:
    with closing(sqlite3.connect(db)) as conn:
        return get_user_version(conn)


def _backup(db: Path, schema: int | None = None) -> Path:
    schema = schema if schema is not None else _schema(db)
    return db_backup.create_backup(db, kind=f"manual-v{schema}", schema_version=schema).path


def _leftovers(db: Path) -> list[str]:
    return sorted(p.name for p in db.parent.iterdir() if p.name.startswith(".restore-"))


def _repack(archive: Path, out: Path, db_bytes: bytes | None = None, manifest_update: dict | None = None,
            extra: list[tuple[str, bytes]] | None = None) -> Path:
    """A copy of an archive with the database bytes or the manifest changed, or extra members."""
    with tarfile.open(archive, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        original = tar.extractfile("logs.db").read()
    data = original if db_bytes is None else db_bytes
    manifest["files"][0]["size_bytes"] = len(data)
    manifest.update(manifest_update or {})
    members = [("logs.db", data)] + (extra or []) + [("manifest.json", json.dumps(manifest).encode())]
    with tarfile.open(out, "w:gz") as tar:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return out


def test_restore_puts_the_backup_in_place_and_keeps_the_old_database(db, capsys):
    _set(db, "marker", "in the backup")
    archive = _backup(db)
    _set(db, "marker", "after the backup")
    _set(db, "later", "only in the old database")

    assert cli.restore_database(archive.name, str(db)) == 0

    assert _get(db, "marker") == "in the backup" and _get(db, "later") is None
    (folder,) = [p for p in db.parent.iterdir() if p.name.startswith("before-restore-")]
    assert folder.is_dir() and _get(folder / "logs.db", "later") == "only in the old database"
    assert not db.with_name("logs.db-wal").exists() or db.with_name("logs.db-wal").stat().st_size == 0
    assert _leftovers(db) == []
    assert archive.exists()
    out = capsys.readouterr().out
    assert f"Restored {archive.name}" in out and f"schema {LATEST}" in out
    assert f"kept in {folder}" in out and "Delete that folder once" in out


def test_restore_of_an_archive_from_the_next_schema_while_it_is_known(db, monkeypatch):
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", list(MIGRATIONS) + [(NEXT, lambda c: c.execute("CREATE TABLE next_marker (id INTEGER)"))])
    run_migrations(db, backup=False)
    _set(db, "marker", "next backup")
    archive = _backup(db)
    _set(db, "marker", "changed")
    assert cli.restore_database(str(archive), str(db)) == 0
    assert _schema(db) == NEXT and _get(db, "marker") == "next backup"


def test_an_older_schema_is_restored_and_upgraded_at_the_next_start(db, tmp_path, monkeypatch, capsys):
    old = tmp_path / "old" / "logs.db"
    old.parent.mkdir()
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", MIGRATIONS[:2])
    run_migrations(old)
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", MIGRATIONS)
    archive = _backup(old, 2)
    assert cli.restore_database(str(archive), str(db)) == 0
    assert _schema(db) == 2
    assert f"upgrades it to schema {LATEST} when it starts" in capsys.readouterr().out


def test_a_newer_schema_is_refused(db, monkeypatch, capsys):
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", list(MIGRATIONS) + [(NEXT, lambda c: None)])
    newer = db.with_name("newer.db")
    run_migrations(newer)
    archive = db_backup.create_backup(newer, kind=f"manual-v{NEXT}", schema_version=NEXT).path
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", MIGRATIONS)
    before = db.read_bytes()

    assert cli.restore_database(str(archive), str(db)) == 1

    err = capsys.readouterr().err
    assert f"schema {NEXT} database, made by a newer LogShed" in err and "Nothing was changed." in err
    assert db.read_bytes() == before and _leftovers(db) == []
    assert not [p for p in db.parent.iterdir() if "before-restore" in p.name]


@pytest.mark.parametrize("damage", ["truncated", "not_gzip", "bad_page", "wrong_size"])
def test_a_damaged_archive_is_refused_and_the_database_kept(db, tmp_path, damage, capsys):
    _set(db, "marker", "current")
    archive = _backup(db)
    bad = tmp_path / "bad.tar.gz"
    if damage == "truncated":
        bad.write_bytes(archive.read_bytes()[: archive.stat().st_size // 2])
    elif damage == "not_gzip":
        bad.write_bytes(b"this is not an archive" * 100)
    elif damage == "bad_page":
        with tarfile.open(archive, "r:gz") as tar:
            data = bytearray(tar.extractfile("logs.db").read())
        page = 4096
        data[page * 3: page * 4] = b"\xff" * page
        _repack(archive, bad, db_bytes=bytes(data))
    else:
        with tarfile.open(archive, "r:gz") as tar:
            data = tar.extractfile("logs.db").read()
        _repack(archive, bad)
        with tarfile.open(bad, "r:gz") as tar:
            manifest = json.loads(tar.extractfile("manifest.json").read())
        manifest["files"][0]["size_bytes"] = len(data) + 1
        with tarfile.open(bad, "w:gz") as tar:
            for name, body in (("logs.db", data), ("manifest.json", json.dumps(manifest).encode())):
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
    before = db.read_bytes()

    assert cli.restore_database(str(bad), str(db)) == 1

    err = capsys.readouterr().err
    assert "Nothing was changed." in err
    expected = {
        "truncated": "not a readable backup archive",
        "not_gzip": "not a readable backup archive",
        "bad_page": "damaged",
        "wrong_size": "not the size its manifest records",
    }[damage]
    assert expected in err, err
    assert db.read_bytes() == before and _get(db, "marker") == "current"
    assert _leftovers(db) == []
    assert not [p for p in db.parent.iterdir() if "before-restore" in p.name]


@pytest.mark.parametrize(
    "case",
    ["path_member", "two_databases", "format_2", "no_manifest"],
)
def test_archives_that_are_not_logshed_backups_are_refused(db, tmp_path, case):
    archive = _backup(db)
    bad = tmp_path / "bad.tar.gz"
    if case == "path_member":
        _repack(archive, bad, extra=[("../escape.db", b"x")])
    elif case == "two_databases":
        _repack(archive, bad, extra=[("other.db", b"x")])
    elif case == "format_2":
        _repack(archive, bad, manifest_update={"format": 2})
    else:
        with tarfile.open(archive, "r:gz") as tar:
            data = tar.extractfile("logs.db").read()
        with tarfile.open(bad, "w:gz") as tar:
            info = tarfile.TarInfo("logs.db")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    before = db.read_bytes()
    with pytest.raises(restore_mod.RestoreError):
        restore_mod.restore_backup(bad, db, 3, say=lambda text: None)
    assert db.read_bytes() == before and _leftovers(db) == []
    assert not (tmp_path / "escape.db").exists() and not (db.parent.parent / "escape.db").exists()


def test_refused_while_logshed_holds_the_lock(db, capsys):
    archive = _backup(db)
    fd = instance_lock.acquire(db, exclusive=False)
    try:
        assert cli.restore_database(str(archive), str(db)) == 1
    finally:
        instance_lock.release(fd)
    assert "LogShed is running on" in capsys.readouterr().err
    assert not [p for p in db.parent.iterdir() if "before-restore" in p.name]


def test_refused_while_another_connection_writes(db, capsys):
    archive = _backup(db)
    with closing(sqlite3.connect(db, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert cli.restore_database(str(archive), str(db)) == 1
        conn.execute("ROLLBACK")
    assert "Another program is using" in capsys.readouterr().err


async def test_the_app_holds_the_lock_and_does_not_start_during_a_restore(db, monkeypatch, free_syslog_port):
    from unittest.mock import AsyncMock

    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("DATA_DIR", str(db.parent))
    monkeypatch.setenv("SECRET_KEY_PATH", str(db.parent / ".secret_key"))
    monkeypatch.setattr(main_mod, "_start_services", AsyncMock())
    monkeypatch.setattr(main_mod, "_stop_services", AsyncMock())
    lifecycle.reset()
    app = main_mod.create_app()

    async with main_mod.lifespan(app):
        with pytest.raises(instance_lock.InstanceLockBusy):
            instance_lock.acquire(db, exclusive=True)
    # Released at shutdown
    instance_lock.release(instance_lock.acquire(db, exclusive=True))

    fd = instance_lock.acquire(db, exclusive=True)
    try:
        with pytest.raises(instance_lock.InstanceLockBusy):
            async with main_mod.lifespan(app):
                pass
    finally:
        instance_lock.release(fd)


def test_restore_into_an_empty_folder(db, tmp_path, capsys):
    archive = _backup(db)
    target = tmp_path / "fresh" / "logs.db"
    target.parent.mkdir()
    assert cli.restore_database(str(archive), str(target)) == 0
    assert _schema(target) == LATEST
    assert "kept as" not in capsys.readouterr().out


def test_a_backup_without_logs_says_so(db, capsys):
    with closing(get_connection(db)) as conn:
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
            "VALUES ('2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00', '10.0.0.1', 'nas', 'app', 1, 6, 'm', 'r')"
        )
        conn.commit()
    archive = db_backup.create_backup(db, kind=f"pre-v{LATEST}-to-v{NEXT}", schema_version=LATEST, target_schema=NEXT,
                                      method=db_backup.METHOD_WITHOUT_LOGS).path
    assert cli.restore_database(str(archive), str(db)) == 0
    assert "made without the logs" in capsys.readouterr().out
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0


def test_too_little_space_is_refused_before_writing(db, monkeypatch, capsys):
    archive = _backup(db)
    monkeypatch.setattr(restore_mod.shutil, "disk_usage", lambda p: DiskUsage(10 * GiB, 10 * GiB, 1024))
    assert cli.restore_database(str(archive), str(db)) == 1
    assert "Not enough free space" in capsys.readouterr().err
    assert _leftovers(db) == []


def test_an_unknown_archive_name_is_refused(db, capsys):
    assert cli.restore_database("logshed-backup-manual-v3-20200101T000000Z.tar.gz", str(db)) == 1
    assert "There is no backup archive" in capsys.readouterr().err


def test_run_as_root_gives_the_database_to_its_owner(db, monkeypatch):
    archive = _backup(db)
    calls = []
    monkeypatch.setattr(db_backup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(db_backup, "_owner", lambda path: (1000, 1000))
    monkeypatch.setattr(db_backup.os, "chown", lambda path, uid, gid: calls.append((Path(path).name, uid, gid)))
    assert cli.restore_database(str(archive), str(db)) == 0
    assert [c for c in calls if c[0].startswith(".restore-")] and all(c[1:] == (1000, 1000) for c in calls)
    assert db.stat().st_mode & 0o777 == 0o640



def test_restore_removes_a_failed_upgrade_marker_and_a_stale_copy(db, capsys):
    archive = _backup(db)
    lifecycle.marker_path(db).write_text("{}")
    stale = db.with_name(".restore-20260101T000000Z.db.tmp")
    stale.write_bytes(b"x" * 1000)
    assert cli.restore_database(str(archive), str(db)) == 0
    out = capsys.readouterr().out
    assert not lifecycle.marker_path(db).exists() and "next start runs the upgrade again" in out
    assert not stale.exists() and "left by an interrupted restore" in out


def test_a_restore_ends_every_session(db):
    """The backup's older session epoch would bring back sessions ended after it was made."""
    _set(db, "session_epoch", "2")
    archive = _backup(db)
    _set(db, "session_epoch", "7")  # sign-outs after the backup

    assert cli.restore_database(archive.name, str(db)) == 0
    assert _get(db, "session_epoch") == "8"


def test_a_restore_ends_sessions_issued_under_the_backup_too(db):
    archive = _backup(db)  # no epoch stored: every session so far is at 0
    assert cli.restore_database(archive.name, str(db)) == 0
    assert _get(db, "session_epoch") == "1"


def test_an_unreadable_current_database_does_not_stop_the_restore(db, tmp_path):
    _set(db, "session_epoch", "3")
    archive = _backup(db)
    for suffix in ("-wal", "-shm"):
        db.with_name(db.name + suffix).unlink(missing_ok=True)
    db.write_bytes(b"this is not a database" * 200)

    assert cli.restore_database(archive.name, str(db)) == 0
    assert _get(db, "session_epoch") == "4"
