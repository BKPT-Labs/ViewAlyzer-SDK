# viewalyzer-cli

Python bindings for the **ViewAlyzer** headless CLI — automate embedded trace
capture, analytics queries, and regression assertions from Python, pytest,
and CI.

ViewAlyzer records RTOS/baremetal trace streams from real targets (ST-Link
SWO, J-Link RTT, RAM-buffer draining, UDP, serial) into self-describing
`.vadb` recordings — standard SQLite files — and answers analytics queries
about them as JSON. This package wraps that CLI so a hardware-in-the-loop
test can be three lines of Python.

```python
from viewalyzer_cli import ViewAlyzer

va = ViewAlyzer()  # finds the installed ViewAlyzer app
rec = va.record("board.vacfg.json", output="run1.vadb", duration_s=10)

assert rec.total_events > 0                      # capture actually captured
assert rec.is_clean()                            # no corrupt bytes
assert rec.inversions()["inversions"] == []      # no priority inversions
assert rec.task_stats()[0]["cpu_percent"] < 80   # CPU headroom held
```

Zero dependencies — stdlib only. Requires a ViewAlyzer installation
(the app is the engine; this package is the steering wheel).

## Install

```
pip install viewalyzer-cli
```

Then check the wiring:

```
viewalyzer-doctor
```

which prints the executable the bindings found (or how to point them at
one) and the CLI's version handshake.

## Finding the ViewAlyzer executable

First hit wins:

1. the `VIEWALYZER` environment variable (path to the executable) — set but
   wrong raises immediately rather than silently falling back;
2. `PATH` (`ViewAlyzer` / `viewalyzer`, `.exe` implied on Windows);
3. the standard install locations — `%ProgramFiles%\ViewAlyzer` and
   `%LOCALAPPDATA%\Programs\ViewAlyzer` on Windows, `/Applications` and
   `~/Applications` on macOS, `/usr/local/bin`, `~/.local/bin`, and
   `/opt/ViewAlyzer` on Linux.

Or pass a path explicitly: `ViewAlyzer("/path/to/ViewAlyzer")`.

## Capturing

```python
# From a committed connection config (.vacfg.json)...
rec = va.record("board.vacfg.json", output="ci-run.vadb", duration_s=10)

# ...or an inline dict with the same keys (CLI flag names, no leading --):
rec = va.record(
    {"transport": "udp", "udp-ip": "127.0.0.1", "udp-port": 5005,
     "cpu-clock-hz": 170_000_000, "cobs": True},
    output="ci-run.vadb",
    duration_s=10,
)
```

`rec.path` is the authoritative on-disk file (the CLI forces the `.vadb`
extension), `rec.recording_id` the id for later queries.

Memory polling needs no firmware instrumentation at all:

```python
symbols = va.list_symbols("firmware.elf", filter="motor")
rec = va.record_polls("firmware.elf", ["tick_counter", "adc_value"],
                      duration_s=10, poll_hz=100, config="board.vacfg.json")
```

## Querying

Tiered, size-bounded JSON via the CLI — start at `summary`, drill down:

```python
rec.timeline()                       # per-task CPU%, slice stats, jitter
rec.timeline(tier="bucketed", t_start_us=0, t_end_us=1_000_000,
             bucket_us=10_000)      # CPU% over time
rec.events()                         # counts by kind, top tasks
rec.user_traces()                    # data channels: min/max/mean/last
rec.inversions()                     # every priority inversion, full story
rec.sql("SELECT name, cpu_percent FROM va_task_stats "
        "ORDER BY cpu_percent DESC LIMIT 5")
```

Tiered responses arrive in the CLI's envelope — the analytics live under
the `data` key (`rec.timeline()["data"]["tasks"]`); the bindings pass the
payload through unmodified.

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
from viewalyzer_cli import ViewAlyzer

@pytest.fixture(scope="session")
def va():
    client = ViewAlyzer()
    assert client.version()["schema_version"] == 1
    return client

@pytest.fixture(scope="session")
def rec(va, tmp_path_factory):
    out = tmp_path_factory.mktemp("trace") / "run.vadb"
    r = va.record("board.vacfg.json", output=out, duration_s=10)
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

Everything raises `ViewAlyzerError` with a machine-readable `.code` — the
CLI's own envelope codes (`no_such_recording`, `bad_sql`, `bad_arguments`,
`window_too_wide` — with `.suggestion` telling you how to narrow the
window) plus the bindings' own (`binary_missing`, `timeout`, `bad_output`,
`record_failed`, `file_not_found`). Capture failures surface the CLI's
`ERROR:` diagnostics (e.g. *Failed to connect to target*), not a bare exit
code.

## Portable configs on shared drives

`.vacfg.json` files committed with a project are portable *except* for
machine-absolute tool paths (`jlink`, `openocd`, ...). Don't edit the
shared file for your machine — load it, override in memory, and pass the
dict:

```python
cfg = json.loads(Path("board.vacfg.json").read_text())
if not Path(cfg.get("jlink", "")).exists():          # path from another OS
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
[docs/API.md](docs/API.md).
