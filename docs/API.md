# viewalyzer-cli — API reference

Python bindings for the ViewAlyzer headless CLI. Everything lives in one
import:

```python
from viewalyzer_cli import (
    ViewAlyzer,        # the client
    Recording,         # a handle on one .vadb recording
    ViewAlyzerError,   # every failure, CLI-side or bindings-side
    BinaryNotFound,    # subclass: executable couldn't be located
    find_viewalyzer,   # discovery as a standalone function
    SCHEMA_VERSION,    # the CLI wire-protocol version these bindings target
)
```

Design in one sentence: every method is **one CLI invocation** (one process,
one JSON payload, exit) except the direct-SQLite readers on `Recording`,
which open the `.vadb` file itself read-only and involve no subprocess.

---

## Locating the executable

Discovery order, first hit wins:

| Step | Source | Notes |
|---|---|---|
| 1 | `VIEWALYZER` env var | Path to the executable. Set-but-wrong **raises** `BinaryNotFound` instead of silently falling through. |
| 2 | `PATH` | Tries `ViewAlyzer` then `viewalyzer`; `.exe` is implied on Windows. |
| 3 | Standard installs | Windows: `%ProgramFiles%\ViewAlyzer\`, `%LOCALAPPDATA%\Programs\ViewAlyzer\`. macOS: `/Applications` and `~/Applications` (`ViewAlyzer.app`). Linux: `/usr/local/bin/viewalyzer`, `~/.local/bin/viewalyzer`, `/opt/ViewAlyzer` (incl. AppImages). |

```python
find_viewalyzer() -> Path | None
find_viewalyzer_with_source() -> (Path | None, "env" | "path" | "install" | "not found")
```

From a terminal, `viewalyzer-doctor` (or `python -m viewalyzer_cli`) prints
what was found and runs the version handshake; exit code 0 means ready.

---

## class `ViewAlyzer`

```python
ViewAlyzer(binary=None, *, query_timeout_s=30.0)
```

- `binary` — path to the executable, **or a list** forming an argv prefix
  (how the test suite substitutes a fake CLI:
  `ViewAlyzer([sys.executable, "fake_cli.py"])`). Omit to auto-discover;
  raises `BinaryNotFound` if nothing is found.
- `query_timeout_s` — timeout for query/list calls. Captures compute their
  own timeout from the requested duration (plus ~15 s of headroom).

### Handshake & utilities

| Method | Returns |
|---|---|
| `version()` | `{"schema_version": 1, "app": "ViewAlyzer", "version": "1.2.0"}` — call once at startup and compare against `SCHEMA_VERSION`. |
| `get_license()` | Activation state and effective caps (e.g. max recording seconds). |
| `analyze_memory(elf, *, map_file=None)` | Static flash/RAM breakdown of a firmware image. |
| `list_symbols(elf, *, filter=None)` | Pollable symbols (`symbol_legend`: name, address, size, type). Also handy to verify a pinned `rtt-address` against `_SEGGER_RTT` in the ELF you actually flashed. |
| `list_probes(*, jlink_path=None, stlink_path=None, cube_programmer_path=None)` | Connected debug probes: `{"probes": [{"type": "jlink"\|"stlink", "serial", "description"}], "warnings"?}`. With several probes attached, pin one via the `jlink-serial` / `stlink-serial` config keys. |

### The recording index

| Method | Notes |
|---|---|
| `list_recordings()` | Raw index payload: `{"recordings": [{recording_id, path, schema_name, duration_us, size_bytes, created_utc}, ...]}`. |
| `recordings()` | Same, as bound `Recording` handles (`info` carries the index fields). |
| `open(path_or_id)` | Handle on an existing recording. A `.vadb` **path** enables the direct-SQLite readers; a 12-hex **id** supports CLI queries only. |
| `delete_recording(rec_or_id)` | **Destructive** — removes the index entry *and deletes the file on disk*. |
| `delete_all_recordings()` | **Destructive** — all of the above, for every indexed recording. |

The index lives in the per-user app-data dir. If your CI manages recording
files itself, ignore the index and pass paths.

### Capture

```python
record(config, *, output, duration_s, extra_flags=(), timeout_s=None) -> Recording
```

- `config` — a `.vacfg.json` path **or an inline dict** with the same keys
  (the CLI flag names without the leading `--`: `transport`,
  `target-device`, `cpu-clock-hz`, `rtt-channel`, tool paths, ...). Inline
  dicts are written to a temp file for the call and cleaned up after. Two
  keys are config-only with no flag equivalent: `rtt-address` and
  `target-init`. Supported transports: `stlink-swo`, `stlink-rambuf`,
  `jlink-rambuf`, `jlink-rtt`, `udp`, `serial`.
- `extra_flags` — appended verbatim for flags without a dedicated
  parameter (e.g. `["--log", path]`, `["--rambuf-driver", "openocd"]`,
  port overrides). CLI flags win over config-file values.
- `output` — where to write the `.vadb`. The CLI **forces the `.vadb`
  extension**; the returned `Recording.path` is the authoritative on-disk
  file, which may therefore differ from `output`.
- Returns a `Recording` with `recording_id` (parsed from the CLI's stable
  `Recording registered: id=...` line) and `path`.
- On failure raises `ViewAlyzerError("record_failed", ...)` whose message
  contains the CLI's `ERROR:` diagnostics (e.g. *Failed to connect to
  target*), not just an exit code. A capture that produced a missing or
  empty file is also a failure.

```python
record_polls(elf, symbols, *, duration_s, poll_hz=100,
             config=None, target_device=None,
             extra_flags=(), timeout_s=None) -> Recording
