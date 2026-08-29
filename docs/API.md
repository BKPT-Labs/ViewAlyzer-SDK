# viewalyzer-sdk: API reference

Python SDK for the ViewAlyzer headless CLI (`viewalyzer-cli`, wire-protocol
`schema_version` 2). Everything lives in one import:

```python
from viewalyzer_sdk import (
    ViewAlyzer,                 # the client
    Recording,                  # a handle on one .vadb recording
    StreamSession,              # a live capture: iterate samples or events(), then result()
    StreamSample,               # one live data point
    StreamMeta,                 # one announced stream (id, name, type)
    StreamEvent,                # any other live stream line (t, t_us, data)
    ViewAlyzerError,            # every failure, CLI-side or SDK-side
    ViewAlyzerWarning,          # the SDK's warnings.warn category
    BinaryNotFound,             # subclass: executable couldn't be located
    find_viewalyzer,            # discovery as a standalone function
    SCHEMA_VERSION,             # the CLI wire-protocol version this SDK targets (2)
    SUPPORTED_SCHEMA_VERSIONS,  # every version it understands: (1, 2)
    QUERY_VERBS,                # the query verbs query() accepts
    HWTRACE_ARCHS,              # cores hwtrace_dry_run() plans for
)
```

Design in one sentence: every method is **one CLI invocation** (one process,
one JSON payload, exit) except the direct-SQLite readers on `Recording`,
which open the `.vadb` file itself read-only and involve no subprocess, and
`stream()`, whose one CLI invocation stays alive for the capture and is
wrapped in a `StreamSession`.

The SDK speaks the CLI's `--headless --flag` argv form (`viewalyzer-cli`
keeps it as contract next to its verb form); a verb may still follow it,
which is how `hwtrace_dry_run()` works.

The public API is a frozen contract: releases add, never rename, remove
or change existing signatures or behaviour. See `CHANGELOG.md`.

---

## Locating the executable

Discovery order, first hit wins:

| Step | Source | Notes |
|---|---|---|
| 1 | `VIEWALYZER` env var | Path to the executable. Set-but-wrong **raises** `BinaryNotFound` instead of silently falling through. |
| 2 | `PATH` | Tries `viewalyzer-cli` (the headless engine), then `ViewAlyzer` and `viewalyzer` (the GUI binary forwards `--headless`); `.exe` is implied on Windows. |
| 3 | Standard installs | Windows: `%ProgramFiles%\ViewAlyzer\`, `%LOCALAPPDATA%\Programs\ViewAlyzer\`. macOS: `/Applications` and `~/Applications` (`ViewAlyzer.app`). Linux: `/usr/local/bin/viewalyzer`, `~/.local/bin/viewalyzer`, `/opt/ViewAlyzer` (incl. AppImages). |

```python
find_viewalyzer() -> Path | None
find_viewalyzer_with_source() -> (Path | None, "env" | "path" | "install" | "not found")
```

From a terminal, `viewalyzer-doctor` (or `python -m viewalyzer_sdk`) prints
what was found, runs the version handshake (warning only when the binary's
`schema_version` is outside `SUPPORTED_SCHEMA_VERSIONS`), and then the
app's own health check; exit code 0 means the SDK can talk to the app.

---

## class `ViewAlyzer`

```python
ViewAlyzer(binary=None, *, query_timeout_s=30.0)
```

- `binary`: path to the executable, **or a list** forming an argv prefix
  (how the test suite substitutes a fake CLI:
  `ViewAlyzer([sys.executable, "fake_cli.py"])`). Omit to auto-discover;
  raises `BinaryNotFound` if nothing is found.
- `query_timeout_s`: timeout for query/list calls. Captures compute their
  own timeout from the requested duration (plus ~15 s of headroom).

### Handshake & discovery

| Method | Returns |
|---|---|
| `version()` | `{"schema_version": 2, "app": "ViewAlyzer", "version": "1.2.0", "core": "rust", "edition", "git_sha", "built", "transports": [...], "license": {...}}`. Call once at startup and check `schema_version in SUPPORTED_SCHEMA_VERSIONS`. |
| `doctor(*, jlink_path=None, stlink_path=None, cube_programmer_path=None, arm_gdb_path=None)` | Setup health check: `{"checks": [{"id", "name", "required", "status": "ok"\|"none"\|"missing"\|"free", "detail"?, "path"?, "hint"?}], "app_version", "edition", "license"}`. This CLI's check ids: `probes_stlink`, `probes_jlink`, `probes_cmsis_dap` (`none` = none connected, `detail` lists serials), `serial_ports`, `recordings_dir` (`path`), `probe_rs_targets`, `license` (`free` = free-mode caps apply). A missing optional item is a report entry, not an error. The tool-path keyword arguments are kept for older builds that spawned vendor tools; this CLI accepts and ignores them. |
| `list_probes(*, jlink_path=None, stlink_path=None, cube_programmer_path=None)` | Connected debug probes: `{"probes": [{"type": "stlink"\|"jlink"\|"cmsis-dap", "serial", "description", "vid", "pid"}], "warnings": []}`. With several probes of a kind attached, pin one via the `stlink-serial` / `jlink-serial` config keys or the capture refuses. Tool-path arguments: same note as `doctor()`. |
| `list_ports()` | Serial ports for the `serial` transport: `{"ports": ["COM7", ...]}` (names, to use as the `serial-port` config key). |
| `list_targets(*, filter=None)` | probe-rs target names for `target-device`: `{"filter", "count", "targets": [{"name", "architecture"}]}`. `filter` is a case-insensitive substring; the unfiltered registry has thousands of entries. |
| `analyze_memory(elf, *, map_file=None)` | Static flash/RAM breakdown of a firmware image. With the linker MAP: `has_map_data` and `map` (region capacities with used/free/percent, MAP-placed sections, discarded input sections). |
| `list_symbols(elf, *, filter=None)` | Pollable data symbols (`symbol_legend`: name, address, size, type; plus counts). Also handy to verify a pinned `rtt-address` against `_SEGGER_RTT` in the ELF you actually flashed. |

### Hardware-trace planning

```python
hwtrace_dry_run(*, arch, cpu_clock_hz, swo_freq_hz=2_000_000, itm_port=1,
                caps=None, hardware_trace=None, no_init_swo=False,
                extra_flags=()) -> dict
