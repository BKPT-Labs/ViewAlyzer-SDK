# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""Opt-in real CLI coverage using an existing clean recording; never captures.

Set VIEWALYZER_SDK_REAL_CLI and VIEWALYZER_SDK_REAL_RECORDING to run.
The recording needs measured summary rates and zero loss/corruption.
"""
import copy
import json
import os
from pathlib import Path

import pytest

from viewalyzer_sdk import FingerprintMismatch, ViewAlyzer

CLI = os.environ.get("VIEWALYZER_SDK_REAL_CLI", "")
RECORDING = os.environ.get("VIEWALYZER_SDK_REAL_RECORDING", "")
pytestmark = pytest.mark.skipif(not (CLI and RECORDING), reason="set real CLI and saved recording paths")


@pytest.fixture()
def baseline(tmp_path):
    rec = ViewAlyzer(CLI).open(RECORDING)
    path = tmp_path / "baseline.vafp.json"
    result = rec.fingerprint(sections=["summary", "health"], out=path,
                             require_provenance=["recorder_version"])
    assert result["fingerprint_schema_version"] == 2
    assert rec.assert_matches(path)["verdict"] == "pass"
    return rec, path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case,expected", [
    ("pass", "pass"), ("warn", "warn"), ("fail", "fail"),
    ("clock", "incomparable"), ("ignored_clock", "pass"),
    ("loss", "invalid"), ("legacy", "incomparable"),
    ("ignored_bit", "pass"), ("required_bit", "incomparable"),
])
def test_saved_recording_gate_preserves_product_verdict(baseline, case, expected):
    rec, path, original = baseline
    doc = copy.deepcopy(original)
    if case in ("warn", "fail"):
        metric = doc["sections"]["summary"]["_"]["events_per_second"]
        assert metric["value"] > 0
        for field in ("value", "min", "max"):
            metric[field] /= 1.22 if case == "warn" else 2
    elif case in ("clock", "ignored_clock"):
        doc["provenance"]["cpu_hz"] *= 2
        if case == "ignored_clock":
            doc["compatibility"]["fields"]["cpu_hz"] = "ignore"
    elif case == "loss":
        doc["measurement"]["integrity"]["lost_events"] = 1
        # Neither metric selection nor ignored provenance may bypass integrity.
        doc["sections"].pop("health")
        doc["compatibility"]["fields"] = {key: "ignore" for key in doc["compatibility"]["fields"]}
    elif case == "legacy":
        doc["schema_version"] = 1
        doc.pop("compatibility")
    elif case in ("ignored_bit", "required_bit"):
        doc["schema_version"] = 3
        doc["compatibility"]["ignored_bits"] = {"trace_categories": 1}
        doc["provenance"]["trace_categories"] ^= 1 if case == "ignored_bit" else 2
    path.write_text(json.dumps(doc), encoding="utf-8")
    report = rec.compare(path, ci=True)
    assert report["ci"] is True
    assert report["verdict"] == expected
    if expected in ("pass", "warn"):
        assert rec.assert_matches(path)["verdict"] == expected
    else:
        with pytest.raises(FingerprintMismatch) as error:
            rec.assert_matches(path)
        assert error.value.code == "fingerprint_" + expected
        assert error.value.report["verdict"] == expected
        assert error.value.report["compatibility"] == report["compatibility"]
        if case == "clock":
            assert "Timestamp clock" in error.value.message
            assert "RTOS" not in error.value.message


def test_saved_recording_whole_field_options_and_envelope(baseline, tmp_path):
    rec, _, _ = baseline
    path = tmp_path / "envelope-without-extension"
    # A repeated input exercises SDK argument forwarding, not independent-run
    # learning. Real baselines should use separate representative captures.
    result = rec.fingerprint(runs=[RECORDING], sections="summary,health", out=path,
                    ignore_provenance="cpu_hz", require_provenance=["recorder_version"])
    assert result["out"] == str(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["runs"] == 2
    assert doc["compatibility"]["fields"]["cpu_hz"] == "ignore"
    assert doc["compatibility"]["fields"]["recorder_version"] == "require"
    # Authoring preserves the requested filename. Comparison identifies JSON
    # baselines by their suffix, so use the documented extension when loading.
    baseline_path = path.with_suffix(".vafp.json")
    path.rename(baseline_path)
    assert rec.assert_matches(baseline_path)["verdict"] == "pass"
