from pathlib import Path

from rrdmcp.sar_index import (
    SAR_ACTIVITY_META,
    SAR_FIELD_META,
    activity_key,
    build_index,
    walk_statistics,
)

# Debian 12 + sysstat 12.6.1 の `sadf -j -- -A` 実出力を縮小したサンプル
# (timestamp/restartsは呼び出し側で除外される前提なので含めない)
SAMPLE_STATISTICS_BLOCK = {
    "timestamp": {"date": "2026-09-16", "time": "12:00:00", "utc": 1, "interval": 600},
    "cpu-load": [
        {"cpu": "all", "usr": 1.5, "sys": 0.5, "idle": 98.0},
        {"cpu": "0", "usr": 2.0, "sys": 1.0, "idle": 97.0},
    ],
    "memory": {"memfree": 5840512, "memused": 442436, "avail": 7330044},
    "io": {
        "tps": 1.2,
        "io-reads": {"rtps": 0.0, "bread": 0.0},
        "io-writes": {"wtps": 1.2, "bwrtn": 15.69},
    },
    "disk": [
        {"disk-device": "vda", "tps": 0.0, "rkB": 0.0, "wkB": 0.0},
        {"disk-device": "vdb", "tps": 0.98, "rkB": 0.0, "wkB": 7.84},
    ],
    "network": {
        "net-dev": [
            {"iface": "lo", "rxkB": 0.0, "txkB": 0.0},
            {"iface": "eth0", "rxkB": 0.04, "txkB": 0.04},
        ],
        "net-nfs": {"call": 0.0, "retrans": 0.0},
    },
    "power-management": {
        "cpu-frequency": [{"number": "all", "frequency": 0.0}],
    },
    "filesystems": [
        {"filesystem": "/dev/vdb1", "MBfsfree": 16307, "MBfsused": 22029},
    ],
    "restarts": [],
}


def test_walk_statistics_flattens_simple_array_with_instance_key():
    result = walk_statistics(SAMPLE_STATISTICS_BLOCK)
    assert result["cpu-load.all"] == {"usr": 1.5, "sys": 0.5, "idle": 98.0}
    assert result["cpu-load.0"] == {"usr": 2.0, "sys": 1.0, "idle": 97.0}
    assert result["disk.vda"] == {"tps": 0.0, "rkB": 0.0, "wkB": 0.0}
    assert result["disk.vdb"] == {"tps": 0.98, "rkB": 0.0, "wkB": 7.84}


def test_walk_statistics_flattens_all_scalar_dict():
    result = walk_statistics(SAMPLE_STATISTICS_BLOCK)
    assert result["memory"] == {"memfree": 5840512, "memused": 442436, "avail": 7330044}


def test_walk_statistics_handles_mixed_scalar_and_nested_dict():
    result = walk_statistics(SAMPLE_STATISTICS_BLOCK)
    assert result["io"] == {"tps": 1.2}
    assert result["io.io-reads"] == {"rtps": 0.0, "bread": 0.0}
    assert result["io.io-writes"] == {"wtps": 1.2, "bwrtn": 15.69}


def test_walk_statistics_handles_two_level_nesting():
    result = walk_statistics(SAMPLE_STATISTICS_BLOCK)
    assert result["network.net-dev.eth0"] == {"rxkB": 0.04, "txkB": 0.04}
    assert result["network.net-dev.lo"] == {"rxkB": 0.0, "txkB": 0.0}
    assert result["network.net-nfs"] == {"call": 0.0, "retrans": 0.0}
    assert result["power-management.cpu-frequency.all"] == {"frequency": 0.0}


def test_walk_statistics_sanitizes_instance_values_with_slashes():
    result = walk_statistics(SAMPLE_STATISTICS_BLOCK)
    assert result["filesystems._dev_vdb1"] == {"MBfsfree": 16307, "MBfsused": 22029}


def test_walk_statistics_excludes_timestamp_and_restarts():
    result = walk_statistics(SAMPLE_STATISTICS_BLOCK)
    assert "timestamp" not in result
    assert "restarts" not in result
    assert not any(k.startswith("restarts") for k in result)


def test_walk_statistics_skips_elements_missing_the_instance_key():
    # Heterogeneous array: the first element carries "cpu" (which picks
    # "cpu" as the inferred instance_key for the whole array), but a later
    # element doesn't have it. Must be skipped, not raise KeyError.
    block = {
        "cpu-load": [
            {"cpu": "all", "usr": 1.5},
            {"usr": 99.0},  # no "cpu" key
        ],
    }
    result = walk_statistics(block)
    assert result["cpu-load.all"] == {"usr": 1.5}
    assert len(result) == 1


