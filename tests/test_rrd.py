from pathlib import Path

import pytest

from rrdmcp.errors import RrdFileNotAvailableError
from rrdmcp.rrd import fetch, info, render_graph, rrd_path, sanitize_name, type_letter


def test_sanitize_name_replaces_invalid_chars():
    assert sanitize_name("diskstats_iops.sda") == "diskstats_iops.sda"
    assert sanitize_name("foo/bar baz") == "foo_bar_baz"


def test_type_letter_maps_ds_types():
    assert type_letter("GAUGE") == "g"
    assert type_letter("COUNTER") == "c"
    assert type_letter("DERIVE") == "d"
    assert type_letter("ABSOLUTE") == "a"


def test_rrd_path_builds_expected_filename(tmp_path: Path):
    path = rrd_path(tmp_path, "grp", "host1", "cpu", "user", "GAUGE")
    assert path == tmp_path / "grp" / "host1-cpu-user-g.rrd"


def test_fetch_raises_when_file_missing(tmp_path: Path):
    with pytest.raises(RrdFileNotAvailableError):
        fetch(tmp_path / "does-not-exist.rrd", "-1h", "now")


def test_fetch_returns_points_from_real_rrd(munin_root: Path):
    rrd_file = munin_root / "testgroup" / "testhost.example.com-cpu-user-g.rrd"
    result = fetch(rrd_file, "-2h", "now")
    assert result.ds_names == ["42"]
    assert len(result.points) > 0
    assert all(isinstance(ts, int) for ts, _ in result.points)


def test_info_returns_ds_and_rra_details(munin_root: Path):
    rrd_file = munin_root / "testgroup" / "testhost.example.com-cpu-user-g.rrd"
    result = info(rrd_file)
    assert result["ds[42].type"] == "GAUGE"


def test_render_graph_returns_png_bytes(munin_root: Path):
    rrd_file = munin_root / "testgroup" / "testhost.example.com-cpu-user-g.rrd"
    png = render_graph(
        [(rrd_file, "User")],
        "-2h",
        "now",
        "CPU usage",
        "%",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
