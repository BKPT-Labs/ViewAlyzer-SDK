# Changelog

The public API is additive only: nothing listed here renames, removes, or
changes the behaviour of an existing class, method, parameter, or constant.

## 1.3.0 (2026-08-29)

Catch-up with the native-driver CLI (`viewalyzer-cli`, wire-protocol
schema 2).

- `SCHEMA_VERSION` is 2; new `SUPPORTED_SCHEMA_VERSIONS = (1, 2)`.
  `viewalyzer-doctor` warns only when the binary reports a version outside
  that tuple; the real-CLI contract suite checks membership.
- Query verbs the CLI has and the SDK rejected: `summary`, `verdicts`,
  `profile`, `itm-console`, `dwt-data`, `dwt-exc`, `dwt-counters`,
  `swo-load` (untiered). `ViewAlyzer.query()` gains `kinds=`,
  `channels=`, `threshold_us=`. `etm` stays listed; this CLI answers it
  with `etm_not_present`.
- `Recording` helpers: `report()` (the CLI's `summary` query; the SQLite
  `summary()` is unchanged), `verdicts()`, `profile(elf=, budget=)`,
  `itm_console(port=)` (sent as `--port` and also filtered client-side),
  `dwt_data(elf=)`, `dwt_exc()`, `dwt_counters()`, `swo_load()`.
- `ViewAlyzer.list_ports()`, `list_targets(filter=)`, and
  `hwtrace_dry_run(...)` (bare register image, exit-2 config errors raised
  as `bad_config`). New constant `HWTRACE_ARCHS`.
- Streaming: `StreamEvent(t, t_us, data)` and `StreamSession.events()`,
  which yields every stream line (`stream_init`, `stream_meta`,
  `itm_text`, `pc_samples`, `swo_load`, `dwt_data`, `exc`, ...) alongside
  the samples in arrival order. Plain iteration still yields samples only;
  the two consumers are exclusive per session. `StreamMeta.type` falls
  back to the line's `display` key (captures). `stream_end` is optional:
  the feed ends on EOF.
- `record()` / `stream()`: `symbols=` / `poll_hz=` are not polled during a
  capture by this CLI; the SDK now says so with a `ViewAlyzerWarning`
  (new `UserWarning` subclass) and points at `record_polls()`.
- `record_polls()`: a debug-probe transport is required; `config=` (a
  `.vacf` or a dict with `transport`) is checked up front and a bare
  `target_device` is refused with a clear `bad_arguments`.
- Fix: `Runner.run_json()` takes the last JSON line when the CLI prints
  progress lines before its envelope (`poll`, `snapshot`), so
  `record_polls()` and `snapshot()` work against this CLI instead of
  raising `bad_output`.
- Fix: the direct SQLite readers (`summary()`, `task_stats()`, `meta()`,
  the `total_events` family) close their connection when done; the
  lingering read handle blocked the CLI from overwriting the same `.vadb`
  on Windows. `connect()` still hands the caller an open connection.
- `doctor()` / `list_probes()` document the native-driver check ids and
  that the tool-path keyword arguments have no effect on this CLI.
- Docs: schema 2, the new methods and verbs, the stream event table, the
  symbols-on-capture caveat, the `record_polls` transport requirement, and
  the real `hardware-trace` config block (the ETM/ETR block documented in
  1.0.1 is not implemented by this CLI).

## 1.2.0 (2026-08-26)

- Discovery tries `viewalyzer-cli` on `PATH` before the GUI binary.
- Contract test suite against a real binary (`tests/test_real_cli.py`,
  gated by `VIEWALYZER_SDK_REAL_CLI`; captures need
  `VIEWALYZER_SDK_REAL_CONFIG`).
- `analyze_memory(elf, map_file=)` covers the linker MAP report.
- Stays on the `--headless --flag` argv form (the CLI keeps it as
  contract); the 2.0.0 verb-form rewrite was reverted before release.
- License note: the license methods are the only online calls.

## 1.1.0 (2026-08-20)

- Live trace streaming: `ViewAlyzer.stream()` returns a `StreamSession`
  yielding `StreamSample` points while the capture runs; `stop()` is
  portable via `--stop-file` (plus SIGINT on POSIX); `result()` returns
  the finished `Recording`. `StreamMeta` describes announced streams.
- Documented the streaming scope: data channels only, including DWT
  watches via `--dwt-watch`.

## 1.0.1 (2026-08-16)

- `Recording.is_clean` is a property (the call form still works with a
  `DeprecationWarning`).
- `Recording(path)` raises a friendly `bad_arguments` pointing at
  `ViewAlyzer().open(path)`.
- Docs: the `hardware-trace` `.vacf` block recipe.

## 1.0.0 (2026-08-13)

First PyPI release as `viewalyzer-sdk` (renamed from `viewalyzer-cli`,
moved out of `ViewAlyzer-App/python/`): `ViewAlyzer` client (version,
doctor, licensing, recording index, `record`, `record_polls`, `snapshot`,
generic `query`), `Recording` (tiered CLI queries, series, fingerprint /
compare, direct SQLite reads with `total_events`, `lost_events`,
`seq_gaps`, `is_clean`), discovery, `ViewAlyzerError`, and the
`viewalyzer-doctor` entry point. Trusted-publishing workflow to PyPI.
