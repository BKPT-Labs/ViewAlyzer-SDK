# SDK API reference

Everything exported by `viewalyzer_sdk`. Every method that calls the CLI returns the CLI's JSON envelope as a `dict` (keys as the [CLI queries page](../viewalyzer-cli/queries.html) lists them), raises `ViewAlyzerError` on an error envelope, and prefixes the argv with `--headless` (the flag form every `viewalyzer-cli` build accepts).

## ViewAlyzer

```python
class ViewAlyzer(binary=None, *, query_timeout_s=30.0)
```

`binary` is a path to `viewalyzer-cli` (or the app binary), an argv prefix (a list, for a wrapper), or `None` to discover one (`VIEWALYZER` env, `PATH`, install locations; `BinaryNotFound` when nothing is found). `query_timeout_s` bounds every query call; captures compute their own timeout from the duration.

### Target and environment

| Method | CLI | Returns |
|---|---|---|
| `version()` | `version` | `{schema_version, app, version, core, edition, transports[], license{}}` |
| `doctor(*, jlink_path=None, stlink_path=None, cube_programmer_path=None, arm_gdb_path=None)` | `doctor` | `{app_version, edition, license, checks[]}`; the path keywords are kept for compatibility and have no effect on this CLI |
| `list_probes()` | `probes` | `{probes: [{type, serial, description, vid, pid}], warnings}` |
| `list_ports()` | `ports` | `{ports: ["COM7", ...]}` |
| `list_targets(*, filter=None)` | `targets` | `{filter, count, targets: [{name, architecture}]}` |
| `list_symbols(elf, *, filter=None)` | `symbols` | `{symbol_legend: [{name, address, size, type}], ...}` |
| `analyze_memory(elf, *, map_file=None)` | `memory` | flash and RAM by section; with the map, region capacities |
| `hwtrace_dry_run(*, arch, cpu_clock_hz, swo_freq_hz=2_000_000, itm_port=1, caps=None, hardware_trace=None, no_init_swo=False)` | `hwtrace --dry-run` | the register image `{arch, cpu_hz, swo_hz, writes[], refused[], applied}`; a configuration error raises `ViewAlyzerError("bad_config")` |
| `get_license()`, `activate_license(key)`, `validate_license()`, `deactivate_license()` | `license ...` | `get` is local; the other three are the only calls that go online |

### Recording

| Method | CLI | Returns |
|---|---|---|
| `record(config, *, output, duration_s, elf=None, symbols=(), poll_hz=None, extra_flags=(), timeout_s=None)` | `capture` | a `Recording`. `config` is a `.vacf` path or a dict of connection keys. `extra_flags` passes any CLI flag through (`["--dwt", "--dwt-pc", "16384"]`). `symbols`/`poll_hz` are kept for compatibility: this CLI's capture does not poll symbols and the SDK warns (`ViewAlyzerWarning`); use `record_polls()` |
| `stream(config, *, output, duration_s, ...)` | `capture --stream --stop-file` | a `StreamSession`; see [Live streaming](streaming.md) |
| `record_polls(elf, symbols, *, duration_s, poll_hz=100, config=None, target_device=None, extra_flags=(), timeout_s=None)` | `poll` | a `Recording` of `poll_trace` samples. `symbols` are `name[:type]` entries; `config` must name a debug-probe transport (the CLI refuses a bare `target_device`) |
| `snapshot(config, *, output, elf=None, extra_flags=(), timeout_s=120.0)` | `snapshot` | a `Recording` of the firmware's RAM ring, no reset |
| `open(recording)` | none | a `Recording` handle from a path or a 12-hex id |
| `recordings()`, `list_recordings()` | `recordings` | the index as `Recording` objects, or the raw envelope |
| `delete_recording(recording)`, `delete_all_recordings()` | `recordings --delete-...` | |
| `query(verb, recording, *, tier=None, budget=None, t_start_us=None, t_end_us=None, bucket_us=None, max_slices=None, sql=None, elf=None, kinds=None, channels=None, threshold_us=None, extra_flags=(), timeout_s=None)` | `query <verb>` | the envelope; prefer the named methods on `Recording` |

```python
va = ViewAlyzer()
img = va.hwtrace_dry_run(arch="v7m", cpu_clock_hz=170_000_000, swo_freq_hz=10_000_000,
                         hardware_trace={"dwt": {"enable": True, "exception-trace": True, "pc-sample-cyc": 16384}})
print([w["reg"] for w in img["writes"]], img["refused"])
```

## Recording

```python
class Recording(client, *, recording_id=None, path=None, info=None)
```

