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
