"""A handle on one ``.vadb`` recording.

Two ways to read a recording, both exposed here:

- **Through the CLI** (:meth:`Recording.timeline`, :meth:`Recording.events`,
  :meth:`Recording.cpu`, :meth:`Recording.timers`, :meth:`Recording.sql`,
  :meth:`Recording.report`, the hardware-trace queries
  :meth:`Recording.profile`, :meth:`Recording.itm_console`,
  :meth:`Recording.dwt_data`, ...): pre-shaped, size-bounded JSON. This is
  what you want for assertions over analytics the app already computes.
- **Directly** (:meth:`Recording.connect`, :meth:`Recording.summary`,
  :meth:`Recording.task_stats`): a ``.vadb`` is a standard SQLite database,
  so overview reads need no subprocess at all. Connections are opened
  read-only.

All query-layer times are microseconds since recording start. Raw
``va_events.t_cycles`` values are CPU cycles (convert with
``meta.va_cpu_hz``), except ``poll_trace`` rows which are microseconds.
"""
from __future__ import annotations

import contextlib
import sqlite3
import urllib.parse
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

from .errors import ViewAlyzerError

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .client import ViewAlyzer

Number = Union[int, float, str, None]
PathLike = Union[str, Path]


class _CallableBool(int):
    """Transitional return type for :attr:`Recording.is_clean`, which was a
    method through 1.0.0 and is a property now (matching ``total_events`` and
    friends). Behaves as a plain bool; calling it keeps the old form working
    with a :class:`DeprecationWarning`. Removed in 2.0."""

    def __call__(self) -> bool:
        warnings.warn(
            "Recording.is_clean is now a property; drop the parentheses",
            DeprecationWarning,
            stacklevel=2,
        )
        return bool(self)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return repr(bool(self))


