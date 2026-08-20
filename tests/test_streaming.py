"""StreamSession against the fake CLI: the full --stream lifecycle."""
import time

import pytest

from viewalyzer_sdk import Recording, StreamMeta, StreamSample, ViewAlyzerError

CFG = {"transport": "stlink-rambuf", "target-device": "STM32L031K6"}


def test_stream_yields_samples_then_recording(va, tmp_path):
    out = tmp_path / "live.vadb"
    with va.stream(CFG, output=out, duration_s=0.3) as s:
        samples = list(s)
    rec = s.result()

    assert samples, "no samples arrived"
    assert all(isinstance(x, StreamSample) for x in samples)

    # Meta resolution: the pre-announced stream names its samples...
    beats = [x for x in samples if x.id == 3]
    assert beats and all(x.name == "Beat" for x in beats)
    assert beats[0].stream_type == "counter"
    assert beats[0].is_float is False
    assert beats[1].t_s == pytest.approx(beats[1].t_us / 1e6)
    # ...and so does the one registered mid-capture.
    loads = [x for x in samples if x.id == 4]
    assert loads and all(x.name == "Load" for x in loads)
    assert all(x.is_float for x in loads)

    assert s.streams == {
        3: StreamMeta(3, "Beat", "counter"),
        4: StreamMeta(4, "Load", "graph"),
    }
    assert s.init["duration_s"] == pytest.approx(0.3)

    # Diagnostic stderr lines are kept out of the sample feed but visible.
    assert any("draining ring buffer" in ln for ln in s.log)

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
                s.stop()  # iteration then ends on the CLI's stream_end
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
