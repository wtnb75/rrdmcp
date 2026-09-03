from pathlib import Path

from rrdmcp.munin_datafile import load_datafile, parse_datafile

SIMPLE_TEXT = """version 2.999.4
grp;host1:cpu.graph_title CPU usage
grp;host1:cpu.graph_vlabel %
grp;host1:cpu.user.label User
grp;host1:cpu.user.type GAUGE
grp;host1:cpu.user.warning 80
grp;host1:cpu.user.critical 95
grp;host1:cpu.idle.label Idle
"""

MULTIGRAPH_TEXT = """version 2.999.4
grp;host1:diskstats_iops.sda.graph_title Disk IOPS sda
grp;host1:diskstats_iops.sda.reads.label Reads
grp;host1:diskstats_iops.sda.reads.type DERIVE
"""

NO_GROUP_TEXT = """version 2.999.4
host1:cpu.graph_title CPU usage
host1:cpu.user.label User
"""


def test_parses_graph_level_attributes():
    index = parse_datafile(SIMPLE_TEXT)
    plugin = index[("grp", "host1")]["cpu"]
    assert plugin.graph_title == "CPU usage"
    assert plugin.graph_vlabel == "%"


def test_parses_field_level_attributes():
    index = parse_datafile(SIMPLE_TEXT)
    plugin = index[("grp", "host1")]["cpu"]
    assert plugin.fields["user"].label == "User"
    assert plugin.fields["user"].type == "GAUGE"
    assert plugin.fields["user"].warning == "80"
    assert plugin.fields["user"].critical == "95"
    assert plugin.fields["idle"].label == "Idle"


def test_parses_multigraph_plugin_name_with_dot():
    index = parse_datafile(MULTIGRAPH_TEXT)
    plugin = index[("grp", "host1")]["diskstats_iops.sda"]
    assert plugin.graph_title == "Disk IOPS sda"
    assert plugin.fields["reads"].label == "Reads"
    assert plugin.fields["reads"].type == "DERIVE"


def test_parses_missing_group_as_empty_string():
    index = parse_datafile(NO_GROUP_TEXT)
    plugin = index[("", "host1")]["cpu"]
    assert plugin.graph_title == "CPU usage"
    assert plugin.fields["user"].label == "User"


def test_load_datafile_reads_from_path(tmp_path: Path):
    p = tmp_path / "datafile"
    p.write_text(SIMPLE_TEXT)
    index = load_datafile(p)
    assert index[("grp", "host1")]["cpu"].graph_title == "CPU usage"
