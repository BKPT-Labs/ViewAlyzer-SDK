"""A handle on one ``.vadb`` recording.

Two ways to read a recording, both exposed here:

- **Through the CLI** (:meth:`Recording.timeline`, :meth:`Recording.events`,
  :meth:`Recording.user_traces`, :meth:`Recording.inversions`,
  :meth:`Recording.sql`): pre-shaped, size-bounded JSON - what you want for
  assertions over analytics the app already computes.
- **Directly** (:meth:`Recording.connect`, :meth:`Recording.summary`,
  :meth:`Recording.task_stats`): a ``.vadb`` is a standard SQLite database,
  so overview reads need no subprocess at all. Connections are opened
  read-only.

All query-layer times are microseconds since recording start. Raw
``va_events.t_cycles`` values are CPU cycles (convert with
``meta.va_cpu_hz``), except ``poll_trace`` rows which are microseconds.
"""
from __future__ import annotations

import sqlite3
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from .errors import ViewAlyzerError

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .client import ViewAlyzer

Number = Union[int, float, str, None]


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
        if recording_id is None and path is None:
            raise ViewAlyzerError(
                "bad_arguments", "a Recording needs a recording_id or a path"
            )
        self.recording_id = recording_id
        self.path: Optional[Path] = Path(path) if path is not None else None
        #: Extra fields the CLI reported for this recording (index entry or
        #: capture result), e.g. ``duration_us``, ``size_bytes``,
        #: ``created_utc``, poll ``summary``. Purely informational.
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

    def inversions(self) -> Dict[str, Any]:
        """Priority-inversion report (no tiers). The priority comparison is
        RTOS-aware."""
        return self._client.query("inversions", self)

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
        ``span_seconds``, ``corrupt_bytes``, ..."""
        return self._key_value_table("va_summary")

    def task_stats(self, *, include_synthetic: bool = False) -> List[dict]:
        """Per-task whole-trace stats (``va_task_stats``) as a list of dicts,
        highest CPU first. Synthetic lanes (``_RTOS_``, ``ISR:*``, ``Fn:*``)
        are filtered out unless *include_synthetic* is set."""
        with self.connect() as con:
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
        'succeeded' with 0 events is almost always a broken setup - assert
        on this, not just on the file existing."""
        value = self.summary().get("total_events")
        return int(value) if value is not None else 0

    def is_clean(self) -> bool:
        """True when the capture had no corrupt bytes."""
        return int(self.summary().get("corrupt_bytes") or 0) == 0

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
        with self.connect() as con:
            rows = con.execute(f"SELECT key, value FROM {table}").fetchall()
        return {key: _coerce(value) for key, value in rows}


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
