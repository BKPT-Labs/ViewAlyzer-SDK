import json

import pytest

from viewalyzer_sdk import Recording, SCHEMA_VERSION, ViewAlyzer, ViewAlyzerError


def test_version_handshake(va):
    info = va.version()
    assert info["app"] == "ViewAlyzer"
    assert info["schema_version"] == SCHEMA_VERSION


def test_doctor(va):
    report = va.doctor()
    checks = {c["id"]: c for c in report["checks"]}
    assert checks["stlink_probes"]["status"] == "ok"
    assert checks["jlink_probes"]["hint"]


def test_list_recordings_and_handles(va):
    recs = va.recordings()
    assert len(recs) == 1
    assert recs[0].recording_id == "f76593b93473"
    assert recs[0].info["schema_name"] == "Zephyr"
    assert recs[0].ref == "f76593b93473"


def test_record_with_config_file(va, tmp_path):
    cfg = tmp_path / "board.vacf"
    cfg.write_text(json.dumps({"transport": "udp", "udp-port": 5005}))
    rec = va.record(cfg, output=tmp_path / "run1.vadb", duration_s=1)
    assert rec.recording_id == "abcdef123456"
    assert rec.path is not None and rec.path.is_file()
    assert rec.total_events == 1234


def test_record_with_inline_config_dict(va, tmp_path):
    rec = va.record(
        {"transport": "udp", "udp-port": 5005},
        output=tmp_path / "run2.vadb",
        duration_s=1,
    )
    assert rec.total_events == 1234


def test_record_forces_vadb_extension(va, tmp_path):
    rec = va.record(
        {"transport": "udp"}, output=tmp_path / "run3.db", duration_s=1
    )
    # The CLI's "Recording saved:" line, not our --output, names the file.
    assert rec.path.suffix == ".vadb"
    assert rec.path.is_file()


def test_record_with_symbol_watch(va, tmp_path):
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF")
    rec = va.record(
        {"transport": "stlink-swo"},
        output=tmp_path / "watch.vadb",
        duration_s=1,
        elf=elf,
        symbols=["tick_counter:u32", "adc_value"],
        poll_hz=500,
    )
    assert rec.total_events == 1234


def test_record_symbols_require_elf(va, tmp_path):
    with pytest.raises(ViewAlyzerError) as e:
        va.record(
            {"transport": "stlink-swo"},
            output=tmp_path / "x.vadb",
            duration_s=1,
            symbols=["tick_counter"],
        )
    assert e.value.code == "bad_arguments"


def test_record_failure_surfaces_error_lines(va, tmp_path):
    with pytest.raises(ViewAlyzerError) as e:
        va.record({"transport": "fail"}, output=tmp_path / "x.vadb", duration_s=1)
    assert e.value.code == "record_failed"
    assert "Failed to connect" in e.value.message


def test_record_cooldown_surfaces_envelope_code(va, tmp_path):
    with pytest.raises(ViewAlyzerError) as e:
        va.record(
            {"transport": "cooldown"}, output=tmp_path / "x.vadb", duration_s=1
        )
    assert e.value.code == "cooldown_active"


def test_record_accepts_single_symbol_string(va, tmp_path):
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF")
    rec = va.record(
        {"transport": "stlink-swo"},
        output=tmp_path / "one.vadb",
        duration_s=1,
        elf=elf,
        symbols="tick_counter:u32",
    )
    assert rec.total_events == 1234


def test_fingerprint_accepts_bare_strings(va, tmp_path):
    rec = Recording(va, recording_id="f76593b93473")
    fp = rec.fingerprint(sections="summary")
    assert fp["sections"] == ["summary"]


def test_snapshot(va, tmp_path):
    rec = va.snapshot(
        {"transport": "stlink-rambuf"}, output=tmp_path / "post.vadb"
    )
    assert rec.recording_id == "5aa9d4114f00"
    assert rec.info["summary"]["ring"] == "post-mortem"
    assert rec.total_events == 777


def test_snapshot_rejects_non_rambuf_transport(va, tmp_path):
    with pytest.raises(ViewAlyzerError) as e:
        va.snapshot({"transport": "stlink-swo"}, output=tmp_path / "x.vadb")
    assert e.value.code == "bad_arguments"


def test_snapshot_empty_ring_is_an_error(va, tmp_path):
    with pytest.raises(ViewAlyzerError) as e:
        va.snapshot(
            {"transport": "stlink-rambuf", "rambuf-address": "0xEMPTY"},
            output=tmp_path / "x.vadb",
        )
    assert e.value.code == "empty_snapshot"


def test_query_error_envelope(va):
    with pytest.raises(ViewAlyzerError) as e:
        va.query("sql", "f76593b93473", sql="SELECT * FROM boom")
    assert e.value.code == "bad_sql"