class Recording:
    """One recording, addressed by ``recording_id`` and/or file path.

    Every CLI query accepts either form, so a handle is valid with just one
    of the two. Direct SQLite reads require ``path``.
    """

    def __init__(
        self,
        client: "ViewAlyzer",
        *,
        recording_id: Optional[str] = None,
        path: Union[str, Path, None] = None,
        info: Optional[Dict[str, Any]] = None,
    ) -> None:
        if isinstance(client, (str, Path)):
            raise ViewAlyzerError(
                "bad_arguments",
                f"Recording's first argument is the ViewAlyzer client, not a "
                f"path (got {str(client)!r}). Open a file with "
                f"ViewAlyzer().open(path).",
            )
        if recording_id is None and path is None:
            raise ViewAlyzerError(
                "bad_arguments", "a Recording needs a recording_id or a path"
            )
        self.recording_id = recording_id
        self.path: Optional[Path] = Path(path) if path is not None else None
        #: Extra fields the CLI reported for this recording (index entry or
        #: capture result), e.g. ``duration_us``, ``size_bytes``,
        #: ``created_utc``, poll/snapshot ``summary``. Purely informational.
        self.info: Dict[str, Any] = dict(info or {})
        self._client = client

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        ident = self.recording_id or (self.path and self.path.name)
        return f"Recording({ident})"

    @property
    def ref(self) -> str:
        """The ``--recording`` argument: the id when known, else the path."""
        if self.recording_id:
            return self.recording_id
        return str(self.path)

    # ----- tiered CLI queries ---------------------------------------------

    def timeline(self, tier: str = "summary", **window: Any) -> Dict[str, Any]:
        """Scheduler / CPU analytics. Start with ``tier="summary"``;
        ``bucketed``/``raw`` additionally take ``t_start_us``, ``t_end_us``,
        and (for bucketed) ``bucket_us``."""
        return self._client.query("timeline", self, tier=tier, **window)

    def events(self, tier: str = "summary", **window: Any) -> Dict[str, Any]:
        """Unified event stream (task starts/stops, sync ops, contentions,
        allocations, ...)."""
        return self._client.query("events", self, tier=tier, **window)

    def user_traces(self, tier: str = "summary", **window: Any) -> Dict[str, Any]:
        """Firmware-emitted value traces and memory-poll samples."""
        return self._client.query("user-traces", self, tier=tier, **window)

    def timers(
        self, tier: str = "summary", *, elf: Optional[PathLike] = None
    ) -> Dict[str, Any]:
        """Software-timer analytics: per-timer lateness stats
        (phase-corrected mean / p99 / max), violations, lateness histogram,
        work-queue lanes. ``tier="raw"`` adds per-fire records with violation
        causes plus arm/stop and work marks. Pass *elf* to resolve timer
        callback names from the firmware image."""
        return self._client.query("timers", self, tier=tier, elf=elf)

    def etm(
        self, tier: str = "summary", *, budget: Optional[str] = None
    ) -> Dict[str, Any]:
        """ETM call-tree profile (recordings captured with instruction
        trace): top functions by self time, per-handler interrupt stats,
        per-file rollups, line-coverage counts. ``tier="raw"`` adds dynamic
        call-graph edges.

        The native-driver CLI does not capture ETM and answers this verb
        with ``ViewAlyzerError("etm_not_present")``; the statistical
        (PC-sample) profile is :meth:`profile`."""
        return self._client.query("etm", self, tier=tier, budget=budget)

    # ----- whole-recording report and Trace Domain verdicts ---------------

    def report(self) -> Dict[str, Any]:
        """The CLI's ``summary`` query: whole-recording scalars the engine
        computes on load (duration, events by class, context switches,
        preemptions, CPU load, channels, integrity counters), under
        ``["data"]``. Distinct from :meth:`summary`, which reads the
        precomputed ``va_summary`` table straight out of the file with no
        subprocess; use that for quick assertions and this for the
        engine's full picture."""
        return self._client.query("summary", self)

    def verdicts(self) -> Dict[str, Any]:
        """Trace Domain verdicts: the spans where an installed domain's
        rule fired, as ``{"count", "verdicts": [{"id", "name",
        "severity", "t_start_us", "t_end_us", "peak", "explain",
        "evidence": [{"channel", "at_start", "at_end", "label_start",
        "label_end", "summary"}]}]}``. ``count == 0`` when no installed
        domain claims a channel of this recording."""
        return self._client.query("verdicts", self)

    # ----- hardware-trace queries (DWT / ITM / SWO captures) --------------
    #
    # Each raises ViewAlyzerError("no_hw_trace") on a recording that holds
    # none of the rows it reads; the message says which capture flags
    # produce them. Payloads are enveloped: results under ["data"].

    def profile(
        self, *, elf: Optional[PathLike] = None, budget: Optional[str] = None
    ) -> Dict[str, Any]:
        """PC-sample hotspots (statistical profile) from DWT PC sampling
        over SWO or ``DWT_PCSR`` polling: ``data`` holds
        ``total_samples``, ``sleep_samples``, ``span_s``,
        ``sample_rate_hz``, ``hotspots[]``, ``source`` (``dwt-swo`` |
        ``pcsr-poll``) and ``interval_cycles``. Pass *elf* to symbolicate
        the hotspots (function names, and file:line when the image has
        DWARF). *budget* caps the hotspot count (16 / 32 / 64)."""
        return self._client.query("profile", self, elf=elf, budget=budget)

    def itm_console(self, *, port: Optional[int] = None) -> Dict[str, Any]:
        """ITM stimulus-port console text (a firmware ``printf`` routed to
        the ITM): ``data["ports"]`` is a list of ``{"port", "lines":
        [{"t_us", "text", "partial"?}], "bytes", "lines_truncated"?}``,
        lines split on newline with the time of their first byte.

        *port* keeps only that stimulus port. It is sent to the CLI as
        ``--port N`` (newer builds filter server-side) and the returned
        ``data["ports"]`` is also filtered here, so older builds that
        ignore the flag behave the same."""
        flags: List[str] = []
        if port is not None:
            flags += ["--port", str(int(port))]
        payload = self._client.query("itm-console", self, extra_flags=flags)
        if port is not None:
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("ports"), list):
                data["ports"] = [
                    p
                    for p in data["ports"]
                    if isinstance(p, dict) and p.get("port") == int(port)
                ]
        return payload

    def dwt_data(self, *, elf: Optional[PathLike] = None) -> Dict[str, Any]:
        """DWT data-trace watches (``--dwt-watch`` comparators):
        ``data["watches"]`` is a list of ``{"cmp", "name", "address",
        "function", "count", "first": [{"t_us", "value", "rw", "pc"?}]}``
        with up to 256 samples per comparator. Pass *elf* to symbolicate
        the access PCs of ``:pc`` watches."""
        return self._client.query("dwt-data", self, elf=elf)

    def dwt_exc(self) -> Dict[str, Any]:
        """DWT exception trace (``--dwt-exc``): ``data["exceptions"]`` per
        vector ``{"num", "name", "enter", "exit", "return", "max_depth"}``
        plus ``data["events"]`` ``{"t_us", "num", "func"}`` (up to 4096)."""
        return self._client.query("dwt-exc", self)

    def dwt_counters(self) -> Dict[str, Any]:
        """DWT event counters (``--dwt-counters``): ``data["counters"]``
        keyed ``cpi``, ``exc``, ``sleep``, ``lsu``, ``fold``, ``cyc`` with
        ``{"wraps", "cycles", "rate_per_s"}`` each, ``span_s``, and
        ``cpu_load_pct`` derived from the sleep counter (``None`` without
        it)."""
        return self._client.query("dwt-counters", self)

    def swo_load(self) -> Dict[str, Any]:
        """SWO pin utilisation of an SWO capture: ``data`` holds
        ``bytes``, ``seconds``, ``bytes_per_s``, ``swo_hz``, ``share_pct``
        and ``overflows`` (ITM overflow packets: the trace port was
        oversubscribed and data was lost on-chip)."""
        return self._client.query("swo-load", self)

    # ----- untiered CLI queries -------------------------------------------

    def inversions(self) -> Dict[str, Any]:
        """Priority-inversion report (no tiers). The priority comparison is
        RTOS-aware."""
        return self._client.query("inversions", self)

    def cpu(
        self,
        *,
        t_start_us: Optional[int] = None,
        t_end_us: Optional[int] = None,
        budget: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The CPU panel's scheduler statistics, number-for-number with the
        GUI: busy-union load, min/peak sliding-window load with times,
        context switches, preemptions; per-task CPU%, exec-time percentiles
        net of preemption, activation period and outliers, blocked /
        inversion / failed-op counts, stack usage; plus the findings list.
        Window params scope everything."""
        return self._client.query(
            "cpu", self, t_start_us=t_start_us, t_end_us=t_end_us, budget=budget
        )

    def comms(
        self,
        *,
        t_start_us: Optional[int] = None,
        t_end_us: Optional[int] = None,
        bucket_us: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Communication paths (producer -> via -> consumer): rate, median /
        p99 / max latency, blocked counts; per-resource send/receive/pending
        totals. Window params recompute path stats over the window;
        *bucket_us* adds the derived backlog-depth series per resource."""
        return self._client.query(
            "comms", self, t_start_us=t_start_us, t_end_us=t_end_us,
            bucket_us=bucket_us,
        )

    def series(
        self,
        kind: str,
        *,
        task: Optional[str] = None,
        metric: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        bucket_us: Optional[int] = None,
    ) -> Dict[str, Any]:
        """A timeline-gutter series as ``[[t_us, value], ...]`` arrays.

        Kinds: ``cpu-load`` (sliding-window CPU load, window = *bucket_us*),
        ``stack`` (aggregate high-water curve, bytes), ``heap`` (allocated
        bytes, failed allocs, capacity), ``event-rate`` (events/second
        bins), ``task-timing`` (per-instance exec time or period; needs
        *task* and optionally *metric* = ``"exec"`` | ``"period"``), and
        ``interval`` (latency between two reference points; needs *from_*
        and *to* as ``"task:<name>"``, ``"trace:<name>"``, or
        ``"resource:<name>"``)."""
        flags: List[str] = ["--kind", kind]
        if task is not None:
            flags += ["--task", task]
        if metric is not None:
            flags += ["--metric", metric]
        if from_ is not None:
            flags += ["--from", from_]
        if to is not None:
            flags += ["--to", to]
        return self._client.query(
            "series", self, bucket_us=bucket_us, extra_flags=flags
        )

    def sql(self, statement: str, *, budget: Optional[str] = None) -> Dict[str, Any]:
        """One read-only SQL statement, executed by the CLI against this
        recording. Returns ``{"columns": [...], "rows": [...], ...}``.
        Row caps scale with *budget* (``low``/``med``/``high``)."""
        return self._client.query("sql", self, sql=statement, budget=budget)

    def sql_rows(self, statement: str, *, budget: Optional[str] = None) -> List[dict]:
        """Like :meth:`sql`, but returns rows as dicts keyed by column name."""
        payload = self.sql(statement, budget=budget)
        columns = payload.get("columns") or []
        return [dict(zip(columns, row)) for row in payload.get("rows") or []]

    # ----- fingerprint / baseline (golden-run regression testing) ---------

    def fingerprint(
        self,
        *,
        runs: Union[PathLike, Sequence[PathLike]] = (),
        sections: Union[str, Sequence[str], None] = None,
        tolerance_pct: Optional[float] = None,
        warn_pct: Optional[float] = None,
        out: Optional[PathLike] = None,
    ) -> Dict[str, Any]:
        """Distill this recording's summary tables into a small,
        git-committable ``.vafp.json`` baseline: per-metric values plus warn
        and fail thresholds, and capture provenance.

        *runs* merges extra recordings into one baseline whose min/max
        envelope reflects observed run-to-run variance. *sections* limits
        which metric groups are included (``summary``, ``tasks``,
        ``traces``, ``timers``, ``comms``, ``health``, ``etm``).
        *tolerance_pct* sets the fail threshold, *warn_pct* the warn
        threshold; both are per-metric-editable in the written file.
        Pass *out* to also write the baseline to disk."""
        if isinstance(runs, (str, Path)):
            runs = [runs]
        if isinstance(sections, str):
            sections = [sections]
        flags: List[str] = []
        if runs:
            flags += ["--runs", ",".join(str(r) for r in runs)]
        if sections:
            flags += ["--sections", ",".join(sections)]
        if tolerance_pct is not None:
            flags += ["--tolerance-pct", str(tolerance_pct)]
        if warn_pct is not None:
            flags += ["--warn-pct", str(warn_pct)]
        if out is not None:
            flags += ["--out", str(out)]
        return self._client.query("fingerprint", self, extra_flags=flags)

    def compare(self, baseline: PathLike) -> Dict[str, Any]:
        """Compare this recording against a baseline (``.vafp.json``, or a
        ``.vadb`` fingerprinted on the fly). Returns per-metric results,
        missing/new tasks-traces-functions, and an overall ``verdict``
        (``pass`` / ``warn`` / ``fail``). A fail verdict is data, not an
        exception: check ``payload["verdict"]``."""
        return self._client.query(
            "compare", self,
            extra_flags=["--baseline", str(_existing_path(baseline, "baseline"))],
        )

    # ----- direct SQLite access (no subprocess) ---------------------------

    def connect(self) -> sqlite3.Connection:
        """Open the ``.vadb`` file itself, read-only. The file is a standard
        SQLite database; the schema is documented in the CLI Integration
        Guide. Caller closes the connection."""
        p = self._require_path()
        # URI form is what enables mode=ro; quote so spaces survive, keep
        # '/' and ':' so Windows drive letters do (sqlite accepts D:/...).
        uri = "file:" + urllib.parse.quote(p.as_posix(), safe="/:") + "?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def meta(self) -> Dict[str, Number]:
        """The recording's ``meta`` table (provenance: ``va_cpu_hz``,
        ``va_os``, ``capture_source``, ...) as a typed dict."""
        return self._key_value_table("meta")

    def summary(self) -> Dict[str, Number]:
        """The precomputed whole-trace overview (``va_summary``):
        ``total_events``, ``cpu_load_percent``, ``context_switches``,
        ``span_seconds``, ``corrupt_bytes``, ... Read straight from the
        file; the engine-computed equivalent through the CLI is
        :meth:`report`."""
        return self._key_value_table("va_summary")

    def task_stats(self, *, include_synthetic: bool = False) -> List[dict]:
        """Per-task whole-trace stats (``va_task_stats``) as a list of dicts,
        highest CPU first. Synthetic lanes (``_RTOS_``, ``ISR:*``, ``Fn:*``)
        are filtered out unless *include_synthetic* is set."""
        # closing(): a sqlite3 connection's own context manager only commits;
        # an open read handle would block the CLI from overwriting the file
        # on Windows.
        with contextlib.closing(self.connect()) as con:
            con.row_factory = sqlite3.Row
            rows = [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM va_task_stats ORDER BY cpu_percent DESC"
                )
            ]
        if not include_synthetic:
            rows = [
                r
                for r in rows
                if r.get("name") != "_RTOS_"
                and not str(r.get("name", "")).startswith(("ISR:", "Fn:"))
            ]
        return rows

    @property
    def total_events(self) -> int:
        """Total captured events, from ``va_summary``. A capture that
        'succeeded' with 0 events is almost always a broken setup; assert
        on this, not just on the file existing."""
        value = self.summary().get("total_events")
        return int(value) if value is not None else 0

    @property
    def has_sequence_info(self) -> bool:
        """True when the recorder stamped packets with sequence numbers
        (wire protocol v3, ``VA_SEQ_COUNTER``). Only then can loss be
        *proven*: a pre-v3 recording reporting no loss means "unknown",
        not "zero"."""
        return int(self.summary().get("seq_present") or 0) != 0

    @property
    def lost_events(self) -> int:
        """Exact number of packets the recorder emitted that never arrived
        (dropped at the source by a full buffer, or lost in transport),
        from the v3 sequence counter. 0 when the stream carries no
        sequence info; check :attr:`has_sequence_info` to distinguish
        verified-zero from unknowable."""
        return int(self.summary().get("lost_events") or 0)

    @property
    def seq_gaps(self) -> int:
        """Number of distinct loss bursts behind :attr:`lost_events`."""
        return int(self.summary().get("seq_gaps") or 0)

    @property
    def is_clean(self) -> bool:
        """True when the capture had no corrupt bytes AND no sequence-counter
        loss. This is the assertion to use for "every emitted event made it
        into the recording": corrupt bytes measure damage to what arrived,
        lost events measure what never arrived at all.

        A property since 1.0.1 (was a method); the call form still works
        through 1.x with a DeprecationWarning."""
        return _CallableBool(
            int(self.summary().get("corrupt_bytes") or 0) == 0
            and self.lost_events == 0
        )

    # ----- helpers --------------------------------------------------------

    def _require_path(self) -> Path:
        if self.path is None:
            raise ViewAlyzerError(
                "bad_arguments",
                "this Recording has no file path (only an id); direct reads "
                "need the .vadb path - use ViewAlyzer.recordings() or pass "
                "the path to ViewAlyzer.open()",
            )
        if not self.path.is_file():
            raise ViewAlyzerError(
                "file_not_found", f"recording file not found: {self.path}"
            )
        return self.path

    def _key_value_table(self, table: str) -> Dict[str, Number]:
        if table not in ("meta", "va_summary"):
            raise ViewAlyzerError("bad_arguments", f"not a key/value table: {table}")
        with contextlib.closing(self.connect()) as con:
            rows = con.execute(f"SELECT key, value FROM {table}").fetchall()
        return {key: _coerce(value) for key, value in rows}


def _existing_path(path: PathLike, what: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ViewAlyzerError("file_not_found", f"{what} not found: {p}")
    return p


def _coerce(value: Any) -> Number:
    """va key/value tables store everything as text; give numbers back."""
    if not isinstance(value, str):
        return value
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