```

Runs `hwtrace --dry-run` and returns the ordered register image a capture
would program for an ITM / DWT / TPIU setup on that core, with no target
attached: `{"schema", "arch", "cpu_hz", "swo_hz", "writes": [{"reg",
"addr", "value"}], "refused": [{"feature", "reason"}], "applied"}`.
`bkpt_gdbserver --dry-run` prints the same image for the same inputs.

- `arch`: one of `HWTRACE_ARCHS` (`"v6m"`, `"v7m"`, `"v8m"`); anything else
  is `bad_arguments` before the CLI is spawned.
- `cpu_clock_hz` (required; the TPIU prescaler derives from it),
  `swo_freq_hz`, `itm_port`: the capture keys of the same names.
- `caps`: the core's trace capabilities as a dict, a JSON string, or
  `"@file"`: `{"numcomp": 4, "notrcpkt": 0, "nocyccnt": 0, "noprfcnt": 0,
  "itm": 1, "tpiu": 1}`. Default: four comparators, everything present.
- `hardware_trace`: the `hardware-trace` config block (see the recipe
  below) as a dict, a JSON string, an existing file path, or `"@file"`.
- `extra_flags`: flat overrides on top of the block, e.g.
  `["--dwt-watch", "g_temp@0x20000410:4:data-rw:pc"]`.
- Unlike every other verb, the CLI prints this image **bare** (no
  `schema_version` wrapper) and answers a configuration error with exit
  code 2 and `{"error": "<reason>"}`; the SDK raises that as
  `ViewAlyzerError("bad_config", reason)`. A regular envelope (e.g.
  `bad_arguments`) is raised with its own code as usual.

### Licensing

| Method | Notes |
|---|---|
| `get_license()` | Local license state + effective caps (e.g. max recording seconds, `cooldown_remaining_s`). Read-only, never contacts the license server. |
| `activate_license(key)` | Activate this machine with a key (network required). |
| `validate_license()` | Refresh license state against the server (network required). |
| `deactivate_license()` | Release this machine's seat (network required). |

These are the only methods that talk to the license server, and each is an
explicit call: a capture never phones home. Against a ViewAlyzer build without
a license backend they raise `ViewAlyzerError("unsupported")`. Unlicensed installs can still
capture, with a duration cap and a cooldown between captures; a capture
blocked by the cooldown raises `ViewAlyzerError` with
`code == "cooldown_active"` and `retry_after_s` in the CLI's message.

### The recording index

| Method | Notes |
|---|---|
| `list_recordings()` | Raw index payload: `{"recordings": [{recording_id, path, schema_name, duration_us, size_bytes, created_utc}, ...]}`. |
| `recordings()` | Same, as bound `Recording` handles (`info` carries the index fields). |
| `open(path_or_id)` | Handle on an existing recording. A `.vadb` **path** enables the direct-SQLite readers; a 12-hex **id** supports CLI queries only. |
| `delete_recording(rec_or_id)` | **Destructive**: removes the index entry *and deletes the file on disk*. |
| `delete_all_recordings()` | **Destructive**: all of the above, for every indexed recording. |

The index lives in the per-user app-data dir. If your CI manages recording
files itself, ignore the index and pass paths.

### Capture

```python
record(config, *, output, duration_s, elf=None, symbols=(), poll_hz=None,
       extra_flags=(), timeout_s=None) -> Recording
