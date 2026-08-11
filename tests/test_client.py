import json

import pytest

from viewalyzer_cli import Recording, SCHEMA_VERSION, ViewAlyzer, ViewAlyzerError


def test_version_handshake(va):
    info = va.version()
    assert info["app"] == "ViewAlyzer"
    assert info["schema_version"] == SCHEMA_VERSION


def test_get_license(va):
    assert "max_record_s" in va.get_license()


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


def test_record_failure_surfaces_error_lines(va, tmp_path):
    with pytest.raises(ViewAlyzerError) as e:
        va.record({"transport": "fail"}, output=tmp_path / "x.vadb", duration_s=1)
    assert e.value.code == "record_failed"
    assert "Failed to connect" in e.value.message


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


def test_list_probes(va):
    payload = va.list_probes()
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
