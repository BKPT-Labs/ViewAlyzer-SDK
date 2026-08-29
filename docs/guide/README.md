# Python SDK

`viewalyzer-sdk` drives the ViewAlyzer headless CLI from Python: capture a trace, stream it live, query the recording, assert on it in a test. It is a thin, typed layer over `viewalyzer-cli` (every call is one CLI process; every payload is the CLI's JSON envelope), so anything the [CLI section](../viewalyzer-cli/) documents is reachable, and the two never disagree.

```bash
$ pip install viewalyzer-sdk
```

Pure Python, no dependencies beyond the standard library. The package is `viewalyzer_sdk`; version 1.3.0 speaks the CLI's schema 2 (and still reads schema 1).

## Finding the binary

The SDK does not ship the CLI; it finds the one installed with ViewAlyzer or BKPT Debug. In order: the `VIEWALYZER` environment variable (a path to `viewalyzer-cli` or the `viewalyzer` app binary), then `viewalyzer-cli` / `ViewAlyzer` / `viewalyzer` on `PATH`, then the app's install locations. `ViewAlyzer(binary=...)` takes a path or an argv prefix explicitly.

```bash
$ viewalyzer-doctor                       # the SDK's preflight: which binary, its version, probes, ports, license
```

`viewalyzer-doctor` exits 2 when no binary is found and 3 when the environment check fails, so a CI job can gate on it.

## A first capture

```python
from viewalyzer_sdk import ViewAlyzer

va = ViewAlyzer()
print(va.version()["version"], [p["serial"] for p in va.list_probes()["probes"]])

rec = va.record("nucleo_g474_rambuf.vacf", output="run.vadb", duration_s=5, elf="build/rambuf/firmware.elf")
print(rec.recording_id, rec.total_events, "lost", rec.lost_events)

for task in rec.timeline()["tasks"]:
    print(task["name"], task["cpu_percent"], "% p99", task["p99_slice_us"], "us")
```

`config` is a `.vacf` path or an inline dict with the same keys (`transport`, `target-device`, `stlink-serial`, `cpu-clock-hz`, ...), written to a temporary file for the CLI:

```python
rec = va.record(
    {"transport": "stlink-rambuf", "target-device": "STM32G474RE", "stlink-serial": "0033004B3033510735393935"},
    output="run.vadb", duration_s=10, elf="firmware.elf",
)
```

`record()` returns once the file is written; `rec` carries the CLI's summary (`rec.info`), the path and the 12-hex id, query methods that call the CLI, and a read-only SQLite connection for everything else. Failures raise `ViewAlyzerError` with the CLI's error code (`empty_capture`, `bad_config`, `cooldown_active` with `limits`), never a bare exit status.

## What else is here

- [API reference](api.md): `ViewAlyzer`, `Recording`, the errors, discovery, the constants.
- [Live streaming](streaming.md): `stream()` and `StreamSession`, samples and every other event kind, stopping early.
- [Tests and CI](testing.md): pytest fixtures, `is_clean`, fingerprints and `compare`, the SDK's own real-CLI suite.

Free mode applies through the SDK exactly as through the CLI: 5 s captures, a 5 s cooldown (`ViewAlyzerError("cooldown_active")` with `retry_after_s` in `limits`), the first 10 lanes.
