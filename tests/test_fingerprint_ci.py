# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""Strict gates must never be silently accepted by an older CLI."""

import pytest

from viewalyzer_sdk import FingerprintMismatch, Recording, ViewAlyzerError


def test_old_cli_cannot_silently_ignore_ci_or_provenance_rules(va, tmp_path):
    rec = Recording(va, recording_id="f76593b93473")
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")
    with pytest.raises(ViewAlyzerError, match="unsupported_cli"):
        rec.assert_matches(baseline)
    with pytest.raises(ViewAlyzerError, match="unsupported_cli"):
        rec.fingerprint(ignore_provenance="cpu_hz")


@pytest.mark.parametrize("verdict", ["pass", "warn", "fail", "incomparable", "invalid"])
def test_assertion_uses_product_verdict_and_retains_evidence(va, tmp_path, monkeypatch, verdict):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")
    report = {"ci": True, "verdict": verdict, "compatibility": {
        "fields": [{"field": "cpu_hz", "rule": "require", "status": "mismatch",
                    "message": "Timestamp clock changed"}]}}

    def run(args, timeout):
        assert args == ["--query", "compare", "--recording", "run", "--baseline", str(baseline), "--ci"]
        return report

    monkeypatch.setattr(va._runner, "run_json", run)
    rec = Recording(va, recording_id="run")
    if verdict in ("pass", "warn"):
        assert rec.assert_matches(baseline) is report
    else:
        with pytest.raises(FingerprintMismatch) as error:
            rec.assert_matches(baseline)
        assert error.value.report is report
        assert error.value.code == "fingerprint_" + verdict
        assert "Timestamp clock" in error.value.message


def test_fingerprint_sends_bare_and_sequence_field_lists(va, monkeypatch):
    def run(args, timeout):
        assert args[-4:] == ["--require-provenance", "build_flags,trace_categories", "--ignore-provenance", "cpu_hz"]
        return {"fingerprint": {"schema_version": 2}}
    monkeypatch.setattr(va._runner, "run_json", run)
    Recording(va, recording_id="run").fingerprint(
        require_provenance=["build_flags", "trace_categories"], ignore_provenance="cpu_hz")


def test_unknown_verdict_is_not_accepted(va, monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")
    monkeypatch.setattr(va._runner, "run_json", lambda *args: {"ci": True, "verdict": "unknown"})
    with pytest.raises(ViewAlyzerError, match="bad_output"):
        Recording(va, recording_id="run").assert_matches(baseline)
