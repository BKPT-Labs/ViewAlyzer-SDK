# Tests and CI

A trace is a measurement, so it can be asserted on. The SDK turns a capture into a pytest fixture and the recording's statistics into plain assertions; fingerprints and `compare` give you a regression gate without hand-picked thresholds.

## A fixture per capture

```python
# conftest.py
import pytest
from viewalyzer_sdk import ViewAlyzer

PROBE = "0033004B3033510735393935"

@pytest.fixture(scope="session")
def va():
    return ViewAlyzer()                                   # VIEWALYZER env, then PATH, then the install

@pytest.fixture(scope="session")
def rec(va, tmp_path_factory):
    out = tmp_path_factory.mktemp("trace") / "run.vadb"
    return va.record({"transport": "stlink-rambuf", "target-device": "STM32G474RE", "stlink-serial": PROBE},
                     output=out, duration_s=10, elf="build/rambuf/firmware.elf",
                     extra_flags=["--no-register"])
```

```python
# test_timing.py
def test_capture_is_a_measurement(rec):
    assert rec.is_clean                                   # no lost events, no corrupt bytes, no sequence gaps
    assert rec.info["duration_us"] > 9_000_000

def test_systick_budget(rec):
    lanes = {t["name"]: t for t in rec.timeline()["tasks"]}
    assert lanes["ISR:SysTick"]["p99_slice_us"] < 5.0
    assert lanes["ISR:SysTick"]["max_jitter_us"] < 10.0

def test_no_priority_inversion(rec):
    assert rec.inversions()["inversions"] == []

def test_sine_channel(rec):
    c = rec.user_traces()
    sine = next(v for k, v in c["channels"].items() if c["trace_legend"][k]["name"] == "Sine Wave")
    assert 400 < sine["rate_hz"] < 600
    assert sine["min"] >= -100 and sine["max"] <= 100
```

`is_clean` first, always: a capture that lost events is not a measurement of the firmware. `extra_flags=["--no-register"]` keeps the runner's recording index from filling up; pin the probe serial on any machine that can have two probes.

## Fingerprints as the baseline

```python
def test_against_baseline(rec):
    res = rec.compare("ci/app.vafp.json")
    assert res["verdict"] in ("pass", "warn"), [r for r in res["results"] if r["status"] in ("fail", "missing")]
```

Make the baseline once from a known-good run (or several, to learn the envelope) and commit it:

```python
good = va.open("good1.vadb")
good.fingerprint(runs=["good2.vadb", "good3.vadb"], out="ci/app.vafp.json")
```

The sections, tolerances and the file format are on the [CLI's CI page](../viewalyzer-cli/ci.html); the file is hand-editable.

## Hardware trace in a test

```python
def test_isr_load_and_console(va, tmp_path):
    rec = va.record("nucleo_g474_swo.vacf", output=tmp_path / "hw.vadb", duration_s=10, elf="firmware.elf",
                    extra_flags=["--dwt", "--dwt-exc", "--dwt-pc", "16384"])
    exc = {e["name"]: e for e in rec.dwt_exc()["data"]["exceptions"]}
    assert 9_500 <= exc["SysTick"]["enter"] <= 10_500           # 1 kHz tick over 10 s
    assert rec.swo_load()["data"]["overflows"] == 0
    lines = [l["text"] for p in rec.itm_console(port=0)["data"]["ports"] for l in p["lines"]]
    assert any("boot ok" in l for l in lines)
```

## Preflight and skipping

```python
import pytest
from viewalyzer_sdk import find_viewalyzer, ViewAlyzer

pytestmark = pytest.mark.skipif(find_viewalyzer() is None, reason="no viewalyzer-cli on this machine")

def test_probe_present():
    probes = ViewAlyzer().list_probes()["probes"]
    if not any(p["serial"] == PROBE for p in probes):
        pytest.skip("bench probe not attached")
```

`viewalyzer-doctor` from a shell does the same check for a job step (exit 2: no binary; exit 3: the environment check failed).

## Without hardware

For the code around the trace (parsers, report generators) run against a saved recording: `ViewAlyzer().open("fixtures/good.vadb")` gives the same `Recording`, every query works, and `connect()` opens the SQLite file read-only. The SDK's own test suite runs on a fake CLI (`tests/fake_viewalyzer.py`) that answers every verb with the real shapes, and a real-binary suite gated on `VIEWALYZER_SDK_REAL_CLI=<path>` (with `VIEWALYZER_SDK_REAL_CONFIG` and `VIEWALYZER_SDK_REAL_ELF` for the capture round trip), which is what runs on the bench.

## A job

```yaml
jobs:
  trace:
    runs-on: [self-hosted, bench-g474]
    steps:
      - uses: actions/checkout@v4
      - run: pip install viewalyzer-sdk pytest
      - run: viewalyzer-doctor
      - run: python build.py --flash --serial ${{ vars.PROBE_SERIAL }}
      - run: pytest tests/trace -q --junitxml=trace.xml
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: trace, path: "trace.xml" }
```
