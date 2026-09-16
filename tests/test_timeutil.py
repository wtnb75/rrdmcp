from datetime import UTC, datetime

from rrdmcp.timeutil import try_parse_iso8601


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
