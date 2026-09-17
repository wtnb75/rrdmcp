from datetime import UTC, datetime

import pytest

from rrdmcp.timeutil import normalize_time_to_epoch, try_parse_iso8601


def test_parses_aware_iso8601_to_epoch():
    result = try_parse_iso8601("2026-09-07T12:00:00Z")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_parses_naive_iso8601_as_utc():
    result = try_parse_iso8601("2026-09-07T12:00:00")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_returns_none_for_non_iso8601_strings():
    assert try_parse_iso8601("-1h") is None
    assert try_parse_iso8601("now") is None
    assert try_parse_iso8601("1757246400") is None


def test_normalize_time_to_epoch_accepts_unix_timestamp_string():
    assert normalize_time_to_epoch("1757246400") == 1757246400


def test_normalize_time_to_epoch_accepts_iso8601():
    result = normalize_time_to_epoch("2026-09-07T12:00:00Z")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_normalize_time_to_epoch_raises_value_error_for_relative_expressions():
    with pytest.raises(ValueError):
        normalize_time_to_epoch("-1h")
    with pytest.raises(ValueError):
        normalize_time_to_epoch("now")
