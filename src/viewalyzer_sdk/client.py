"""High-level client for the ViewAlyzer headless CLI.

    from viewalyzer_sdk import ViewAlyzer

    va = ViewAlyzer()                      # finds the installed app
    rec = va.record("board.vacf", output="run1.vadb", duration_s=10)
    assert rec.total_events > 0
    assert rec.inversions()["inversions"] == []

Every method is one CLI invocation (one process, one JSON payload); there is
no daemon and no session state, which is exactly what makes this safe to run
from pytest and CI.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import warnings
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from .discovery import find_viewalyzer
from .errors import BinaryNotFound, ViewAlyzerError, ViewAlyzerWarning
from .recording import Recording
from .runner import (
    DEFAULT_QUERY_TIMEOUT_S,
    RECORD_TIMEOUT_PAD_S,
    BinarySpec,
    Runner,
    parse_payload,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .streaming import StreamSession

#: The agent wire-protocol version this SDK was written against (2 since
#: the hardware-trace queries and the extra stream events; additive over 1).
#: Check the CLI's own value once via :meth:`ViewAlyzer.version`.
SCHEMA_VERSION = 2
#: Every wire-protocol version whose payload shapes this SDK understands.
#: ``viewalyzer-doctor`` warns only when the binary reports something else.
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

#: Query verbs with three tiers (``summary`` | ``bucketed`` | ``raw``).
TIERED_VERBS = ("timeline", "events", "user-traces")
#: Query verbs with two tiers (``summary`` | ``raw``). ``etm`` is kept for
#: recordings from an ETM-capable engine; the native-transport CLI answers
#: it with ``ViewAlyzerError("etm_not_present")``.
TWO_TIER_VERBS = ("timers", "etm")
#: Query verbs without tiers. ``summary`` through ``swo-load`` at the end
#: are the whole-recording scalars, the Trace Domain verdicts, and the
#: hardware-trace (DWT / ITM / SWO) queries; the latter raise
#: ``no_hw_trace`` on a recording that carries none of those rows.
UNTIERED_VERBS = (
    "inversions",
    "cpu",
    "comms",
    "series",
    "sql",
    "fingerprint",
    "compare",
    "slices",
    "slice-details",
    "events-all",
    "user-traces-all",
    "summary",
    "verdicts",
    "profile",
    "itm-console",
    "dwt-data",
    "dwt-exc",
    "dwt-counters",
    "swo-load",
)
QUERY_VERBS = (*TIERED_VERBS, *TWO_TIER_VERBS, *UNTIERED_VERBS)

#: Cores ``hwtrace --dry-run`` can plan a register image for.
HWTRACE_ARCHS = ("v6m", "v7m", "v8m")

# Stable stdout contracts of capture mode (see the CLI Integration Guide):
#   [headless] Recording saved: /abs/path/run1.vadb (5576 KB)
#   [headless] Recording registered: id=f76593b93473
_RECORDING_SAVED_RE = re.compile(r"Recording saved:\s*(.+?)\s*\(\d+\s*KB\)")
_RECORDING_ID_RE = re.compile(r"Recording registered:\s*id=([0-9a-f]{12})")

#: Default timeout for :meth:`ViewAlyzer.snapshot` (probe attach + RAM read
#: + parse; no capture duration is involved).
DEFAULT_SNAPSHOT_TIMEOUT_S = 120.0

PathLike = Union[str, Path]
#: A connection config: a ``.vacf`` path, or an inline dict whose keys
#: are the CLI flag names without the leading ``--``.
ConfigSpec = Union[PathLike, Mapping[str, Any]]


class ViewAlyzer:
    """Client bound to one ViewAlyzer executable.

    Args:
        binary: path to the executable, or a full argv prefix (list) for
            wrappers. Omit to auto-discover: ``VIEWALYZER`` env var, then
            ``PATH``, then the OS's standard install locations.
        query_timeout_s: timeout for query/list calls. Captures compute
            their own timeout from the requested duration.
    """

    def __init__(
        self,
        binary: Optional[BinarySpec] = None,
        *,
        query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
    ) -> None:
        if binary is None:
            found = find_viewalyzer()
            if found is None:
                raise BinaryNotFound(
                    "ViewAlyzer executable not found. Install the ViewAlyzer "
                    "app, add it to PATH, or set the VIEWALYZER environment "
                    "variable to the executable's path."
                )
            binary = found
        self._runner = Runner(binary)
        self._query_timeout_s = query_timeout_s

    @property
    def binary(self) -> str:
        """Path of the executable this client drives."""
        return self._runner.binary

    # ----- handshake / utilities ------------------------------------------

    def version(self) -> Dict[str, Any]:
        """``{"schema_version": 2, "app": "ViewAlyzer", "version": "1.2.0",
        "core": "rust", "edition", "transports": [...], "license": {...}}``.
        Call once at startup; if ``schema_version`` is not in
        :data:`SUPPORTED_SCHEMA_VERSIONS`, response shapes may not match
        this SDK."""
        return self._runner.run_json(["--version"], self._query_timeout_s)

    def doctor(
        self,
        *,
        jlink_path: Optional[PathLike] = None,
        stlink_path: Optional[PathLike] = None,
        cube_programmer_path: Optional[PathLike] = None,
        arm_gdb_path: Optional[PathLike] = None,
    ) -> Dict[str, Any]:
        """Setup health check. Returns ``{"checks": [{"id", "name",
        "required", "status": "ok"|"missing"|"none"|"free", "path"?,
        "version"?, "detail"?, "hint"?}, ...], "app_version", "edition",
        "license"}``. The native-driver CLI reports ``probes_stlink``,
        ``probes_jlink``, ``probes_cmsis_dap`` (``none`` = none connected),
        ``serial_ports``, ``recordings_dir``, ``probe_rs_targets`` and
        ``license`` (``free`` = free-mode caps apply); older builds listed
        external tools (``libusb``, ``jlink_library``, ...) instead. A
        missing optional item is a report entry, not an error.

        The tool-path arguments are kept for older builds that spawned
        vendor tools; the native-driver CLI accepts and ignores them."""
        args: List[Any] = ["--doctor"]
        args += _tool_path_flags(
            jlink_path, stlink_path, cube_programmer_path, arm_gdb_path
        )
        return self._runner.run_json(args, max(self._query_timeout_s, 60.0))

    def analyze_memory(
        self, elf: PathLike, *, map_file: Optional[PathLike] = None
    ) -> Dict[str, Any]:
        """Static flash/RAM breakdown of a firmware image."""
        args: List[Any] = ["--analyze-memory", "--elf", _existing(elf, "ELF")]
        if map_file is not None:
            args += ["--map", _existing(map_file, "map file")]
        return self._runner.run_json(args, self._query_timeout_s)

    def list_symbols(
        self, elf: PathLike, *, filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Pollable symbols in a firmware ELF (name, address, size, type)."""
        args: List[Any] = ["--list-symbols", "--elf", _existing(elf, "ELF")]
        if filter:
            args += ["--filter", filter]
        return self._runner.run_json(args, self._query_timeout_s)

    def list_probes(
        self,
        *,
        jlink_path: Optional[PathLike] = None,
        stlink_path: Optional[PathLike] = None,
        cube_programmer_path: Optional[PathLike] = None,
    ) -> Dict[str, Any]:
        """Connected debug probes with serial numbers:
        ``{"probes": [{"type": "jlink"|"stlink"|"cmsis-dap", "serial",
        "description", "vid"?, "pid"?}], "warnings": [...]?}``. Pass a
        serial to a capture via the ``jlink-serial`` / ``stlink-serial``
        config keys when more than one probe is attached (with a single
        probe the drivers auto-select).

        The tool-path arguments are kept for older builds that spawned
        vendor enumerators; the native-driver CLI accepts and ignores
        them."""
        args: List[Any] = ["--list-probes"]
        args += _tool_path_flags(jlink_path, stlink_path, cube_programmer_path, None)
        return self._runner.run_json(args, self._query_timeout_s)

    def list_ports(self) -> Dict[str, Any]:
        """Serial ports the ``serial`` transport can open:
        ``{"ports": ["COM7", "/dev/ttyACM0", ...]}``. Names, not device
        descriptions; pass one as the ``serial-port`` config key."""
        return self._runner.run_json(["--list-ports"], self._query_timeout_s)

    def list_targets(self, *, filter: Optional[str] = None) -> Dict[str, Any]:
        """probe-rs target names the debug-probe transports accept as
        ``target-device``: ``{"filter", "count", "targets": [{"name",
        "architecture"}, ...]}``. *filter* is a case-insensitive substring
        (``"STM32G474"``); omit it for the whole registry (thousands of
        entries)."""
        args: List[Any] = ["--list-targets"]
        if filter:
            args += ["--filter", filter]
        return self._runner.run_json(args, self._query_timeout_s)

    def hwtrace_dry_run(
        self,
        *,
        arch: str,
        cpu_clock_hz: int,
        swo_freq_hz: int = 2_000_000,
        itm_port: int = 1,
        caps: Union[Mapping[str, Any], str, None] = None,
        hardware_trace: Union[Mapping[str, Any], PathLike, None] = None,
        no_init_swo: bool = False,
        extra_flags: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """The ordered register image a capture would program for a
        hardware-trace (ITM / DWT / TPIU) setup on a given core, without
        touching a target: ``{"schema", "arch", "cpu_hz", "swo_hz",
        "writes": [{"reg", "addr", "value"}], "refused": [{"feature",
        "reason"}], "applied"}``. Use it to review or diff a board's
        ``hardware-trace`` block in CI, or to compare against
        ``bkpt_gdbserver --dry-run`` for the same inputs.

        *arch* is one of :data:`HWTRACE_ARCHS` (``v6m``, ``v7m``, ``v8m``).
        *cpu_clock_hz* is required (the TPIU prescaler derives from it);
        *swo_freq_hz* and *itm_port* mirror the capture keys. *caps*
        describes the core's trace capabilities (``numcomp``, ``notrcpkt``,
        ``nocyccnt``, ``noprfcnt``, ``itm``, ``tpiu``) as a dict, a JSON
        string, or ``"@file"``; default: four comparators, everything
        present. *hardware_trace* is the ``hardware-trace`` config block
        (dict, JSON string, or a file path / ``"@file"``). *extra_flags*
        adds flat overrides such as ``["--dwt-watch", "0x20000410:4"]``.

        Unlike the other query verbs, the CLI prints this image bare (no
        ``schema_version`` wrapper) and answers a configuration error with
        exit code 2 and ``{"error": "<reason>"}``; that is raised as
        ``ViewAlyzerError("bad_config", reason)``.
        """
        if arch not in HWTRACE_ARCHS:
            raise ViewAlyzerError(
                "bad_arguments", f"arch must be one of {HWTRACE_ARCHS}, got {arch!r}"
            )
        args: List[Any] = [
            "hwtrace",
            "--dry-run",
            "--arch",
            arch,
            "--cpu-clock-hz",
            int(cpu_clock_hz),
            "--swo-freq-hz",
            int(swo_freq_hz),
            "--itm-port",
            int(itm_port),
        ]
        if caps is not None:
            args += ["--caps", _json_or_ref(caps)]
        if hardware_trace is not None:
            args += ["--hardware-trace", _json_or_ref(hardware_trace, path_ok=True)]
        if no_init_swo:
            args.append("--no-init-swo")
        args += list(extra_flags)
        r = self._runner.run(args, self._query_timeout_s)
        text = r.stdout.strip()
        if not text:
            raise ViewAlyzerError(
                "bad_output",
                f"empty stdout (exit {r.exit_code}); stderr: {r.stderr.strip()[:400]}",
            )
        payload = parse_payload(text)
        if payload is None:
            raise ViewAlyzerError(
                "bad_output",
                f"non-JSON stdout (exit {r.exit_code}): no JSON object line "
                f"found. head: {text[:200]}",
            )
        if not isinstance(payload, dict):
            raise ViewAlyzerError(
                "bad_output", f"expected a JSON object, got {type(payload).__name__}"
            )
        if "error" in payload:
            if "message" in payload:
                # A regular envelope (e.g. bad_arguments from the verb itself).
                from .errors import raise_for_envelope

                raise_for_envelope(payload)
            raise ViewAlyzerError("bad_config", str(payload.get("error") or "error"))
        return payload

    # ----- licensing ------------------------------------------------------

    def get_license(self) -> Dict[str, Any]:
        """Local license state and effective policy caps (e.g. the maximum
        recording duration). Read-only; never contacts the license
        server."""
        return self._runner.run_json(["--get-license"], self._query_timeout_s)

    def activate_license(self, key: str, *, timeout_s: float = 60.0) -> Dict[str, Any]:
        """Activate this machine with a license key (contacts the license
        server; requires network)."""
        return self._runner.run_json(["--activate-license", key], timeout_s)

    def validate_license(self, *, timeout_s: float = 60.0) -> Dict[str, Any]:
        """Refresh this machine's license state against the license server
        (requires network)."""
        return self._runner.run_json(["--validate-license"], timeout_s)

    def deactivate_license(self, *, timeout_s: float = 60.0) -> Dict[str, Any]:
        """Release this machine's license seat (contacts the license
        server; requires network)."""
        return self._runner.run_json(["--deactivate-license"], timeout_s)

    # ----- the recording index --------------------------------------------

    def list_recordings(self) -> Dict[str, Any]:
        """The raw recording-index payload (``{"recordings": [...]}``)."""
        return self._runner.run_json(["--list-recordings"], self._query_timeout_s)

    def recordings(self) -> List[Recording]:
        """The recording index as bound :class:`Recording` handles, in the
        CLI's order (newest first)."""
        result = []
        for entry in self.list_recordings().get("recordings") or []:
            result.append(
                Recording(
                    self,
                    recording_id=entry.get("recording_id"),
                    path=entry.get("path"),
                    info=entry,
                )
            )
        return result

    def open(self, recording: Union[PathLike, str]) -> Recording:
        """A handle on an existing recording: a ``.vadb`` path (enables
        direct SQLite reads) or a 12-hex ``recording_id``."""
        text = str(recording)
        if re.fullmatch(r"[0-9a-f]{12}", text):
            return Recording(self, recording_id=text)
        return Recording(self, path=_existing(recording, "recording"))

    def delete_recording(self, recording: Union[Recording, str]) -> None:
        """**Destructive.** Removes the index entry *and deletes the .vadb
        file on disk*."""
        ref = recording.ref if isinstance(recording, Recording) else str(recording)
        self._run_ok(["--delete-recording", ref])

    def delete_all_recordings(self) -> None:
        """**Destructive.** Clears the index and deletes every indexed
        recording file."""
        self._run_ok(["--delete-all-recordings"])

    # ----- capture --------------------------------------------------------

    def record(
        self,
        config: ConfigSpec,
        *,
        output: PathLike,
        duration_s: float,
        elf: Optional[PathLike] = None,
        symbols: Union[str, Sequence[str]] = (),
        poll_hz: Optional[int] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> Recording:
        """Record the target's trace stream for *duration_s* seconds.

        *config* is a ``.vacf`` path or an inline dict with the same
        keys (``transport``, ``target-device``, ``cpu-clock-hz``, tool
        paths, ...). CLI flags win over config-file values.

        Pass *elf* to let the CLI resolve the ring's control-block address
        (RAM-buffer transports) or ``_SEGGER_RTT`` (RTT) from the firmware
        image instead of scanning RAM.

        *symbols* / *poll_hz* were the symbol watch of older engines (the
        named variables memory-polled during the capture). **The
        native-driver CLI does not poll symbols during a capture**: it
        accepts the flags and ignores them, so the SDK emits a
        :class:`~viewalyzer_sdk.errors.ViewAlyzerWarning` and the capture
        runs without the watch. Use :meth:`record_polls` for a memory poll,
        or a ``--dwt-watch`` hardware watch via *extra_flags* on an SWO
        transport. Each symbol is ``name`` or ``name:type`` with type one
        of ``u8 u16 u32 i8 i16 i32 f32``; symbols still need *elf*.

        *extra_flags* are appended verbatim for flags without a dedicated
        parameter (e.g. ``["--log", path]`` or port overrides).

        Returns a :class:`Recording` whose ``path`` is the authoritative
        on-disk file; the CLI forces the ``.vadb`` extension, so it may
        differ from *output*.
        """
        symbols = _as_symbol_list(symbols)
        args: List[Any] = [
            "--output",
            str(output),
            "--duration",
            duration_s,
        ]
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        if symbols:
            if elf is None:
                raise ViewAlyzerError(
                    "bad_arguments", "symbols need an elf to resolve against"
                )
            args += ["--symbols", ",".join(symbols)]
        if poll_hz is not None:
            args += ["--poll-hz", poll_hz]
        _warn_symbols_on_capture(symbols, poll_hz, "record")
        args += list(extra_flags)

        timeout = timeout_s if timeout_s is not None else duration_s + RECORD_TIMEOUT_PAD_S
        with _config_path(config) as config_path:
            r = self._runner.run(["--config", config_path, *args], timeout)
        if r.exit_code != 0:
            # A blocked capture may carry a machine-readable envelope on
            # stdout (e.g. cooldown_active with retry_after_s); prefer its
            # code over the generic one.
            _raise_any_error_envelope(r.stdout)
            raise ViewAlyzerError("record_failed", _capture_failure_detail(r))
        combined = f"{r.stdout}\n{r.stderr}"
        saved = _RECORDING_SAVED_RE.search(combined)
        actual = Path(saved.group(1)) if saved else Path(output)
        if not actual.is_file() or actual.stat().st_size == 0:
            raise ViewAlyzerError(
                "record_failed",
                "capture reported success but the recording file is missing "
                f"or empty: {actual}",
            )
        registered = _RECORDING_ID_RE.search(combined)
        return Recording(
            self,
            recording_id=registered.group(1) if registered else None,
            path=actual,
            info={"duration_s": duration_s},
        )

    def stream(
        self,
        config: ConfigSpec,
        *,
        output: PathLike,
        duration_s: float,
        elf: Optional[PathLike] = None,
        symbols: Union[str, Sequence[str]] = (),
        poll_hz: Optional[int] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> "StreamSession":
        """Record like :meth:`record`, but stream live samples while the
        capture runs.

        Starts the same capture as :meth:`record` (same *config* / *elf*
        semantics, same ``.vadb`` landing on disk) with the CLI's
        ``--stream`` tap enabled, and returns a
        :class:`~viewalyzer_sdk.streaming.StreamSession` immediately, while
        the CLI is still connecting. Iterate the session for
        :class:`~viewalyzer_sdk.streaming.StreamSample` points as they
        arrive (firmware user traces, Trace Domain sampled channels, and
        ``--dwt-watch`` hardware watches via *extra_flags*), or use
        :meth:`~viewalyzer_sdk.streaming.StreamSession.events` for the
        whole live feed (ITM console text, PC-sample batches, SWO load,
        DWT data-trace rows, exception counts as well); stop early with
        :meth:`~viewalyzer_sdk.streaming.StreamSession.stop`, and take the
        finished Recording from
        :meth:`~viewalyzer_sdk.streaming.StreamSession.result`::

            with va.stream("board.vacf", output="run.vadb",
                           duration_s=60) as s:
                for sample in s:
                    chart.add(sample.name, sample.t_s, sample.value)
            rec = s.result()

        *symbols* / *poll_hz* are accepted but **not polled during a
        capture by the native-driver CLI** (a
        :class:`~viewalyzer_sdk.errors.ViewAlyzerWarning` says so); use
        :meth:`record_polls` for a memory poll. Sample ``t_us`` is the live
        arrival timeline (t=0 at the first sample); the recording keeps
        exact device-clock timestamps, so use the Recording for analysis
        and the stream for display. Early stop needs a CLI with
        ``--stop-file`` support to work on Windows; elsewhere SIGINT covers
        older builds.

        *timeout_s* bounds the whole session (default: *duration_s* plus
        connect/finalize headroom); past it, iteration raises and the CLI
        is killed.
        """
        from .streaming import StreamSession  # late: avoids a module cycle

        symbols = _as_symbol_list(symbols)
        args: List[Any] = [
            "--output",
            str(output),
            "--duration",
            duration_s,
        ]
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        if symbols:
            if elf is None:
                raise ViewAlyzerError(
                    "bad_arguments", "symbols need an elf to resolve against"
                )
            args += ["--symbols", ",".join(symbols)]
        if poll_hz is not None:
            args += ["--poll-hz", poll_hz]
        _warn_symbols_on_capture(symbols, poll_hz, "stream")
        args += list(extra_flags)
        return StreamSession(
            self,
            self._runner,
            config,
            args,
            output=output,
            duration_s=duration_s,
            timeout_s=timeout_s,
        )

    def record_polls(
        self,
        elf: PathLike,
        symbols: Union[str, Sequence[str]],
        *,
        duration_s: float,
        poll_hz: int = 100,
        config: Optional[ConfigSpec] = None,
        target_device: Optional[str] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> Recording:
        """Sample target variables over the debug probe; no firmware
        instrumentation required.

        **A debug-probe transport is required in practice**: pass *config*
        as a ``.vacf`` path or a dict with at least ``transport``
        (``stlink-rambuf``, ``stlink-swo``, ``stlink-rtt``, ``jlink-*``) and
        usually ``target-device``. A bare *target_device* is not enough
        for the native-driver CLI (it answers ``bad_config``), so the SDK
        raises ``ViewAlyzerError("bad_arguments", ...)`` up front when
        neither *config* nor a ``--transport`` in *extra_flags* names one.
        *target_device* still overrides the config's value. Each symbol
        is ``name`` or ``name:type`` (``u8 u16 u32 i8 i16 i32 f32``).
        Returns a :class:`Recording` with the poll summary
        (``sample_count``, ``sample_loss_percent``, ``symbols_polled``,
        ``poll_hz``) in ``info["summary"]``.
        """
        symbols = _as_symbol_list(symbols)
        if not symbols:
            raise ViewAlyzerError("bad_arguments", "no symbols to poll")
        _require_poll_transport(config, extra_flags)
        args: List[Any] = [
            "--record-polls",
            "--elf",
            _existing(elf, "ELF"),
            "--symbols",
            ",".join(symbols),
            "--duration-s",
            duration_s,
            "--poll-hz",
            poll_hz,
        ]
        if target_device:
            args += ["--target-device", target_device]
        args += list(extra_flags)
        # Probe handshake + finalization need headroom beyond the poll time.
        timeout = (
            timeout_s
            if timeout_s is not None
            else duration_s + RECORD_TIMEOUT_PAD_S + 15.0
        )
        if config is not None:
            with _config_path(config) as config_path:
                payload = self._runner.run_json(
                    [*args, "--config", config_path], timeout
                )
        else:
            payload = self._runner.run_json(args, timeout)
        return Recording(
            self,
            recording_id=payload.get("recording_id"),
            path=payload.get("path"),
            info=payload,
        )

    def snapshot(
        self,
        config: ConfigSpec,
        *,
        output: PathLike,
        elf: Optional[PathLike] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: float = DEFAULT_SNAPSHOT_TIMEOUT_S,
    ) -> Recording:
        """Post-mortem snapshot: attach to the target WITHOUT reset, read
        the firmware's RAM trace ring through the probe, and save the
        recovered window as a normal ``.vadb`` recording.

        Requires a RAM-buffer transport (``stlink-rambuf`` or
        ``jlink-rambuf``) in *config*. There is no duration: the target
        recorded untethered, this reads out what is in RAM. Pass *elf* (or
        a ``rambuf-address`` config key) to skip the RAM scan for the
        ring's control block.

        Returns a :class:`Recording` with the snapshot summary (ring kind,
        events, window bytes, wrap state, ...) in ``info["summary"]``.
        Raises ``ViewAlyzerError("empty_snapshot", ...)`` when the ring
        holds no parseable events."""
        args: List[Any] = ["--snapshot", "--output", str(output)]
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        args += list(extra_flags)
        with _config_path(config) as config_path:
            payload = self._runner.run_json(
                [*args, "--config", config_path], timeout_s
            )
        return Recording(
            self,
            recording_id=payload.get("recording_id"),
            path=payload.get("path"),
            info=payload,
        )

    # ----- queries --------------------------------------------------------

    def query(
        self,
        verb: str,
        recording: Union[Recording, str, Path],
        *,
        tier: Optional[str] = None,
        budget: Optional[str] = None,
        t_start_us: Optional[int] = None,
        t_end_us: Optional[int] = None,
        bucket_us: Optional[int] = None,
        max_slices: Optional[int] = None,
        sql: Optional[str] = None,
        elf: Optional[PathLike] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
        kinds: Union[str, Sequence[str], None] = None,
        channels: Union[str, Sequence[str], None] = None,
        threshold_us: Optional[float] = None,
    ) -> Dict[str, Any]:
        """One ``--query`` call. Prefer the named helpers on
        :class:`Recording`; this generic form exists for flag combinations
        the helpers don't cover. *extra_flags* are appended verbatim (the
        ``series`` and ``fingerprint`` helpers use this for their
        verb-specific flags).

        *kinds* filters ``events`` / ``events-all`` by event kind and
        *channels* filters ``user-traces`` / ``user-traces-all`` by channel
        name or code (a bare string or a sequence; sent as ``--kinds a,b``
        / ``--channels a,b``). *threshold_us* is the lateness a ``timers``
        fire must exceed to count as a violation (``--threshold-us``,
        default 500)."""
        if verb not in QUERY_VERBS:
            raise ViewAlyzerError(
                "bad_arguments",
                f"unknown query verb {verb!r}; expected one of {QUERY_VERBS}",
            )
        ref = recording.ref if isinstance(recording, Recording) else str(recording)
        args: List[Any] = ["--query", verb, "--recording", ref]
        if tier is not None:
            args += ["--tier", tier]
        if budget is not None:
            args += ["--budget", budget]
        if t_start_us is not None:
            args += ["--t-start-us", t_start_us]
        if t_end_us is not None:
            args += ["--t-end-us", t_end_us]
        if bucket_us is not None:
            args += ["--bucket-us", bucket_us]
        if max_slices is not None:
            args += ["--max-slices", max_slices]
        if sql is not None:
            args += ["--sql", sql]
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        if kinds:
            args += ["--kinds", ",".join(_as_symbol_list(kinds))]
        if channels:
            args += ["--channels", ",".join(_as_symbol_list(channels))]
        if threshold_us is not None:
            args += ["--threshold-us", threshold_us]
        args += list(extra_flags)
        return self._runner.run_json(
            args, timeout_s if timeout_s is not None else self._query_timeout_s
        )

    # ----- private --------------------------------------------------------

    def _run_ok(self, args: Sequence[Any]) -> None:
        r = self._runner.run(args, self._query_timeout_s)
        text = r.stdout.strip()
        if text.startswith("{"):
            try:
                from .errors import raise_for_envelope

                raise_for_envelope(json.loads(text))
            except json.JSONDecodeError:
                pass
        if r.exit_code != 0:
            raise ViewAlyzerError("command_failed", _capture_failure_detail(r))


def _existing(path: PathLike, what: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ViewAlyzerError("file_not_found", f"{what} not found: {p}")
    return p


def _as_symbol_list(symbols: Union[str, Sequence[str]]) -> List[str]:
    """Accept one symbol as a bare string, or any sequence of symbols."""
    if isinstance(symbols, str):
        return [symbols]
    return list(symbols)


def _warn_symbols_on_capture(
    symbols: Sequence[str], poll_hz: Optional[int], method: str
) -> None:
    """The native-driver CLI's capture verb accepts ``--symbols`` /
    ``--poll-hz`` and ignores them (only the poll verb reads them). Say so
    instead of letting the caller wait for samples that never come."""
    if not symbols and poll_hz is None:
        return
    what = "symbols" if symbols else "poll_hz"
    warnings.warn(
        f"ViewAlyzer.{method}(): {what} are not polled during a capture by "
        "this CLI (the flags are accepted and ignored); the capture runs "
        "without the symbol watch. Use ViewAlyzer.record_polls() for a "
        "memory poll, or a --dwt-watch hardware watch via extra_flags on an "
        "SWO transport.",
        ViewAlyzerWarning,
        stacklevel=3,
    )


def _require_poll_transport(
    config: Optional[ConfigSpec], extra_flags: Sequence[str]
) -> None:
    """The poll verb needs a debug-probe transport; fail before spawning
    when it is knowably missing (no config and no --transport flag, or an
    inline dict without one). A .vacf path is left to the CLI to judge."""
    if config is None:
        if any(
            f == "--transport" or str(f).startswith("--transport=")
            for f in extra_flags
        ):
            return
        raise ViewAlyzerError(
            "bad_arguments",
            "record_polls needs a debug-probe transport: pass config= as a "
            ".vacf path or a dict with at least 'transport' (stlink-rambuf, "
            "stlink-swo, stlink-rtt, jlink-rambuf, jlink-rtt, jlink-swo) and "
            "'target-device'. A bare target_device is not enough for this CLI.",
        )
    if isinstance(config, Mapping) and not config.get("transport"):
        raise ViewAlyzerError(
            "bad_arguments",
            "record_polls config dict needs a 'transport' key naming a "
            "debug-probe transport (stlink-rambuf, stlink-swo, stlink-rtt, "
            "jlink-rambuf, jlink-rtt, jlink-swo).",
        )


def _json_or_ref(value: Any, *, path_ok: bool = False) -> str:
    """A ``--caps`` / ``--hardware-trace`` argument: dicts become JSON
    text, ``@file`` references and JSON strings pass through, and (when
    *path_ok*) an existing file path becomes ``@path``."""
    if isinstance(value, Mapping):
        return json.dumps(dict(value))
    text = str(value)
    if text.startswith("@") or text.lstrip().startswith("{"):
        return text
    if path_ok and Path(text).is_file():
        return "@" + text
    return text


def _raise_any_error_envelope(stdout: str) -> None:
    """Scan capture-mode stdout for a JSON error envelope and raise it.
    Capture output mixes progress lines with (at most one) envelope."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                from .errors import raise_for_envelope

                raise_for_envelope(payload)


def _tool_path_flags(
    jlink_path: Optional[PathLike],
    stlink_path: Optional[PathLike],
    cube_programmer_path: Optional[PathLike],
    arm_gdb_path: Optional[PathLike],
) -> List[str]:
    flags: List[str] = []
    if jlink_path is not None:
        flags += ["--jlink", str(jlink_path)]
    if stlink_path is not None:
        flags += ["--stlink", str(stlink_path)]
    if cube_programmer_path is not None:
        flags += ["--cube-programmer", str(cube_programmer_path)]
    if arm_gdb_path is not None:
        flags += ["--arm-gdb", str(arm_gdb_path)]
    return flags


def _capture_failure_detail(r: Any) -> str:
    """The CLI writes its `[headless] ERROR: ...` diagnostics to *stdout*;
    prefer those lines over a bare exit code."""
    combined = f"{r.stdout}\n{r.stderr}"
    errors = [ln.strip() for ln in combined.splitlines() if "ERROR" in ln]
    if errors:
        return "; ".join(dict.fromkeys(errors))
    if r.stderr.strip():
        return r.stderr.strip()
    tail = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()][-3:]
    if tail:
        return f"exited {r.exit_code}: {' / '.join(tail)}"
    return f"ViewAlyzer exited {r.exit_code}"


class _config_path:
    """Context manager turning a ConfigSpec into an on-disk path. Inline
    dicts are written to a temp ``.vacf`` and removed afterwards."""

    def __init__(self, config: ConfigSpec) -> None:
        self._config = config
        self._temp: Optional[Path] = None

    def __enter__(self) -> Path:
        if isinstance(self._config, Mapping):
            fd, name = tempfile.mkstemp(suffix=".vacf", prefix="viewalyzer-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dict(self._config), f)
            self._temp = Path(name)
            return self._temp
        return _existing(self._config, "connection config")

    def __exit__(self, *exc: Any) -> None:
        if self._temp is not None:
            try:
                self._temp.unlink()
            except OSError:
                pass
