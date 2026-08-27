# viewalyzer-sdk

Python SDK for the **ViewAlyzer** headless CLI: automate embedded trace
capture, analytics queries, and regression assertions from Python, pytest,
and CI.

ViewAlyzer records RTOS/baremetal trace streams from real targets (ST-Link
SWO, J-Link RTT, RAM-buffer draining, UDP, serial) into self-describing
`.vadb` recordings, which are standard SQLite files, and answers analytics
queries about them as JSON. This package wraps that CLI so a
hardware-in-the-loop test can be three lines of Python.

```python
from viewalyzer_sdk import ViewAlyzer

va = ViewAlyzer()  # finds the installed ViewAlyzer app
rec = va.record("board.vacf", output="run1.vadb", duration_s=10)

assert rec.total_events > 0                      # capture actually captured
assert rec.is_clean                              # no corruption, no loss
assert rec.inversions()["inversions"] == []      # no priority inversions
assert rec.task_stats()[0]["cpu_percent"] < 80   # CPU headroom held
```

Zero dependencies, stdlib only. Requires a ViewAlyzer installation
(the app is the engine; this package is the steering wheel). Get the app
at [viewalyzer.net](https://viewalyzer.net).

## Install

```
pip install viewalyzer-sdk
```

Then check the wiring:

```
viewalyzer-doctor
```

which prints the executable the SDK found (or how to point it at one), the
CLI's version handshake, and the app's own health check of external tools
and attached probes.

## Finding the ViewAlyzer executable

First hit wins:

1. the `VIEWALYZER` environment variable (path to the executable); set but
   wrong raises immediately rather than silently falling back;
2. `PATH` (`viewalyzer-cli`, then `ViewAlyzer` / `viewalyzer`; `.exe` implied
   on Windows). Either binary works: `viewalyzer-cli` is the headless engine
   on its own, the GUI binary forwards `--headless` to the same engine;
3. the standard install locations: `%ProgramFiles%\ViewAlyzer` and
   `%LOCALAPPDATA%\Programs\ViewAlyzer` on Windows, `/Applications` and
   `~/Applications` on macOS, `/usr/local/bin`, `~/.local/bin`, and
   `/opt/ViewAlyzer` on Linux.

Or pass a path explicitly: `ViewAlyzer("/path/to/ViewAlyzer")`. Nothing is
ever hardcoded: every tool path the CLI uses (J-Link install dir,
arm-none-eabi-gdb, ...) can be set through the connection config or the
method arguments.

## Capturing

```python
# From a committed connection config (.vacf)...
rec = va.record("board.vacf", output="ci-run.vadb", duration_s=10)

# ...or an inline dict with the same keys (CLI flag names, no leading --):
rec = va.record(
    {"transport": "udp", "udp-ip": "127.0.0.1", "udp-port": 5005,
     "cpu-clock-hz": 170_000_000, "cobs": True},
    output="ci-run.vadb",
    duration_s=10,
)

# Watch variables during the same capture (memory-polled over the probe):
rec = va.record("board.vacf", output="run.vadb", duration_s=10,
                elf="firmware.elf", symbols=["adc_value:u16"], poll_hz=200)
```

`rec.path` is the authoritative on-disk file (the CLI forces the `.vadb`
extension), `rec.recording_id` the id for later queries.

Memory polling needs no firmware instrumentation at all:

```python
symbols = va.list_symbols("firmware.elf", filter="motor")
rec = va.record_polls("firmware.elf", ["tick_counter", "adc_value"],
                      duration_s=10, poll_hz=100, config="board.vacf")
```

And for firmware using the RAM-buffer transport, a post-mortem snapshot
reads the trace ring out of target RAM without resetting anything:

```python
rec = va.snapshot("board.vacf", output="crash.vadb", elf="firmware.elf")
print(rec.info["summary"])   # ring kind, events, window bytes, ...
```

## Live streaming

`stream()` runs the same capture as `record()` but hands you the data
points **while the capture runs**: firmware user traces (`VA_LogTrace`)
and polled symbols arrive as an iterator of samples, ready to graph in a
UI, feed a dashboard, or watch for a trigger condition. The same `.vadb`
recording still lands on disk; the stream is a tap, not a diversion.

```python
with va.stream("board.vacf", output="run.vadb", duration_s=60,
               elf="firmware.elf", symbols=["adc_value:u16"]) as s:
    for sample in s:                     # live, in arrival order
        chart.add(sample.name, sample.t_s, sample.value)
        if sample.name == "adc_value" and sample.value > 4000:
            s.stop()                     # finalize early, keep the partial

rec = s.result()                         # the finished Recording
assert rec.total_events > 0
```

Each `StreamSample` has `id`, `name`, `t_us` (and `t_s`), `value`, and
`is_float`; `s.streams` maps ids to `StreamMeta(name, type)` as streams
announce themselves (a new firmware trace can appear mid-capture).
`stop()` is portable, including Windows, and keeps everything captured so
far; use the stream for display and the returned `Recording` for
analysis, where timestamps are exact device-clock values.

Three kinds of channel stream, and none needs the others:

| Channel | Firmware requirement | How |
|---|---|---|
| User traces (`VA_LogTrace`) | recorder integrated | just `--stream`; announced as their setup packets arrive |
| Polled symbols | **none** | `elf=` + `symbols=` (+ `poll_hz=`); software-polled over the probe, any transport |
| DWT data watches | **none** | `extra_flags=["--dwt", "--dwt-watch", "beat@0x20000008:4,work@0x20000004:4"]`; hardware comparators catch **every write** (up to 4, SWO transports); your `name@` labels come back as `sample.name` |

### What does not stream

The live feed is **data channels only**: named series of timestamped
values. Everything else the capture records still lands in the `.vadb`
but is not emitted live, deliberately: task/scheduler slices (the point
of slices is seeing the swarm of them on a zoomable timeline, which is a
GUI, not an iterator), PC samples and the statistical profile, exception
trace, firmware string messages, and live CPU load. For those, open the
recording in the ViewAlyzer app, or query the finished `Recording`
(`timeline()`, `cpu()`, `events()`, ...) after the capture.

## Querying

Tiered, size-bounded JSON via the CLI. Start at `summary`, drill down:

```python
rec.timeline()                       # per-task CPU%, slice stats, jitter
rec.timeline(tier="bucketed", t_start_us=0, t_end_us=1_000_000,
             bucket_us=10_000)      # CPU% over time
rec.events()                         # counts by kind, top tasks
rec.user_traces()                    # data channels: min/max/mean/last
rec.cpu()                            # the CPU panel's scheduler statistics
rec.timers()                         # per-timer lateness stats, violations
rec.comms()                          # producer -> consumer paths, latency
rec.etm()                            # ETM call-tree profile (if captured)
rec.series("cpu-load")               # timeline series as [[t_us, value], ...]
rec.inversions()                     # every priority inversion, full story
rec.sql("SELECT name, cpu_percent FROM va_task_stats "
        "ORDER BY cpu_percent DESC LIMIT 5")
```

Golden-run regression testing distills a recording into a small,
git-committable baseline and gates CI on deviations:

```python
rec.fingerprint(out="golden.vafp.json")       # commit this file
result = new_rec.compare("golden.vafp.json")  # later runs
assert result["verdict"] in ("pass", "warn")
```

And because a `.vadb` **is** a SQLite database, overview reads skip the
subprocess entirely:

```python
rec = va.open("ci-run.vadb")
rec.summary()      # va_summary: total_events, cpu_load_percent, ...
rec.task_stats()   # per-task stats, synthetic lanes filtered out
rec.meta()         # provenance: va_cpu_hz, va_os, ...
con = rec.connect()  # read-only sqlite3.Connection for anything else
```

## pytest recipe

```python
# conftest.py
import pytest
from viewalyzer_sdk import ViewAlyzer

@pytest.fixture(scope="session")
def va():
    client = ViewAlyzer()
    assert client.version()["schema_version"] == 1
    return client

@pytest.fixture(scope="session")
def rec(va, tmp_path_factory):
    out = tmp_path_factory.mktemp("trace") / "run.vadb"
    r = va.record("board.vacf", output=out, duration_s=10)
    assert r.total_events > 0, "empty capture - check probe/firmware setup"
    return r

# test_regression.py
def test_no_priority_inversions(rec):
    assert rec.inversions()["inversions"] == []

def test_control_task_period_jitter(rec):
    stats = {t["name"]: t for t in rec.task_stats()}
    assert abs(stats["control_tid"]["max_jitter_us"]) < 200
```

## Error handling

Everything raises `ViewAlyzerError` with a machine-readable `.code`: the
CLI's own envelope codes (`no_such_recording`, `bad_sql`, `bad_arguments`,
`window_too_wide` with `.suggestion` telling you how to narrow the window)
plus the SDK's own (`binary_missing`, `timeout`, `bad_output`,
`record_failed`, `file_not_found`). Capture failures surface the CLI's
`ERROR:` diagnostics (e.g. *Failed to connect to target*), not a bare exit
code.

## Portable configs

`.vacf` files committed with a project are portable *except* for
machine-absolute tool paths (`jlink`, `arm-gdb`, ...). Don't edit the
shared file for your machine: load it, override in memory, and pass the
dict:

```python
cfg = json.loads(Path("board.vacf").read_text())
if not Path(cfg.get("jlink", "")).exists():          # path from another machine
    cfg["jlink"] = shutil.which("JLinkGDBServerCL") or cfg["jlink"]
rec = va.record(cfg, output="run.vadb", duration_s=10)
```

## Notes

- Query-layer times are **microseconds** since recording start; raw
  `va_events.t_cycles` values are CPU cycles (`meta.va_cpu_hz`).
- `delete_recording()` / `delete_all_recordings()` delete the files on
  disk, not just index entries.
- One process per call, no daemon: parallel pytest workers are fine as long
  as they don't fight over the same debug probe.

Full method-by-method reference, error-code table, and troubleshooting
(including what to do about a capture with `total_events == 0`):
[docs/API.md](https://github.com/BKPT-Labs/ViewAlyzer-SDK/blob/main/docs/API.md).
