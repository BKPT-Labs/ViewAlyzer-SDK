import sqlite3

import pytest

from viewalyzer_sdk import Recording, ViewAlyzerError


def test_summary_and_total_events(va, vadb):
    rec = va.open(vadb)
    s = rec.summary()
    assert s["total_events"] == 1234
    assert s["cpu_load_percent"] == pytest.approx(33.9)
    assert rec.total_events == 1234
    assert rec.is_clean


def test_sequence_loss_properties(va, vadb, tmp_path):
    rec = va.open(vadb)
    assert rec.has_sequence_info
    assert rec.lost_events == 0
    assert rec.seq_gaps == 0

    from fake_viewalyzer import make_vadb

    lossy = tmp_path / "lossy.vadb"
    make_vadb(lossy, seq_present=True, lost_events=37, seq_gaps=2)
    rec = va.open(lossy)
    assert rec.has_sequence_info
    assert rec.lost_events == 37
    assert rec.seq_gaps == 2
    assert not rec.is_clean  # loss makes a capture unclean even with 0 corrupt bytes

    legacy = tmp_path / "legacy.vadb"
    make_vadb(legacy, seq_present=False)
    rec = va.open(legacy)
    assert not rec.has_sequence_info
    assert rec.lost_events == 0  # unknown, not proven zero
    assert rec.is_clean  # pre-v3 recorders keep the old corrupt-bytes-only meaning


def test_is_clean_call_form_still_works_but_warns(va, vadb):
    rec = va.open(vadb)
    with pytest.warns(DeprecationWarning, match="drop the parentheses"):
        assert rec.is_clean() is True  # pre-1.0.1 method form


def test_recording_path_as_first_arg_is_rejected_helpfully(vadb):
    with pytest.raises(ViewAlyzerError) as e:
        Recording(str(vadb))
    assert e.value.code == "bad_arguments"
    assert "ViewAlyzer().open" in str(e.value)


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


def test_direct_readers_release_the_file(va, vadb):
    # A sqlite3 connection's context manager only commits; the readers
    # must close it, or Windows keeps the file locked and the CLI cannot
    # overwrite the same .vadb afterwards.
    rec = va.open(vadb)
    rec.summary()
    rec.meta()
    rec.task_stats()
    assert rec.total_events == 1234
    vadb.unlink()  # raises PermissionError on Windows if a handle lingers
    assert not vadb.exists()


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


# ----- the CLI's whole-recording report, verdicts, hardware-trace queries ---


def test_report_is_the_cli_summary_query_and_summary_stays_sqlite(va, vadb):
    rec = va.open(vadb)
    report = rec.report()
    assert report["query"] == "summary"
    assert report["data"]["events"] == 1234
    # summary() still reads va_summary straight from the file (typed values).
    assert rec.summary()["total_events"] == 1234
    assert "query" not in rec.summary()


def test_verdicts(va):
    rec = Recording(va, recording_id="f76593b93473")
    v = rec.verdicts()
    assert v["query"] == "verdicts" and v["count"] == 1
    assert v["verdicts"][0]["severity"] == "warn"
    assert v["verdicts"][0]["evidence"][0]["channel"] == "vdd"


def test_profile(va, tmp_path):
    rec = Recording(va, recording_id="f76593b93473")
    p = rec.profile()
    assert p["query"] == "profile" and p["elf"] is None
    assert p["data"]["source"] == "dwt-swo"
    assert "symbol" not in p["data"]["hotspots"][0]
    elf = tmp_path / "fw.elf"
    elf.write_bytes(b"\x7fELF")
    p = rec.profile(elf=elf, budget="low")
    assert p["elf"] == str(elf) and p["budget"] == "low"
    assert p["data"]["hotspots"][0]["symbol"] == "main"
    with pytest.raises(ViewAlyzerError) as e:
        rec.profile(elf=tmp_path / "ghost.elf")
    assert e.value.code == "file_not_found"


def test_itm_console_all_ports(va):
    rec = Recording(va, recording_id="f76593b93473")
    c = rec.itm_console()
    assert c["query"] == "itm-console" and c["port_flag"] is None
    assert [p["port"] for p in c["data"]["ports"]] == [0, 1]
    assert c["data"]["ports"][1]["lines"][1]["partial"] is True


def test_itm_console_port_filter_is_sent_and_applied(va):
    rec = Recording(va, recording_id="f76593b93473")
    c = rec.itm_console(port=1)
    assert c["port_flag"] == "1"  # sent as --port for newer builds...
    # ...and filtered here for builds that ignore the flag (the fake does).
    assert [p["port"] for p in c["data"]["ports"]] == [1]
    assert c["data"]["ports"][0]["bytes"] == 8
    assert rec.itm_console(port=7)["data"]["ports"] == []


def test_dwt_queries(va, tmp_path):
    rec = Recording(va, recording_id="f76593b93473")
    d = rec.dwt_data()
    assert d["query"] == "dwt-data" and d["elf"] is None
    assert d["data"]["watches"][0]["name"] == "g_temp"
    assert d["data"]["watches"][0]["first"][1]["pc"] == 134218240
    elf = tmp_path / "fw.elf"
    elf.write_bytes(b"\x7fELF")
    assert rec.dwt_data(elf=elf)["elf"] == str(elf)

    x = rec.dwt_exc()
    assert x["query"] == "dwt-exc"
    assert x["data"]["exceptions"][0]["name"] == "SysTick"
    assert x["data"]["events"][0]["func"] == 1

    c = rec.dwt_counters()
    assert c["query"] == "dwt-counters"
    assert c["data"]["counters"]["cpi"]["wraps"] == 10
    assert c["data"]["cpu_load_pct"] == 70.0


def test_swo_load(va):
    rec = Recording(va, recording_id="f76593b93473")
    s = rec.swo_load()
    assert s["query"] == "swo-load"
    assert s["data"]["share_pct"] == 50.0 and s["data"]["overflows"] == 0


def test_hardware_trace_queries_raise_no_hw_trace(va):
    from fake_viewalyzer import NO_HW_RECORDING

    rec = Recording(va, recording_id=NO_HW_RECORDING)
    for call in (rec.profile, rec.itm_console, rec.dwt_data, rec.dwt_exc,
                 rec.dwt_counters, rec.swo_load):
        with pytest.raises(ViewAlyzerError) as e:
            call()
        assert e.value.code == "no_hw_trace"
