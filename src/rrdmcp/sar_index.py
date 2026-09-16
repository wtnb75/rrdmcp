import json
import shutil
import subprocess
from pathlib import Path

from .discovery import NormalizedField
from .munin_datafile import FieldMeta, PluginMeta
from .rrd import sanitize_name

SADF_TIMEOUT_SECONDS = 30

_INSTANCE_KEYS = ("cpu", "disk-device", "iface", "filesystem", "number")
_TOP_LEVEL_SKIP_KEYS = ("timestamp", "restarts")

SAR_ACTIVITY_META: dict[str, dict[str, str | None]] = {
    "cpu-load": {
        "graph_title": "CPU usage",
        "graph_vlabel": "%",
        "graph_category": "cpu",
    },
    "memory": {
        "graph_title": "Memory usage",
        "graph_vlabel": "kB",
        "graph_category": "memory",
    },
    "disk": {
        "graph_title": "Disk I/O",
        "graph_vlabel": "tps",
        "graph_category": "disk",
    },
    "network.net-dev": {
        "graph_title": "Network traffic",
        "graph_vlabel": "kB/s",
        "graph_category": "network",
    },
}

SAR_FIELD_META: dict[str, dict[str, str]] = {
    "cpu-load": {
        "usr": "User",
        "sys": "System",
        "iowait": "IO wait",
        "idle": "Idle",
    },
    "memory": {
        "memfree": "Free memory",
        "memused": "Used memory",
        "avail": "Available memory",
    },
    "disk": {
        "tps": "Transfers/sec",
        "rkB": "Read kB/s",
        "wkB": "Write kB/s",
    },
    "network.net-dev": {
        "rxkB": "RX kB/s",
        "txkB": "TX kB/s",
    },
}


def walk_statistics(node: dict, path: str = "") -> dict[str, dict[str, float | int]]:
    """Flatten one `sadf -j` statistics block into {plugin: {field: value}}.

    See docs/superpowers/specs/2026-09-16-sar-support-design.md for the
    recursive-walk rules this implements.
    """
    result: dict[str, dict[str, float | int]] = {}
    for key, value in node.items():
        if not path and key in _TOP_LEVEL_SKIP_KEYS:
            continue
        if isinstance(value, list):
            if not value or not isinstance(value[0], dict):
                continue
            instance_key = next((k for k in _INSTANCE_KEYS if k in value[0]), None)
            if instance_key is None:
                continue
            for item in value:
                instance = sanitize_name(str(item[instance_key]))
                plugin = f"{path}.{key}.{instance}" if path else f"{key}.{instance}"
                fields = {
                    k: v
                    for k, v in item.items()
                    if k != instance_key
                    and isinstance(v, (int, float))
                    and not isinstance(v, bool)
                }
                if fields:
                    result[plugin] = fields
            continue
        if isinstance(value, dict):
            scalars = {
                k: v
                for k, v in value.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
            nested = {k: v for k, v in value.items() if isinstance(v, (dict, list))}
            sub_path = f"{path}.{key}" if path else key
            if scalars:
                result[sub_path] = scalars
            if nested:
                result.update(walk_statistics(nested, sub_path))
    return result


def activity_key(plugin: str) -> str:
    """Resolve the SAR_ACTIVITY_META/SAR_FIELD_META lookup key for a plugin.

    `walk_statistics` bakes instance values into the plugin name (e.g.
    "cpu-load.0", "network.net-dev.eth0"); this strips the trailing
    instance segment so it matches the static metadata dicts' keys
    ("cpu-load", "network.net-dev"). Falls back to the plugin name
    unchanged when nothing matches (e.g. "io.io-reads", which has no
    instance dimension and isn't in the static tables).
    """
    if plugin in SAR_ACTIVITY_META or plugin in SAR_FIELD_META:
        return plugin
    if "." in plugin:
        base, _, _ = plugin.rpartition(".")
        if base in SAR_ACTIVITY_META or base in SAR_FIELD_META:
            return base
    return plugin


def _list_sa_files(host_dir: Path) -> list[Path]:
    return sorted(p for p in host_dir.glob("sa[0-3][0-9]") if p.is_file())


def _run_sadf_json(sadf_exe: str, sa_file: Path) -> dict | None:
    try:
        proc = subprocess.run(
            [sadf_exe, "-j", "--", "-A", str(sa_file)],
            capture_output=True,
            text=True,
            timeout=SADF_TIMEOUT_SECONDS,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def build_index(base_path: Path) -> list[NormalizedField]:
    """Discover sar plugin/field entries under SAR_BASE_PATH.

    Returns an empty list (never raises) if `sadf` isn't on PATH or
    `base_path` doesn't exist, so sar support degrades to a no-op when
    unconfigured or unavailable — matching how munin discovery behaves
    when its datafile is missing.
    """
    sadf_exe = shutil.which("sadf")
    if sadf_exe is None or not base_path.is_dir():
        return []

    entries: list[NormalizedField] = []
    for group_dir in sorted(p for p in base_path.iterdir() if p.is_dir()):
        for host_dir in sorted(p for p in group_dir.iterdir() if p.is_dir()):
            sa_files = _list_sa_files(host_dir)
            if not sa_files:
                continue
            data = _run_sadf_json(sadf_exe, sa_files[-1])
            if not isinstance(data, dict):
                continue
            sysstat = data.get("sysstat")
            if not isinstance(sysstat, dict):
                continue
            hosts = sysstat.get("hosts", [])
            if not hosts or not isinstance(hosts[0], dict):
                continue
            statistics = hosts[0].get("statistics", [])
            if not statistics or not isinstance(statistics[-1], dict):
                continue
            metrics = walk_statistics(statistics[-1])
            for plugin, fields in metrics.items():
                key = activity_key(plugin)
                activity_meta = SAR_ACTIVITY_META.get(key)
                field_labels = SAR_FIELD_META.get(key, {})
                plugin_meta = PluginMeta(
                    graph_title=activity_meta["graph_title"] if activity_meta else None,
                    graph_vlabel=activity_meta["graph_vlabel"] if activity_meta else None,
                    graph_category=activity_meta["graph_category"] if activity_meta else None,
                )
                for field_name in fields:
                    field_meta = FieldMeta(label=field_labels.get(field_name, field_name))
                    entries.append(
                        NormalizedField(
                            group=group_dir.name,
                            host=host_dir.name,
                            plugin=plugin,
                            field=field_name,
                            meta=field_meta,
                            plugin_meta=plugin_meta,
                            path=host_dir,
                            rrd_available=True,
                            metadata_available=activity_meta is not None,
                            source="sar",
                        )
                    )
    return entries
