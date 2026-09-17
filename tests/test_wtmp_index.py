from pathlib import Path

from rrdmcp.wtmp_index import LoginSource, build_index


def test_build_index_returns_empty_list_for_missing_base_path(tmp_path: Path):
    assert build_index(tmp_path / "does-not-exist") == []


def test_build_index_finds_plain_wtmp_file(tmp_path: Path):
    host_dir = tmp_path / "web" / "app01"
    host_dir.mkdir(parents=True)
    (host_dir / "wtmp").write_bytes(b"")
    assert build_index(tmp_path) == [
        LoginSource(group="web", host="app01", kind="wtmp")
    ]


def test_build_index_finds_rotated_and_compressed_files(tmp_path: Path):
    host_dir = tmp_path / "web" / "app01"
    host_dir.mkdir(parents=True)
    (host_dir / "btmp.1").write_bytes(b"")
    (host_dir / "btmp.2.gz").write_bytes(b"")
    assert build_index(tmp_path) == [
        LoginSource(group="web", host="app01", kind="btmp")
    ]


def test_build_index_finds_both_kinds_for_same_host(tmp_path: Path):
    host_dir = tmp_path / "web" / "app01"
    host_dir.mkdir(parents=True)
    (host_dir / "wtmp").write_bytes(b"")
    (host_dir / "btmp").write_bytes(b"")
    result = build_index(tmp_path)
    assert {(e.group, e.host, e.kind) for e in result} == {
        ("web", "app01", "wtmp"),
        ("web", "app01", "btmp"),
    }


def test_build_index_skips_host_with_no_matching_files(tmp_path: Path):
    host_dir = tmp_path / "web" / "app01"
    host_dir.mkdir(parents=True)
    (host_dir / "unrelated.txt").write_bytes(b"")
    assert build_index(tmp_path) == []


def test_build_index_covers_multiple_groups_and_hosts(tmp_path: Path):
    for group, host in [("web", "app01"), ("web", "app02"), ("db", "db01")]:
        host_dir = tmp_path / group / host
        host_dir.mkdir(parents=True)
        (host_dir / "wtmp").write_bytes(b"")
    result = build_index(tmp_path)
    assert {(e.group, e.host) for e in result} == {
        ("web", "app01"),
        ("web", "app02"),
        ("db", "db01"),
    }
