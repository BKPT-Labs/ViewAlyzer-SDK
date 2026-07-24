"""A stand-in for the ViewAlyzer binary, faithful to the CLI contract:
`--headless` required, one JSON object on stdout for query modes, error
envelopes with non-zero exit, `[headless]` progress lines for captures,
and the forced `.vadb` output extension. Driven via `sys.executable` so the
suite behaves identically on Windows, macOS, and Linux."""
import json
import sqlite3
import sys


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


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] != "--headless":
        # Without --headless the real app launches the GUI.
        print("GUI mode launched (this should never happen in tests)", file=sys.stderr)
        return 3
    a = argv[1:]

    if "--version" in a:
        return emit({"schema_version": 1, "app": "ViewAlyzer", "version": "9.9.9"})

    if "--get-license" in a:
        return emit({"schema_version": 1, "activated": False, "max_record_s": 30})

    if "--list-recordings" in a:
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

    if "--list-probes" in a:
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

    if "--list-symbols" in a:
        return emit(
            {
                "schema_version": 1,
                "symbol_legend": [
                    {"name": "tick_counter", "address": 536870928, "size": 4}
                ],
            }
        )

    if "--delete-recording" in a:
        which = val(a, "--delete-recording")
        if which == "nope00000000":
            return emit(
                {"error": "no_such_recording", "message": "unknown id"}, 1
            )
        return emit({"schema_version": 1, "deleted": which})

    if "--query" in a:
        verb = val(a, "--query")
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
        if verb == "timeline" and val(a, "--tier") == "bucketed":
            if val(a, "--bucket-us") is None:
                return emit(
                    {"error": "bad_arguments", "message": "--bucket-us required"}, 1
                )
        return emit(
            {
                "schema_version": 1,
                "query": verb,
                "tier": val(a, "--tier"),
                "budget": val(a, "--budget") or "med",
                "inversions": [] if verb == "inversions" else None,
            }
        )

    if "--record-polls" in a:
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

    if "--config" in a and "--output" in a and "--duration" in a:
        with open(val(a, "--config"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("transport") == "fail":
            print("[headless] Connecting...")
            print("[headless] ERROR: Failed to connect to target")
            return 1
        out = val(a, "--output")
        if not out.endswith(".vadb"):  # the real binary forces the extension
            out = out.rsplit(".", 1)[0] + ".vadb"
        make_vadb(out)
        print("[headless] Recording...")
        print("[headless] Recording saved: %s (12 KB)" % out)
        print("[headless] Recording registered: id=abcdef123456")
        return 0

    return emit({"error": "bad_arguments", "message": "unrecognized invocation"}, 1)


if __name__ == "__main__":
    sys.exit(main())
