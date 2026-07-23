import sqlite3

import pytest

from viewalyzer_cli import Recording, ViewAlyzerError


def test_summary_and_total_events(va, vadb):
    rec = va.open(vadb)
    s = rec.summary()
    assert s["total_events"] == 1234
    assert s["cpu_load_percent"] == pytest.approx(33.9)
    assert rec.total_events == 1234
    assert rec.is_clean()


def test_meta_typed(va, vadb):
    meta = va.open(vadb).meta()
    assert meta["va_cpu_hz"] == 170_000_000
    assert meta["va_os"] == "Zephyr"


def test_task_stats_filters_synthetic_lanes(va, vadb):
    rec = va.open(vadb)
    names = [r["name"] for r in rec.task_stats()]
    assert names == ["idle", "sensor_tid"]  # cpu_percent DESC
    all_names = [r["name"] for r in rec.task_stats(include_synthetic=True)]
    assert "_RTOS_" in all_names and "ISR:SysTick" in all_names


def test_connect_is_read_only(va, vadb):
    con = va.open(vadb).connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO meta VALUES ('x', 'y')")
    finally:
        con.close()


def test_direct_reads_need_a_path(va):
    rec = Recording(va, recording_id="f76593b93473")
    with pytest.raises(ViewAlyzerError) as e:
        rec.summary()
    assert e.value.code == "bad_arguments"


def test_missing_file_is_reported(va, tmp_path):
    rec = Recording(va, path=tmp_path / "ghost.vadb")
    with pytest.raises(ViewAlyzerError) as e:
        rec.summary()
    assert e.value.code == "file_not_found"


def test_path_with_spaces_connects(va, tmp_path, vadb):
    spaced = tmp_path / "dir with spaces" / "run copy.vadb"
    spaced.parent.mkdir()
    spaced.write_bytes(vadb.read_bytes())
    assert va.open(spaced).total_events == 1234
