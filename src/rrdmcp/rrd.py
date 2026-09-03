import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import RrdFileNotAvailableError, RrdToolNotFoundError, RrdToolTimeoutError

RRDTOOL_TIMEOUT_SECONDS = 30

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.]")
_GRAPH_COLORS = ["#0000FF", "#FF0000", "#00AA00", "#FF8800", "#AA00AA", "#00AAAA"]


def sanitize_name(name: str) -> str:
    return _SANITIZE_RE.sub("_", name)


def type_letter(ds_type: str) -> str:
    return ds_type[:1].lower()


def rrd_path(
    base_path: Path, group: str, host: str, plugin: str, field: str, ds_type: str
) -> Path:
    filename = f"{host}-{sanitize_name(plugin)}-{sanitize_name(field)}-{type_letter(ds_type)}.rrd"
    return base_path / group / filename


def require_rrdtool() -> str:
    exe = shutil.which("rrdtool")
    if exe is None:
        raise RrdToolNotFoundError("rrdtool command not found in PATH")
    return exe


def _run_rrdtool(args: list[str], text: bool = True) -> subprocess.CompletedProcess:
    exe = require_rrdtool()
    try:
        return subprocess.run(
            [exe, *args],
            capture_output=True,
            text=text,
            timeout=RRDTOOL_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RrdToolTimeoutError(
            f"rrdtool {args[0]} timed out after {RRDTOOL_TIMEOUT_SECONDS}s"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise RrdFileNotAvailableError(
            f"rrdtool {args[0]} failed: {(stderr or '').strip()}"
        ) from exc


@dataclass
class FetchResult:
    step: int
    ds_names: list[str]
    points: list[tuple[int, float | None]]


def _infer_step(points: list[tuple[int, float | None]]) -> int:
    if len(points) < 2:
        return 0
    return points[1][0] - points[0][0]


def fetch(path: Path, start: str, end: str, cf: str = "AVERAGE") -> FetchResult:
    if not path.exists():
        raise RrdFileNotAvailableError(f"RRD file not found: {path}")
    proc = _run_rrdtool(
        ["fetch", str(path), cf, "--start", str(start), "--end", str(end)]
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    ds_names = lines[0].split()
    points: list[tuple[int, float | None]] = []
    for line in lines[1:]:
        ts_str, _, rest = line.partition(":")
        values = rest.split()
        # Munin RRD files always have exactly one DS, literally named "42".
        raw_value = values[0] if values else "nan"
        parsed_value = None if raw_value.lower() == "nan" else float(raw_value)
        points.append((int(ts_str), parsed_value))
    return FetchResult(step=_infer_step(points), ds_names=ds_names, points=points)


def info(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RrdFileNotAvailableError(f"RRD file not found: {path}")
    proc = _run_rrdtool(["info", str(path)])
    result: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"')
    return result


def render_graph(
    paths_and_labels: list[tuple[Path, str]],
    start: str,
    end: str,
    title: str,
    vlabel: str,
    width: int = 800,
    height: int = 300,
) -> bytes:
    for path, _ in paths_and_labels:
        if not path.exists():
            raise RrdFileNotAvailableError(f"RRD file not found: {path}")
    args = [
        "graph",
        "-",
        "--start",
        str(start),
        "--end",
        str(end),
        "--title",
        title,
        "--vertical-label",
        vlabel,
        "--width",
        str(width),
        "--height",
        str(height),
        "--imgformat",
        "PNG",
    ]
    for idx, (path, label) in enumerate(paths_and_labels):
        ds_name = f"v{idx}"
        color = _GRAPH_COLORS[idx % len(_GRAPH_COLORS)]
        args.append(f"DEF:{ds_name}={path}:42:AVERAGE")
        args.append(f"LINE1:{ds_name}{color}:{label}")
    proc = _run_rrdtool(args, text=False)
    return proc.stdout
