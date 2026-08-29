# viewalyzer-sdk

Python SDK for the **ViewAlyzer** headless CLI: automate embedded trace
capture, analytics queries, and regression assertions from Python, pytest,
and CI.

ViewAlyzer records RTOS/baremetal trace streams from real targets (ST-LINK
and J-Link SWO, RTT, RAM-buffer draining, UDP, serial) into self-describing
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
at [viewalyzer.net](https://viewalyzer.net). This release targets CLI
wire-protocol `schema_version` 2 and still understands 1
(`SUPPORTED_SCHEMA_VERSIONS`).

## Install

```
pip install viewalyzer-sdk
```

Then check the wiring:

```
viewalyzer-doctor
```

which prints the executable the SDK found (or how to point it at one), the
CLI's version handshake, and the app's own health check: connected probes
per kind, serial ports, the recordings directory, the target registry, and
the license state.

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

Or pass a path explicitly: `ViewAlyzer("/path/to/ViewAlyzer")`. The CLI
drives probes natively (probe-rs), so no vendor tool paths are needed;
`va.list_probes()`, `va.list_ports()` and `va.list_targets(filter="STM32G474")`
tell you what to put in a connection config.

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

# Pin the RAM ring / RTT control block from the firmware image (no RAM scan):
rec = va.record("board.vacf", output="run.vadb", duration_s=10,
                elf="build/zephyr/zephyr.elf")
```

`rec.path` is the authoritative on-disk file (the CLI forces the `.vadb`
extension), `rec.recording_id` the id for later queries.

`record()` and `stream()` still accept `symbols=` / `poll_hz=` (the symbol
watch of older engines), but **this CLI does not poll symbols during a
capture**: the flags are accepted and ignored, the SDK raises a
`ViewAlyzerWarning`, and the capture runs without the watch. Poll memory
with `record_polls()` instead, or use a `--dwt-watch` hardware watch on an
SWO transport (see the streaming table below).

Memory polling needs no firmware instrumentation, but it does need a
debug-probe transport, so pass a config (a `.vacf` or a dict with at least
`transport`, usually `target-device` too); a bare `target_device` is not
enough and is refused up front:

```python
symbols = va.list_symbols("firmware.elf", filter="motor")
rec = va.record_polls("firmware.elf", ["tick_counter", "adc_value:u16"],
                      duration_s=10, poll_hz=100, config="board.vacf")
print(rec.info["summary"])   # sample_count, sample_loss_percent, ...
```

And for firmware using the RAM-buffer transport, a post-mortem snapshot
reads the trace ring out of target RAM without resetting anything:

```python
rec = va.snapshot("board.vacf", output="crash.vadb", elf="firmware.elf")
print(rec.info["summary"])   # ring kind, events, window bytes, ...
```

## Live streaming

`stream()` runs the same capture as `record()` but hands you the live feed
**while the capture runs**. Iterating the session yields the data points
(firmware user traces, Trace Domain sampled channels, DWT data watches) as
`StreamSample`s, ready to graph in a UI, feed a dashboard, or watch for a
trigger condition. The same `.vadb` recording still lands on disk; the
stream is a tap, not a diversion.

```python
with va.stream("board.vacf", output="run.vadb", duration_s=60) as s:
    for sample in s:                     # live, in arrival order
        chart.add(sample.name, sample.t_s, sample.value)
        if sample.name == "adc_value" and sample.value > 4000:
            s.stop()                     # finalize early, keep the partial

rec = s.result()                         # the finished Recording
assert rec.total_events > 0
```

Each `StreamSample` has `id`, `name`, `t_us` (and `t_s`), `value` and
`is_float`; `s.streams` maps ids to `StreamMeta(name, type)` as streams
announce themselves (a new firmware trace can appear mid-capture).
`stop()` is portable, including Windows, and keeps everything captured so
far; use the stream for display and the returned `Recording` for
analysis, where timestamps are exact device-clock values.

The feed carries more than data points on SWO captures. `s.events()`
yields **every** stream line in arrival order: `StreamSample` for data
points and `StreamEvent(t, t_us, data)` for the rest. Pick one consumer per
session (`for sample in s` or `s.events()`, not both).

```python
with va.stream("board.vacf", output="run.vadb", duration_s=60) as s:
    for ev in s.events():
        if isinstance(ev, StreamSample):
            chart.add(ev.name, ev.t_s, ev.value)
        elif ev.t == "itm_text":
            console.write(ev.data["text"])          # firmware printf over ITM
        elif ev.t == "swo_load" and ev.data["overflows"]:
            print("SWO oversubscribed, lower the sample rate")
```

| `ev.t` | `ev.data` | Source |
|---|---|---|
| `stream_init` | `transport`, `started_utc`, `schema_version` | once, first |
| `stream_meta` | `id`, `name`, `display` | a channel registered (also in `s.streams`) |
| `stream_sample` | delivered as `StreamSample` | user traces, sampled channels, DWT watches |
| `itm_text` | `port`, `t_us`, `text` | ITM stimulus ports (SWO) |
| `pc_samples` | `total`, `sleep`, `pcs[]` | DWT PC sampling, batched |
| `swo_load` | `bytes_per_s`, `share_pct`, `overflows` | SWO pin utilisation |
| `dwt_data` | `rows: [{cmp, t_us, v, size, w, pc}]` | DWT data-trace comparators |
| `exc` | `total`, `max_depth`, `exceptions[]` | DWT exception trace, cumulative |

The feed ends when the CLI closes stderr (there is no `stream_end` line
from this CLI). Task/scheduler slices and live CPU load are not emitted
live; open the recording in the ViewAlyzer app or query the finished
`Recording` for those.

Channels that stream as samples, and what they need:

| Channel | Firmware requirement | How |
|---|---|---|
| User traces (`VA_LogTrace`) | recorder integrated | just `stream()`; announced as their setup packets arrive |
| Trace Domain sampled channels | **none** | installed domain descriptors that claim the target; polled over the debug port |
| DWT data watches | **none** | `extra_flags=["--dwt", "--dwt-watch", "beat@0x20000008:4,work@0x20000004:4"]`; hardware comparators catch **every write** (up to 4, SWO transports); your `name@` labels come back as `sample.name` |

## Querying

Tiered, size-bounded JSON via the CLI. Start at `summary`, drill down:

```python
rec.report()                         # the engine's whole-recording scalars
rec.timeline()                       # per-task CPU%, slice stats, jitter
rec.timeline(tier="bucketed", t_start_us=0, t_end_us=1_000_000,
             bucket_us=10_000)      # CPU% over time
rec.events()                         # counts by kind, top tasks
rec.user_traces()                    # data channels: min/max/mean/last
rec.cpu()                            # the CPU panel's scheduler statistics
rec.timers()                         # per-timer lateness stats, violations
rec.comms()                          # producer -> consumer paths, latency
rec.series("cpu-load")               # timeline series as [[t_us, value], ...]
rec.inversions()                     # every priority inversion, full story
rec.verdicts()                       # Trace Domain rule hits
rec.sql("SELECT name, cpu_percent FROM va_task_stats "
        "ORDER BY cpu_percent DESC LIMIT 5")
```

Hardware-trace captures (SWO with `--dwt`) add the profiler and console
views; each raises `ViewAlyzerError("no_hw_trace")` on a recording without
the rows:

```python
rec.profile(elf="firmware.elf")      # PC-sample hotspots, symbolicated
rec.itm_console(port=0)              # firmware printf lines over ITM
rec.dwt_data()                       # data-watch samples per comparator
rec.dwt_exc()                        # exception enter/exit/return counts
rec.dwt_counters()                   # CPI / sleep / LSU / fold counters
rec.swo_load()                       # SWO pin utilisation and overflows
```

(`rec.etm()` is kept for recordings from an ETM-capable engine; this CLI
answers it with `etm_not_present`.)

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

## Hardware-trace planning

`va.hwtrace_dry_run()` prints the ordered register image a capture would
program for an ITM / DWT / TPIU setup on a given core, without a target
attached. Review a board's `hardware-trace` block in CI, or diff it against
`bkpt_gdbserver --dry-run`:

```python
img = va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=170_000_000,
                         hardware_trace=cfg["hardware-trace"])
