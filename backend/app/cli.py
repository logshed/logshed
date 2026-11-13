"""
CLI tool for LogShed admin operations.

Usage:
    python -m app.cli reset-admin --password <new_password>
    python -m app.cli backup [--db-path <path>]

Provides password recovery by UPSERTing the admin_auth row (id=1)
with a new Argon2id password hash.
"""

import argparse
import datetime
import getpass
import signal
import sqlite3
import sys

from app.core.migrations import (
    MIGRATIONS,
    DatabaseNewerThanAppError,
    MigrationBackupError,
    get_connection,
    get_user_version,
    run_migrations,
)
from app.core.security import hash_password

# Default database path inside the container
_DEFAULT_DB_PATH = "/data/logs.db"

# Same bounds as the web setup and password change forms
_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 128


def reset_admin(password: str, db_path: str = _DEFAULT_DB_PATH) -> None:
    """
    Reset the admin password by UPSERTing admin_auth row id=1.

    Works whether the admin_auth table is empty (first-time) or
    already has a password set (recovery).

    Args:
        password: The new plaintext password to hash and store.
        db_path: Path to the SQLite database.
    """
    # A new database gets its schema. An existing one is left at its schema: admin_auth exists from
    # schema 1 on, and running an upgrade here would bypass recovery mode (a second process backing
    # up and migrating while LogShed waits, or a failed upgrade blocking the password reset).
    conn = get_connection(db_path)
    try:
        version = get_user_version(conn)
    finally:
        conn.close()
    latest = MIGRATIONS[-1][0]
    if version > latest:
        raise DatabaseNewerThanAppError(version, latest, db_path)
    if version == 0:
        run_migrations(db_path)

    password_hash = hash_password(password)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn = get_connection(db_path)
    try:
        # UPSERT: insert if no row exists, update if id=1 already present
        conn.execute(
            """
            INSERT INTO admin_auth (id, password_hash, created_at, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                password_hash = excluded.password_hash,
                updated_at = excluded.updated_at
            """,
            (password_hash, now, now),
        )
        # Every session signed in with the old password ends (a running LogShed sees it within seconds)
        from app.api.deps import bump_session_epoch
        bump_session_epoch(conn)
        conn.commit()
        print("Admin password has been reset successfully. Every browser must sign in again.")
    except Exception as e:
        conn.rollback()
        print(f"Error resetting admin password: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def seed_logs(
    count: int = 500,
    days: int = 45,
    db_path: str = _DEFAULT_DB_PATH,
) -> int:
    """
    Seed dummy log entries distributed across the past `days` days.
    Useful for testing retention pruning, search, and storage metrics.

    Args:
        count: Number of dummy logs to generate.
        days: Number of days in the past to distribute logs across.
        db_path: Path to the SQLite database.

    Returns:
        Number of logs inserted.
    """
    run_migrations(db_path)

    sample_sources = [
        ("192.168.1.1", "router"),
        ("192.168.1.10", "pve-node1"),
        ("192.168.1.20", "truenas"),
        ("192.168.1.50", "k3s-master"),
        ("192.168.1.105", "ha-os"),
    ]

    sample_apps = [
        ("kernel", 3, "BTRFS error (device sdb1): parent transid verify failed"),
        ("systemd", 6, "Started Periodic Background Maintenance Service."),
        ("sshd", 4, "Failed password for invalid user admin from 10.0.0.99 port 44122"),
        ("nginx", 6, "GET /api/v1/health HTTP/1.1 200 45ms - Mozilla/5.0"),
        ("dockerd", 6, "Container 8f9b2c3d started successfully"),
        ("zfs", 3, "ZFS pool 'tank' reported 1 checksum error during scrub"),
        ("cron", 6, "CRON[14221]: (root) CMD (/usr/local/bin/backup.sh > /dev/null)"),
    ]

    now = datetime.datetime.now(datetime.timezone.utc)
    entries = []

    for i in range(count):
        offset_seconds = (days * 86400) * (1.0 - (i / max(count - 1, 1)))
        ts = now - datetime.timedelta(seconds=offset_seconds)
        ts_iso = ts.isoformat()

        ip, alias = sample_sources[i % len(sample_sources)]
        app, severity, base_msg = sample_apps[i % len(sample_apps)]
        message = f"[{i+1}/{count}] {base_msg}"
        raw = f"<{severity + 8}>1 {ts_iso} {alias} {app} - - - {message}"

        entries.append((
            ts_iso,
            ts_iso,
            ip,
            alias,
            app,
            1,  # facility
            severity,
            message,
            raw,
        ))

    conn = get_connection(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO logs (
                timestamp, received_at, source_ip, source_alias,
                app_name, facility, severity, message, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            entries,
        )
        conn.commit()
        print(f"Successfully seeded {len(entries)} logs across the last {days} days into {db_path}.")
        return len(entries)
    except Exception as e:
        conn.rollback()
        print(f"Error seeding logs: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


def backup_database(db_path: str | None = None) -> int:
    """
    Make a manual backup of a database that may be in use. It copies with VACUUM INTO, a read
    snapshot, so the running app keeps writing; a prune or Compact at the same time only costs
    disk space. Prints the archive's path, size and expiry date. Returns the exit status.
    """
    from contextlib import closing
    from pathlib import Path

    from app.core import backup as db_backup
    from app.core.config import get_db_path

    path = Path(db_path) if db_path else get_db_path()
    if not path.is_file():
        print(f"Error: there is no database at {path}.", file=sys.stderr)
        return 1
    with closing(get_connection(path)) as conn:
        schema = get_user_version(conn)
    try:
        result = db_backup.create_backup(
            path, kind=f"manual-v{schema}", schema_version=schema, method=db_backup.METHOD_VACUUM_INTO
        )
    except db_backup.BackupSpaceError as e:
        print(
            f"Error: {e} Stop the container and copy the data folder instead.",
            file=sys.stderr,
        )
        return 1
    except (db_backup.BackupError, OSError, sqlite3.Error) as e:
        print(f"Error: the backup failed: {e}", file=sys.stderr)
        return 1
    expires = db_backup.archive_created_at(result.path) + datetime.timedelta(days=db_backup.BACKUP_EXPIRY_DAYS)
    print(f"Backup written: {result.path}")
    print(f"Size: {db_backup.format_bytes(result.size_bytes)}")
    print(f"Deleted automatically after {expires.strftime('%Y-%m-%d %H:%M')} UTC.")
    return 0


def restore_database(archive: str, db_path: str | None = None) -> int:
    """
    Put a backup archive in place of the database, for a stopped LogShed. The current database is
    moved aside and kept. Prints what it does; returns the exit status.
    """
    from pathlib import Path

    from app.core import backup as db_backup
    from app.core import restore
    from app.core.config import get_db_path
    from app.core.migrations import MIGRATIONS

    path = Path(db_path) if db_path else get_db_path()
    latest = MIGRATIONS[-1][0]

    def _stop(signum, frame):
        # SystemExit runs the restore's clean-up; the default SIGTERM action would leave its copy behind
        raise SystemExit(f"Error: the restore was stopped (signal {signum}). Nothing was changed.")

    previous = signal.signal(signal.SIGTERM, _stop)
    try:
        source = restore.resolve_archive(archive, path)
        result = restore.restore_backup(source, path, latest, say=lambda text: print(text, flush=True))
    except restore.RestoreError as e:
        print(f"Error: {e} Nothing was changed.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(f"Restored {result.archive.name} into {path}.")
    print(f"It is a schema {result.schema_version} database, backed up by LogShed {result.app_version}.")
    if result.omitted_tables:
        print(
            "This backup was made without the logs (before an upgrade that does not change them): it brings back "
            "the settings, rules and history, and holds no logs."
        )
    if result.aside is not None:
        print(
            f"The database it replaced is kept as {result.aside} ({db_backup.format_bytes(result.aside_bytes)}). "
            "Delete it once LogShed runs as you expect."
        )
    if result.marker_removed:
        print("The failed upgrade's marker (.migration_failed) was removed: the next start runs the upgrade again.")
    if result.schema_version < latest:
        print(
            f"This image upgrades it to schema {latest} when it starts (backing it up first). To run it as it is, "
            "start the LogShed version it came from instead."
        )
    print("Start LogShed now. Keep .secret_key as it is: stored passwords and keys are read with it.")
    return 0


def run_mcp_stdio(db_path: str | None = None) -> None:
    """
    Run built-in MCP server over stdio transport.

    All database access resolves its path through DB_PATH (see app.core.config.get_db_path),
    so --db-path is applied by setting it before anything reads settings or opens the database.
    Without the flag, DB_PATH / DATA_DIR from the environment are used, like the web process.
    """
    import os

    from app.core.config import get_cached_setting, invalidate_settings_cache

    if db_path:
        os.environ["DB_PATH"] = db_path
        invalidate_settings_cache()

    if not get_cached_setting("enable_mcp", False):
        print("Error: Model Context Protocol (MCP) is disabled in system settings.", file=sys.stderr)
        sys.exit(1)

    from app.mcp.server import create_mcp_server
    server = create_mcp_server(stdio=True)
    server.run(transport="stdio")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="app.cli",
        description="LogShed CLI administration tool",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # mcp subcommand
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run built-in MCP server over stdio transport",
    )
    mcp_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database (default: DB_PATH, or logs.db in DATA_DIR, as for the web process)",
    )

    # reset-admin subcommand
    reset_parser = subparsers.add_parser(
        "reset-admin",
        help="Reset the admin password",
    )
    reset_parser.add_argument(
        "--password",
        required=False,
        default=None,
        help="New admin password (prompted if omitted)",
    )
    reset_parser.add_argument(
        "--db-path",
        default=_DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {_DEFAULT_DB_PATH})",
    )

    # seed-logs subcommand
    seed_parser = subparsers.add_parser(
        "seed-logs",
        help="Seed dummy logs distributed across a range of days",
    )
    seed_parser.add_argument(
        "--count",
        type=int,
        default=500,
        help="Number of dummy logs to generate (default: 500)",
    )
    seed_parser.add_argument(
        "--days",
        type=int,
        default=45,
        help="Number of past days to span timestamps across (default: 45)",
    )
    seed_parser.add_argument(
        "--db-path",
        default=_DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {_DEFAULT_DB_PATH})",
    )

    # backup subcommand
    backup_parser = subparsers.add_parser(
        "backup",
        help="Back up the database into the backups folder beside it, while LogShed runs",
    )
    backup_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database (default: DB_PATH, or logs.db in DATA_DIR, as for the web process)",
    )

    # restore subcommand
    restore_parser = subparsers.add_parser(
        "restore",
        help="Put a backup archive in place of the database, with LogShed stopped (the current one is kept aside)",
    )
    restore_parser.add_argument(
        "archive",
        help="The archive: a path, or the name of one in the backups folder beside the database",
    )
    restore_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database (default: DB_PATH, or logs.db in DATA_DIR, as for the web process)",
    )

    args = parser.parse_args()

    if args.command == "mcp":
        run_mcp_stdio(args.db_path)
    elif args.command == "backup":
        sys.exit(backup_database(args.db_path))
    elif args.command == "restore":
        sys.exit(restore_database(args.archive, args.db_path))
    elif args.command == "reset-admin":
        password = args.password
        if password:
            print(
                "Warning: a password given with --password is visible in shell history and "
                "process listings. Omit --password to be prompted instead.",
                file=sys.stderr,
            )
        else:
            password = getpass.getpass("Enter new admin password: ")
            confirm = getpass.getpass("Confirm new admin password: ")
            if password != confirm:
                print("Error: Passwords do not match.", file=sys.stderr)
                sys.exit(1)
        if not (_MIN_PASSWORD_LENGTH <= len(password) <= _MAX_PASSWORD_LENGTH):
            print(
                f"Error: Password must be {_MIN_PASSWORD_LENGTH} to {_MAX_PASSWORD_LENGTH} characters.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            reset_admin(password, args.db_path)
        except (DatabaseNewerThanAppError, MigrationBackupError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "seed-logs":
        try:
            seed_logs(args.count, args.days, args.db_path)
        except (DatabaseNewerThanAppError, MigrationBackupError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