```

- `config`: a `.vacf` path **or an inline dict** with the same keys
  (the CLI flag names without the leading `--`: `transport`,
  `target-device`, `cpu-clock-hz`, `rtt-channel`, probe serials, ...).
  Inline dicts are written to a temp file for the call and cleaned up
  after. Transports: `stlink-swo`, `stlink-rambuf`, `stlink-rtt`,
  `jlink-swo`, `jlink-rtt`, `jlink-rambuf`, `udp`, `serial`, `swo-tcp`.
  Unknown keys are errors (`bad_config`); tool-path keys from older
  configs are accepted and reported as "no effect".
- `elf`: lets the CLI pin the ring's control block (`_VA_RAMBUF`, RAM-buffer
  transports) or `_SEGGER_RTT` (RTT) from the firmware image instead of
  scanning RAM. Loss-free boot captures are deterministic with a pinned
  address.
- `symbols` + `poll_hz`: the symbol watch of older engines. **This CLI does
  not poll symbols during a capture**: its capture verb accepts the flags
  and ignores them (only the poll verb reads them). The SDK still sends
  them (for older builds), emits a `ViewAlyzerWarning`, and the capture
  runs without the watch. Use `record_polls()` for a memory poll, or a
  `--dwt-watch` hardware watch via `extra_flags` on an SWO transport.
  `symbols` still require `elf` (`bad_arguments` otherwise).
- `extra_flags`: appended verbatim for flags without a dedicated
  parameter (`["--dwt", "--dwt-pc", "16384"]`, `["--no-register"]`,
  `["--keep-va"]`, ...). CLI flags win over config-file values.
- `output`: where to write the `.vadb`. The CLI **forces the `.vadb`
  extension**; the returned `Recording.path` is the authoritative on-disk
  file, which may therefore differ from `output`.
- Returns a `Recording` with `recording_id` (parsed from the CLI's stable
  `Recording registered: id=...` line) and `path`.
- On failure raises `ViewAlyzerError("record_failed", ...)` whose message
  contains the CLI's `ERROR:` diagnostics (e.g. *Failed to connect to
  target*), not just an exit code; an error envelope on stdout
  (`cooldown_active`, `empty_capture`, `bad_config`) is raised with its
  own code. A capture that produced a missing or empty file is also a
  failure.

```python
record_polls(elf, symbols, *, duration_s, poll_hz=100,
             config=None, target_device=None,
             extra_flags=(), timeout_s=None) -> Recording
```

Samples target variables over the debug probe with no firmware
instrumentation required. **A debug-probe transport is required in
practice**: pass `config` as a `.vacf` path or a dict with at least
`transport` (`stlink-rambuf`, `stlink-swo`, `stlink-rtt`, `jlink-*`) and
usually `target-device`. A bare `target_device` is not enough for this CLI
(it answers `bad_config`), so the SDK raises `ViewAlyzerError("bad_arguments")`
up front when neither `config` nor a `--transport` in `extra_flags` names a
transport, or when a config dict lacks one. `target_device` still overrides
the config's value. Each symbol is `name[:type]` with type one of
`u8 u16 u32 i8 i16 i32 f32` (default: the symbol's size, signed); unknown
symbols are `symbol_not_found`. The poll summary (`span_seconds`,
`sample_count`, `sample_loss_percent`, `symbols_polled`, `poll_hz`) lands
in `Recording.info["summary"]`. Poll timestamps are wall-clock microseconds
(`meta.poll_clk_hz`), not CPU cycles.

```python
snapshot(config, *, output, elf=None, extra_flags=(), timeout_s=120.0)
    -> Recording
```

Post-mortem snapshot for firmware using the RAM-buffer transport
(`VA_TRANSPORT=RAM_BUFFER`): attach **without reset** (the core is halted
for the dump and resumed), read the firmware's trace ring out of target RAM
through the probe, and save the recovered window as a normal `.vadb`
recording. There is no duration; the target recorded untethered and this
reads out what is in RAM. Works with wrap rings (`VA_RAMBUF_MODE_WRAP` /
`VA_SNAPSHOT=1`: the most recent window, `ring: "post-mortem"`) and drop
rings (single-shot dump from boot until the ring filled, `ring:
"live-drop"`). `config` must name a `stlink-rambuf` or `jlink-rambuf`
transport. Pass `elf` (or a `rambuf-address` config key) to skip the RAM
scan. The snapshot summary (ring kind, events, window bytes, wrap/freeze
state, control block, integrity counters) lands in
`Recording.info["summary"]`. An empty or unparseable ring raises
`ViewAlyzerError("empty_snapshot" | "snapshot_failed", ...)`.

The CLI prints progress lines before the JSON envelope of `poll` and
`snapshot`; the SDK reads the last JSON line of stdout as the payload.

### Live streaming

```python
stream(config, *, output, duration_s, elf=None, symbols=(), poll_hz=None,
       extra_flags=(), timeout_s=None) -> StreamSession
