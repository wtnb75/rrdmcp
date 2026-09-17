import re
from datetime import datetime

UTMP_TYPE_NAMES: dict[int, str] = {
    0: "EMPTY",
    1: "RUN_LVL",
    2: "BOOT_TIME",
    3: "NEW_TIME",
    4: "OLD_TIME",
    5: "INIT_PROCESS",
    6: "LOGIN_PROCESS",
    7: "USER_PROCESS",
    8: "DEAD_PROCESS",
    9: "ACCOUNTING",
}

_BRACKET_RE = re.compile(r"\[([^\]]*)\]")


def _parse_line(line: str) -> dict | None:
    """Parse one `utmpdump` output line into a raw record dict, or None if unparseable.

    `utmpdump` (util-linux) prints one utmp/wtmp/btmp record per line in a
    fixed bracketed form:
    [type] [pid] [id] [user] [line] [host] [addr] [ISO 8601 timestamp]
    """
    groups = _BRACKET_RE.findall(line)
    if len(groups) != 8:
        return None
    type_raw, pid_raw, _id, user, line_field, host, _addr, ts_raw = (
        g.strip() for g in groups
    )
    try:
        type_num = int(type_raw)
        pid = int(pid_raw)
        dt = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%S,%f%z")
    except ValueError:
        return None
    return {
        "type_num": type_num,
        "timestamp": int(dt.timestamp()),
        "type": UTMP_TYPE_NAMES.get(type_num, str(type_num)),
        "user": user,
        "line": line_field,
        "host": host,
        "pid": pid,
    }


def _parse_utmpdump_output(text: str) -> list[dict]:
    """Parse full `utmpdump` stdout into event records, dropping EMPTY slots."""
    records = []
    for raw_line in text.splitlines():
        record = _parse_line(raw_line)
        if record is None or record["type_num"] == 0:
            continue
        del record["type_num"]
        records.append(record)
    return records
