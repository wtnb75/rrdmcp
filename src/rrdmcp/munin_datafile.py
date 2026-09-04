import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

GRAPH_LEVEL_PREFIX = "graph_"

_LINE_RE = re.compile(
    r"^(?:(?P<group>[^;:\n]+);)?(?P<host>[^:\n]+):(?P<key>\S+)\s+(?P<value>.*)$"
)


@dataclass
class FieldMeta:
    label: str | None = None
    type: str | None = None
    min: str | None = None
    max: str | None = None
    warning: str | None = None
    critical: str | None = None
    info: str | None = None
    extra: dict[str, str] = dc_field(default_factory=dict)


@dataclass
class PluginMeta:
    graph_title: str | None = None
    graph_vlabel: str | None = None
    graph_category: str | None = None
    graph_info: str | None = None
    extra_graph_attrs: dict[str, str] = dc_field(default_factory=dict)
    fields: dict[str, FieldMeta] = dc_field(default_factory=dict)


DatafileIndex = dict[tuple[str, str], dict[str, PluginMeta]]


def parse_datafile(text: str) -> DatafileIndex:
    index: DatafileIndex = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        group = match.group("group") or ""
        host = match.group("host")
        key = match.group("key")
        value = match.group("value")
        parts = key.split(".")
        if len(parts) < 2:
            continue

        plugins = index.setdefault((group, host), {})

        if parts[-1].startswith(GRAPH_LEVEL_PREFIX):
            plugin_name = ".".join(parts[:-1])
            attribute = parts[-1]
            plugin_meta = plugins.setdefault(plugin_name, PluginMeta())
            if hasattr(plugin_meta, attribute):
                setattr(plugin_meta, attribute, value)
            else:
                plugin_meta.extra_graph_attrs[attribute] = value
            continue

        if len(parts) < 3:
            continue
        plugin_name = ".".join(parts[:-2])
        field_name = parts[-2]
        attribute = parts[-1]
        plugin_meta = plugins.setdefault(plugin_name, PluginMeta())
        field_meta = plugin_meta.fields.setdefault(field_name, FieldMeta())
        if hasattr(field_meta, attribute):
            setattr(field_meta, attribute, value)
        else:
            field_meta.extra[attribute] = value

    return index


def load_datafile(path: Path) -> DatafileIndex:
    return parse_datafile(path.read_text())
