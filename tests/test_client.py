import json
import warnings

import pytest

from viewalyzer_sdk import (
    HWTRACE_ARCHS,
    QUERY_VERBS,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    Recording,
    ViewAlyzer,
    ViewAlyzerError,
    ViewAlyzerWarning,
)


def test_version_handshake(va):
    info = va.version()
    assert info["app"] == "ViewAlyzer"
    assert info["schema_version"] == SCHEMA_VERSION
    assert info["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    assert SCHEMA_VERSION == 2 and SUPPORTED_SCHEMA_VERSIONS == (1, 2)


def test_get_license(va):
    assert "max_record_s" in va.get_license()


def test_doctor(va):
    report = va.doctor()
    checks = {c["id"]: c for c in report["checks"]}
    assert set(checks) == {
        "probes_stlink", "probes_jlink", "probes_cmsis_dap", "serial_ports",
        "recordings_dir", "probe_rs_targets", "license",
    }
    assert checks["probes_stlink"]["status"] == "ok"
    assert checks["probes_jlink"]["status"] == "none"
    assert checks["recordings_dir"]["path"]
    assert checks["license"]["status"] == "free" and checks["license"]["hint"]
    assert all(c["status"] in ("ok", "none", "missing", "free") for c in report["checks"])
    assert report["license"]["licensed"] is False


def test_doctor_tool_paths_are_accepted(va, tmp_path):
    # Kept for older builds; the native-driver CLI ignores them.
    report = va.doctor(jlink_path=tmp_path, arm_gdb_path=tmp_path / "gdb")
    assert report["checks"]


def test_license_lifecycle(va):
    assert va.activate_license("GOOD-KEY")["activated"] is True
    assert va.validate_license()["state"] == "active"
    assert va.deactivate_license()["activated"] is False
    with pytest.raises(ViewAlyzerError) as e:
        va.activate_license("BAD-KEY")
    assert e.value.code == "activation_failed"


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


def test_record_with_symbol_watch_warns_and_still_records(va, tmp_path):
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF")
    with pytest.warns(ViewAlyzerWarning, match="record_polls"):
        rec = va.record(
            {"transport": "stlink-swo"},
            output=tmp_path / "watch.vadb",
            duration_s=1,
            elf=elf,
            symbols=["tick_counter:u32", "adc_value"],
            poll_hz=500,
        )
    assert rec.total_events == 1234


def test_record_poll_hz_alone_warns(va, tmp_path):
    with pytest.warns(ViewAlyzerWarning, match="poll_hz"):
        va.record({"transport": "udp"}, output=tmp_path / "hz.vadb",
                  duration_s=1, poll_hz=100)


def test_record_without_symbols_does_not_warn(va, tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        va.record({"transport": "udp"}, output=tmp_path / "quiet.vadb", duration_s=1)


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
    with pytest.warns(ViewAlyzerWarning):
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
    # The fake prints progress lines before the envelope, as the CLI does.
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


def test_query_verb_table_matches_the_cli(va):
    for verb in (
        "summary", "verdicts", "profile", "itm-console", "dwt-data",
        "dwt-exc", "dwt-counters", "swo-load",
    ):
        assert verb in QUERY_VERBS
        assert va.query(verb, "f76593b93473")["query"] == verb


def test_query_etm_answers_etm_not_present(va):
    rec = Recording(va, recording_id="f76593b93473")
    with pytest.raises(ViewAlyzerError) as e:
        rec.etm()
    assert e.value.code == "etm_not_present"


def test_query_kinds_channels_threshold_flags(va):
    p = va.query("events", "f76593b93473", kinds=["task_switch", "isr_enter"])
    assert p["kinds"] == "task_switch,isr_enter"
    p = va.query("user-traces", "f76593b93473", channels="adc_value")
    assert p["channels"] == "adc_value"
    p = va.query("timers", "f76593b93473", threshold_us=250)
    assert p["threshold_us"] == "250"
    p = va.query("timeline", "f76593b93473")
    assert p["kinds"] is None and p["channels"] is None and p["threshold_us"] is None


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
    elf.write_bytes(b"ELF")
    mp = tmp_path / "fw.map"
    mp.write_text("Memory Configuration")
    mem = va.analyze_memory(elf, map_file=mp)
    assert mem["has_map_data"] is True and mem["map"]["file_path"] == str(mp)
    assert va.analyze_memory(elf)["has_map_data"] is False


def test_list_probes(va):
    payload = va.list_probes()
    assert payload["probes"][0]["type"] == "jlink"
    assert payload["probes"][0]["serial"] == "1260001884"


def test_list_ports(va):
    assert va.list_ports()["ports"] == ["COM7", "COM12"]


def test_list_targets(va):
    everything = va.list_targets()
    assert everything["count"] == len(everything["targets"]) == 5
    assert everything["filter"] == ""
    g4 = va.list_targets(filter="stm32g474")
    assert g4["count"] == 2
    assert {t["name"] for t in g4["targets"]} == {"STM32G474RETx", "STM32G474CEUx"}
    assert all(t["architecture"] == "v7m" for t in g4["targets"])


def test_hwtrace_dry_run_image(va):
    img = va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=170_000_000)
    # Bare image: no schema_version wrapper, unlike every other verb.
    assert "schema_version" not in img
    assert img["arch"] == "v7m" and img["cpu_hz"] == 170_000_000
    assert img["swo_hz"] == 2_000_000 and img["itm_port"] == 1
    assert img["init_swo"] is True
    assert img["writes"][0]["reg"] == "DEMCR"
    assert img["writes"][1]["value"] == "0x%08X" % (170_000_000 // 2_000_000 - 1)
    assert img["refused"] == []


def test_hwtrace_dry_run_inputs(va, tmp_path):
    block = {"dwt": {"enable": True, "watch": [{"addr": "0x20000410", "size": 4}]}}
    img = va.hwtrace_dry_run(
        arch="v8m", cpu_clock_hz=160_000_000, swo_freq_hz=4_000_000, itm_port=2,
        caps={"numcomp": 0, "itm": 1, "tpiu": 1}, hardware_trace=block,
        no_init_swo=True, extra_flags=["--dwt-watch", "g_temp@0x20000410:4"],
    )
    assert img["caps"]["numcomp"] == 0 and img["hardware_trace"] == block
    assert img["swo_hz"] == 4_000_000 and img["itm_port"] == 2
    assert img["init_swo"] is False
    assert img["dwt_watch"] == "g_temp@0x20000410:4"
    assert img["refused"][0]["feature"] == "dwt-watch"
    # The block may also come from a file (passed as @file) or a JSON string.
    f = tmp_path / "hw.json"
    f.write_text(json.dumps(block))
    assert va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=1_000_000,
                              hardware_trace=f)["hardware_trace"] == block
    assert va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=1_000_000,
                              hardware_trace=json.dumps(block))["hardware_trace"] == block


