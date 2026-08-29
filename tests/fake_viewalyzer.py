"""A stand-in for the ViewAlyzer binary (viewalyzer-cli, schema 2), faithful
to the CLI contract: `--headless` required (a verb may follow it, e.g.
`--headless hwtrace --dry-run`), one JSON object on stdout for query modes,
error envelopes with non-zero exit, `[headless]` progress lines for
captures (poll and snapshot print theirs BEFORE their JSON envelope, as the
real binary does), the forced `.vadb` output extension, and the capture
`--stream` feed with every event kind the native CLI emits. Driven via
`sys.executable` so the suite behaves identically on Windows, macOS, and
Linux."""
import json
import sqlite3
import sys

SV = 2

QUERY_VERBS = {
    "summary", "timeline", "events", "user-traces", "timers",
    "inversions", "cpu", "comms", "series", "sql",
    "fingerprint", "compare", "verdicts",
    "slices", "slice-details", "events-all", "user-traces-all",
    "profile", "itm-console", "dwt-data", "dwt-exc", "dwt-counters", "swo-load",
}
HW_VERBS = {"profile", "itm-console", "dwt-data", "dwt-exc", "dwt-counters", "swo-load"}
#: A recording id the hardware-trace verbs answer with no_hw_trace.
NO_HW_RECORDING = "nohwtrace000"

PROBE_TRANSPORTS = (
    "stlink-swo", "stlink-rambuf", "stlink-rtt",
    "jlink-swo", "jlink-rtt", "jlink-rambuf",
)

TARGETS = [
    ("STM32G474RETx", "v7m"),
    ("STM32G474CEUx", "v7m"),
    ("STM32L031K6Tx", "v6m"),
    ("STM32U575ZITxQ", "v8m"),
    ("nRF52840_xxAA", "v7m"),
]


def make_vadb(path, total_events=1234, seq_present=True, lost_events=0, seq_gaps=0):
    import os

    if os.path.exists(path):  # the real binary overwrites its --output
        os.remove(path)
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


def fail(code, message, exit_code=1):
    """A capture-style failure: the `[headless] ERROR:` line, then the
    envelope, both on stdout (what poll / snapshot / capture do)."""
    print("[headless] ERROR: %s" % message)
    return emit({"schema_version": SV, "error": code, "message": message}, exit_code)


def load_config(a):
    p = val(a, "--config")
    if p is None:
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def vadb_name(out):
    if not out.endswith(".vadb"):  # the real binary forces the extension
        out = out.rsplit(".", 1)[0] + ".vadb"
    return out


LICENSE = {"type": "free", "tier": "free", "licensed": False}


