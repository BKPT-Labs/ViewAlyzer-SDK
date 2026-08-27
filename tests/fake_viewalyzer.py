"""A stand-in for the ViewAlyzer binary, faithful to the CLI contract:
a command verb first, one JSON object on stdout for query modes, error
envelopes with non-zero exit, `[headless]` progress lines for captures,
and the forced `.vadb` output extension. Driven via `sys.executable` so the
suite behaves identically on Windows, macOS, and Linux."""
import json
import sqlite3
import sys

QUERY_VERBS = {
    "timeline", "events", "user-traces", "timers", "etm",
    "inversions", "cpu", "comms", "series", "sql",
    "fingerprint", "compare",
    "slices", "slice-details", "events-all", "user-traces-all",
}

VERBS = {"version", "doctor", "recordings", "probes", "symbols", "memory",
         "query", "snapshot", "poll", "capture"}


def make_vadb(path, total_events=1234, seq_present=True, lost_events=0, seq_gaps=0):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE va_summary (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE va_task_stats (name TEXT, cpu_percent REAL, run_count INTEGER);
        """
    )
    con.executemany(
        "INSERT INTO meta VALUES (?, ?)",
        [
            ("format", "va-recording"),
            ("va_cpu_hz", "170000000"),
            ("va_os", "Zephyr"),
            ("has_va", "1"),
        ],
    )
    rows = [
        ("total_events", str(total_events)),
        ("cpu_load_percent", "33.9"),
        ("span_seconds", "8.93"),
        ("corrupt_bytes", "0"),
        ("seq_present", "1" if seq_present else "0"),
    ]
    if seq_present:
        rows += [("lost_events", str(lost_events)), ("seq_gaps", str(seq_gaps))]
    con.executemany("INSERT INTO va_summary VALUES (?, ?)", rows)
    con.executemany(
        "INSERT INTO va_task_stats VALUES (?, ?, ?)",
        [
            ("sensor_tid", 9.0, 800),
            ("idle", 66.1, 900),
            ("_RTOS_", 9.8, 100),
            ("ISR:SysTick", 1.2, 5000),
        ],
    )
    con.commit()
    con.close()


def val(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def emit(obj, code=0):
    print(json.dumps(obj))
    return code


def handle_query(a):
    verb = a[0] if a else None
    if verb not in QUERY_VERBS:
        return emit({"error": "bad_arguments", "message": "unknown query verb"}, 1)
    if val(a, "--recording") is None:
        return emit({"error": "bad_arguments", "message": "--recording required"}, 1)
    if verb == "sql":
        sql = val(a, "--sql") or ""
        if "boom" in sql:
            return emit({"error": "bad_sql", "message": "no such table: boom"}, 1)
        return emit(
            {
                "schema_version": 1,
                "query": "sql",
                "columns": ["name", "cpu"],
                "rows": [["idle", 66.1], ["sensor_tid", 9.0]],
                "row_count": 2,
                "truncated": False,
            }
        )
    if verb == "series":
        kind = val(a, "--kind")
        if kind is None:
            return emit({"error": "bad_arguments", "message": "--kind required"}, 1)
        if kind == "task-timing" and val(a, "--task") is None:
            return emit({"error": "bad_arguments", "message": "--task required"}, 1)
        if kind == "interval" and (val(a, "--from") is None or val(a, "--to") is None):
            return emit({"error": "bad_arguments", "message": "--from/--to required"}, 1)
        return emit(
            {"schema_version": 1, "query": "series", "kind": kind,
             "points": [[0, 1.0], [500000, 2.0]]}
        )
    if verb == "fingerprint":
        payload = {
            "schema_version": 1,
            "query": "fingerprint",
            "sections": (val(a, "--sections") or "summary,tasks").split(","),
            "runs_merged": len((val(a, "--runs") or "").split(",")) if val(a, "--runs") else 1,
            "metrics": {"summary.event_rate_hz": {"value": 138.2}},
        }
        out = val(a, "--out")
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            payload["out"] = out
        return emit(payload)
    if verb == "compare":
        baseline = val(a, "--baseline")
        if baseline is None:
            return emit({"error": "bad_arguments", "message": "--baseline required"}, 1)
        if "failing" in baseline:
            # A fail verdict is a valid payload with exit code 2, NOT an
            # error envelope - the CI-gate contract.
            return emit(
                {"schema_version": 1, "query": "compare", "verdict": "fail",
                 "results": [{"metric": "summary.event_rate_hz", "status": "fail"}]},
                2,
            )
        return emit(
            {"schema_version": 1, "query": "compare", "verdict": "pass", "results": []}
        )
    if verb == "timeline" and val(a, "--tier") == "bucketed":
        if val(a, "--bucket-us") is None:
            return emit({"error": "bad_arguments", "message": "--bucket-us required"}, 1)
    return emit(
        {
            "schema_version": 1,
            "query": verb,
            "tier": val(a, "--tier"),
            "budget": val(a, "--budget") or "med",
            "elf": val(a, "--elf"),
            "inversions": [] if verb == "inversions" else None,
        }
    )


def stream_capture(a):
    """The --stream contract on stderr: stream_init, stream_meta per
    stream (one late, like a firmware trace whose setup packet arrives
    mid-capture), stream_sample lines interleaved with plain diagnostics,
    stream_end last. Honors --stop-file and SIGINT like the real CLI."""
    import os
    import signal
    import time

    sig = {"stop": False}
    try:
        signal.signal(signal.SIGINT, lambda *_: sig.__setitem__("stop", True))
    except ValueError:  # pragma: no cover - non-main thread
        pass

    def jline(obj):
        print(json.dumps(obj), file=sys.stderr, flush=True)

    duration = float(val(a, "--duration"))
    stop_path = val(a, "--stop-file")
    jline({"t": "stream_init", "streams": [], "duration_s": duration,
           "poll_hz": 0})
    jline({"t": "stream_meta", "id": 3, "name": "Beat", "type": "counter"})
    t0 = time.monotonic()
    i = 0
    stopped = False
    while time.monotonic() - t0 < duration:
        if sig["stop"] or (stop_path and os.path.exists(stop_path)):
            stopped = True
            break
        jline({"t": "stream_sample", "id": 3, "t_us": i * 10000, "value": i})
        if i == 2:  # late registration, as firmware setup packets arrive
            jline({"t": "stream_meta", "id": 4, "name": "Load",
                   "type": "graph"})
        if i >= 3:
            jline({"t": "stream_sample", "id": 4, "t_us": i * 10000,
                   "value": i * 0.5, "is_float": True})
        print("[headless] draining ring buffer...", file=sys.stderr,
              flush=True)
        i += 1
        time.sleep(0.02)
    if stopped:
        print("[headless] Stop requested at %.1f s. Finalizing partial "
              "recording..." % (time.monotonic() - t0), flush=True)
    jline({"t": "stream_end"})


def handle_capture(a):
    if "--config" not in a or "--output" not in a or "--duration" not in a:
        return emit({"error": "bad_arguments",
                     "message": "capture needs --config, --output and --duration"}, 1)
    with open(val(a, "--config"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("transport") == "fail":
        print("[headless] Connecting...")
        print("[headless] ERROR: Failed to connect to target")
        return 1
    if cfg.get("transport") == "hang":
        # A wedged probe: no stream lines, no stop-file checks, no exit.
        import time
        print("[headless] Connecting...", flush=True)
        time.sleep(120)
        return 1
    if cfg.get("transport") == "cooldown":
        # Capture mode mixes progress lines with the error envelope.
        print("[headless] Free mode max capture applied.")
        print(json.dumps({"schema_version": 1, "error": "cooldown_active",
                          "message": "wait 42 s", "retry_after_s": 42}))
        print("[headless] ERROR: Capture cooldown active (42 s remaining).",
              file=sys.stderr)
        return 1
    if "--symbols" in a and "--elf" not in a:
        print("[headless] ERROR: --symbols requires --elf")
        return 1
    out = val(a, "--output")
    if not out.endswith(".vadb"):  # the real binary forces the extension
        out = out.rsplit(".", 1)[0] + ".vadb"
    if "--stream" in a:
        stream_capture(a)
    make_vadb(out)
    print("[headless] Recording...")
    if "--symbols" in a:
        print("[headless] Activating MemoryPoller with %d symbol(s)"
              % len(val(a, "--symbols").split(",")))
    print("[headless] Recording saved: %s (12 KB)" % out)
    print("[headless] Recording registered: id=abcdef123456")
    return 0


def handle_snapshot(a):
    with open(val(a, "--config"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("transport") not in ("stlink-rambuf", "jlink-rambuf"):
        return emit(
            {"error": "bad_arguments",
             "message": "snapshot needs a rambuf transport"}, 1
        )
    if cfg.get("rambuf-address") == "0xEMPTY":
        return emit(
            {"error": "empty_snapshot",
             "message": "The snapshot window parsed to 0 events"}, 1
        )
    out = val(a, "--output")
    if not out.endswith(".vadb"):
        out = out.rsplit(".", 1)[0] + ".vadb"
    make_vadb(out, total_events=777)
    print("[headless] Snapshot saved: %s" % out, file=sys.stderr)
    return emit(
        {
            "schema_version": 1,
            "recording_id": "5aa9d4114f00",
            "path": out,
            "summary": {"ring": "post-mortem", "events": 777,
                        "window_bytes": 4096, "wrapped": True, "frozen": True},
        }
    )


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] not in VERBS:
        # Without a command the real GUI binary opens a window.
        print("GUI mode launched (this should never happen in tests)", file=sys.stderr)
        return 3
    verb, a = argv[0], argv[1:]

    if verb == "version":
        return emit({"schema_version": 1, "app": "ViewAlyzer", "version": "9.9.9"})

    if verb == "doctor":
        return emit(
            {
                "schema_version": 1,
                "checks": [
                    {"id": "stlink_probes", "name": "stlink probes", "required": False,
                     "status": "ok", "detail": "1 found"},
                    {"id": "jlink_probes", "name": "jlink probes", "required": False,
                     "status": "none", "detail": "none found",
                     "hint": "Plug in a J-Link or check its USB driver"},
                ],
            }
        )

    if verb == "recordings":
        if "--delete-recording" in a:
            which = val(a, "--delete-recording")
            if which == "nope00000000":
                return emit({"error": "no_such_recording", "message": "unknown id"}, 1)
            return emit({"schema_version": 1, "deleted": which})
        if "--delete-all-recordings" in a:
            return emit({"schema_version": 1, "deleted": "all"})
        return emit(
            {
                "schema_version": 1,
                "recordings": [
                    {
                        "recording_id": "f76593b93473",
                        "path": "/data/run1.vadb",
                        "schema_name": "Zephyr",
                        "duration_us": 8937186,
                        "size_bytes": 5709824,
                        "created_utc": "2026-07-08T05:42:41Z",
                    }
                ],
            }
        )

    if verb == "probes":
        return emit(
            {
                "schema_version": 1,
                "probes": [
                    {"type": "jlink", "serial": "1260001884",
                     "description": "J-Link OB"},
                ],
                "warnings": ["ST-LINK enumeration unavailable"],
            }
        )

    if verb == "symbols":
        return emit(
            {
                "schema_version": 1,
                "elf": val(a, "--elf"),
                "filter": val(a, "--filter") or "",
                "symbol_legend": [
                    {"name": "tick_counter", "address": "0x20000010", "size": 4,
                     "type": "u32"}
                ],
                "data_symbols": 1,
                "text_symbols": 0,
            }
        )

    if verb == "memory":
        return emit({"schema_version": 1, "elf": val(a, "--elf"), "text": 42572,
                     "data": 3002, "bss": 63014, "total": 108588})

    if verb == "query":
        return handle_query(a)

    if verb == "snapshot":
        return handle_snapshot(a)

    if verb == "poll":
        out = val(a, "--elf") + ".poll.vadb"
        make_vadb(out, total_events=42)
        return emit(
            {
                "schema_version": 1,
                "recording_id": "a718d4114f55",
                "path": out,
                "summary": {"sample_count": 42, "symbols_polled": 1},
            }
        )

    return handle_capture(a)


if __name__ == "__main__":
    sys.exit(main())