```

The same capture as `record()` (same `config` / `elf` semantics, same
`.vadb` landing on disk, same `symbols` / `poll_hz` caveat), started with
the CLI's `--stream` tap: while the capture runs, the CLI writes one JSON
line per event on stderr and the session delivers them live. Returns
immediately, with the CLI still connecting; `timeout_s` bounds the whole
session (default `duration_s` plus connect/finalize headroom).

```python
class StreamSession   # context manager + iterator, single-consumer
```

- **Iterate** it (`for sample in s`) for `StreamSample` points in arrival
  order, across all streams: firmware user traces (`VA_LogTrace`), Trace
  Domain sampled channels (polled over the debug port when an installed
  domain claims the target), and DWT data watches (`extra_flags=["--dwt",
  "--dwt-watch", "name@0xADDR[:size],..."]` on an SWO transport; the
  hardware comparators, up to 4, deliver **every write**, with your
  `name@` labels as `sample.name`). Other stream lines are consumed
  silently (their side effects on `streams` / `init` still apply).
- `events()`: yields **every** stream line in arrival order,
  `StreamSample` for `stream_sample` lines and `StreamEvent` for
  everything else (table below). Nothing is dropped and nothing is
  diverted to `log`.
- Pick ONE consumer per session: both drain the same queue, so starting
  the other kind while the capture runs raises
  `ViewAlyzerError("bad_arguments")`. Once the feed has ended either
  returns empty.
- Iteration ends when the capture does (duration reached, `stop()`
  honored, or the CLI exited: **this CLI ends the feed by closing stderr,
  it emits no `stream_end` line**); it raises `ViewAlyzerError("timeout")`
  and kills the CLI if the session deadline passes first.
- `stop()`: finalize now, keep everything captured so far. Non-blocking;
  keep iterating (or call `result()`) to see the capture out. Works on all
  three OSes via the CLI's `--stop-file` channel (plus SIGINT on POSIX).
  CLI builds older than `--stop-file` still stop on POSIX; on Windows they
  run to `duration_s`.
- `result(timeout_s=None) -> Recording`: wait for the CLI to exit and
  return the finished recording, with the same success checks and
  `ViewAlyzerError("record_failed", ...)` diagnostics as `record()`.
  Idempotent; call it after the `with` block.
- `close()` (or leaving the `with` block): stops a still-running capture
  and cleans up temp files. Always use the context manager so an abandoned
  session cannot leave a CLI process behind.
- Live views while capturing: `streams` (dict of id to `StreamMeta`; grows
  as channels register), `init` (the `stream_init` banner: `transport`,
  `started_utc`, `schema_version`), `log` (recent non-stream diagnostic
  stderr lines; stream lines never land here), `pid`, `returncode`.

Stream line kinds on a capture, in the words of the CLI:

| `t` | Payload | Delivered as |
|---|---|---|
| `stream_init` | `schema_version`, `transport`, `started_utc` | `StreamEvent` (and `session.init`) |
| `stream_meta` | `id`, `name`, `display` | `StreamEvent` (and `session.streams`) |
| `stream_sample` | `id`, `t_us`, `value` | `StreamSample` |
| `itm_text` | `port`, `t_us`, `text` | `StreamEvent` (`t_us` set) |
| `pc_samples` | `total`, `sleep`, `pcs[]` (a batch since the previous line) | `StreamEvent` |
| `swo_load` | `bytes_per_s`, `share_pct`, `overflows` | `StreamEvent` |
| `dwt_data` | `rows: [{cmp, t_us, v, size, w, pc}]` | `StreamEvent` |
| `exc` | `total`, `max_depth`, `exceptions: [{num, name, enter, exit, return, max_depth}]` (cumulative, at most every 200 ms) | `StreamEvent` |
| `stream_end` | | `StreamEvent`, then the feed ends (older builds only) |

Poll streams (`viewalyzer-cli poll --stream`, not exposed by the SDK yet)
carry `source: "poll"` + `poll_hz` in `stream_init` and a `type` key on
`stream_meta`. Task/scheduler slices and live CPU load are not streamed;
open the recording in the ViewAlyzer app or query the finished
`Recording` for those.

```python
@dataclass(frozen=True)
class StreamMeta:     # id: int, name: str, type: str
@dataclass(frozen=True)
class StreamSample:   # id, t_us, value, is_float, name, stream_type; t_s property
@dataclass(frozen=True)
class StreamEvent:    # t: str, t_us: int | None, data: dict; t_s property (None without a time)
```

`StreamSample.t_us` is microseconds on the **arrival timeline** (t=0 at
the session's first sample), sized for plotting; the recording on disk
keeps exact device-clock timestamps, so run analysis on the `Recording`,
not on streamed samples. `name` / `stream_type` resolve from the stream's
meta line and can be `None` for a point that outran its registration.
`StreamMeta.type` comes from the line's `type` key when present (poll
streams), else its `display` key (captures: `graph`, `counter`, `gauge`,
`bar`, `toggle`, `histogram`, `table`, `angle`, `register`, `task`,
`isr`), else `graph`. This CLI sends every `value` as a JSON float and no
`is_float` flag, so `is_float` is False on it; older builds set it for
f32 traces. `StreamEvent.data` is the whole parsed line (including `t`).

### Generic query escape hatch

```python
query(verb, recording, *, tier=None, budget=None,
      t_start_us=None, t_end_us=None, bucket_us=None,
      max_slices=None, sql=None, elf=None, extra_flags=(),
      timeout_s=None, kinds=None, channels=None, threshold_us=None) -> dict
