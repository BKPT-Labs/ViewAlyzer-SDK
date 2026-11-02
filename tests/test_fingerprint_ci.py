# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""Strict gates must never be silently accepted by an older CLI."""

import json
from pathlib import Path

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


@pytest.mark.parametrize("version", [2, 3])
def test_supported_policy_versions_and_null_inline_document(va, monkeypatch, version):
    monkeypatch.setattr(va._runner, "run_json", lambda *args: {
        "fingerprint_schema_version": version, "fingerprint": None})
    assert Recording(va, recording_id="run").fingerprint(ignore_provenance="cpu_hz")["fingerprint_schema_version"] == version


@pytest.mark.parametrize("exists", [False, True])
def test_rejected_authoring_preserves_destination_and_cleans_staging(va, tmp_path, monkeypatch, exists):
    destination = tmp_path / "baseline.vafp.json"
    if exists:
        destination.write_text("previous reviewed baseline", encoding="utf-8")

    def old_cli(args, timeout):
        output = Path(args[args.index("--out") + 1])
        assert output != destination
        output.write_text("untrusted old output", encoding="utf-8")
        return {"fingerprint": None}

    monkeypatch.setattr(va._runner, "run_json", old_cli)
    with pytest.raises(ViewAlyzerError, match="unsupported_cli"):
        Recording(va, recording_id="run").fingerprint(ignore_provenance="cpu_hz", out=destination)
    assert destination.exists() == exists
    if exists:
        assert destination.read_text(encoding="utf-8") == "previous reviewed baseline"
    assert not list(tmp_path.glob(".viewalyzer-fingerprint-*"))


def test_confirmed_authoring_replaces_destination_and_returns_requested_path(va, tmp_path, monkeypatch):
    destination = tmp_path / "baseline.vafp.json"
    destination.write_text("previous", encoding="utf-8")
    document = {"format": "vafp", "schema_version": 3}

    def current_cli(args, timeout):
        output = Path(args[args.index("--out") + 1])
        output.write_text(json.dumps(document), encoding="utf-8")
        return {"fingerprint_schema_version": 3, "out": str(output)}

    monkeypatch.setattr(va._runner, "run_json", current_cli)
    result = Recording(va, recording_id="run").fingerprint(ignore_provenance="cpu_hz", out=destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == document
    assert result["out"] == str(destination)
    assert not list(tmp_path.glob(".viewalyzer-fingerprint-*"))


@pytest.mark.parametrize("contents", [None, "not JSON", '{"format":"vafp","schema_version":1}'])
def test_confirmed_response_without_valid_file_preserves_baseline(va, tmp_path, monkeypatch, contents):
    destination = tmp_path / "baseline.vafp.json"
    destination.write_text("reviewed baseline", encoding="utf-8")

    def broken_cli(args, timeout):
        if contents is not None:
            Path(args[args.index("--out") + 1]).write_text(contents, encoding="utf-8")
        return {"fingerprint_schema_version": 3}

    monkeypatch.setattr(va._runner, "run_json", broken_cli)
    with pytest.raises(ViewAlyzerError, match="bad_output"):
        Recording(va, recording_id="run").fingerprint(ignore_provenance="cpu_hz", out=destination)
    assert destination.read_text(encoding="utf-8") == "reviewed baseline"
    assert not list(tmp_path.glob(".viewalyzer-fingerprint-*"))


def test_cli_launch_error_is_not_relabelled_as_file_write_failure(va, tmp_path, monkeypatch):
    destination = tmp_path / "baseline.vafp.json"
    destination.write_text("reviewed baseline", encoding="utf-8")
    failure = PermissionError("CLI is not executable")

    def denied_cli(args, timeout):
        raise failure

    monkeypatch.setattr(va._runner, "run_json", denied_cli)
    with pytest.raises(PermissionError) as error:
        Recording(va, recording_id="run").fingerprint(ignore_provenance="cpu_hz", out=destination)
    assert error.value is failure
    assert destination.read_text(encoding="utf-8") == "reviewed baseline"
    assert not list(tmp_path.glob(".viewalyzer-fingerprint-*"))


@pytest.mark.parametrize("extra", [
    {"compatibility": None, "results": None},
    {"compatibility": {"fields": None}, "measurement_issues": None},
    {"compatibility": {"fields": [None, "bad"]}, "results": [None, 1]},
    {"compatibility": {"fields": [{"rule": "require", "status": "mismatch", "message": "  "}]},
     "results": [{"status": "fail", "message": "", "metric": ""}]},
])
def test_mismatch_preserves_incomplete_report_and_nonempty_message(extra):
    report = {"verdict": "invalid", **extra}
    error = FingerprintMismatch(report)
    assert error.report is report
    assert error.code == "fingerprint_invalid"
    assert error.message == "See comparison report"
