"""StreamSession against the fake CLI: the full --stream lifecycle, with
the native CLI's feed (stream_meta carrying `display`, float values, no
is_float, no stream_end, and the itm_text / swo_load / pc_samples /
dwt_data / exc lines)."""
import json
import time

import pytest

from viewalyzer_sdk import (
    Recording,
    StreamEvent,
    StreamMeta,
    StreamSample,
    ViewAlyzerError,
    ViewAlyzerWarning,
)

CFG = {"transport": "stlink-rambuf", "target-device": "STM32L031K6"}

EVENT_KINDS = ("itm_text", "swo_load", "pc_samples", "dwt_data", "exc")


def test_stream_yields_samples_then_recording(va, tmp_path):
    out = tmp_path / "live.vadb"
    with va.stream(CFG, output=out, duration_s=0.5) as s:
        samples = list(s)
    rec = s.result()

    assert samples, "no samples arrived"
    assert all(isinstance(x, StreamSample) for x in samples)

    # Meta resolution: the pre-announced stream names its samples...
    beats = [x for x in samples if x.id == 3]
    assert beats and all(x.name == "Beat" for x in beats)
    assert beats[0].stream_type == "counter"  # from the line's `display` key
    assert beats[1].t_s == pytest.approx(beats[1].t_us / 1e6)
    # ...and so does the one registered mid-capture.
    loads = [x for x in samples if x.id == 4]
    assert loads and all(x.name == "Load" for x in loads)
    # This CLI sends every value as a JSON float and no is_float flag.
    assert all(isinstance(x.value, float) for x in samples)
    assert all(x.is_float is False for x in samples)

    assert s.streams == {
        3: StreamMeta(3, "Beat", "counter"),
        4: StreamMeta(4, "Load", "graph"),
    }
    assert s.init["transport"] == "stlink-rambuf"
    assert s.init["t"] == "stream_init"

    # Diagnostic stderr lines are kept out of the sample feed but visible;
    # stream lines (JSON with a `t` key) never land in the log.
    assert any("draining ring buffer" in ln for ln in s.log)
    assert not any(ln.startswith("{") for ln in s.log)

    assert isinstance(rec, Recording)
    assert rec.path == out
    assert rec.recording_id == "abcdef123456"
    assert rec.total_events > 0
    assert s.returncode == 0
    assert s.result() is rec  # idempotent


def test_stream_early_stop_finalizes_partial(va, tmp_path):
    t0 = time.monotonic()
    with va.stream(CFG, output=tmp_path / "part.vadb", duration_s=60) as s:
        seen = 0
        for _sample in s:
            seen += 1
            if seen == 3:
                s.stop()  # iteration then ends when the CLI closes stderr
    elapsed = time.monotonic() - t0
    rec = s.result()
    assert seen >= 3
    assert elapsed < 30, "stop() did not shorten a 60 s capture"
    assert rec.path.is_file()


def test_stream_abandoned_session_is_stopped_by_close(va, tmp_path):
    t0 = time.monotonic()
    with va.stream(CFG, output=tmp_path / "aband.vadb", duration_s=60) as s:
        for _sample in s:
            break  # walk away without stop(); __exit__ must clean up
    assert time.monotonic() - t0 < 30
    assert s.returncode == 0
    assert s.result().path.is_file()


def test_stream_timeout_kills_the_cli(va, tmp_path):
    with va.stream(
        {"transport": "hang"}, output=tmp_path / "x.vadb",
        duration_s=60, timeout_s=1.0,
    ) as s:
        with pytest.raises(ViewAlyzerError) as ei:
            for _sample in s:
                pass
        assert ei.value.code == "timeout"
    assert s.returncode is not None, "CLI outlived the timeout"


def test_stream_connect_failure_surfaces_cli_error(va, tmp_path):
    with va.stream(
        {"transport": "fail"}, output=tmp_path / "x.vadb", duration_s=5
    ) as s:
        assert list(s) == []  # feed ends without samples
        with pytest.raises(ViewAlyzerError) as ei:
            s.result()
    assert ei.value.code == "record_failed"
    assert "Failed to connect" in ei.value.message


def test_stream_symbols_require_elf(va, tmp_path):
    with pytest.raises(ViewAlyzerError) as ei:
        va.stream(CFG, output=tmp_path / "x.vadb", duration_s=1,
                  symbols=["tick_counter"])
    assert ei.value.code == "bad_arguments"


def test_stream_symbols_warn_not_polled(va, tmp_path):
    elf = tmp_path / "fw.elf"
    elf.write_bytes(b"\x7fELF")
    with pytest.warns(ViewAlyzerWarning, match="not polled during a capture"):
        s = va.stream(CFG, output=tmp_path / "w.vadb", duration_s=0.2,
                      elf=elf, symbols=["tick_counter:u32"], poll_hz=50)
    with s:
        list(s)
    assert s.result().total_events > 0