```

Prefer the named helpers on `Recording`; this exists for flag combinations
they don't cover. `kinds` (a string or a sequence, sent as `--kinds a,b`)
filters `events` / `events-all` by event kind; `channels` (`--channels`)
filters `user-traces` / `user-traces-all` by channel name or code;
`threshold_us` (`--threshold-us`) is the lateness a `timers` fire must
exceed to count as a violation (default 500). Valid verbs (`QUERY_VERBS`):

| Verbs | Tiers |
|---|---|
| `timeline`, `events`, `user-traces` | `summary` \| `bucketed` \| `raw` |
| `timers` | `summary` \| `raw` |
| `etm` | `summary` \| `raw` on an ETM-capable engine; **this CLI answers `etm_not_present`** |
| `summary`, `inversions`, `cpu`, `comms`, `series`, `sql`, `fingerprint`, `compare`, `verdicts` | none |
| `profile`, `itm-console`, `dwt-data`, `dwt-exc`, `dwt-counters`, `swo-load` | none (hardware-trace queries; `no_hw_trace` on a recording without the rows) |
| `slices`, `slice-details`, `events-all`, `user-traces-all` | none (unbounded variants for consumers that manage their own payload sizes: `max_slices`, `kinds`, `channels`, optional window) |

`recording` may be a `Recording`, an id, or a path.

---

## class `Recording`

Handles come from `record()`, `record_polls()`, `snapshot()`,
`recordings()`, or `open()`. Attributes: `recording_id`, `path`, `info`
(extra fields the CLI reported), `ref` (what gets passed as `--recording`:
the id when known, else the path).

### CLI queries (subprocess, size-bounded JSON)

| Method | What you get |
|---|---|
| `report()` | The CLI's `summary` query: whole-recording scalars the engine computes on load (duration, events by class, context switches, preemptions, CPU load, channels, integrity counters), under `["data"]`. Distinct from the SQLite `summary()` below. |
| `timeline(tier="summary", **window)` | Scheduler/CPU analytics: per-lane cards with CPU %, slice count and percentiles, preemptions, observed period, jitter, `anomalies[]`. `bucketed` = per-lane CPU % over time buckets; `raw` = individual slices in a window. Lanes are `T1`, `T2`, ... per the response's `task_legend`. |
| `events(tier="summary", **window)` | Unified event stream: counts `by_kind`, `top_objects`; bucketed/raw variants. `kinds=` filters. |
| `user_traces(tier="summary", **window)` | Data channels (firmware traces, sampled channels, poll samples): per-channel count/min/max/mean/last/rms/std_dev/rate_hz. `channels=` filters by name or code (`C1`, ... in `trace_legend`). |
| `timers(tier="summary", *, elf=None)` | Per-timer lateness stats (phase-corrected mean/p99/max), violations vs `threshold_us` (500), lateness histogram, work-queue lanes. Predicted lanes are marked, never mixed into measured stats. `raw` adds per-fire records with violation causes plus arm/stop and work marks. |
| `etm(tier="summary", *, budget=None)` | ETM call-tree profile on an ETM-capable engine; **this CLI raises `etm_not_present`** (the PC-sample profile is `profile()`). |
| `cpu(*, t_start_us=None, t_end_us=None, budget=None)` | The CPU panel's scheduler statistics, number-for-number with the GUI: busy-union load, peak/min window load, context switches, preemptions, per-task exec percentiles, stack usage, findings. |
| `comms(*, t_start_us=None, t_end_us=None, bucket_us=None)` | Communication paths (producer -> via -> consumer): rate, median/p99/max latency, blocked counts, per-resource totals. `bucket_us` adds backlog-depth series. |
| `series(kind, *, task=None, metric=None, from_=None, to=None, bucket_us=None)` | One timeline series as `[[t_us, value], ...]`. Kinds: `cpu-load`, `stack`, `heap`, `event-rate`, `task-timing` (needs `task`, optional `metric="exec"\|"period"`), `interval` (needs `from_`/`to` as `"task:<n>"`, `"trace:<n>"`, or `"resource:<n>"`). |
| `inversions()` | Every mutex contention with its `type` (`inversion` when the waiter outranks the holder, RTOS-aware), waiter/holder names and priorities, timing. Top-level `"inversions"` list. |
| `verdicts()` | Trace Domain verdicts: `{"count", "verdicts": [{id, name, severity, t_start_us, t_end_us, peak, explain, evidence[]}]}`. Empty when no installed domain claims a channel. |
| `sql(statement, *, budget=None)` | One read-only SQL statement executed by the CLI: `{"columns", "rows", "row_count", "truncated"}`. |
| `sql_rows(statement, *, budget=None)` | Same, rows as dicts keyed by column name. |

Window kwargs for `bucketed`/`raw` tiers: `t_start_us`, `t_end_us`
(microseconds since recording start; required), `bucket_us` (bucketed
only), `budget` (`"low" | "med" | "high"`, default med), plus `kinds` /
`channels` where the verb takes them.

### Hardware-trace queries (SWO captures with `--dwt`)

Each raises `ViewAlyzerError("no_hw_trace")` on a recording that holds
none of the rows it reads; the message names the capture flags that
produce them. Results sit under `["data"]`.

| Method | What you get |
|---|---|
| `profile(*, elf=None, budget=None)` | PC-sample hotspots (DWT PC sampling over SWO, or `DWT_PCSR` polling on rambuf/RTT): `total_samples`, `sleep_samples`, `span_s`, `sample_rate_hz`, `hotspots[]`, `source` (`dwt-swo` \| `pcsr-poll`), `interval_cycles`. `elf` symbolicates (function names, file:line with DWARF); `budget` caps the hotspot count (16/32/64). |
| `itm_console(*, port=None)` | ITM stimulus-port console text: `ports[]` of `{port, lines: [{t_us, text, partial?}], bytes, lines_truncated?}`, lines split on newline with the time of their first byte. `port` keeps one stimulus port: sent as `--port N` (newer builds filter server-side) and applied to the returned `ports` too, so older builds behave the same. |
| `dwt_data(*, elf=None)` | Data-watch comparators: `watches[]` of `{cmp, name, address, function, count, first: [{t_us, value, rw, pc?}]}` (up to 256 samples each). `elf` symbolicates the access PCs of `:pc` watches. |
| `dwt_exc()` | Exception trace: `exceptions[]` of `{num, name, enter, exit, return, max_depth}` plus `events[]` `{t_us, num, func}` (up to 4096). |
| `dwt_counters()` | Event counters: `counters.{cpi,exc,sleep,lsu,fold,cyc}` = `{wraps, cycles, rate_per_s}`, `span_s`, `cpu_load_pct` (from the sleep counter, `None` without it). |
| `swo_load()` | `{bytes, seconds, bytes_per_s, swo_hz, share_pct, overflows}`; overflows mean the SWO pin was oversubscribed and data was lost on-chip. |

### Golden-run regression testing

| Method | What you get |
|---|---|
| `fingerprint(*, runs=(), sections=None, tolerance_pct=None, warn_pct=None, out=None)` | Distills the recording's summary tables into a small, git-committable `.vafp.json` baseline: per-metric values plus warn/fail thresholds (counts normalized to per-second rates) and capture provenance. `runs` merges extra recordings so the baseline's min/max envelope reflects run-to-run variance. `sections` picks from `summary, tasks, traces, timers, comms, health`. Thresholds are per metric and hand-editable in the file. `out` also writes it to disk. |
| `compare(baseline)` | Compares this recording against a baseline (`.vafp.json` in either engine's dialect, or a `.vadb` fingerprinted on the fly). Per-metric results (pass within the warn band, warn up to the fail band, fail beyond), missing/new items, provenance warnings, and an overall `"verdict"`. **A fail verdict is data, not an exception**: the CLI exits 2 for CI gating, but the SDK returns the payload; check `result["verdict"]`. |

**Response envelope.** Every query payload carries `schema_version`,
`recording_id`, `path`, `query` and `budget`; the analytics verbs put
their results under `data`:

```python
{"schema_version": 2, "recording_id": ..., "path": ..., "query": "timeline",
 "budget": "med", "tier": "summary", "window": {...},
 "data": { ... the actual analytics ... }}
