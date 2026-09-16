import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .errors import (
    SarFileNotAvailableError,
    SarInvalidTimeError,
    SarToolNotFoundError,
    SarToolTimeoutError,
)
from .sar_index import walk_statistics
from .timeutil import try_parse_iso8601

SADF_TIMEOUT_SECONDS = 30


@dataclass
class FetchResult:
    step: int
    ds_names: list[str]
    points: list[tuple[int, float | None]]


def _normalize_time_to_epoch(value: str) -> int:
    epoch = try_parse_iso8601(value)
    if epoch is not None:
        return epoch
    try:
        return int(value)
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
        dt = datetime.strptime(
            f"{ts['date']} {ts['time']}", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=UTC)
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
    for day in _date_range(start_dt, end_dt):
        sa_file = _sa_file_for_date(host_dir, day)
        if not sa_file.exists():
            continue
        start_hms = start_dt.strftime("%H:%M:%S") if day == start_dt.date() else None
        end_hms = end_dt.strftime("%H:%M:%S") if day == end_dt.date() else None
        data = _run_sadf(sa_file, start_hms, end_hms)
        all_points.extend(_extract_points(data, plugin, field))

    all_points.sort(key=lambda p: p[0])
    return FetchResult(
        step=_infer_step(all_points), ds_names=[field], points=all_points
    )
