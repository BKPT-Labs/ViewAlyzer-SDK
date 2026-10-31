# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
from unittest.mock import Mock
from viewalyzer_sdk import Recording


def test_native_queries_keep_u64_intervals_and_missing_values(va, tmp_path):
    epoch = (1 << 60) + 123
    payload = {"schema_version": 3, "clock": "host_read_intervals",
               "rows": [{"lower_ns": epoch, "upper_ns": epoch + 2, "value": None}],
               "next_offset": None}
    va._runner.run_json = Mock(return_value=payload)
    rec = Recording(va, path=tmp_path / "probe.vadb")
    assert rec.native_signals(channel_id=2, offset=7, budget="low") == payload
    args = va._runner.run_json.call_args.args[0]
    assert args[-4:] == ["--offset", "7", "--channel-id", "2"]
    assert "native-signals" in args and "low" in args
    rec.hardware(offset=9)
    args = va._runner.run_json.call_args.args[0]
    assert "hardware" in args and args[-2:] == ["--offset", "9"]
