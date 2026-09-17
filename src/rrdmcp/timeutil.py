from datetime import UTC, datetime


def try_parse_iso8601(value: str) -> int | None:
    """Convert an ISO 8601 timestamp string to a Unix epoch second.

    Returns None if `value` is not a valid ISO 8601 string (e.g. rrdtool
    AT-STYLE expressions like "-1h"/"now", or a bare unix timestamp).
    Naive (timezone-less) timestamps are assumed to be UTC.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def normalize_time_to_epoch(value: str) -> int:
    """Convert a unix timestamp string or an ISO 8601 string to a Unix epoch second.

    Raises ValueError if `value` is neither (e.g. rrdtool AT-STYLE
    expressions like "-1h"/"now", which only rrd.py's rrdtool-backed path
    understands). Callers that need a domain-specific error message should
    catch ValueError and re-raise their own exception.
    """
    epoch = try_parse_iso8601(value)
    if epoch is not None:
        return epoch
    return int(value)
