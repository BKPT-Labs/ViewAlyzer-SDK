# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""Derived runtime maps: one Rust projector, disposable SQLite, no hardware."""
from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Sequence, Union

from .errors import ViewAlyzerError
from .recording import Recording

if TYPE_CHECKING:
    from .client import ViewAlyzer


class RuntimeMap:
    """A map derived from a .vadb; each query validates/reuses its cache.

    Times in results and SQL columns are device nanoseconds. Query window
    arguments use microseconds, matching other SDK query methods. Relations
    are observed operations, not inferred deliveries or static dependencies.
    """

    def __init__(self, client: "ViewAlyzer", recording: Union[Recording, str, Path],
                 *, cache_dir: Optional[Union[str, Path]] = None,
                 timeout_s: Optional[float] = None) -> None:
        self._client = client
        self._ref = recording.ref if isinstance(recording, Recording) else str(recording)
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._timeout = timeout_s if timeout_s is not None else client._query_timeout_s
        self._closed = False
        self.info: Dict[str, Any] = {}
        self.query("build")

    @property
    def path(self) -> Path:
        """Current cache path. Regenerate after eviction; don't archive it."""
        return Path(self.info["cache"]["path"])

    def query(self, kind: str = "summary", *, node: Optional[str] = None,
              name: Optional[str] = None, kind_filter: Optional[str] = None,
              tags: Union[str, Sequence[str], None] = None,
              t_start_us: Optional[int] = None, t_end_us: Optional[int] = None,
              limit: Optional[int] = None, offset: Optional[int] = None,
              sql: Optional[str] = None) -> Dict[str, Any]:
        """Bounded query envelope with ``data``, pagination and provenance.

        ``nodes`` supports name/kind filters. ``edges`` and ``activity``
        support node, tags and inclusive time windows. ``states`` supports
        node and time. SQL returns ``columns``/``rows`` instead of ``data``.
        Follow ``next_offset`` while ``truncated`` is true for list queries.
        """
        if self._closed:
            raise ViewAlyzerError("map_closed", "This temporary map context has ended")
        if kind not in ("build", "summary", "nodes", "edges", "activity", "states", "sql"):
            raise ViewAlyzerError("bad_arguments", f"Unknown map query {kind!r}")
        args = ["map", kind, "--recording", self._ref]
        if self._cache_dir is not None:
            args += ["--cache-dir", str(self._cache_dir)]
        for flag, value in (("node", node), ("name", name), ("kind", kind_filter),
                            ("t-start-us", t_start_us), ("t-end-us", t_end_us),
                            ("limit", limit), ("offset", offset), ("sql", sql)):
            if value is not None:
                args += ["--" + flag, str(value)]
        if tags is not None:
            args += ["--tags", tags if isinstance(tags, str) else ",".join(tags)]
        result = self._client._runner.run_json(args, self._timeout)
        self.info = result
        return result

    def summary(self) -> Dict[str, Any]:
        """Counts, integrity/omission counters, source identity and cache info."""
        return self.query("summary")

    def nodes(self, *, kind: Optional[str] = None, **filters: Any) -> Dict[str, Any]:
        return self.query("nodes", kind_filter=kind, **filters)

    def edges(self, **filters: Any) -> Dict[str, Any]:
        return self.query("edges", **filters)

    def activity(self, **filters: Any) -> Dict[str, Any]:
        return self.query("activity", **filters)

    def states(self, **filters: Any) -> Dict[str, Any]:
        return self.query("states", **filters)

    def sql(self, statement: str, *, limit: int = 200) -> Dict[str, Any]:
        """One read-only SELECT/CTE, with the CLI's row/byte/time limits."""
        return self.query("sql", sql=statement, limit=limit)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """``with graph.connect() as db: ...``; closes on context exit.

        Refreshes the cache first. The connection reads a fixed snapshot;
        direct SQL has no CLI result limits. Close it before leaving an
        enclosing ``temporary_map`` context (especially on Windows).
        """
        self.summary()
        path = self.path.as_posix()
        # Keep UNC servers in the path, not SQLite's restricted URI authority.
        if path.startswith("//"):
            path = "//" + path
        uri = "file:" + urllib.parse.quote(path, safe="/:") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            yield conn
        finally:
            conn.close()


@contextlib.contextmanager
def temporary_map(client: "ViewAlyzer", recording: Union[Recording, str, Path],
                  *, timeout_s: Optional[float] = None) -> Iterator[RuntimeMap]:
    with tempfile.TemporaryDirectory(prefix="viewalyzer-map-") as directory:
        graph = RuntimeMap(client, recording, cache_dir=directory, timeout_s=timeout_s)
        try:
            yield graph
        finally:
            graph._closed = True