```

so it's `rec.timeline()["data"]["tasks"]`. The SDK deliberately passes
the CLI payload through unmodified: the payload *is* the contract, and
reshaping it here would fork the documentation (the one exception is the
client-side `port` filter of `itm_console()`).

Know which of the three shapes you're holding:

| Call | Shape |
|---|---|
| `report()`, `timeline()`, `timers()`, `cpu()`, `comms()`, `series()`, `profile()`, `itm_console()`, `dwt_data()`, `dwt_exc()`, `dwt_counters()`, `swo_load()` | Enveloped: results under `["data"]`. |
| `events()`, `user_traces()`, `inversions()`, `verdicts()`, `sql()`, `fingerprint()`, `compare()` | Top-level keys, no `data` wrapper (e.g. `["by_kind"]`, `["traces"]`, `["inversions"]`, `["verdicts"]`, `["columns"]`/`["rows"]`, `["verdict"]`). |
| `summary()`, `task_stats()`, `meta()`, `total_events` | Plain Python values (dicts / ints) read straight from SQLite, no CLI payload at all. |

**Size bounds.** If a response would blow the tier's budget you get
`ViewAlyzerError` with `code == "window_too_wide"`; its `.suggestion` dict
is the CLI's machine-readable advice (narrow the window / raise the
budget / filter with `kinds`). Follow it rather than retrying blindly.

### Direct SQLite reads (no subprocess; needs `path`)

A `.vadb` is a standard SQLite database; these readers open it read-only.

| Member | What you get |
|---|---|
| `summary()` | `va_summary` as a typed dict: `total_events`, `cpu_load_percent`, `context_switches`, `span_seconds`, `corrupt_bytes`, ... (The engine-computed equivalent through the CLI is `report()`.) |
| `total_events` (property) | Int from `va_summary`. **Assert on this, not on file existence**: a capture can "succeed" with 0 events when the probe/firmware setup is broken. |
| `has_sequence_info` (property) | True when the recorder stamped packets with sequence numbers (wire protocol v3). Only then can loss be *proven*. |
| `lost_events` (property) | Exact count of emitted packets that never arrived, from the v3 sequence counter. 0 without sequence info means "unknown", not "verified zero". |
| `seq_gaps` (property) | Number of distinct loss bursts behind `lost_events`. |
| `is_clean` (property) | `corrupt_bytes == 0` **and** `lost_events == 0`: every emitted event made it into the recording. (Was a method through 1.0.0; the call form still works with a `DeprecationWarning`.) |
| `task_stats(include_synthetic=False)` | `va_task_stats` rows as dicts, highest CPU first. Synthetic lanes (`_RTOS_`, `ISR:*`, `Fn:*`) filtered unless requested. Columns include `cpu_percent`, `run_count`, `avg_period_us`, `min/max_jitter_us`, `priority`, `stack_used_bytes`, `mutex_contentions`, ... |
| `meta()` | Provenance: `va_cpu_hz`, `va_os`, `capture_source`, `hardware_trace` (SWO captures: the applied image, `refused[]`, `swo_load`), ... |
| `connect()` | A read-only `sqlite3.Connection` for anything else (full schema: `va_events`, `va_objects`, `va_trace_stats`, `health`, `raw_log`, `va_signal_blocks`). Caller closes it. |

Units: query-layer times are **microseconds** since recording start. Raw
`va_events.t_cycles` are CPU cycles (`seconds = t_cycles / meta.va_cpu_hz`),
except `poll_trace` rows, which are microseconds. Hardware trace rides
`va_events` too: kinds `dwt_pc`, `dwt_exc`, `dwt_data`, `dwt_data_pc`,
`dwt_counters`, `itm_text`.

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
| `binary_missing` | SDK | Executable not found / `VIEWALYZER` points nowhere. |
| `timeout` | SDK | Call exceeded its timeout. |
| `bad_output` | SDK | stdout carried no JSON object. |
| `record_failed` | SDK | Capture failed; message carries the CLI's `ERROR:` lines. |
| `file_not_found` | SDK | An input path (ELF, config, recording, baseline) doesn't exist. |
| `bad_arguments` | both | Invalid flag/parameter combination (SDK: unknown verb or arch, symbols without ELF, `record_polls` without a transport). |
| `bad_config` | both | Connection config rejected (unknown key, missing transport, poll on a non-probe transport); SDK: an `hwtrace_dry_run()` configuration error. |
| `no_such_recording` | CLI | Unknown recording id/path. |
| `bad_recording` | CLI | The file is not a recording the engine can load. |
| `window_too_wide` | CLI | Response over budget; follow `.suggestion`. |
| `bad_sql` | CLI | Rejected statement (writes, PRAGMAs, multi-statement, syntax). |
| `no_hw_trace` | CLI | A hardware-trace query on a recording without those rows; the message names the capture flags. |
| `etm_not_present` | CLI | `etm` on this CLI (no ETM capture). |
| `empty_capture` | CLI | The capture ended with 0 events; no file is left behind. |
| `capture_failed` | CLI | Transport or probe failure during a capture. |
| `cooldown_active` | CLI | Free-mode capture cooldown still counting down (`retry_after_s`). |
| `empty_snapshot`, `snapshot_failed` | CLI | The RAM ring parsed to 0 events / could not be read. |
| `symbol_not_found` | CLI | A `record_polls` symbol is not in the ELF. |
| `internal` | CLI | CLI-side failure. |

### Warnings

`ViewAlyzerWarning` (a `UserWarning` subclass) is the category of the
SDK's `warnings.warn` calls: a request the CLI accepts but does not act
on, today `symbols=` / `poll_hz=` on `record()` / `stream()`. Silence or
escalate it with the standard machinery, e.g.
`warnings.simplefilter("error", ViewAlyzerWarning)` in a strict CI job.

---

## Recipes

### Portable configs across machines

Committed `.vacf` files travel across machines and OSes: the native probe
drivers need no tool paths. The machine-specific keys are probe serials
(`stlink-serial`, `jlink-serial`) and serial-port names; don't edit the
shared file, merge over it in memory:

```python
cfg = json.loads(Path("board.vacf").read_text())
probes = va.list_probes()["probes"]
cfg["stlink-serial"] = next(p["serial"] for p in probes if p["type"] == "stlink")
rec = va.record(cfg, output="run.vadb", duration_s=10)
```

### Pinning hardware trace per board (`hardware-trace` block)

SWO transports carry the full DWT stream; rambuf and RTT transports sample
the PC by polling `DWT_PCSR` over the debug port (`--dwt --dwt-pc N`). The
nested `hardware-trace` block holds the whole ITM / DWT / trace-port
setup, so a board's `.vacf` carries it and the same dict works for
`record()`, `stream()` and `hwtrace_dry_run()`:

```python
cfg = json.loads(Path("board.vacf").read_text())
cfg["hardware-trace"] = {
    "itm": {"ports": 3, "privilege": 0, "timestamps": 1},   # ITM_TER mask, ITM_TPR, TSPrescale 0|1|4|16|64
    "dwt": {"enable": True, "exception-trace": True, "pc-sample-cyc": 16384,
            "counters": ["cpi", "exc", "sleep", "lsu", "fold"],   # cyc is refused while PC sampling is on
            "watch": [{"addr": "0x20000410", "size": 4, "function": "data-rw",
                       "pc": True, "name": "g_temp"}]},
    "trace-port": {"swo-hz": 2_000_000, "protocol": "nrz"},
}
img = va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=cfg["cpu-clock-hz"],
                         hardware_trace=cfg["hardware-trace"])
