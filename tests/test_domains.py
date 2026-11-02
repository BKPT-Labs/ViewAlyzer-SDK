# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""The SDK forwards domain validation to the native CLI without local rules."""
import json
import os
from pathlib import Path

import pytest

from viewalyzer_sdk import ViewAlyzer, ViewAlyzerError


@pytest.mark.parametrize("valid", [True, False])
def test_validation_forwards_path_and_preserves_the_native_report(monkeypatch, valid):
    client = ViewAlyzer("test-cli", query_timeout_s=17)
    path = Path("folder with spaces") / "domain.vadomain"
    report = {"domain_api": 42, "valid": valid, "files": [{"file": str(path), "valid": valid,
              "errors": [] if valid else [{"path": '$["future_field"]', "pointer": "/future_field", "message": "native diagnostic"}]}]}
    calls = []
    def run(args, timeout):
        calls.append((args, timeout))
        return report
    monkeypatch.setattr(client._runner, "run_json", run)
    assert client.validate_trace_domain(path) is report
    assert calls == [(["parse-trace-domain", str(path)], 17)]


def test_schema_is_returned_from_the_executable(monkeypatch):
    client = ViewAlyzer("test-cli")
    report = {"domain_api": 42, "schema": {"title": "Native schema"}}
    def run(args, timeout):
        assert args == ["parse-trace-domain", "--schema"]
        return report
    monkeypatch.setattr(client._runner, "run_json", run)
    assert client.trace_domain_schema() is report


def test_native_command_errors_are_preserved(monkeypatch):
    client = ViewAlyzer("test-cli")
    def run(args, timeout):
        raise ViewAlyzerError("bad_arguments", "unknown command 'parse-trace-domain'")
    monkeypatch.setattr(client._runner, "run_json", run)
    with pytest.raises(ViewAlyzerError, match="parse-trace-domain"):
        client.validate_trace_domain("domain.vadomain")


@pytest.mark.skipif(not os.environ.get("VIEWALYZER_SDK_REAL_CLI"), reason="native CLI not configured")
def test_native_parser_integration_without_hardware(tmp_path):
    client = ViewAlyzer(os.environ["VIEWALYZER_SDK_REAL_CLI"])
    domain = tmp_path / "test domain.vadomain"
    domain.write_text(json.dumps({"id": "test.sdk", "name": "Test", "version": "1"}), encoding="utf-8")
    assert client.validate_trace_domain(domain)["valid"]
    domain.write_text('{"id":"test.sdk","name":"Test","version":"1","sampled_state":{"rate_hz":0,"channels":[]}}', encoding="utf-8")
    report = client.validate_trace_domain(domain)
    assert not report["valid"]
    assert report["files"][0]["errors"][0]["pointer"] == "/sampled_state/rate_hz"
    assert "schema" in client.trace_domain_schema()
