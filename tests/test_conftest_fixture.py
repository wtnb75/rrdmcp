from pathlib import Path


def test_munin_root_creates_rrd_files_and_datafile(munin_root: Path):
    group_dir = munin_root / "testgroup"
    assert (group_dir / "testhost.example.com-cpu-user-g.rrd").exists()
    assert (group_dir / "testhost.example.com-cpu-system-g.rrd").exists()
    assert (group_dir / "testhost.example.com-cpu-idle-g.rrd").exists()
    datafile_text = (munin_root / "datafile").read_text()
    assert "cpu.graph_title CPU usage" in datafile_text
    assert "cpu.user.warning 80" in datafile_text