assert not img["refused"], img["refused"]
for w in img["writes"]:
    print(w["reg"], w["addr"], w["value"])
```

## pytest recipe

```python
# conftest.py
import pytest
from viewalyzer_sdk import SUPPORTED_SCHEMA_VERSIONS, ViewAlyzer

@pytest.fixture(scope="session")
def va():
    client = ViewAlyzer()
    assert client.version()["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
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
`bad_config`, `no_hw_trace`, `window_too_wide` with `.suggestion` telling
you how to narrow the window) plus the SDK's own (`binary_missing`,
`timeout`, `bad_output`, `record_failed`, `file_not_found`). Capture
failures surface the CLI's `ERROR:` diagnostics (e.g. *Failed to connect to
target*), not a bare exit code. Requests the CLI accepts but does not act
on (`symbols=` on a capture) raise a `ViewAlyzerWarning`, a `UserWarning`
subclass you can silence or escalate with the `warnings` module.

## Portable configs

`.vacf` files committed with a project are portable across machines and
OSes with the native probe drivers; the only machine-specific keys are
probe serials (`stlink-serial`, `jlink-serial`) and serial-port names.
Don't edit the shared file for your machine: load it, override in memory,
and pass the dict:

```python
cfg = json.loads(Path("board.vacf").read_text())
cfg["stlink-serial"] = va.list_probes()["probes"][0]["serial"]   # this bench's probe
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
Release history: [CHANGELOG.md](https://github.com/BKPT-Labs/ViewAlyzer-SDK/blob/main/CHANGELOG.md).
