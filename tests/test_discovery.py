from pathlib import Path

import pytest

from rrdmcp.discovery import (
    build_index,
    fallback_scan,
    list_fields,
    list_hosts,
    list_plugins,
    resolve_field,
)
from rrdmcp.errors import FieldNotFoundError, HostNotFoundError, PluginNotFoundError
from rrdmcp.munin_datafile import load_datafile


def test_build_index_from_datafile(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    assert len(entries) == 3
    assert all(e.metadata_available for e in entries)
    user_entry = next(e for e in entries if e.field == "user")
    assert user_entry.rrd_available is True
    assert user_entry.meta.warning == "80"


def test_list_hosts(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    hosts = list_hosts(entries)
    assert hosts == [{"group": "testgroup", "host": "testhost.example.com"}]


def test_list_plugins(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    plugins = list_plugins(entries, "testgroup", "testhost.example.com")
    assert plugins == [
        {
            "plugin": "cpu",
            "graph_title": "CPU usage",
            "graph_category": "system",
            "graph_vlabel": "%",
        }
    ]


def test_list_plugins_raises_for_unknown_host(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    with pytest.raises(HostNotFoundError):
        list_plugins(entries, "testgroup", "no-such-host")


def test_list_fields(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    fields = list_fields(entries, "testgroup", "testhost.example.com", "cpu")
    field_names = {f["field"] for f in fields}
    assert field_names == {"user", "system", "idle"}
    user_field = next(f for f in fields if f["field"] == "user")
    assert user_field["warning"] == "80"
    assert user_field["rrd_available"] is True


def test_list_fields_raises_for_unknown_plugin(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    with pytest.raises(PluginNotFoundError):
        list_fields(entries, "testgroup", "testhost.example.com", "no-such-plugin")


def test_resolve_field(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    resolved = resolve_field(entries, "testgroup", "testhost.example.com", "cpu", "user")
    assert resolved.path.name == "testhost.example.com-cpu-user-g.rrd"
    assert resolved.rrd_available is True


def test_resolve_field_raises_for_unknown_field(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    with pytest.raises(FieldNotFoundError):
        resolve_field(entries, "testgroup", "testhost.example.com", "cpu", "no-such-field")


def test_fallback_scan_without_datafile(munin_root: Path):
    entries_raw = fallback_scan(munin_root)
    assert len(entries_raw) == 3
    fields = {e["field"] for e in entries_raw}
    assert fields == {"user", "system", "idle"}
    assert all(e["metadata_available"] is False for e in entries_raw)


def test_build_index_falls_back_when_no_datafile(munin_root: Path):
    entries = build_index(munin_root, None)
    assert len(entries) == 3
    assert all(e.metadata_available is False for e in entries)
    assert all(e.plugin == "" for e in entries)
