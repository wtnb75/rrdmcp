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
from rrdmcp.munin_datafile import load_datafile, parse_datafile


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
            "graph_info": None,
            "extra_graph_attrs": {},
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


def test_list_plugins_and_list_fields_expose_unknown_attributes(munin_root: Path):
    text = """version 2.999.4
testgroup;testhost.example.com:cpu.graph_title CPU usage
testgroup;testhost.example.com:cpu.graph_args --base 1000 -r --lower-limit 0 --upper-limit 200
testgroup;testhost.example.com:cpu.user.label User
testgroup;testhost.example.com:cpu.user.type GAUGE
testgroup;testhost.example.com:cpu.user.draw AREA
"""
    datafile_index = parse_datafile(text)
    entries = build_index(munin_root, datafile_index)

    plugins = list_plugins(entries, "testgroup", "testhost.example.com")
    assert plugins[0]["extra_graph_attrs"] == {
        "graph_args": "--base 1000 -r --lower-limit 0 --upper-limit 200"
    }

    fields = list_fields(entries, "testgroup", "testhost.example.com", "cpu")
    user_field = next(f for f in fields if f["field"] == "user")
    assert user_field["extra"] == {"draw": "AREA"}


def test_resolve_field(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    resolved = resolve_field(
        entries, "testgroup", "testhost.example.com", "cpu", "user"
    )
    assert resolved.path.name == "testhost.example.com-cpu-user-g.rrd"
    assert resolved.rrd_available is True


def test_resolve_field_raises_for_unknown_field(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    with pytest.raises(FieldNotFoundError):
        resolve_field(
            entries, "testgroup", "testhost.example.com", "cpu", "no-such-field"
        )


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


def test_build_index_with_empty_datafile_falls_back_for_all_rrd_files(
    munin_root: Path,
):
    # A present-but-empty datafile covers nothing, so every RRD file on disk
    # is "uncovered" and merged in via the fallback scan — same outcome as
    # datafile_index=None, since there is no rich data to prefer over it.
    entries = build_index(munin_root, {})
    assert len(entries) == 3
    assert all(e.metadata_available is False for e in entries)
    assert all(e.plugin == "" for e in entries)


def test_build_index_merges_datafile_entries_with_uncovered_rrd_files(
    munin_root: Path,
):
    # Datafile covering only "user" and "system" — "idle"'s RRD file exists
    # on disk but has no datafile entry, e.g. a host that dropped out of the
    # current munin.conf while its historical RRD files remain.
    partial_datafile_text = """version 2.999.4
testgroup;testhost.example.com:cpu.graph_title CPU usage
testgroup;testhost.example.com:cpu.graph_vlabel %
testgroup;testhost.example.com:cpu.graph_category system
testgroup;testhost.example.com:cpu.user.label User
testgroup;testhost.example.com:cpu.user.type GAUGE
testgroup;testhost.example.com:cpu.user.warning 80
testgroup;testhost.example.com:cpu.user.critical 95
testgroup;testhost.example.com:cpu.system.label System
testgroup;testhost.example.com:cpu.system.type GAUGE
"""
    datafile_index = parse_datafile(partial_datafile_text)
    entries = build_index(munin_root, datafile_index)

    assert len(entries) == 3

    rich = [e for e in entries if e.metadata_available]
    assert {e.field for e in rich} == {"user", "system"}
    assert all(e.plugin == "cpu" for e in rich)
    assert all(e.host == "testhost.example.com" for e in rich)

    degraded = [e for e in entries if not e.metadata_available]
    assert len(degraded) == 1
    assert degraded[0].field == "idle"
    assert degraded[0].plugin == ""
    assert degraded[0].path.name == "testhost.example.com-cpu-idle-g.rrd"

    # No duplicate paths between the datafile-based and fallback-based entries.
    paths = [e.path for e in entries]
    assert len(paths) == len(set(paths))