def test_hwtrace_dry_run_config_error_is_bad_config(va):
    # Exit 2 with {"error": <reason>}: no code, no message key.
    with pytest.raises(ViewAlyzerError) as e:
        va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=0)
    assert e.value.code == "bad_config"
    assert "--cpu-clock-hz" in e.value.message
    with pytest.raises(ViewAlyzerError) as e:
        va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=1, caps="not json")
    assert e.value.code == "bad_config" and "--caps" in e.value.message


def test_hwtrace_dry_run_rejects_unknown_arch_client_side(va):
    with pytest.raises(ViewAlyzerError) as e:
        va.hwtrace_dry_run(arch="v9m", cpu_clock_hz=1)
    assert e.value.code == "bad_arguments"
    assert HWTRACE_ARCHS == ("v6m", "v7m", "v8m")


def test_query_max_slices_passthrough(va):
    payload = va.query("slice-details", "f76593b93473", max_slices=50)
    assert payload["query"] == "slice-details"


def test_record_polls_with_config(va, tmp_path):
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF")
    # The fake, like the CLI, prints progress lines before its envelope.
    rec = va.record_polls(
        elf, ["tick_counter"], duration_s=1, poll_hz=10,
        config={"transport": "stlink-rambuf", "target-device": "STM32G474RE"},
    )
    assert rec.recording_id == "a718d4114f55"
    assert rec.info["summary"]["sample_count"] == 42
    assert rec.total_events == 42


def test_record_polls_with_config_file_and_transport_flag(va, tmp_path):
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF")
    cfg = tmp_path / "board.vacf"
    cfg.write_text(json.dumps({"transport": "jlink-rtt", "target-device": "STM32G474RE"}))
    assert va.record_polls(elf, "tick_counter", duration_s=1, config=cfg).total_events == 42
    # Config-less mode still works when the transport comes as a flag.
    rec = va.record_polls(
        elf, "tick_counter", duration_s=1, target_device="STM32G474RE",
        extra_flags=["--transport", "stlink-swo"],
    )
    assert rec.total_events == 42


def test_record_polls_needs_a_transport(va, tmp_path):
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF")
    # No config at all: refused before the CLI is spawned.
    with pytest.raises(ViewAlyzerError) as e:
        va.record_polls(elf, ["tick_counter"], duration_s=1, poll_hz=10)
    assert e.value.code == "bad_arguments" and "transport" in e.value.message
    # A bare target_device is not enough either.
    with pytest.raises(ViewAlyzerError) as e:
        va.record_polls(elf, ["tick_counter"], duration_s=1, target_device="STM32G474RE")
    assert e.value.code == "bad_arguments"
    # An inline dict without a transport.
    with pytest.raises(ViewAlyzerError) as e:
        va.record_polls(elf, ["tick_counter"], duration_s=1,
                        config={"target-device": "STM32G474RE"})
    assert e.value.code == "bad_arguments"
    # A non-probe transport reaches the CLI, whose bad_config envelope
    # (printed after a [headless] ERROR line) is surfaced as-is.
    with pytest.raises(ViewAlyzerError) as e:
        va.record_polls(elf, ["tick_counter"], duration_s=1, config={"transport": "udp"})
    assert e.value.code == "bad_config"


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
