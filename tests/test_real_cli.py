"""Contract checks against a REAL ViewAlyzer binary (skipped by default).

The rest of the suite drives the fake CLI so it runs anywhere. This module
runs the same client against the actual executable when you point it at
one, and against real hardware when you also hand it a connection config:

    VIEWALYZER_SDK_REAL_CLI=/path/to/viewalyzer-cli   pytest tests/test_real_cli.py
    VIEWALYZER_SDK_REAL_CONFIG=board.vacf             (optional: enables the capture tests)
    VIEWALYZER_SDK_REAL_ELF=firmware.elf              (optional: symbol listing + memory)

Nothing here asserts numbers that depend on the firmware; it asserts the
shapes and behaviours the SDK relies on (version handshake, JSON envelopes,
error envelopes, the `[headless]` lines that carry the recording path and
id, and the `--stream` / `--stop-file` stop channel). The schema check is a
membership test against SUPPORTED_SCHEMA_VERSIONS: a binary on any wire
version this SDK understands passes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from viewalyzer_sdk import SUPPORTED_SCHEMA_VERSIONS, ViewAlyzer, ViewAlyzerError

REAL_CLI = os.environ.get("VIEWALYZER_SDK_REAL_CLI", "").strip()
REAL_CONFIG = os.environ.get("VIEWALYZER_SDK_REAL_CONFIG", "").strip()
REAL_ELF = os.environ.get("VIEWALYZER_SDK_REAL_ELF", "").strip()
REAL_MAP = os.environ.get("VIEWALYZER_SDK_REAL_MAP", "").strip()

pytestmark = pytest.mark.skipif(
    not REAL_CLI, reason="set VIEWALYZER_SDK_REAL_CLI to a ViewAlyzer binary to run"
)
needs_hardware = pytest.mark.skipif(
    not REAL_CONFIG, reason="set VIEWALYZER_SDK_REAL_CONFIG to a .vacf to run captures"
)
needs_elf = pytest.mark.skipif(not REAL_ELF, reason="set VIEWALYZER_SDK_REAL_ELF to an ELF")


@pytest.fixture()
def real() -> ViewAlyzer:
    assert Path(REAL_CLI).is_file(), REAL_CLI
    return ViewAlyzer(REAL_CLI)


def test_version_handshake(real):
    info = real.version()
    assert info["app"] == "ViewAlyzer"
    assert info["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    assert isinstance(info["version"], str) and info["version"]


def test_list_probes_shape(real):
    payload = real.list_probes()
    assert payload["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    assert isinstance(payload["probes"], list)
    for p in payload["probes"]:
        assert {"type", "serial"} <= set(p)


def test_list_recordings_shape(real):
    payload = real.list_recordings()
    assert payload["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    assert isinstance(payload["recordings"], list)


def test_unknown_recording_is_an_error_envelope(real):
    with pytest.raises(ViewAlyzerError) as e:
        real.query("timeline", "000000000000", tier="summary")
    # the envelope, not the exit code, is the failure signal
    assert e.value.code
    assert "000000000000" in e.value.message or e.value.code


def test_doctor_runs(real):
    report = real.doctor()
    assert report["schema_version"] in SUPPORTED_SCHEMA_VERSIONS


@needs_elf
def test_list_symbols_against_real_elf(real):
    syms = real.list_symbols(REAL_ELF, filter="")
    assert syms["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    assert Path(syms["elf"]).name == Path(REAL_ELF).name


@needs_elf
def test_analyze_memory_against_real_elf(real):
    mem = real.analyze_memory(REAL_ELF)
    assert mem["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    assert mem["flash_used"] > 0 and mem["has_map_data"] is False


@pytest.mark.skipif(not (REAL_ELF and REAL_MAP), reason="set VIEWALYZER_SDK_REAL_MAP to a linker MAP")
def test_analyze_memory_with_map(real):
    mem = real.analyze_memory(REAL_ELF, map_file=REAL_MAP)
    assert mem["has_map_data"] is True
    regions = mem["map"]["memory_regions"]
    assert regions and all(r["length"] >= r["used"] for r in regions)
    assert mem["map"]["discarded_count"] == len(mem["map"]["discarded_sections"])


@needs_hardware
def test_record_then_query(real, tmp_path):
    rec = real.record(REAL_CONFIG, output=tmp_path / "run.vadb", duration_s=4, elf=REAL_ELF or None)
    assert rec.path.is_file()
    assert rec.total_events > 0, "0 events: check the transport and that the firmware is running"
    # loss counters are a bench property (probe throughput); the contract is that they are reported
    assert rec.has_sequence_info and rec.lost_events >= 0 and rec.seq_gaps >= 0
    summary = real.query("timeline", rec, tier="summary")
    assert summary["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    # a bare-metal target has only synthetic lanes (ISR:, Fn:); an RTOS has real tasks too
    assert rec.task_stats(include_synthetic=True)


@needs_hardware
def test_stream_then_stop(real, tmp_path):
    with real.stream(REAL_CONFIG, output=tmp_path / "live.vadb", duration_s=30, elf=REAL_ELF or None) as s:
        seen = 0
        for sample in s:
            seen += 1
            if seen >= 20:
                break
        s.stop()
        rec = s.result(timeout_s=30)
    assert seen >= 20
    assert rec.path.is_file()
    assert rec.total_events > 0
