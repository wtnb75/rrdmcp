import gzip
import re
import shutil
import subprocess
import zlib
from datetime import datetime
from pathlib import Path

from .errors import WtmpToolNotFoundError

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


def _list_kind_files(host_dir: Path, kind: str) -> list[Path]:
    """Enumerate the base file and any rotated/compressed generations for `kind`.

    Matches `{kind}`, `{kind}.1`, `{kind}.2.gz`, etc. Rotation generation
    counts aren't fixed (depends on the deployment's logrotate config), so
    this globs rather than assuming a bound.
    """
    files = []
    plain = host_dir / kind
    if plain.is_file():
        files.append(plain)
    files.extend(sorted(host_dir.glob(f"{kind}.[0-9]*")))
    return files


WTMPDUMP_TIMEOUT_SECONDS = 30


def require_utmpdump() -> str:
    exe = shutil.which("utmpdump")
    if exe is None:
        raise WtmpToolNotFoundError("utmpdump command not found in PATH")
    return exe


def _run_utmpdump(exe: str, path: Path) -> list[dict] | None:
    """Run utmpdump on one wtmp/btmp file and parse its output.

    Returns None (never raises) on any execution failure — a non-zero
    exit, a timeout, or an OS-level error (including a corrupt .gz) — so
    one bad rotated file doesn't abort a fetch spanning several files.
    """
    try:
        if path.suffix == ".gz":
            data = gzip.decompress(path.read_bytes())
            proc = subprocess.run(
                [exe],
                input=data,
                capture_output=True,
                timeout=WTMPDUMP_TIMEOUT_SECONDS,
                check=True,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
        else:
            proc = subprocess.run(
                [exe, str(path)],
                capture_output=True,
                text=True,
                timeout=WTMPDUMP_TIMEOUT_SECONDS,
                check=True,
            )
            stdout = proc.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, EOFError, zlib.error):
        return None
    return _parse_utmpdump_output(stdout)
