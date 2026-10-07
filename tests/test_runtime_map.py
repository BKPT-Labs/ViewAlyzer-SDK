# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
import hashlib
import os
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from viewalyzer_sdk import Recording, RuntimeMap, ViewAlyzer, ViewAlyzerError


def mock_runner(va):
    def respond(args, timeout):
        directory = Path(args[args.index("--cache-dir") + 1]) if "--cache-dir" in args else Path("unused")
        return {"schema_version": 2, "cache": {"path": str(directory / "map.sqlite")}, "summary": {"activities": 2}}
    va._runner.run_json = Mock(side_effect=respond)
    return va._runner.run_json


def test_map_arguments_and_recording_handle(va, tmp_path):
    runner = mock_runner(va)
    recording = Recording(va, path=tmp_path / "run with spaces.vadb")
    graph = recording.runtime_map(cache_dir=tmp_path, timeout_s=123)
    assert isinstance(graph, RuntimeMap)
    graph.activity(node="actor:2", tags=["contended", "failed"], t_start_us=0, t_end_us=123, limit=20, offset=40)
    args, timeout = runner.call_args.args
    assert args == ["map", "activity", "--recording", recording.ref, "--cache-dir", str(tmp_path), "--node", "actor:2", "--t-start-us", "0", "--t-end-us", "123", "--limit", "20", "--offset", "40", "--tags", "contended,failed"]
    assert timeout == 123
    graph.nodes(name="Motor's mutex Ω", kind="mutex")
    assert runner.call_args.args[0][-4:] == ["--name", "Motor's mutex Ω", "--kind", "mutex"]
    graph.sql("SELECT count(*) FROM map_edges", limit=1)
    assert runner.call_args.args[0][-4:] == ["--limit", "1", "--sql", "SELECT count(*) FROM map_edges"]


def test_temporary_map_cleans_up_even_after_error_and_closes_handle(va):
    mock_runner(va)
    with pytest.raises(RuntimeError, match="job failed"):
        with va.temporary_map("run.vadb") as graph:
            directory = graph.path.parent
            graph.path.write_bytes(b"temporary cache")
            assert directory.is_dir()
            raise RuntimeError("job failed")
    assert not directory.exists()
    with pytest.raises(ViewAlyzerError) as err:
        graph.summary()
    assert err.value.code == "map_closed"


def test_direct_connection_is_read_only_handles_uri_characters_and_closes(va, tmp_path):
    mock_runner(va)
    directory = tmp_path / "cache #1 & Ω"
    directory.mkdir()
    path = directory / "map.sqlite"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE map_nodes(id TEXT)")
        writer.execute("INSERT INTO map_nodes VALUES ('actor:1')")
    writer.close()
    graph = va.runtime_map("run.vadb", cache_dir=directory)
    with graph.connect() as db:
        assert db.execute("SELECT id FROM map_nodes").fetchone() == ("actor:1",)
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM map_nodes")
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    path.unlink()  # Windows would refuse this if a connection leaked.


def test_map_cli_errors_propagate(va):
    va._runner.run_json = Mock(side_effect=ViewAlyzerError("recording_busy", "Close the writer"))
    with pytest.raises(ViewAlyzerError) as err:
        va.runtime_map("run.vadb")
    assert err.value.code == "recording_busy"


@pytest.mark.skipif(not os.environ.get("VIEWALYZER_MAP_TEST_CLI") or not os.environ.get("VIEWALYZER_MAP_TEST_RECORDING"), reason="Optional offline native CLI + saved recording")
def test_native_recording_cache_filters_pagination_and_sql(tmp_path):
    va = ViewAlyzer(os.environ["VIEWALYZER_MAP_TEST_CLI"])
    source = Path(os.environ["VIEWALYZER_MAP_TEST_RECORDING"])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with va.temporary_map(source, timeout_s=120) as graph:
        cache_path = graph.path
        first = graph.info
        assert first["cache"]["reused"] is False
        assert first["summary"]["activities"] > 0
        assert graph.summary()["cache"]["reused"] is True
        with graph.connect() as db:
            total = db.execute("SELECT count(*) FROM map_activity").fetchone()[0]
            assert total == first["summary"]["activities"]
            expected = db.execute("SELECT a.id FROM map_activity a JOIN map_edges e ON e.id=a.edge_id WHERE e.tag IN ('contended','failed') ORDER BY a.time_ns,a.id").fetchall()
            offset = 0
            ids = []
            while True:
                page = graph.activity(tags=["contended", "failed"], limit=37, offset=offset)
                ids.extend((row["id"],) for row in page["data"])
                assert all(row["tag"] in ("contended", "failed") for row in page["data"])
                if not page["truncated"]:
                    break
                offset = page["next_offset"]
            assert ids == expected
            assert graph.sql("SELECT count(*) FROM map_activity")["rows"] == [[total]]
            with pytest.raises(sqlite3.OperationalError):
                db.execute("DELETE FROM map_nodes")
        with pytest.raises(ViewAlyzerError) as err:
            graph.sql("WITH x AS (SELECT 1) DELETE FROM map_nodes")
        assert err.value.code == "bad_sql"
    assert not cache_path.parent.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
