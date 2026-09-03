import re
from dataclasses import dataclass
from pathlib import Path

from .errors import FieldNotFoundError, HostNotFoundError, PluginNotFoundError
from .munin_datafile import DatafileIndex, FieldMeta, PluginMeta
from .rrd import rrd_path

_FALLBACK_RE = re.compile(
    r"^(?P<host_plugin>.+)-(?P<field>[^-]+)-(?P<type>[a-z])\.rrd$"
)


@dataclass
class NormalizedField:
    group: str
    host: str
    plugin: str
    field: str
    meta: FieldMeta
    plugin_meta: PluginMeta
    path: Path
    rrd_available: bool
    metadata_available: bool


def fallback_scan(base_path: Path) -> list[dict]:
    """Best-effort discovery when no datafile is available.

    Host/plugin boundaries within `host_plugin` cannot be determined
    reliably from the filename alone (both may contain hyphens), so they
    are reported as a single combined string with no metadata.
    """
    entries: list[dict] = []
    for rrd_file in sorted(base_path.rglob("*.rrd")):
        group = rrd_file.parent.name
        match = _FALLBACK_RE.match(rrd_file.name)
        if not match:
            continue
        entries.append(
            {
                "group": group,
                "host_plugin": match.group("host_plugin"),
                "field": match.group("field"),
                "type": match.group("type"),
                "path": str(rrd_file),
                "metadata_available": False,
            }
        )
    return entries


def _build_from_datafile(
    base_path: Path, datafile_index: DatafileIndex
) -> list[NormalizedField]:
    entries: list[NormalizedField] = []
    for (group, host), plugins in datafile_index.items():
        for plugin_name, plugin_meta in plugins.items():
            for field_name, field_meta in plugin_meta.fields.items():
                ds_type = field_meta.type or "GAUGE"
                path = rrd_path(
                    base_path, group, host, plugin_name, field_name, ds_type
                )
                entries.append(
                    NormalizedField(
                        group=group,
                        host=host,
                        plugin=plugin_name,
                        field=field_name,
                        meta=field_meta,
                        plugin_meta=plugin_meta,
                        path=path,
                        rrd_available=path.exists(),
                        metadata_available=True,
                    )
                )
    return entries


def _build_from_fallback(base_path: Path) -> list[NormalizedField]:
    entries: list[NormalizedField] = []
    for raw in fallback_scan(base_path):
        entries.append(
            NormalizedField(
                group=raw["group"],
                host=raw["host_plugin"],
                plugin="",
                field=raw["field"],
                meta=FieldMeta(),
                plugin_meta=PluginMeta(),
                path=Path(raw["path"]),
                rrd_available=True,
                metadata_available=False,
            )
        )
    return entries


def build_index(
    base_path: Path, datafile_index: DatafileIndex | None
) -> list[NormalizedField]:
    datafile_entries = _build_from_datafile(base_path, datafile_index or {})
    covered_paths = {e.path for e in datafile_entries}
    fallback_entries = [
        e for e in _build_from_fallback(base_path) if e.path not in covered_paths
    ]
    return datafile_entries + fallback_entries


def list_hosts(entries: list[NormalizedField]) -> list[dict]:
    seen = sorted({(e.group, e.host) for e in entries})
    return [{"group": group, "host": host} for group, host in seen]


def _require_host(entries: list[NormalizedField], group: str, host: str) -> None:
    if not any(e.group == group and e.host == host for e in entries):
        raise HostNotFoundError(f"host not found: group={group!r} host={host!r}")


def list_plugins(entries: list[NormalizedField], group: str, host: str) -> list[dict]:
    _require_host(entries, group, host)
    seen: dict[str, PluginMeta] = {}
    for e in entries:
        if e.group == group and e.host == host:
            seen.setdefault(e.plugin, e.plugin_meta)
    return [
        {
            "plugin": plugin,
            "graph_title": meta.graph_title,
            "graph_category": meta.graph_category,
            "graph_vlabel": meta.graph_vlabel,
            "graph_info": meta.graph_info,
            "extra_graph_attrs": meta.extra_graph_attrs,
        }
        for plugin, meta in sorted(seen.items())
    ]


def _require_plugin(
    entries: list[NormalizedField], group: str, host: str, plugin: str
) -> None:
    _require_host(entries, group, host)
    if not any(
        e.group == group and e.host == host and e.plugin == plugin for e in entries
    ):
        raise PluginNotFoundError(
            f"plugin not found: group={group!r} host={host!r} plugin={plugin!r}"
        )


def list_fields(
    entries: list[NormalizedField], group: str, host: str, plugin: str
) -> list[dict]:
    _require_plugin(entries, group, host, plugin)
    matched = [
        e for e in entries if e.group == group and e.host == host and e.plugin == plugin
    ]
    return [
        {
            "field": e.field,
            "label": e.meta.label,
            "type": e.meta.type,
            "min": e.meta.min,
            "max": e.meta.max,
            "warning": e.meta.warning,
            "critical": e.meta.critical,
            "info": e.meta.info,
            "extra": e.meta.extra,
            "rrd_available": e.rrd_available,
            "metadata_available": e.metadata_available,
        }
        for e in sorted(matched, key=lambda e: e.field)
    ]


def resolve_field(
    entries: list[NormalizedField], group: str, host: str, plugin: str, field: str
) -> NormalizedField:
    _require_plugin(entries, group, host, plugin)
    for e in entries:
        if (
            e.group == group
            and e.host == host
            and e.plugin == plugin
            and e.field == field
        ):
            return e
    raise FieldNotFoundError(
        f"field not found: group={group!r} host={host!r} plugin={plugin!r} field={field!r}"
    )
