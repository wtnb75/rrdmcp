from pathlib import Path

import pytest

from rrdmcp.errors import SarInvalidTimeError
from rrdmcp.sar import _date_range, _normalize_time_to_epoch, fetch

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