def handle_query(a):
    verb = val(a, "--query")
    ref = val(a, "--recording")
    if ref is None:
        return emit({"schema_version": SV, "error": "bad_arguments",
                     "message": "pass a recording: <file> or --recording <id|path>"}, 1)
    if verb == "etm":
        return emit({"schema_version": SV, "error": "etm_not_present",
                     "message": "--query etm needs hardware trace (DWT / ETM / CoreSight "
                                "discovery), which the native transports do not capture yet"}, 1)
    if verb in ("irq", "target-caps"):
        return emit({"schema_version": SV, "error": "no_hw_trace" if verb == "irq" else "bad_arguments",
                     "message": "--query %s needs hardware trace" % verb}, 1)
    if verb not in QUERY_VERBS:
        return emit({"schema_version": SV, "error": "bad_arguments",
                     "message": "unknown query '%s'" % verb}, 1)
    if verb in HW_VERBS and ref == NO_HW_RECORDING:
        return emit({"schema_version": SV, "error": "no_hw_trace",
                     "message": "This recording has no hardware-trace rows. "
                                "Capture over SWO with --dwt to get them."}, 1)
    base = {"schema_version": SV, "recording_id": ref, "query": verb,
            "budget": val(a, "--budget") or "med"}
    if verb == "sql":
        sql = val(a, "--sql") or ""
        if "boom" in sql:
            return emit({"schema_version": SV, "error": "bad_sql",
                         "message": "no such table: boom"}, 1)
        return emit(dict(base, columns=["name", "cpu"],
                         rows=[["idle", 66.1], ["sensor_tid", 9.0]],
                         row_count=2, truncated=False))
    if verb == "series":
        kind = val(a, "--kind")
        if kind is None:
            return emit({"error": "bad_arguments", "message": "--kind required"}, 1)
        if kind == "task-timing" and val(a, "--task") is None:
            return emit({"error": "bad_arguments", "message": "--task required"}, 1)
        if kind == "interval" and (val(a, "--from") is None or val(a, "--to") is None):
            return emit({"error": "bad_arguments", "message": "--from/--to required"}, 1)
        return emit(dict(base, kind=kind, points=[[0, 1.0], [500000, 2.0]]))
    if verb == "fingerprint":
        payload = dict(
            base,
            sections=(val(a, "--sections") or "summary,tasks").split(","),
            runs_merged=len((val(a, "--runs") or "").split(",")) if val(a, "--runs") else 1,
            metrics={"summary.event_rate_hz": {"value": 138.2}},
        )
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
            return emit(dict(base, verdict="fail",
                             results=[{"metric": "summary.event_rate_hz", "status": "fail"}]), 2)
        return emit(dict(base, verdict="pass", results=[]))
    if verb == "summary":
        return emit(dict(base, data={
            "duration_us": 8937186, "events": 1234, "context_switches": 500,
            "preemptions": 12, "cpu_load_pct": 33.9, "channels": 2,
            "lost_events": 0, "corrupt_bytes": 0}))
    if verb == "verdicts":
        return emit(dict(base, count=1, verdicts=[{
            "id": "rail-sag", "name": "Rail sag", "severity": "warn",
            "t_start_us": 100, "t_end_us": 900, "peak": 2.9,
            "explain": "VDD dipped under load",
            "evidence": [{"channel": "vdd", "at_start": 3.3, "at_end": 2.9,
                          "label_start": "3.30 V", "label_end": "2.90 V",
                          "summary": "vdd 3.30 V -> 2.90 V"}]}]))
    if verb == "profile":
        elf = val(a, "--elf")
        hot = {"pc": 134218240, "samples": 400, "pct": 40.0}
        if elf:
            hot.update({"symbol": "main", "file": "main.c", "line": 42})
        return emit(dict(base, elf=elf, data={
            "total_samples": 1000, "sleep_samples": 100, "span_s": 1.0,
            "sample_rate_hz": 1000.0, "hotspots": [hot], "source": "dwt-swo",
            "interval_cycles": 16384}))
    if verb == "itm-console":
        return emit(dict(base, port_flag=val(a, "--port"), data={"ports": [
            {"port": 0, "lines": [{"t_us": 0, "text": "boot"}], "bytes": 5},
            {"port": 1, "lines": [{"t_us": 10, "text": "HIL:1"},
                                  {"t_us": 20, "text": "HI", "partial": True}], "bytes": 8},
        ]}))
    if verb == "dwt-data":
        return emit(dict(base, elf=val(a, "--elf"), data={"watches": [{
            "cmp": 0, "name": "g_temp", "address": "0x20000410", "function": "data-w",
            "count": 3, "first": [{"t_us": 10, "value": 1, "rw": "w"},
                                  {"t_us": 20, "value": 2, "rw": "w", "pc": 134218240}]}]}))
    if verb == "dwt-exc":
        return emit(dict(base, data={
            "exceptions": [{"num": 15, "name": "SysTick", "enter": 100, "exit": 100,
                            "return": 0, "max_depth": 1}],
            "events": [{"t_us": 1000, "num": 15, "func": 1}]}))
    if verb == "dwt-counters":
        return emit(dict(base, data={
            "counters": {"cpi": {"wraps": 10, "cycles": 2560, "rate_per_s": 2560.0},
                         "sleep": {"wraps": 3, "cycles": 768, "rate_per_s": 768.0}},
            "span_s": 1.0, "cpu_load_pct": 70.0}))
    if verb == "swo-load":
        return emit(dict(base, data={
            "bytes": 250000, "seconds": 2.0, "bytes_per_s": 125000.0,
            "swo_hz": 2000000, "share_pct": 50.0, "overflows": 0}))
    if verb == "timeline" and val(a, "--tier") == "bucketed":
        if val(a, "--bucket-us") is None:
            return emit({"error": "bad_arguments", "message": "--bucket-us required"}, 1)
    return emit(dict(
        base,
        tier=val(a, "--tier"),
        elf=val(a, "--elf"),
        kinds=val(a, "--kinds"),
        channels=val(a, "--channels"),
        threshold_us=val(a, "--threshold-us"),
        inversions=[] if verb == "inversions" else None,
    ))


