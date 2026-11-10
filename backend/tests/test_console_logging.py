"""
LogShed's own messages on the console (stderr), which is what `docker logs` shows: one handler on
logger 'app', with the time in the server's time zone, the level and the logger name.
"""
import datetime
import importlib
import logging
import re

import app.main
from app.main import create_app

CONSOLE_LINE = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} {level:<8} {name}: {message}$"


def _console_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger("app").handlers if isinstance(h, logging.StreamHandler)]


def _console_pattern(level: str, name: str, message: str) -> re.Pattern:
    return re.compile(
        CONSOLE_LINE.replace("{level:<8}", f"{level:<8}").replace("{name}", re.escape(name)).replace(
            "{message}", re.escape(message)
        ),
        re.M,
    )


def test_console_handler_is_added_once():
    create_app.uncached()
    create_app.uncached()
    importlib.import_module("app.main")
    assert len(_console_handlers()) == 1


def test_info_reaches_stderr_with_time_level_and_logger_name(capfd):
    capfd.readouterr()
    logging.getLogger("app.core.migrations").info("Migrating from 2 to 3")
    err = capfd.readouterr().err
    assert _console_pattern("INFO", "app.core.migrations", "Migrating from 2 to 3").search(err), err


def test_warning_is_printed_once_with_its_level(capfd):
    capfd.readouterr()
    logging.getLogger("app.collectors.syslog").warning("UDP receive buffer is smaller than asked")
    err = capfd.readouterr().err
    assert err.count("UDP receive buffer is smaller than asked") == 1, err
    assert _console_pattern("WARNING", "app.collectors.syslog", "UDP receive buffer is smaller than asked").search(err), err


def test_debug_is_not_printed(capfd):
    app_logger = logging.getLogger("app")
    previous = app_logger.level
    app_logger.setLevel(logging.DEBUG)
    try:
        capfd.readouterr()
        logging.getLogger("app.core.pipeline").debug("one debug line")
        assert "one debug line" not in capfd.readouterr().err
    finally:
        app_logger.setLevel(previous)


def test_console_time_is_in_the_server_time_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14, so 11:00 UTC is already the next day
    handlers = _console_handlers()
    assert handlers
    record = logging.getLogger("app.core.migrations").makeRecord(
        "app.core.migrations", logging.INFO, __file__, 1, "Successfully migrated", None, None
    )
    record.created = datetime.datetime(2026, 10, 13, 11, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
    assert handlers[0].format(record) == "2026-10-14 01:00:00 INFO     app.core.migrations: Successfully migrated"


def test_internal_log_level_keeps_info_on_the_console(capfd):
    """The stored level (WARNING by default, or DISABLED) does not hide INFO from the console."""
    try:
        for level in ("WARNING", "ERROR", "DISABLED"):
            app.main.configure_internal_log_handler(level)
            capfd.readouterr()
            logging.getLogger("app.main").info(f"Started workers at {level}")
            assert f"Started workers at {level}" in capfd.readouterr().err
    finally:
        app.main.configure_internal_log_handler(None)
