import sys
from pathlib import Path

import pytest

from viewalyzer_cli import ViewAlyzer

FAKE_CLI = Path(__file__).parent / "fake_viewalyzer.py"


@pytest.fixture()
def va() -> ViewAlyzer:
    """Client wired to the fake CLI via the argv-prefix binary form."""
    return ViewAlyzer([sys.executable, str(FAKE_CLI)])


@pytest.fixture()
def vadb(tmp_path) -> Path:
    """A real .vadb (SQLite) file with the documented overview tables."""
    sys.path.insert(0, str(FAKE_CLI.parent))
    try:
        from fake_viewalyzer import make_vadb
    finally:
        sys.path.pop(0)
    path = tmp_path / "run1.vadb"
    make_vadb(path, total_events=1234)
    return path