Returned by `record()`, `snapshot()`, `record_polls()`, `open()` and `recordings()`. `recording_id` (12 hex, when registered), `path` (a `Path`, when known), `info` (the CLI's summary dict for a fresh capture: `events`, `lost_events`, `duration_us`, `os`, `cpu_hz`, `transport`), `ref` (the id or the path, whatever names it to the CLI).

### Queries (one CLI call each)

| Method | Verb |
|---|---|
| `report()` | `summary` (the whole-recording scalars; distinct from `summary()` below, which reads the SQLite table) |
| `timeline(tier="summary", **window)`, `events(...)`, `user_traces(...)` | the tiered verbs; `window` takes `t_start_us`, `t_end_us`, `bucket_us`, `budget`, `channels`, `kinds` |
| `cpu(*, t_start_us=None, t_end_us=None, budget=None)` | `cpu` |
| `inversions()` | `inversions` |
| `timers(tier="summary", *, elf=None)` | `timers` (`query("timers", rec, threshold_us=...)` for a violation threshold other than 500 us) |
| `comms(*, t_start_us=None, t_end_us=None, bucket_us=None)` | `comms` |
| `series(kind, *, task=None, metric=None, from_=None, to=None, bucket_us=None)` | `series --kind cpu-load\|event-rate\|stack\|heap\|task-timing\|interval` |
| `verdicts()` | `verdicts` |
| `profile(*, elf=None, budget=None)` | `profile`: PC-sample hotspots, symbolicated with `elf` |
| `itm_console(*, port=None)` | `itm-console [--port N]` (also filtered client-side, so an older CLI behaves the same) |
| `dwt_data(*, elf=None)`, `dwt_exc()`, `dwt_counters()`, `swo_load()` | the hardware-trace queries; `ViewAlyzerError("no_hw_trace")` on a recording without it |
| `sql(statement, *, budget=None)`, `sql_rows(statement, *, budget=None)` | `sql`: the envelope, or the rows as dicts keyed by column |
| `fingerprint(*, runs=(), sections=None, tolerance_pct=None, warn_pct=None, out=None)` | `fingerprint` |
| `compare(baseline)` | `compare`: the envelope with `verdict` and `results[]` (a regression is data, not an exception) |
| `etm(tier="summary", *, budget=None)` | `etm`: answers `ViewAlyzerError("etm_not_present")` on this CLI, kept for compatibility |

```python
rec = va.open("run.vadb")
hot = rec.profile(elf="firmware.elf")["data"]["hotspots"][:5]
exc = rec.dwt_exc()["data"]["exceptions"]
text = [l["text"] for p in rec.itm_console(port=0)["data"]["ports"] for l in p["lines"]]
samples = rec.user_traces(tier="raw", t_start_us=1_000_000, t_end_us=1_010_000, channels="Sine Wave")["samples"]
```

### The file itself (no CLI call)

| Member | Returns |
|---|---|
| `connect()` | a read-only `sqlite3.Connection` (`?mode=ro`); use it as a context manager |
| `meta()` | the `meta` table as a dict (`va_cpu_hz`, `va_os`, ...) |
| `summary()` | the `va_summary` table |
| `task_stats(*, include_synthetic=False)` | `va_task_stats` rows |
| `total_events`, `lost_events`, `seq_gaps`, `has_sequence_info` | integrity counters from `health` |
| `is_clean` | `True` when nothing was lost or corrupted: the first assert in any test |

```python
with rec.connect() as db:
    kinds = db.execute("select kind, count(*) from va_events group by kind").fetchall()
```

## Streaming types

`StreamSession`, `StreamMeta(id, name, type)`, `StreamSample(id, t_us, value, is_float, name, stream_type)` with `t_s`, and `StreamEvent(t, t_us, data)` with `t_s`: see [Live streaming](streaming.md).

## Errors and warnings

```python
class ViewAlyzerError(Exception):   # .code, .message, .suggestion, .limits
class BinaryNotFound(ViewAlyzerError)   # code "binary_missing"
class ViewAlyzerWarning(UserWarning)
```

`ViewAlyzerError` is raised for every CLI error envelope, keyed on the `error` field rather than the exit status: `code` is the CLI's token (`bad_config`, `no_such_recording`, `window_too_wide`, `empty_capture`, `cooldown_active`, `no_hw_trace`, ...), `suggestion` and `limits` carry the envelope's hints (`limits["retry_after_s"]` on a cooldown). SDK-side codes: `binary_missing`, `timeout`, `bad_output` (the CLI printed no JSON), `record_failed`, `command_failed`, `file_not_found`, `bad_arguments`.

`ViewAlyzerWarning` is emitted (through `warnings`) for parameters this CLI does not act on, such as `symbols` on `record()`.

## Discovery and constants

| Name | Meaning |
|---|---|
| `find_viewalyzer(env=None)` | the binary path the SDK would use, or `None` |
| `find_viewalyzer_with_source(env=None)` | `(path, "env" \| "path" \| "install" \| "not found")` |
| `ENV_VAR` | `"VIEWALYZER"` |
| `SCHEMA_VERSION`, `SUPPORTED_SCHEMA_VERSIONS` | `2`, `(1, 2)`; `viewalyzer-doctor` warns on a binary outside the tuple |
| `QUERY_VERBS`, `TIERED_VERBS`, `TWO_TIER_VERBS`, `UNTIERED_VERBS` | the verbs `query()` accepts |
| `HWTRACE_ARCHS` | `("v6m", "v7m", "v8m")` for `hwtrace_dry_run` |
| `__version__` | `"1.3.0"` |

The public API is a contract: names, parameters and behaviour are kept across releases and only added to, the same rule the CLI follows.
