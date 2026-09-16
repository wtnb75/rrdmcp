import subprocess
from pathlib import Path

import pytest

from rrdmcp import sar
from rrdmcp.errors import (
    SarFileNotAvailableError,
    SarInvalidTimeError,
    SarToolTimeoutError,
)
from rrdmcp.sar import _date_range, _normalize_time_to_epoch, fetch, render_graph

SAR_GROUP = "sargroup"
SAR_HOST = "sarhost.example.com"


def test_normalize_time_to_epoch_accepts_unix_timestamp_string():
    assert _normalize_time_to_epoch("1757246400") == 1757246400


def test_normalize_time_to_epoch_accepts_iso8601():
    from datetime import UTC, datetime

    result = _normalize_time_to_epoch("2026-09-07T12:00:00Z")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_normalize_time_to_epoch_rejects_relative_expressions():
    with pytest.raises(SarInvalidTimeError):
        _normalize_time_to_epoch("-1h")
    with pytest.raises(SarInvalidTimeError):
        _normalize_time_to_epoch("now")


def test_date_range_spans_multiple_days():
    from datetime import UTC, datetime

    start = datetime(2026, 9, 14, 23, 0, 0, tzinfo=UTC)
    end = datetime(2026, 9, 16, 1, 0, 0, tzinfo=UTC)
    days = _date_range(start, end)
    assert [d.day for d in days] == [14, 15, 16]


def test_date_range_single_day():
    from datetime import UTC, datetime

    start = datetime(2026, 9, 16, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 9, 16, 23, 0, 0, tzinfo=UTC)
    days = _date_range(start, end)
    assert [d.day for d in days] == [16]


def test_fetch_returns_empty_points_when_no_sa_files_exist(tmp_path: Path):
    result = fetch(tmp_path, "cpu-load.all", "usr", "1757246400", "1757250000")
    assert result.points == []
    assert result.ds_names == ["usr"]


def test_fetch_raises_file_not_available_on_called_process_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host_dir = tmp_path / SAR_GROUP / SAR_HOST
    host_dir.mkdir(parents=True)
    (host_dir / "sa05").write_text("")

    monkeypatch.setattr(sar.shutil, "which", lambda name: "/usr/bin/sadf")

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="boom")

    monkeypatch.setattr(sar.subprocess, "run", fake_run)

    with pytest.raises(SarFileNotAvailableError):
        fetch(
            host_dir,
            "cpu-load.all",
            "usr",
            "2020-01-05T00:00:00Z",
            "2020-01-05T01:00:00Z",
        )


def test_fetch_raises_timeout_error_when_sadf_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host_dir = tmp_path / SAR_GROUP / SAR_HOST
    host_dir.mkdir(parents=True)
    (host_dir / "sa05").write_text("")

    monkeypatch.setattr(sar.shutil, "which", lambda name: "/usr/bin/sadf")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=sar.SADF_TIMEOUT_SECONDS)

    monkeypatch.setattr(sar.subprocess, "run", fake_run)

    with pytest.raises(SarToolTimeoutError):
        fetch(
            host_dir,
            "cpu-load.all",
            "usr",
            "2020-01-05T00:00:00Z",
            "2020-01-05T01:00:00Z",
        )


def test_fetch_raises_file_not_available_on_unparseable_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host_dir = tmp_path / SAR_GROUP / SAR_HOST
    host_dir.mkdir(parents=True)
    (host_dir / "sa05").write_text("")

    monkeypatch.setattr(sar.shutil, "which", lambda name: "/usr/bin/sadf")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 0, stdout="not valid json", stderr=""
        )

    monkeypatch.setattr(sar.subprocess, "run", fake_run)

    with pytest.raises(SarFileNotAvailableError):
        fetch(
            host_dir,
            "cpu-load.all",
            "usr",
            "2020-01-05T00:00:00Z",
            "2020-01-05T01:00:00Z",
        )