assert not img["refused"], img["refused"]                    # what the core lacks, with the reason
rec = va.record(cfg, output="run.vadb", duration_s=10)       # DWT / ITM rows land in the .vadb
rec.profile(elf="firmware.elf"); rec.dwt_data(); rec.itm_console(port=0)
```

The flat keys still work as `extra_flags` and win over the file: `--dwt`,
`--dwt-exc`, `--dwt-pc N`, `--dwt-watch [name@]0xADDR[:size][:data-rw|data-r|data-w|pc|address][:pc]`
(up to 4), `--dwt-counters cpi,exc,sleep,lsu,fold,cyc`, `--itm-ports`,
`--itm-privilege`, `--itm-timestamps off|1|4|16|64`, `--dwt-path auto|poll|swo`,
and `--hardware-trace <json|@file>` for the whole block. What the core
lacks is refused with the reason in the capture log
(`[hwtrace] ... not enabled: ...`) and in `meta()["hardware_trace"]`
(`refused[]`), never silently. See the CLI reference (`docs/CLI.md` in the
ViewAlyzer repository), "Hardware trace (ITM / DWT)", for every key.

**Not implemented by this CLI:** the on-chip ETM capture profile of
earlier releases (`"chip": "auto"` / `--onchip-scan list` tokens and the
`"etr-window": {"base", "size"}` ETR RAM window documented in 1.0.1). This
CLI validates no ETR window and `rec.etm()` answers `etm_not_present`;
the block above is the complete supported set.

### Verifying a pinned RTT address before capturing

```python
syms = va.list_symbols("build/zephyr/zephyr.elf", filter="_SEGGER_RTT")
addr = syms["symbol_legend"][0]["address"]
assert addr == int(cfg["rtt-address"], 16), "config pin doesn't match flashed ELF"
```

(Or pass `elf=` to `record()` and let the CLI pin it from the image.)

### CI regression gate (pytest)

```python
@pytest.fixture(scope="session")
def rec(va, tmp_path_factory):
    out = tmp_path_factory.mktemp("trace") / "ci.vadb"
    r = va.record("board.vacf", output=out, duration_s=10)
    assert r.total_events > 0, "empty capture - probe or firmware setup broken"
    assert r.is_clean
    return r

