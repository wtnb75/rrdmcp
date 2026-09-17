import io
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .errors import (
    SarFileNotAvailableError,
    SarInvalidTimeError,
    SarToolNotFoundError,
    SarToolTimeoutError,
)
from .rrd import GRAPH_COLORS
from .sar_index import walk_statistics
from .timeutil import normalize_time_to_epoch

SADF_TIMEOUT_SECONDS = 30


@dataclass
class FetchResult:
    step: int
    ds_names: list[str]
    points: list[tuple[int, float | None]]


def _normalize_time_to_epoch(value: str) -> int:
    try:
        return normalize_time_to_epoch(value)
    except ValueError as exc:
        raise SarInvalidTimeError(
            "sar data source requires a unix timestamp or an ISO 8601 "
            f"string (rrdtool-style relative expressions are not supported), got: {value!r}"
        ) from exc


def require_sadf() -> str:
    exe = shutil.which("sadf")
    if exe is None:
        raise SarToolNotFoundError("sadf command not found in PATH")
    return exe


def _sa_file_for_date(host_dir: Path, day: date) -> Path:
    return host_dir / f"sa{day.day:02d}"


def _date_range(start_dt: datetime, end_dt: datetime) -> list[date]:
    days = []
    current = start_dt.date()
    while current <= end_dt.date():
        days.append(current)
        current += timedelta(days=1)
    return days


def _run_sadf(sa_file: Path, start_hms: str | None, end_hms: str | None) -> dict:
    exe = require_sadf()
    args = [exe, "-j"]
    if start_hms is not None:
        args += ["-s", start_hms]
    if end_hms is not None:
        args += ["-e", end_hms]
    args += ["--", "-A", str(sa_file)]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=SADF_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise SarToolTimeoutError(
            f"sadf timed out after {SADF_TIMEOUT_SECONDS}s"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise SarFileNotAvailableError(f"sadf failed on {sa_file}: {stderr}") from exc
    except OSError as exc:
        raise SarFileNotAvailableError(
            f"failed to execute sadf for {sa_file}: {exc}"
        ) from exc
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SarFileNotAvailableError(
            f"sadf produced unparseable JSON output for {sa_file}"
        ) from exc


def _extract_points(
    data: dict, plugin: str, field: str
) -> list[tuple[int, float | None]]:
    hosts = data.get("sysstat", {}).get("hosts", [])
    if not hosts:
        return []
    points: list[tuple[int, float | None]] = []
    for stat in hosts[0].get("statistics", []):
        ts = stat.get("timestamp", {})
        date_str = ts.get("date")
        time_str = ts.get("time")
        if date_str is None or time_str is None:
            continue
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=UTC
        )
        metrics = walk_statistics(stat)
        value = metrics.get(plugin, {}).get(field)
        points.append((int(dt.timestamp()), value))
    return points


def _infer_step(points: list[tuple[int, float | None]]) -> int:
    if len(points) < 2:
        return 0
    return points[1][0] - points[0][0]


def fetch(host_dir: Path, plugin: str, field: str, start: str, end: str) -> FetchResult:
    """Fetch one field's time series by reading and merging daily sar log files.

    `host_dir` is `SAR_BASE_PATH/<group>/<host>/`. `plugin`/`field` must be
    the same names `sar_index.build_index` assigned (e.g. plugin="cpu-load.0",
    field="usr"). Missing daily files are skipped as gaps, never an error.
    """
    start_epoch = _normalize_time_to_epoch(start)
    end_epoch = _normalize_time_to_epoch(end)
    start_dt = datetime.fromtimestamp(start_epoch, tz=UTC)
    end_dt = datetime.fromtimestamp(end_epoch, tz=UTC)

    all_points: list[tuple[int, float | None]] = []
    seen_files: set[Path] = set()
    for day in _date_range(start_dt, end_dt):
        sa_file = _sa_file_for_date(host_dir, day)
        if sa_file in seen_files:
            # Ranges spanning >31 days (or crossing a month boundary) can
            # map two different calendar days onto the same sa* file
            # (e.g. day 5 of two different months both resolve to sa05).
            # Skip it the second time to avoid duplicate/misattributed points.
            continue
        seen_files.add(sa_file)
        if not sa_file.exists():
            continue
        start_hms = start_dt.strftime("%H:%M:%S") if day == start_dt.date() else None
        end_hms = end_dt.strftime("%H:%M:%S") if day == end_dt.date() else None
        data = _run_sadf(sa_file, start_hms, end_hms)
        all_points.extend(_extract_points(data, plugin, field))

    all_points = [
        (ts, value) for ts, value in all_points if start_epoch <= ts <= end_epoch
    ]
    all_points.sort(key=lambda p: p[0])
    return FetchResult(
        step=_infer_step(all_points), ds_names=[field], points=all_points
    )


def render_graph(
    points_and_labels: list[tuple[list[tuple[int, float | None]], str]],
    start: str,
    end: str,
    title: str,
    vlabel: str,
    width: int = 800,
    height: int = 300,
) -> bytes:
    fig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    for idx, (points, label) in enumerate(points_and_labels):
        times = [datetime.fromtimestamp(ts, tz=UTC) for ts, _ in points]
        values = [v for _, v in points]
        color = GRAPH_COLORS[idx % len(GRAPH_COLORS)]
        ax.plot(times, values, label=label, color=color)
    ax.set_title(title)
    ax.set_ylabel(vlabel)
    if points_and_labels:
        ax.legend()
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