def handle_hwtrace(a):
    """`hwtrace --dry-run`: the bare register image (NO schema_version
    wrapper), or exit 2 with {"error": <reason>} on a configuration
    error, as the real verb does."""
    if "--dry-run" not in a:
        return emit({"schema_version": SV, "error": "bad_arguments",
                     "message": "hwtrace: only --dry-run is available here"}, 1)
    errors = []
    arch = val(a, "--arch")
    if arch is None:
        errors.append("--arch v7m|v8m|v6m is required for a dry run")
    elif arch not in ("v6m", "v7m", "v8m"):
        errors.append("--arch must be v7m | v8m | v6m (got '%s')" % arch)
    cpu = val(a, "--cpu-clock-hz")
    if cpu is None or int(cpu) == 0:
        errors.append("--cpu-clock-hz is required (the TPIU prescaler is derived from it)")
    caps = {"numcomp": 4}
    if val(a, "--caps") is not None:
        try:
            caps = json.loads(val(a, "--caps"))
        except ValueError as e:
            errors.append("--caps: %s" % e)
    ht = {}
    if val(a, "--hardware-trace") is not None:
        text = val(a, "--hardware-trace")
        if text.startswith("@"):
            with open(text[1:], "r", encoding="utf-8") as f:
                text = f.read()
        try:
            ht = json.loads(text)
        except ValueError as e:
            errors.append("--hardware-trace: not JSON: %s" % e)
    if errors:
        print(json.dumps({"error": "; ".join(errors)}))
        return 2
    swo = int(val(a, "--swo-freq-hz") or 2000000)
    writes = [
        {"reg": "DEMCR", "addr": "0xE000EDFC", "value": "0x01000000"},
        {"reg": "TPIU_ACPR", "addr": "0xE0040010", "value": "0x%08X" % (int(cpu) // swo - 1)},
    ]
    refused = []
    if caps.get("numcomp", 4) == 0 and (ht.get("dwt") or {}).get("watch"):
        refused.append({"feature": "dwt-watch", "reason": "no comparators (numcomp=0)"})
    print(json.dumps({
        "schema": 1, "arch": arch, "cpu_hz": int(cpu), "swo_hz": swo,
        "itm_port": int(val(a, "--itm-port") or 1),
        "init_swo": "--no-init-swo" not in a,
        "caps": caps, "hardware_trace": ht, "dwt_watch": val(a, "--dwt-watch"),
        "writes": writes, "refused": refused,
        "applied": {"dwt": bool(ht.get("dwt"))},
    }))
    return 0


def stream_capture(a, transport):
    """The capture --stream contract on stderr, as the native CLI emits it:
    stream_init (transport, no duration), stream_meta with `display` (one
    late, like a firmware trace whose setup packet arrives mid-capture),
    stream_sample lines with float values and no is_float, plus itm_text /
    swo_load / pc_samples / dwt_data / exc lines interleaved with plain
    diagnostics. NO stream_end: the feed ends when stderr closes. Honors
    --stop-file and SIGINT like the real CLI."""
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
    jline({"t": "stream_init", "schema_version": SV, "transport": transport,
           "started_utc": "2026-08-29T00:00:00Z"})
    jline({"t": "stream_meta", "id": 3, "name": "Beat", "display": "counter"})
    t0 = time.monotonic()
    i = 0
    stopped = False
    while time.monotonic() - t0 < duration:
        if sig["stop"] or (stop_path and os.path.exists(stop_path)):
            stopped = True
            break
        jline({"t": "stream_sample", "id": 3, "t_us": i * 10000, "value": float(i)})
        if i == 2:  # late registration, as firmware setup packets arrive
            jline({"t": "stream_meta", "id": 4, "name": "Load", "display": "graph"})
        if i >= 3:
            jline({"t": "stream_sample", "id": 4, "t_us": i * 10000, "value": i * 0.5})
        phase = i % 6
        if phase == 1:
            jline({"t": "itm_text", "port": 0, "t_us": i * 10000, "text": "boot ok %d\n" % i})
        elif phase == 2:
            jline({"t": "swo_load", "bytes_per_s": 125000.0, "share_pct": 50.0, "overflows": 0})
        elif phase == 3:
            jline({"t": "pc_samples", "total": 64, "sleep": 8, "pcs": [134217728, 134217984]})
        elif phase == 4:
            jline({"t": "dwt_data", "rows": [{"cmp": 0, "t_us": i * 10000, "v": 42,
                                              "size": 4, "w": True, "pc": None}]})
        elif phase == 5:
            jline({"t": "exc", "total": 12, "max_depth": 2, "exceptions": [
                {"num": 15, "name": "SysTick", "enter": 6, "exit": 6, "return": 0,
                 "max_depth": 1}]})
        print("[headless] draining ring buffer...", file=sys.stderr, flush=True)
        i += 1
        time.sleep(0.02)
    if stopped:
        print("Stop requested at %.1f s. Finalizing partial recording..."
              % (time.monotonic() - t0), flush=True)


def handle_poll(a):
    """The poll verb: needs a debug-probe transport (bad_config otherwise),
    prints progress lines and the two contract lines on stdout, then the
    envelope as the LAST stdout line."""
    cfg = load_config(a)
    transport = val(a, "--transport") or cfg.get("transport")
    if transport is None:
        return fail("bad_config", "poll needs a debug-probe transport "
                    "(--transport stlink-*/jlink-* or a --config)")
    if transport not in PROBE_TRANSPORTS:
        return fail("bad_config", "poll needs a debug probe, not %s" % transport)
    syms = (val(a, "--symbols") or "").split(",")
    hz = val(a, "--poll-hz") or "100"
    out = vadb_name(val(a, "--output") or val(a, "--elf") + ".poll.vadb")
    print("[headless] Poll: %d symbol(s) at %s Hz via %s" % (len(syms), hz, transport))
    for s in syms:
        print("[poll] %s @ 0x20000010 (u32, 4 bytes)" % s.split(":")[0])
    make_vadb(out, total_events=42)
    print("[headless] Recording saved: %s (4 KB)" % out)
    print("[headless] Recording registered: id=a718d4114f55")
    return emit({
        "schema_version": SV,
        "recording_id": "a718d4114f55",
        "path": out,
        "summary": {"span_seconds": 1.0, "sample_count": 42, "sample_loss_percent": 0.0,
                    "symbols_polled": len(syms), "poll_hz": float(hz)},
    })


def handle_snapshot(a):
    cfg = load_config(a)
    print("[headless] Snapshot: %s" % cfg.get("transport"))
    if cfg.get("transport") not in ("stlink-rambuf", "jlink-rambuf"):
        return fail("bad_arguments", "--snapshot needs a rambuf transport")
    if cfg.get("rambuf-address") == "0xEMPTY":
        return fail("empty_snapshot", "The snapshot window parsed to 0 events")
    out = vadb_name(val(a, "--output"))
    make_vadb(out, total_events=777)
    print("[headless] Recording saved: %s (8 KB)" % out)
    print("[headless] Recording registered: id=5aa9d4114f00")
    return emit({
        "schema_version": SV,
        "recording_id": "5aa9d4114f00",
        "path": out,
        "summary": {"ring": "post-mortem", "events": 777,
                    "window_bytes": 4096, "wrapped": True, "frozen": True},
    })


def handle_capture(a):
    cfg = load_config(a)
    transport = cfg.get("transport")
    if transport == "fail":
        print("[headless] Connecting...")
        print("[headless] ERROR: Failed to connect to target")
        return 1
    if transport == "hang":
        # A wedged probe: no stream lines, no stop-file checks, no exit.
        import time
        print("[headless] Connecting...", flush=True)
        time.sleep(120)
        return 1
    if transport == "cooldown":
        # Capture mode mixes progress lines with the error envelope.
        print("[headless] Free mode max capture applied.")
        print(json.dumps({"schema_version": SV, "error": "cooldown_active",
                          "message": "wait 42 s", "retry_after_s": 42}))
        print("[headless] ERROR: Capture cooldown active (42 s remaining).",
              file=sys.stderr)
        return 1
    # --symbols / --poll-hz: accepted and ignored, exactly like the native
    # CLI's capture verb (only the poll verb reads them).
    out = vadb_name(val(a, "--output"))
    print("[headless] Capture: %s" % transport)
    if "--stream" in a:
        stream_capture(a, transport)
    make_vadb(out)
    print("[headless] Recording...")
    print("[headless] Recording saved: %s (12 KB)" % out)
    print("[headless] Recording registered: id=abcdef123456")
    print(json.dumps({"schema_version": SV, "recording_id": "abcdef123456", "path": out,
                      "summary": {"events": 1234, "lost_events": 0, "corrupt_bytes": 0,
                                  "transport": transport}}))
    return 0


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] != "--headless":
        # Without --headless the real app launches the GUI.
        print("GUI mode launched (this should never happen in tests)", file=sys.stderr)
        return 3
    a = argv[1:]

    # An explicit verb after --headless wins over the legacy flag mapping.
    if a and a[0] == "hwtrace":
        return handle_hwtrace(a[1:])

    if "--version" in a:
        return emit({"schema_version": SV, "app": "ViewAlyzer", "version": "9.9.9",
                     "core": "rust", "edition": "full",
                     "transports": list(PROBE_TRANSPORTS) + ["udp", "serial", "swo-tcp"],
                     "license": LICENSE})

    if "--doctor" in a:
        return emit({
            "schema_version": SV,
            "app_version": "9.9.9",
            "edition": "full",
            "license": LICENSE,
            "checks": [
                {"id": "probes_stlink", "name": "stlink probes", "required": False,
                 "status": "ok", "detail": "0033004B3033510735393935"},
                {"id": "probes_jlink", "name": "jlink probes", "required": False,
                 "status": "none", "detail": "none connected"},
                {"id": "probes_cmsis_dap", "name": "cmsis-dap probes", "required": False,
                 "status": "none", "detail": "none connected"},
                {"id": "serial_ports", "name": "serial ports", "required": False,
                 "status": "ok", "detail": "COM7"},
                {"id": "recordings_dir", "name": "recordings directory", "required": True,
                 "status": "ok", "path": "C:/Users/ci/AppData/Roaming/ViewAlyzer-GPUI/recordings",
                 "hint": None},
                {"id": "probe_rs_targets", "name": "built-in target registry",
                 "required": True, "status": "ok", "detail": "1234 targets"},
                {"id": "license", "name": "license", "required": False, "status": "free",
                 "detail": "Free mode",
                 "hint": "free mode caps apply: run `license activate <key>` or install "
                         "an OEM license file"},
            ],
        })

    if "--get-license" in a:
        return emit({"schema_version": SV, "activated": False, "max_record_s": 30})

    if "--activate-license" in a:
        key = val(a, "--activate-license")
        if key == "BAD-KEY":
            return emit({"error": "activation_failed", "message": "unknown key"}, 1)
        return emit({"schema_version": SV, "activated": True, "tier": "pro"})

    if "--validate-license" in a:
        return emit({"schema_version": SV, "activated": True, "state": "active"})

    if "--deactivate-license" in a:
        return emit({"schema_version": SV, "activated": False})

    if "--list-recordings" in a:
        return emit({
            "schema_version": SV,
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
        })

    if "--list-probes" in a:
        return emit({
            "schema_version": SV,
            "probes": [
                {"type": "jlink", "serial": "1260001884", "description": "J-Link OB",
                 "vid": "1366", "pid": "0101"},
            ],
            "warnings": ["ST-LINK enumeration unavailable"],
        })

    if "--list-ports" in a:
        return emit({"schema_version": SV, "ports": ["COM7", "COM12"]})

    if "--list-targets" in a:
        f = (val(a, "--filter") or "").lower()
        hits = [{"name": n, "architecture": arch} for n, arch in TARGETS if f in n.lower()]
        return emit({"schema_version": SV, "filter": val(a, "--filter") or "",
                     "count": len(hits), "targets": hits})

    if "--analyze-memory" in a:
        mp = val(a, "--map")
        return emit({"schema_version": SV, "elf": val(a, "--elf"), "text": 42572,
                     "data": 3002, "bss": 63014, "total": 108588,
                     "has_map_data": mp is not None,
                     "map": {"file_path": mp, "memory_regions": []} if mp else None})

    if "--list-symbols" in a:
        return emit({
            "schema_version": SV,
            "total_symbols": 1,
            "returned": 1,
            "truncated": False,
            "symbols": [
                {"name": "tick_counter", "address": 536870928, "size": 4}
            ],
        })

    if "--delete-recording" in a:
        which = val(a, "--delete-recording")
        if which == "nope00000000":
            return emit({"error": "no_such_recording", "message": "unknown id"}, 1)
        return emit({"schema_version": SV, "deleted": which})

    if "--query" in a:
        return handle_query(a)

    if "--snapshot" in a:
        return handle_snapshot(a)

    if "--record-polls" in a:
        return handle_poll(a)

    if "--config" in a and "--output" in a and "--duration" in a:
        return handle_capture(a)

    return emit({"error": "bad_arguments", "message": "unrecognized invocation"}, 1)


if __name__ == "__main__":
    sys.exit(main())
