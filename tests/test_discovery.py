import sys
from pathlib import Path

import pytest

from viewalyzer_sdk import BinaryNotFound, ENV_VAR, find_viewalyzer
from viewalyzer_sdk.discovery import find_viewalyzer_with_source


def _make_exe(tmp_path: Path, name: str) -> Path:
    exe = tmp_path / (name + (".exe" if sys.platform == "win32" else ""))
    exe.write_bytes(b"#!fake\n")
    exe.chmod(0o755)
    return exe


def test_env_var_wins(tmp_path):
    exe = _make_exe(tmp_path, "ViewAlyzer")
    path, source = find_viewalyzer_with_source({ENV_VAR: str(exe)})
    assert path == exe and source == "env"


def test_env_var_pointing_nowhere_raises(tmp_path):
    with pytest.raises(BinaryNotFound) as e:
        find_viewalyzer({ENV_VAR: str(tmp_path / "missing")})
    assert ENV_VAR in str(e.value)


def test_path_lookup(tmp_path):
    _make_exe(tmp_path, "ViewAlyzer")
    path, source = find_viewalyzer_with_source({"PATH": str(tmp_path)})
    assert path is not None and path.parent == tmp_path
    assert source == "path"


def test_not_found_returns_none(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    # Env limited to a bare PATH: no env var, no hits, no install dirs vars.
    path, source = find_viewalyzer_with_source({"PATH": str(empty)})
    if source == "install":  # a real install on this machine - fine too
        assert path is not None
    else:
        assert path is None and source == "not found"