def test_activity_key_strips_instance_suffix():
    assert activity_key("cpu-load.0") == "cpu-load"
    assert activity_key("cpu-load.all") == "cpu-load"
    assert activity_key("network.net-dev.eth0") == "network.net-dev"
    assert activity_key("memory") == "memory"


def test_activity_key_falls_back_to_plugin_when_unknown():
    assert activity_key("io.io-reads") == "io.io-reads"


def test_sar_activity_meta_has_entries_for_common_activities():
    assert SAR_ACTIVITY_META["cpu-load"]["graph_title"]
    assert SAR_ACTIVITY_META["memory"]["graph_vlabel"]
    assert SAR_FIELD_META["cpu-load"]["usr"]


SAR_GROUP = "sargroup"
SAR_HOST = "sarhost.example.com"


def test_build_index_returns_empty_list_when_sadf_unavailable(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert build_index(tmp_path) == []


def test_build_index_returns_empty_list_for_missing_base_path(tmp_path: Path):
    assert build_index(tmp_path / "does-not-exist") == []


def _make_sa_host_dir(tmp_path: Path) -> Path:
    host_dir = tmp_path / SAR_GROUP / SAR_HOST
    host_dir.mkdir(parents=True)
    (host_dir / "sa01").write_bytes(b"not-real-sar-binary-data")
    return host_dir


def test_build_index_skips_host_when_subprocess_raises_oserror(
    tmp_path: Path, monkeypatch
):
    _make_sa_host_dir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sadf")

    def _raise(*args, **kwargs):
        raise FileNotFoundError("sadf binary vanished")

    monkeypatch.setattr("subprocess.run", _raise)
    assert build_index(tmp_path) == []


def test_build_index_skips_host_when_sadf_json_top_level_is_not_a_dict(
    tmp_path: Path, monkeypatch
):
    _make_sa_host_dir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sadf")
    monkeypatch.setattr(
        "rrdmcp.sar_index._run_sadf_json", lambda sadf_exe, sa_file: []
    )
    assert build_index(tmp_path) == []


def test_build_index_skips_host_when_sadf_json_hosts_entry_is_not_a_dict(
    tmp_path: Path, monkeypatch
):
    _make_sa_host_dir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sadf")
    monkeypatch.setattr(
        "rrdmcp.sar_index._run_sadf_json",
        lambda sadf_exe, sa_file: {"sysstat": {"hosts": ["not-a-dict"]}},
    )
    assert build_index(tmp_path) == []


def test_build_index_skips_host_on_unexpected_exception_but_keeps_others(
    tmp_path: Path, monkeypatch
):
    """One host raising an unexpected exception mid-processing must not
    abort discovery for other hosts (per build_index's "never raises"
    contract, and to keep munin-only callers of _load_entries safe from a
    single malformed sar host)."""
    import rrdmcp.sar_index as sar_index_module

    bad_host_dir = tmp_path / SAR_GROUP / "badhost"
    bad_host_dir.mkdir(parents=True)
    (bad_host_dir / "sa01").write_bytes(b"x")

    good_host_dir = tmp_path / SAR_GROUP / SAR_HOST
    good_host_dir.mkdir(parents=True)
    (good_host_dir / "sa01").write_bytes(b"x")

    def fake_run_sadf_json(sadf_exe, sa_file):
        host_name = sa_file.parent.name
        marker = "boom" if host_name == "badhost" else "cpu-load"
        return {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {marker: [{"cpu": "all", "usr": 1.0}]},
                        ]
                    }
                ]
            }
        }

    real_walk_statistics = sar_index_module.walk_statistics

    def fake_walk_statistics(node, path=""):
        if "boom" in node:
            raise RuntimeError("simulated unexpected shape")
        return real_walk_statistics(node, path)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sadf")
    monkeypatch.setattr(sar_index_module, "_run_sadf_json", fake_run_sadf_json)
    monkeypatch.setattr(sar_index_module, "walk_statistics", fake_walk_statistics)

    entries = sar_index_module.build_index(tmp_path)

    assert all(e.host != "badhost" for e in entries)
    assert any(e.host == SAR_HOST for e in entries)


def test_build_index_discovers_entries_from_real_sar_log(sar_root: Path):
    entries = build_index(sar_root)
    assert len(entries) > 0
    assert all(e.source == "sar" for e in entries)
    assert all(e.group == SAR_GROUP for e in entries)
    assert all(e.host == SAR_HOST for e in entries)
    assert all(e.rrd_available for e in entries)

    cpu_entries = [e for e in entries if e.plugin.startswith("cpu-load.")]
    assert len(cpu_entries) > 0
    assert any(e.metadata_available for e in cpu_entries)
    usr_field = next(e for e in cpu_entries if e.field == "usr")
    assert usr_field.meta.label == "User"
    assert usr_field.plugin_meta.graph_title == "CPU usage"
