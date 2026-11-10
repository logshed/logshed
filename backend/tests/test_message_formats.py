"""The log line formats the Docker collector knows, checked against the cases the Console's tidying also runs.

frontend/src/test/Formatters.test.ts reads the same file, so a format added for the collectors must
also be shown properly by the web interface.
"""
import json
from pathlib import Path

import pytest

from app.collectors.docker_collector import _parse_docker_message_content
from app.core.pipeline import clean_log_text

CASES = json.loads((Path(__file__).parent / "fixtures" / "message_formats.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_docker_collector_stores_the_shared_message(case):
    # The order the collector uses: pick the message out of the line, then strip escapes and controls
    message, _severity = _parse_docker_message_content(case["line"], 6)
    assert clean_log_text(message) == case["stored"]


def test_shared_cases_cover_every_format_the_collector_parses():
    names = " ".join(c["name"] for c in CASES)
    for fmt in ("logfmt", "Maintainerr", "Valkey", "ANSI"):
        assert fmt in names