def test_query_rejects_unknown_verb(va):
    with pytest.raises(ViewAlyzerError) as e:
        va.query("nope", "f76593b93473")
    assert e.value.code == "bad_arguments"


def test_recording_query_helpers(va):
    rec = Recording(va, recording_id="f76593b93473")
    assert rec.timeline()["query"] == "timeline"
    assert rec.events(tier="summary")["tier"] == "summary"
    assert rec.inversions()["inversions"] == []
    rows = rec.sql_rows("SELECT name, cpu FROM va_task_stats")
    assert rows[0] == {"name": "idle", "cpu": 66.1}


def test_new_query_helpers(va, tmp_path):
    rec = Recording(va, recording_id="f76593b93473")
    assert rec.cpu()["query"] == "cpu"
    assert rec.comms(bucket_us=100_000)["query"] == "comms"
    assert rec.etm(tier="raw")["tier"] == "raw"
    elf = tmp_path / "fw.elf"
    elf.write_bytes(b"\x7fELF")
    assert rec.timers(elf=elf)["query"] == "timers"


def test_series_kinds(va):
    rec = Recording(va, recording_id="f76593b93473")
    assert rec.series("cpu-load")["kind"] == "cpu-load"
    assert rec.series("task-timing", task="control_tid", metric="exec")["kind"] == \
        "task-timing"
    assert rec.series("interval", from_="task:producer", to="task:consumer")[
        "points"
    ]
    with pytest.raises(ViewAlyzerError):
        rec.series("task-timing")  # --task missing


def test_fingerprint_and_compare(va, tmp_path):
    rec = Recording(va, recording_id="f76593b93473")
    out = tmp_path / "golden.vafp.json"
    fp = rec.fingerprint(sections=["summary", "tasks"], tolerance_pct=20, out=out)
    assert fp["sections"] == ["summary", "tasks"]
    assert out.is_file()

    verdict = rec.compare(out)
    assert verdict["verdict"] == "pass"

    failing = tmp_path / "failing.vafp.json"
    failing.write_text("{}")
    # A fail verdict is data (exit code 2 + payload), not an exception.
    assert rec.compare(failing)["verdict"] == "fail"


def test_compare_requires_existing_baseline(va, tmp_path):
    rec = Recording(va, recording_id="f76593b93473")
    with pytest.raises(ViewAlyzerError) as e:
        rec.compare(tmp_path / "ghost.vafp.json")
    assert e.value.code == "file_not_found"


def test_analyze_memory_passes_map(va, tmp_path):
    elf = tmp_path / "fw.elf"
    elf.write_bytes(b"\x7fELF")
    mp = tmp_path / "fw.map"
    mp.write_text("Memory Configuration\n")
    mem = va.analyze_memory(elf, map_file=mp)
    assert mem["has_map_data"] is True and mem["map"]["file_path"] == str(mp)
    assert va.analyze_memory(elf)["has_map_data"] is False


def test_license_methods_stay_but_report_unsupported(va):
    for call in (va.get_license, va.validate_license, va.deactivate_license):
        with pytest.raises(ViewAlyzerError) as e:
            call()
        assert e.value.code == "unsupported"
    with pytest.raises(ViewAlyzerError):
        va.activate_license("ANY-KEY")


def test_list_probes(va):
    payload = va.list_probes(stlink_path="C:/ST")  # 1.x kwargs still accepted
    assert payload["probes"][0]["type"] == "jlink"
    assert payload["probes"][0]["serial"] == "1260001884"


def test_query_max_slices_passthrough(va):
    payload = va.query("slice-details", "f76593b93473", max_slices=50)
    assert payload["query"] == "slice-details"


def test_record_polls(va, tmp_path):
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF")
    rec = va.record_polls(elf, ["tick_counter"], duration_s=1, poll_hz=10)
    assert rec.recording_id == "a718d4114f55"
    assert rec.info["summary"]["sample_count"] == 42
    assert rec.total_events == 42


def test_missing_binary_message():
    with pytest.raises(ViewAlyzerError) as e:
        ViewAlyzer("definitely-not-a-real-binary-12345").version()
    assert e.value.code == "binary_missing"
    assert "VIEWALYZER" in e.value.message


def test_open_by_id_and_path(va, vadb):
    by_id = va.open("f76593b93473")
    assert by_id.recording_id == "f76593b93473" and by_id.path is None
    by_path = va.open(vadb)
    assert by_path.path == vadb and by_path.recording_id is None


def test_delete_recording_envelope(va):
    va.delete_recording("f76593b93473")  # ok
    with pytest.raises(ViewAlyzerError) as e:
        va.delete_recording("nope00000000")
    assert e.value.code == "no_such_recording"
