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
    assert lanes["ISR:SysTick"]["max_switch_in_interval_deviation_us"] < 10.0

def test_no_priority_inversion(rec):
    assert rec.inversions()["inversions"] == []

def test_sine_channel(rec):
    c = rec.user_traces()
    sine = next(v for k, v in c["channels"].items() if c["trace_legend"][k]["name"] == "Sine Wave")
    assert 400 < sine["rate_hz"] < 600
    assert sine["min"] >= -100 and sine["max"] <= 100
```

`is_clean` first, always: a capture that lost events is not a measurement of the firmware. `extra_flags=["--no-register"]` keeps the runner's recording index from filling up; pin the probe serial on any machine that can have two probes.

## Fingerprints, baselines and envelopes

A **fingerprint** is a `.vafp.json` file containing selected measurements,
tolerances, recording settings and comparison rules. A **baseline** is the
fingerprint you choose as your reference. An **envelope** is one fingerprint
learned from several recordings: each numeric metric stores its mean and observed
minimum/maximum. All three use the same file and comparison API.

Create a baseline from saved recordings without connecting to a board:

```python
from viewalyzer_sdk import ViewAlyzer

va = ViewAlyzer()  # or ViewAlyzer("/path/to/viewalyzer-cli")
good = va.open("good1.vadb")
good.fingerprint(
    runs=["good2.vadb", "good3.vadb"],
    out="app.vafp.json",
)
```

This includes `good1.vadb` plus the two additional runs. Use separate captures
of the same representative workload. This method reads existing recordings;
it does not schedule or start three captures. One run is also a valid baseline:
omit `runs`. Commit the reviewed file alongside the firmware.

### Select measurements separately from recording settings

There are two independent questions:

| Question | SDK control | Example |
|---|---|---|
| Which measurements should be judged? | `sections` or the saved GUI metric selection | `sections=["summary", "traces", "health"]` leaves out per-task metrics |
| Must the recordings have matching settings? | `require_provenance` / `ignore_provenance` | `ignore_provenance=["trace_categories"]` permits the recorded category settings to differ; it does not remove any task metrics |

For example, requiring the recorder version in addition to the default rules:

```python
good.fingerprint(require_provenance=["recorder_version"], out="app.vafp.json")
```

Only if you have deliberately reviewed a timebase difference, allow it when
creating the baseline:

```python
good.fingerprint(ignore_provenance=["cpu_hz"], out="clock-reviewed.vafp.json")
```

`cpu_hz` means the recorder timestamp clock, not necessarily the core clock.
The supported fields are `os`, `cpu_hz`, `capture_source`, `recorder_version`,
`trace_categories` and `build_flags`. Required values must be known and equal;
ignored values remain in the report. These rules do not verify board identity,
the full RTOS configuration or workload identity. Your harness must check those
when they are not represented in the recording.

The SDK arguments select whole provenance fields. For individual trace categories
or build flags, edit the switches in the GUI and use **Save baseline copy...**.
The SDK can compare that same format-3 file, including its per-option exceptions.
Whole-field-only authoring produces format 2. The CLI response's
`schema_version: 3` is a separate JSON protocol version; it is not the file version.

Excluding Tasks leaves other selected measurements, such as Summary CPU load and
total task count, in place. Ignoring provenance never bypasses measurement-integrity
checks, and neither kind of selection changes what the device records.

### Fail a test using the strict gate

```python
def test_against_baseline(va):
    candidate = va.open("candidate.vadb")
    candidate.assert_matches("app.vafp.json")
```

`assert_matches()` runs `compare(..., ci=True)`. It returns the full report on
`pass` or an ordinary tolerance `warn`. It raises `FingerprintMismatch` for:

| Verdict | Meaning |
|---|---|
| `fail` | A selected metric regressed or a required baseline item is missing |
| `incomparable` | Required recording settings differ or are unknown, or a legacy baseline lacks an explicit policy |
| `invalid` | Measurement evidence is unusable: for example loss, corruption, an empty selection or a selected unmeasured/lower-bound value |

Strict checks apply to baseline and candidate evidence. Removing health metrics
from the comparison does not permit a capture with lost events to pass.
An older CLI that silently ignores strict mode is rejected with
`ViewAlyzerError.code == "unsupported_cli"`; a CLI that rejects an unknown flag
can return its own command error instead. Both fail the test. Update the CLI as well as the SDK. When explicit rules and `out=` are supplied,
the SDK stages the file and replaces the destination only after the CLI confirms
a supported policy format; a rejected CLI cannot overwrite the reviewed baseline.

To retain evidence in a test failure:

```python
import json
from pathlib import Path
from viewalyzer_sdk import FingerprintMismatch

candidate = va.open("candidate.vadb")
try:
    candidate.assert_matches("app.vafp.json")
except FingerprintMismatch as error:
    Path("fingerprint-report.json").write_text(
        json.dumps(error.report, indent=2), encoding="utf-8"
    )
    raise  # keep the test/job failed
```

For a report viewer, use `candidate.compare("app.vafp.json", ci=True)` and handle
the returned verdict yourself. **Do not check only `verdict != "fail"`**: that
would accept `incomparable` and `invalid`. Accept only `pass` or `warn`, or use
`assert_matches()`. Ordinary `compare(baseline)` returns verdicts as data and
keeps the legacy non-strict behavior for old baselines.

Rebuild legacy baselines from usable recordings before enabling strict CI; changing
a version field cannot create missing measurement evidence. See the
[CLI CI guide](../viewalyzer-cli/ci.html) for defaults, exit codes and migration,
and the [Fingerprint view](../viewalyzer/fingerprint.html) for the GUI controls.

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