```

Samples target variables over the debug probe — no firmware instrumentation
required. Needs a probe transport (not udp/serial). Pass `config` to reuse a
connection file, or `target_device` plus tool paths via `extra_flags`
(e.g. `["--arm-gdb", path]`) for config-less mode. The poll summary
(`sample_count`, `sample_loss_percent`, `symbols_polled`) lands in
`Recording.info["summary"]`. Poll timestamps are wall-clock microseconds,
not CPU cycles.

### Generic query escape hatch

```python
query(verb, recording, *, tier=None, budget=None,
      t_start_us=None, t_end_us=None, bucket_us=None,
      max_slices=None, sql=None, timeout_s=None) -> dict
```

Prefer the named helpers on `Recording`; this exists for verbs/flags they
don't cover. Valid verbs: tiered — `timeline`, `events`, `user-traces`;
untiered — `inversions`, `slices`, `slice-details` (takes `max_slices`),
`events-all`, `user-traces-all`, `sql`. The `*-all` and slice verbs bypass
the tier budgets — they're for consumers that manage their own payload
sizes. `recording` may be a `Recording`, an id, or a path.

---

## class `Recording`

Handles come from `record()`, `record_polls()`, `recordings()`, or
`open()`. Attributes: `recording_id`, `path`, `info` (extra fields the CLI
reported), `ref` (what gets passed as `--recording`: the id when known,
else the path).

### CLI queries (subprocess, size-bounded JSON)

| Method | What you get |
|---|---|
| `timeline(tier="summary", **window)` | Scheduler/CPU analytics: per-task CPU %, slice count and percentiles, jitter profile. `bucketed` = per-task CPU % over time buckets; `raw` = individual slices in a window. |
| `events(tier="summary", **window)` | Unified event stream: counts by kind, top tasks; bucketed/raw variants. |
| `user_traces(tier="summary", **window)` | Data channels (firmware traces + poll samples): per-channel count/min/max/mean/last. |
| `inversions()` | Priority-inversion report, RTOS-aware priority comparison. Top-level `"inversions"` list. |
| `sql(statement, *, budget=None)` | One read-only SQL statement executed by the CLI: `{"columns", "rows", "row_count", "truncated"}`. |
| `sql_rows(statement, *, budget=None)` | Same, rows as dicts keyed by column name. |

Window kwargs for `bucketed`/`raw` tiers: `t_start_us`, `t_end_us`
(microseconds since recording start; required), `bucket_us` (bucketed
only), `budget` (`"low" | "med" | "high"`, default med).

**Response envelope.** Tiered responses arrive as

```python
{"recording_id": ..., "query": "timeline", "tier": "summary",
 "budget": "med", "window": {...}, "schema_version": 1,
 "data": { ... the actual analytics ... }}
```

so it's `rec.timeline()["data"]["tasks"]`. The bindings deliberately pass
the CLI payload through unmodified — the payload *is* the contract, and
reshaping it here would fork the documentation.

Know which of the three shapes you're holding:

| Call | Shape |
|---|---|
| `timeline()` / `events()` / `user_traces()` | Enveloped — results under `["data"]`. |
| `inversions()`, `sql()` | Top-level keys (`["inversions"]`; `["columns"]`/`["rows"]`) — no `data` wrapper. |
| `summary()`, `task_stats()`, `meta()`, `total_events` | Plain Python values (dicts / ints) read straight from SQLite — no CLI payload at all. |

**Size bounds.** If a response would blow the tier's budget you get
`ViewAlyzerError` with `code == "window_too_wide"`; its `.suggestion` dict
is the CLI's machine-readable advice (narrow the window / raise the
budget). Follow it rather than retrying blindly.

### Direct SQLite reads (no subprocess — needs `path`)

A `.vadb` is a standard SQLite database; these readers open it read-only.

| Member | What you get |
|---|---|
| `summary()` | `va_summary` as a typed dict: `total_events`, `cpu_load_percent`, `context_switches`, `span_seconds`, `corrupt_bytes`, ... |
| `total_events` (property) | Int from `va_summary`. **Assert on this, not on file existence** — a capture can "succeed" with 0 events when the probe/firmware setup is broken. |
| `is_clean()` | `corrupt_bytes == 0`. |
| `task_stats(include_synthetic=False)` | `va_task_stats` rows as dicts, highest CPU first. Synthetic lanes (`_RTOS_`, `ISR:*`, `Fn:*`) filtered unless requested. Columns include `cpu_percent`, `run_count`, `avg_period_us`, `min/max_jitter_us`, `priority`, `stack_used_bytes`, `mutex_contentions`, ... |
| `meta()` | Provenance: `va_cpu_hz`, `va_os`, `capture_source`, ... |
| `connect()` | A read-only `sqlite3.Connection` for anything else (full schema: `va_events`, `va_objects`, `va_trace_stats`, `health`). Caller closes it. |

Units: query-layer times are **microseconds** since recording start. Raw
`va_events.t_cycles` are CPU cycles (`seconds = t_cycles / meta.va_cpu_hz`)
— except `poll_trace` rows, which are microseconds.

---

## Errors

Single exception type, machine-readable code:

```python
try:
    rec.timeline(tier="raw", t_start_us=0, t_end_us=10_000_000)
