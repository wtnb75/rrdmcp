import os
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from . import discovery, rrd
from .errors import RrdMcpError
from .munin_datafile import load_datafile

mcp = MCPServer("rrdmcp")


def _base_path() -> Path:
    return Path(os.environ.get("MUNIN_RRD_BASE_PATH", "/var/lib/munin"))


def _datafile_path() -> Path:
    default = str(_base_path() / "datafile")
    return Path(os.environ.get("MUNIN_DATAFILE_PATH", default))


def _load_entries() -> list[discovery.NormalizedField]:
    base_path = _base_path()
    datafile_path = _datafile_path()
    datafile_index = load_datafile(datafile_path) if datafile_path.exists() else None
    return discovery.build_index(base_path, datafile_index)


@mcp.tool()
def list_hosts() -> list[dict] | dict:
    """List all (group, host) pairs discovered from the Munin datafile."""
    try:
        return discovery.list_hosts(_load_entries())
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_plugins(group: str, host: str) -> list[dict] | dict:
    """List plugins for a given host."""
    try:
        return discovery.list_plugins(_load_entries(), group, host)
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_fields(group: str, host: str, plugin: str) -> list[dict] | dict:
    """List fields for a given plugin."""
    try:
        return discovery.list_fields(_load_entries(), group, host, plugin)
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_metadata(group: str, host: str, plugin: str, field: str | None = None) -> dict:
    """Get metadata for a plugin, or a single field within it if `field` is given."""
    try:
        entries = _load_entries()
        if field is None:
            fields = discovery.list_fields(entries, group, host, plugin)
            plugins = discovery.list_plugins(entries, group, host)
            plugin_info = next(p for p in plugins if p["plugin"] == plugin)
            return {**plugin_info, "fields": fields}
        resolved = discovery.resolve_field(entries, group, host, plugin, field)
        return {
            "field": resolved.field,
            "label": resolved.meta.label,
            "type": resolved.meta.type,
            "min": resolved.meta.min,
            "max": resolved.meta.max,
            "warning": resolved.meta.warning,
            "critical": resolved.meta.critical,
            "info": resolved.meta.info,
            "extra": resolved.meta.extra,
            "rrd_available": resolved.rrd_available,
            "metadata_available": resolved.metadata_available,
        }
    except RrdMcpError as exc:
        return {"error": str(exc)}


def _aggregate_points(
    points: list[tuple[int, float | None]], resolution: int
) -> list[dict]:
    """Group points into UTC-epoch-aligned buckets of `resolution` seconds.

    Buckets with no non-None values in range are omitted entirely.
    """
    buckets: dict[int, list[float]] = {}
    for ts, value in points:
        if value is None:
            continue
        bucket_start = (ts // resolution) * resolution
        buckets.setdefault(bucket_start, []).append(value)
    return [
        {
            "start": bucket_start,
            "avg": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "count": len(values),
        }
        for bucket_start, values in sorted(buckets.items())
    ]


def _summarize_points(points: list[tuple[int, float | None]]) -> dict:
    """Aggregate all non-None values in `points` into a single avg/min/max/count."""
    values = [value for _, value in points if value is not None]
    if not values:
        return {"avg": None, "min": None, "max": None, "count": 0}
    return {
        "avg": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "count": len(values),
    }


@mcp.tool()
def fetch_series(
    group: str,
    host: str,
    plugin: str,
    field: str,
    start: str,
    end: str,
    resolution: int | None = None,
    summary: bool = False,
    top_n: int | None = None,
    top_by: str = "avg",
    order: str = "desc",
) -> dict:
    """Fetch time series data for a single field.

    `start`/`end` accept a unix timestamp or any string rrdtool understands
    (e.g. "-1d", "now").

    If `resolution` (seconds) is given, points are aggregated into
    UTC-epoch-aligned buckets of that size (avg/min/max/count) instead of
    returning every raw sample — use this for long time ranges to avoid
    returning hundreds of raw points.

    If `summary` is true, the whole range is aggregated into a single
    avg/min/max/count instead of buckets or raw points. Cannot be combined
    with `resolution`.

    If `top_n` is given (requires `resolution`), only the N buckets with the
    highest (or, with `order="asc"`, lowest) `top_by` value ("avg", "min", or
    "max") are returned, sorted by that value; `total_buckets` in the result
    reports how many buckets existed before filtering.
    """
    try:
        if resolution is not None and resolution <= 0:
            return {"error": "resolution must be a positive number of seconds"}
        if summary and resolution is not None:
            return {"error": "summary and resolution cannot be used together"}
        if top_n is not None and resolution is None:
            return {"error": "top_n requires resolution to be set"}
        if top_n is not None and top_n <= 0:
            return {"error": "top_n must be a positive integer"}
        if top_by not in ("avg", "min", "max"):
            return {"error": "top_by must be one of 'avg', 'min', 'max'"}
        if order not in ("asc", "desc"):
            return {"error": "order must be one of 'asc', 'desc'"}
        entries = _load_entries()
        resolved = discovery.resolve_field(entries, group, host, plugin, field)
        if not resolved.rrd_available:
            return {
                "error": f"RRD file not available for {group}/{host}/{plugin}/{field}"
            }
        result = rrd.fetch(resolved.path, start, end)
        if summary:
            return {
                "step": result.step,
                "ds_names": result.ds_names,
                "summary": _summarize_points(result.points),
            }
        if resolution is not None:
            buckets = _aggregate_points(result.points, resolution)
            response = {
                "step": result.step,
                "resolution": resolution,
                "ds_names": result.ds_names,
            }
            if top_n is not None:
                buckets = sorted(
                    buckets, key=lambda b: b[top_by], reverse=(order == "desc")
                )
                response["total_buckets"] = len(buckets)
                buckets = buckets[:top_n]
            response["buckets"] = buckets
            return response
        return {
            "step": result.step,
            "ds_names": result.ds_names,
            "points": [{"timestamp": ts, "value": val} for ts, val in result.points],
        }
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def render_graph(
    group: str,
    host: str,
    plugin: str,
    fields: list[str],
    start: str,
    end: str,
    width: int = 800,
    height: int = 300,
) -> "Image | dict":
    """Render a PNG graph overlaying the given fields of a plugin."""
    try:
        entries = _load_entries()
        discovery._require_plugin(entries, group, host, plugin)
        paths_and_labels = []
        for field in fields:
            resolved = discovery.resolve_field(entries, group, host, plugin, field)
            if not resolved.rrd_available:
                return {
                    "error": f"RRD file not available for {group}/{host}/{plugin}/{field}"
                }
            label = resolved.meta.label or field
            paths_and_labels.append((resolved.path, label))
        plugins = discovery.list_plugins(entries, group, host)
        plugin_info = next(p for p in plugins if p["plugin"] == plugin)
        title = plugin_info["graph_title"] or f"{host} {plugin}"
        vlabel = plugin_info["graph_vlabel"] or ""
        png_bytes = rrd.render_graph(
            paths_and_labels, start, end, title, vlabel, width, height
        )
        return Image(data=png_bytes, format="png")
    except RrdMcpError as exc:
        return {"error": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