def test_no_priority_inversions(rec):
    assert rec.inversions()["inversions"] == []

def test_cpu_headroom(rec):
    busiest = rec.task_stats()[0]
    assert busiest["name"] == "idle" or busiest["cpu_percent"] < 80

def test_against_golden_run(rec):
    result = rec.compare("golden.vafp.json")
    assert result["verdict"] != "fail", result["results"]

def test_no_domain_verdicts(rec):
    assert rec.verdicts()["count"] == 0
```

### Troubleshooting an empty capture (`total_events == 0`)

1. `viewalyzer-doctor`: is the right executable being used, is the probe
   listed under `probes_stlink` / `probes_jlink`, and is the license in
   the mode you expect (free mode caps captures at 5 s)?
2. Wrong probe serial or serial-port name in the config (see the
   portable-config recipe; `list_probes()` / `list_ports()` tell you what
   is attached).
3. Pinned `rtt-address` doesn't match the ELF actually flashed (see above;
   beware sibling build directories holding a stale ELF).
4. Stale hardware breakpoints left on the target by a previous debug
   session can silently halt it on `no-reset` captures. Power-cycle the
   board or clear breakpoints, then retry.
5. Exit code 0 and an existing `.vadb` do **not** mean data arrived;
   always assert `total_events > 0`. (This CLI refuses to write a 0-event
   capture at all: `empty_capture`.)

### Parallelism

No daemon, no shared state: parallel pytest workers are safe as long as
they don't contend for the same debug probe. Serialize capture steps per
probe (e.g. a session-scoped fixture or a lock); queries and direct SQLite
reads can run fully parallel.
