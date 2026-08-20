"""Live streaming of a running capture (``--stream``).

With ``--stream``, the headless CLI emits one JSON object per line on
**stderr** while a capture records: a ``stream_init`` banner first, a
``stream_meta`` line per discovered stream (firmware-emitted user traces
as their setup packets arrive, ``--symbols`` polls up front), one
``stream_sample`` per point, and ``stream_end`` when the capture
finalizes. Everything else on stderr stays a plain diagnostic line.

:class:`StreamSession` wraps that feed for hosts that want to graph or
react to trace data while the recording is still being written (an IDE
panel, a live dashboard, a long-running monitor):

    with va.stream("board.vacf", output="run.vadb", duration_s=60) as s:
        for sample in s:                # live, in arrival order
            chart.add(sample.name, sample.t_s, sample.value)
            if enough(sample):
                s.stop()                # finalize early, keep the partial
    rec = s.result()                    # the finished Recording
    assert rec.total_events > 0

The same recording lands on disk regardless of streaming; iteration is a
tap, not a diversion. Early stop is portable: the session always passes
``--stop-file`` (the CLI finalizes the moment that file exists) and, on
POSIX, also sends SIGINT. Older CLI builds without ``--stop-file`` warn
and ignore it; there SIGINT still stops POSIX sessions, while on Windows
the session can only wait for the duration to elapse (or terminate the
process and lose the partial recording, which :meth:`StreamSession.close`
does as a last resort).

A session is single-consumer: iterate it from one thread. Samples are
delivered in arrival order across all streams; interleave is the wire
order, not a per-stream sort.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    Union,
)

from .errors import ViewAlyzerError
from .recording import Recording
from .runner import RECORD_TIMEOUT_PAD_S, Runner

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .client import ViewAlyzer

#: Seconds a session waits for the CLI to finalize the recording after an
#: early stop (probe detach + SQLite finalize + registry write).
STOP_FINALIZE_PAD_S = 15.0

#: Diagnostic-line history kept per session (oldest lines drop first).
_LOG_KEEP = 2000

# Queue sentinels (identity-compared).
_END = object()  # the CLI said stream_end
_EOF = object()  # stderr closed (process exited)


@dataclass(frozen=True)
class StreamMeta:
    """One announced stream: a firmware user trace or a polled symbol.

    Attributes:
        id: wire id, unique within the session; samples carry it.
        name: the trace name the firmware registered (or the symbol name).
        type: presentation kind from the firmware's registration, e.g.
            ``graph``, ``counter``, ``gauge``, ``toggle``, ``histogram``.
    """

    id: int
    name: str
    type: str


@dataclass(frozen=True)
class StreamSample:
    """One live data point.

    Attributes:
        id: the stream's wire id (see :class:`StreamMeta`).
        t_us: microseconds since the first sample of the session (arrival
            timeline; the recording on disk keeps exact device-clock times).
        value: the sample value; ``int`` unless ``is_float``.
        is_float: True for f32 traces/symbols.
        name: the stream's name, when its meta line has arrived (the CLI
            announces polls up front and firmware traces before their
            points in practice, so this is rarely None).
        stream_type: the stream's presentation kind, same caveat as *name*.
    """

    id: int
    t_us: int
    value: Union[int, float]
    is_float: bool
    name: Optional[str]
    stream_type: Optional[str]

    @property
    def t_s(self) -> float:
        """Sample time in seconds (float), for plotting."""
        return self.t_us / 1e6


class StreamSession:
    """A live capture: iterate for samples, then take the recording.

    Created by :meth:`ViewAlyzer.stream`; not constructed directly. The
    CLI process is already running when you hold one of these. Use it as
    a context manager so an abandoned session still stops the capture and
    cleans up:

        with va.stream(cfg, output="run.vadb", duration_s=30) as s:
            for sample in s:
                ...

    Attributes are live views; they grow while the capture runs.
    """

    def __init__(
        self,
        client: "ViewAlyzer",
        runner: Runner,
        config: Any,
        args: Sequence[Any],
        *,
        output: Union[str, Path],
        duration_s: float,
        timeout_s: Optional[float],
    ) -> None:
        from .client import _config_path  # late: avoids a module cycle

        self._client = client
        self._output = Path(output)
        self._duration_s = float(duration_s)
        self._deadline = time.monotonic() + (
            timeout_s if timeout_s is not None else duration_s + RECORD_TIMEOUT_PAD_S
        )
        self._events: "queue.Queue[Any]" = queue.Queue()
        self._streams: Dict[int, StreamMeta] = {}
        self._init: Dict[str, Any] = {}
        self._log: "deque[str]" = deque(maxlen=_LOG_KEEP)
        self._stdout_lines: List[str] = []
        self._ended = False
        self._stop_requested = False
        self._closed = False
        self._result: Optional[Recording] = None

        # The stop file must NOT exist yet; its own temp dir gives us a
        # collision-free path and one thing to remove at close.
        self._stop_dir = Path(tempfile.mkdtemp(prefix="viewalyzer-stream-"))
        self._stop_file = self._stop_dir / "stop"

        # The CLI reads --config at startup; an inline dict's temp .vacf
        # must outlive the spawn, so the session owns its lifetime.
        self._config_ctx = _config_path(config)
        config_file = self._config_ctx.__enter__()
        try:
            self._proc = runner.popen(
                [
                    "--config",
                    config_file,
                    *args,
                    "--stream",
                    "--stop-file",
                    self._stop_file,
                ]
            )
        except BaseException:
            self._config_ctx.__exit__(None, None, None)
            self._rm_stop_dir()
            raise

        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="viewalyzer-stream-stderr", daemon=True
        )
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="viewalyzer-stream-stdout", daemon=True
        )
        self._stderr_thread.start()
        self._stdout_thread.start()

    # ----- live views ------------------------------------------------------

    @property
    def streams(self) -> Dict[int, StreamMeta]:
        """Streams announced so far, by wire id. Grows during the capture
        as firmware traces register (returns a snapshot copy)."""
        return dict(self._streams)

    @property
    def init(self) -> Dict[str, Any]:
        """The ``stream_init`` banner (``duration_s``, ...); empty until
        the CLI has emitted it."""
        return dict(self._init)

    @property
    def log(self) -> List[str]:
        """Non-stream diagnostic lines seen on stderr so far (bounded;
        oldest lines drop first)."""
        return list(self._log)

    @property
    def pid(self) -> int:
        """OS process id of the CLI."""
        return self._proc.pid

    @property
    def returncode(self) -> Optional[int]:
        """CLI exit code, or None while it is still running."""
        return self._proc.poll()

    # ----- consuming samples ----------------------------------------------

    def __iter__(self) -> Iterator[StreamSample]:
        """Yield :class:`StreamSample` points as they arrive, until the
        capture ends (duration reached, :meth:`stop`, or CLI exit).
        Raises ``ViewAlyzerError("timeout")`` if the session's deadline
        passes with the CLI still running (the process is then killed)."""
        while not self._ended:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise ViewAlyzerError(
                    "timeout",
                    f"stream session exceeded its deadline "
                    f"({self._duration_s:.0f}s capture); the CLI was killed "
                    f"and the recording is likely incomplete or missing",
                )
            try:
                item = self._events.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if item is _END or item is _EOF:
                self._ended = True
                return
            yield item

    # ----- control ---------------------------------------------------------

    def stop(self) -> None:
        """Ask the CLI to finalize now and keep the partial recording.

        Portable and non-blocking: creates the session's ``--stop-file``
        (and sends SIGINT on POSIX); the CLI notices within its poll tick,
        stops the capture, and writes the ``.vadb`` as if the duration had
        elapsed. Keep iterating (or call :meth:`result`) to see it out.
        """
        self._stop_requested = True
        try:
            self._stop_file.touch()
        except OSError:
            pass
        if os.name == "posix" and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGINT)
            except (OSError, ValueError):
                pass
        # Finalize is seconds, not the remaining duration.
        self._deadline = min(
            self._deadline, time.monotonic() + STOP_FINALIZE_PAD_S
        )

    def result(self, *, timeout_s: Optional[float] = None) -> Recording:
        """Wait for the capture to finish and return the
        :class:`~viewalyzer_sdk.recording.Recording`.

        Call after iteration ends (or after :meth:`stop`). Waits for the
        CLI to exit, up to *timeout_s* (default: the session deadline plus
        finalize headroom), then applies the same success checks as
        :meth:`ViewAlyzer.record`: zero exit, error envelopes surfaced,
        the saved file present and non-empty. Idempotent.
        """
        if self._result is not None:
            return self._result
        budget = (
            timeout_s
            if timeout_s is not None
            else max(0.0, self._deadline - time.monotonic()) + STOP_FINALIZE_PAD_S
        )
        try:
            self._proc.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            self._kill()
            raise ViewAlyzerError(
                "timeout",
                f"stream session did not finish within {budget:.0f}s; "
                "the CLI was killed and the recording is likely incomplete",
            ) from None
        self._stderr_thread.join(timeout=5.0)
        self._stdout_thread.join(timeout=5.0)

        from .client import (  # late: avoids a module cycle
            _RECORDING_ID_RE,
            _RECORDING_SAVED_RE,
            _capture_failure_detail,
            _raise_any_error_envelope,
        )

        stdout = "\n".join(self._stdout_lines)
        stderr = "\n".join(self._log)
        if self._proc.returncode != 0:
            _raise_any_error_envelope(stdout)
            raise ViewAlyzerError(
                "record_failed",
                _capture_failure_detail(_Streams(self._proc.returncode, stdout, stderr)),
            )
        combined = f"{stdout}\n{stderr}"
        saved = _RECORDING_SAVED_RE.search(combined)
        actual = Path(saved.group(1)) if saved else self._output
        if not actual.is_file() or actual.stat().st_size == 0:
            raise ViewAlyzerError(
                "record_failed",
                "capture reported success but the recording file is missing "
                f"or empty: {actual}",
            )
        registered = _RECORDING_ID_RE.search(combined)
        self._result = Recording(
            self._client,
            recording_id=registered.group(1) if registered else None,
            path=actual,
            info={"duration_s": self._duration_s},
        )
        return self._result

    def close(self) -> None:
        """Stop the capture if still running, reap the process, and remove
        the session's temp files. Idempotent; called by ``__exit__``.

        A CLI that ignores both stop channels (pre-``--stop-file`` build on
        Windows) is terminated after ``STOP_FINALIZE_PAD_S``, which loses
        the partial recording; :meth:`result` then reports that.
        """
        if self._closed:
            return
        self._closed = True
        if self._proc.poll() is None:
            if not self._stop_requested:
                self.stop()
            try:
                self._proc.wait(timeout=STOP_FINALIZE_PAD_S)
            except subprocess.TimeoutExpired:
                self._kill()
        self._stderr_thread.join(timeout=5.0)
        self._stdout_thread.join(timeout=5.0)
        for pipe in (self._proc.stdout, self._proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass
        self._config_ctx.__exit__(None, None, None)
        self._rm_stop_dir()

    def __enter__(self) -> "StreamSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ----- internals -------------------------------------------------------

    def _kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    pass

    def _rm_stop_dir(self) -> None:
        try:
            if self._stop_file.is_file():
                self._stop_file.unlink()
            self._stop_dir.rmdir()
        except OSError:
            pass

    def _read_stderr(self) -> None:
        """Reader thread: NDJSON stream lines to the queue, everything
        else to the diagnostic log. Always ends with an EOF sentinel so
        iteration can never hang on a dead process."""
        try:
            for raw in self._proc.stderr:  # type: ignore[union-attr]
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if not line.startswith("{"):
                    self._log.append(line)
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    self._log.append(line)
                    continue
                kind = obj.get("t") if isinstance(obj, dict) else None
                if kind == "stream_sample":
                    sid = int(obj.get("id", -1))
                    meta = self._streams.get(sid)
                    self._events.put(
                        StreamSample(
                            id=sid,
                            t_us=int(obj.get("t_us", 0)),
                            value=obj.get("value", 0),
                            is_float=bool(obj.get("is_float", False)),
                            name=meta.name if meta else None,
                            stream_type=meta.type if meta else None,
                        )
                    )
                elif kind == "stream_meta":
                    meta = StreamMeta(
                        id=int(obj.get("id", -1)),
                        name=str(obj.get("name", "")),
                        type=str(obj.get("type", "graph")),
                    )
                    self._streams[meta.id] = meta
                elif kind == "stream_init":
                    self._init = obj
                elif kind == "stream_end":
                    self._events.put(_END)
                else:
                    self._log.append(line)
        except (OSError, ValueError):  # pipe torn down mid-read
            pass
        finally:
            self._events.put(_EOF)

    def _read_stdout(self) -> None:
        """Reader thread: drain stdout (progress + the stable contract
        lines) so the pipe can never fill and stall the CLI."""
        try:
            for raw in self._proc.stdout:  # type: ignore[union-attr]
                line = raw.rstrip("\r\n")
                if line:
                    self._stdout_lines.append(line)
        except (OSError, ValueError):  # pragma: no cover
            pass


class _Streams:
    """Duck-typed stand-in for runner.RunResult, for the shared failure
    formatter."""

    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