# ----- events(): the whole feed --------------------------------------------


def test_events_delivers_every_kind_in_arrival_order(va, tmp_path):
    with va.stream(CFG, output=tmp_path / "ev.vadb", duration_s=0.5) as s:
        feed = list(s.events())
    rec = s.result()
    assert rec.total_events > 0

    assert all(isinstance(x, (StreamSample, StreamEvent)) for x in feed)
    samples = [x for x in feed if isinstance(x, StreamSample)]
    events = [x for x in feed if isinstance(x, StreamEvent)]
    assert samples and events

    # The banner comes first, the pre-announced meta before its first sample,
    # the late meta before the first sample of ITS stream.
    assert feed[0].t == "stream_init" and feed[0].data["transport"] == "stlink-rambuf"
    kinds = [x.t for x in events]
    metas = [x for x in events if x.t == "stream_meta"]
    assert [m.data["id"] for m in metas] == [3, 4]
    first_load = next(i for i, x in enumerate(feed)
                      if isinstance(x, StreamSample) and x.id == 4)
    late_meta = next(i for i, x in enumerate(feed)
                     if isinstance(x, StreamEvent) and x.t == "stream_meta"
                     and x.data["id"] == 4)
    assert late_meta < first_load

    # Every capture event kind the native CLI emits, with its payload intact.
    for kind in EVENT_KINDS:
        assert kind in kinds, kind
    by_kind = {x.t: x for x in events}
    itm = by_kind["itm_text"]
    assert itm.data["port"] == 0 and itm.data["text"].startswith("boot ok")
    assert itm.t_us is not None and itm.t_s == pytest.approx(itm.t_us / 1e6)
    assert by_kind["swo_load"].data["share_pct"] == 50.0
    assert by_kind["swo_load"].t_us is None and by_kind["swo_load"].t_s is None
    assert by_kind["pc_samples"].data["pcs"] == [134217728, 134217984]
    row = by_kind["dwt_data"].data["rows"][0]
    assert row["cmp"] == 0 and row["v"] == 42 and row["w"] is True
    assert by_kind["exc"].data["exceptions"][0]["name"] == "SysTick"
    # No stream_end from this CLI: the feed ended on EOF.
    assert "stream_end" not in kinds

    # Samples still resolve their meta, and nothing leaked into the log.
    assert all(x.name in ("Beat", "Load") for x in samples)
    assert not any(ln.startswith("{") for ln in s.log)
    assert any("draining" in ln for ln in s.log)
    assert s.streams[4] == StreamMeta(4, "Load", "graph")


def test_events_are_frozen_and_carry_the_whole_line(va, tmp_path):
    with va.stream(CFG, output=tmp_path / "fz.vadb", duration_s=0.3) as s:
        ev = next(x for x in s.events() if isinstance(x, StreamEvent)
                  and x.t == "itm_text")
        s.stop()
        for _ in s.events():
            pass
    assert ev.data["t"] == "itm_text"
    assert json.dumps(ev.data)  # plain JSON-able dict
    with pytest.raises(AttributeError):
        ev.t = "other"  # frozen dataclass


def test_events_and_iteration_are_mutually_exclusive_per_session(va, tmp_path):
    with va.stream(CFG, output=tmp_path / "mx.vadb", duration_s=60) as s:
        it = iter(s)
        next(it)  # plain iteration has claimed the session
        with pytest.raises(ViewAlyzerError) as e:
            next(s.events())
        assert e.value.code == "bad_arguments"
        assert "one consumer" in e.value.message
        s.stop()
        for _ in it:
            pass
    assert s.result().path.is_file()
    # Once the feed has ended either consumer just returns empty.
    assert list(s.events()) == []
    assert list(s) == []

    with va.stream(CFG, output=tmp_path / "mx2.vadb", duration_s=60) as s:
        ev = s.events()
        next(ev)
        with pytest.raises(ViewAlyzerError):
            next(iter(s))
        s.stop()
        for _ in ev:
            pass
    assert s.result().path.is_file()


def test_events_early_stop_and_timeout_behave_like_iteration(va, tmp_path):
    t0 = time.monotonic()
    with va.stream(CFG, output=tmp_path / "es.vadb", duration_s=60) as s:
        n = 0
        for _ in s.events():
            n += 1
            if n == 5:
                s.stop()
    assert time.monotonic() - t0 < 30
    assert s.result().path.is_file()

    with va.stream({"transport": "hang"}, output=tmp_path / "x.vadb",
                   duration_s=60, timeout_s=1.0) as s:
        with pytest.raises(ViewAlyzerError) as ei:
            for _ in s.events():
                pass
        assert ei.value.code == "timeout"