except ViewAlyzerError as e:
    e.code        # "window_too_wide"
    e.message     # human explanation
    e.suggestion  # optional dict: the CLI's suggested next step
    e.limits      # optional dict: the cap that was hit
```

| Code | Origin | Meaning |
|---|---|---|
| `binary_missing` | bindings | Executable not found / `VIEWALYZER` points nowhere. |
| `timeout` | bindings | Call exceeded its timeout. |
| `bad_output` | bindings | stdout wasn't the expected single JSON object. |
| `record_failed` | bindings | Capture failed; message carries the CLI's `ERROR:` lines. |
| `file_not_found` | bindings | An input path (ELF, config, recording) doesn't exist. |
| `bad_arguments` | both | Invalid flag/parameter combination. |
| `no_such_recording` | CLI | Unknown recording id/path. |
| `window_too_wide` | CLI | Response over budget; follow `.suggestion`. |
| `bad_sql` | CLI | Rejected statement (writes, PRAGMAs, multi-statement, syntax). |
| `internal` | CLI | CLI-side failure. |

---

## Recipes

### Dual-boot / shared-drive configs

Committed `.vacfg.json` files travel across OSes, but tool paths in them
(e.g. `jlink`) are machine-absolute. Don't edit the shared file — merge
over it in memory:

```python
cfg = json.loads(Path("board.vacfg.json").read_text())
if not Path(cfg.get("jlink", "")).exists():
    cfg["jlink"] = shutil.which("JLinkGDBServerCL") or \
        r"C:\Program Files\SEGGER\JLink_V962\JLinkGDBServerCL.exe"
rec = va.record(cfg, output="run.vadb", duration_s=10)
```

### Verifying a pinned RTT address before capturing

```python
syms = va.list_symbols("build-win/zephyr/zephyr.elf", filter="_SEGGER_RTT")
addr = syms["symbol_legend"][0]["address"]
assert addr == int(cfg["rtt-address"], 16), "config pin doesn't match flashed ELF"
```

### CI regression gate (pytest)

```python
@pytest.fixture(scope="session")
def rec(va, tmp_path_factory):
    out = tmp_path_factory.mktemp("trace") / "ci.vadb"
    r = va.record("board.vacfg.json", output=out, duration_s=10)
    assert r.total_events > 0, "empty capture - probe or firmware setup broken"
    assert r.is_clean()
    return r

def test_no_priority_inversions(rec):
    assert rec.inversions()["inversions"] == []

def test_cpu_headroom(rec):
    busiest = rec.task_stats()[0]
    assert busiest["name"] == "idle" or busiest["cpu_percent"] < 80

def test_control_loop_jitter(rec):
    stats = {t["name"]: t for t in rec.task_stats()}
    assert abs(stats["control_tid"]["max_jitter_us"]) < 200
```

### Troubleshooting an empty capture (`total_events == 0`)

1. `viewalyzer-doctor` — is the right executable being used?
2. Wrong or stale tool path in the config (see the dual-boot recipe).
3. Pinned `rtt-address` doesn't match the ELF actually flashed (see above —
   and beware sibling `build/` vs `build-win/` dirs on shared drives).
4. Stale hardware breakpoints left on the target by a previous debug
   session can silently halt it on `no-reset` captures — power-cycle the
   board or clear breakpoints, then retry.
5. Exit code 0 and an existing `.vadb` do **not** mean data arrived —
   always assert `total_events > 0`.

### Parallelism

No daemon, no shared state: parallel pytest workers are safe as long as
they don't contend for the same debug probe. Serialize capture steps per
probe (e.g. a session-scoped fixture or a lock); queries and direct SQLite
reads can run fully parallel.