def test_fetch_dedups_and_filters_points_for_range_spanning_month_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A range >31 days maps multiple calendar days onto the same sa* file
    (day-of-month collision, e.g. Jan 5 / Feb 5 / Mar 5 all -> sa05).

    fetch() must (a) read that file only once per call rather than once per
    colliding day, and (b) filter out any points whose real timestamp falls
    outside the requested [start, end] window even though _extract_points
    read them correctly.
    """
    from datetime import UTC, datetime

    host_dir = tmp_path / SAR_GROUP / SAR_HOST
    host_dir.mkdir(parents=True)
    (host_dir / "sa05").write_text("")

    call_count = {"sa05": 0}

    def fake_run_sadf(sa_file, start_hms, end_hms):
        if sa_file.name == "sa05":
            call_count["sa05"] += 1
        return {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {
                                "timestamp": {
                                    "date": "2019-12-01",
                                    "time": "00:00:00",
                                },
                                "cpu-load": [{"cpu": "all", "usr": 999.0}],
                            },
                            {
                                "timestamp": {
                                    "date": "2020-01-05",
                                    "time": "10:00:00",
                                },
                                "cpu-load": [{"cpu": "all", "usr": 1.0}],
                            },
                        ]
                    }
                ]
            }
        }

    monkeypatch.setattr(sar, "_run_sadf", fake_run_sadf)

    start = datetime(2020, 1, 5, 0, 0, 0, tzinfo=UTC)
    end = datetime(2020, 3, 5, 23, 0, 0, tzinfo=UTC)
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())

    result = fetch(host_dir, "cpu-load.all", "usr", str(start_epoch), str(end_epoch))

    # sa05 is hit by day-of-month collisions for Jan 5, Feb 5, and Mar 5
    # within this range, but must only be read (and its points merged) once.
    assert call_count["sa05"] == 1

    timestamps = [ts for ts, _ in result.points]
    assert len(timestamps) == len(set(timestamps))
    assert all(start_epoch <= ts <= end_epoch for ts in timestamps)
    # The 2019-12-01 point is outside the requested window and must be
    # filtered out even though it was read successfully.
    assert not any(v == 999.0 for _, v in result.points)


def test_fetch_raises_file_not_available_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host_dir = tmp_path / SAR_GROUP / SAR_HOST
    host_dir.mkdir(parents=True)
    (host_dir / "sa05").write_text("")

    monkeypatch.setattr(sar.shutil, "which", lambda name: "/usr/bin/sadf")

    def fake_run(*args, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(sar.subprocess, "run", fake_run)

    with pytest.raises(SarFileNotAvailableError):
        fetch(
            host_dir,
            "cpu-load.all",
            "usr",
            "2020-01-05T00:00:00Z",
            "2020-01-05T01:00:00Z",
        )


def test_extract_points_skips_statistics_entries_missing_date_or_time():
    from rrdmcp.sar import _extract_points

    data = {
        "sysstat": {
            "hosts": [
                {
                    "statistics": [
                        {
                            "timestamp": {"time": "10:00:00"},  # missing date
                            "cpu-load": [{"cpu": "all", "usr": 1.0}],
                        },
                        {
                            "timestamp": {"date": "2020-01-05"},  # missing time
                            "cpu-load": [{"cpu": "all", "usr": 2.0}],
                        },
                        {
                            "timestamp": {
                                "date": "2020-01-05",
                                "time": "11:00:00",
                            },
                            "cpu-load": [{"cpu": "all", "usr": 3.0}],
                        },
                    ]
                }
            ]
        }
    }
    points = _extract_points(data, "cpu-load.all", "usr")
    assert len(points) == 1
    assert points[0][1] == 3.0


def test_fetch_returns_points_from_real_sar_log(sar_root: Path):
    host_dir = sar_root / SAR_GROUP / SAR_HOST
    yesterday_epoch = int(__import__("time").time()) - 60
    now_epoch = int(__import__("time").time()) + 60
    result = fetch(
        host_dir, "cpu-load.all", "usr", str(yesterday_epoch), str(now_epoch)
    )
    assert result.ds_names == ["usr"]
    assert len(result.points) > 0
    assert all(isinstance(ts, int) for ts, _ in result.points)


def test_render_graph_returns_png_bytes():
    points_a = [(1000, 10.0), (1010, 20.0), (1020, 15.0)]
    points_b = [(1000, 5.0), (1010, 8.0), (1020, 6.0)]
    png = render_graph(
        [(points_a, "User"), (points_b, "System")],
        "1000",
        "1020",
        "CPU usage",
        "%",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_graph_handles_none_values():
    points = [(1000, 10.0), (1010, None), (1020, 15.0)]
    png = render_graph([(points, "User")], "1000", "1020", "CPU usage", "%")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
