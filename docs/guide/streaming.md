# Live streaming

`stream()` records like `record()` but hands you the samples while the capture runs, from the CLI's `--stream` lines on stderr. The session ends by itself at `duration_s`, or when you call `stop()` (the SDK's `--stop-file`), and `result()` returns the finished `Recording`.

```python
from viewalyzer_sdk import ViewAlyzer

va = ViewAlyzer()
with va.stream("nucleo_g474_rambuf.vacf", output="run.vadb", duration_s=30, elf="firmware.elf") as s:
    for sample in s:                              # StreamSample, in arrival order
        if sample.name == "Sine Wave" and abs(sample.value) > 90:
            print(f"{sample.t_s:.3f} s  {sample.name} = {sample.value}")
            s.stop()                              # finalize now; the file is still a normal recording
            break
    rec = s.result()                              # the Recording once the CLI has written it
print(rec.total_events, rec.is_clean)
```

## StreamSample

| Field | Meaning |
|---|---|
| `id` | the stream's wire id (the firmware's trace id, or the polled symbol's index) |
| `t_us`, `t_s` | microseconds (seconds) since the session's first sample, on the arrival timeline; the recording keeps the exact device-clock times |
| `value` | the sample; this CLI sends every value as a JSON number with a fraction (`98.0`), so it is a `float` |
| `name`, `stream_type` | from the stream's `StreamMeta` once its meta line has arrived (the CLI announces channels before their points) |
| `is_float` | the line's `is_float` flag when a CLI sends one; this CLI does not, so it is `False` |

`s.streams` is the `{id: StreamMeta(id, name, type)}` table so far; `s.init` the `stream_init` line (`schema_version`, `transport`, `started_utc`); `s.log` the CLI's non-JSON progress lines; `s.pid` and `s.returncode` the process.

## Every event kind

A capture streams more than samples on an SWO transport with hardware trace: ITM text, the pin's load, program-counter samples, data-watch values, the exception table. `events()` yields all of them in arrival order, `StreamSample` for `stream_sample` lines and `StreamEvent(t, t_us, data)` for everything else, with `data` the whole parsed line.

```python
with va.stream("nucleo_g474_swo.vacf", output="run.vadb", duration_s=20, elf="firmware.elf",
               extra_flags=["--dwt", "--dwt-exc", "--dwt-pc", "16384",
                            "--dwt-watch", "sig_noise@0x200000E4:4:data-w"]) as s:
    for ev in s.events():
        if isinstance(ev, StreamSample):
            continue
        if ev.t == "itm_text" and ev.data["port"] == 0:
            print("printf:", ev.data["text"].rstrip())
        elif ev.t == "swo_load" and ev.data["share_pct"] > 80:
            print("pin at", ev.data["share_pct"], "% with", ev.data["overflows"], "overflows")
        elif ev.t == "dwt_data":
            for row in ev.data["rows"]:
                print("cmp", row["cmp"], "wrote", row["v"], "at", row["t_us"], "us")
        elif ev.t == "exc":
            print({e["name"]: e["enter"] for e in ev.data["exceptions"]})
```

| `t` | `data` | When |
|---|---|---|
| `stream_init` | `schema_version`, `transport`, `started_utc` | first line |
| `stream_meta` | `id`, `name`, `display` (`type` on poll streams) | as channels are discovered |
| `itm_text` | `port`, `t_us`, `text` | firmware text on an ITM stimulus port (SWO transports) |
| `swo_load` | `bytes_per_s`, `share_pct`, `overflows` | every 500 ms (SWO transports) |
| `pc_samples` | `total`, `sleep`, `pcs[]` | DWT PC samples since the previous line |
| `dwt_data` | `rows[]` of `{cmp, t_us, v, size, w, pc}` | data-watch values, raw, per comparator |
| `exc` | `total`, `max_depth`, `exceptions[]` of `{num, name, enter, exit, return, max_depth}` | cumulative, at most every 200 ms |

Pick one consumer per session: `for sample in s` or `s.events()`, not both (they drain the same queue). Nothing is dropped: a kind a newer CLI adds arrives as a `StreamEvent` too.

## Stopping and results

`stop()` asks the CLI to finalize (the stop file); it returns at once, and the iteration ends when the process exits. `result(timeout_s=None)` waits for the recording (the capture's own timeout plus a grace period by default) and raises `ViewAlyzerError` with the CLI's code if the capture failed (`empty_capture` when nothing arrived). `close()` (or leaving the `with` block) stops a still-running session and reaps the process. There is no end marker on the stream: the CLI's exit is the end.

## Polling variables live

`record_polls()` returns only when it is done; to watch a variable live without firmware instrumentation, stream a `poll` through the CLI directly or graph it in the BKPT Debug extension. A `stream()` with `symbols=` does not poll on this CLI (the capture verb ignores the flag and the SDK warns).
