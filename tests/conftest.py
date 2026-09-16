import shutil
import subprocess
import time
from pathlib import Path

import pytest

RRDTOOL_AVAILABLE = shutil.which("rrdtool") is not None

TEST_GROUP = "testgroup"
TEST_HOST = "testhost.example.com"
TEST_PLUGIN = "cpu"
TEST_FIELDS = {"user": "GAUGE", "system": "GAUGE", "idle": "GAUGE"}


@pytest.fixture
def munin_root(tmp_path: Path) -> Path:
    if not RRDTOOL_AVAILABLE:
        pytest.skip("rrdtool command not available")

    group_dir = tmp_path / TEST_GROUP
    group_dir.mkdir()

    start = int(time.time()) - 1000
    for field, ds_type in TEST_FIELDS.items():
        rrd_file = (
            group_dir / f"{TEST_HOST}-{TEST_PLUGIN}-{field}-{ds_type[0].lower()}.rrd"
        )
        subprocess.run(
            [
                "rrdtool",
                "create",
                str(rrd_file),
                "--start",
                str(start),
                "--step",
                "10",
                f"DS:42:{ds_type}:20:0:100",
                "RRA:AVERAGE:0.5:1:200",
            ],
            check=True,
            capture_output=True,
        )
        update_args = ["rrdtool", "update", str(rrd_file)]
        for i in range(1, 51):
            ts = start + i * 10
            value = 10 + (i % 30)
            update_args.append(f"{ts}:{value}")
        subprocess.run(update_args, check=True, capture_output=True)

    datafile_content = f"""version 2.999.4
{TEST_GROUP};{TEST_HOST}:cpu.graph_title CPU usage
{TEST_GROUP};{TEST_HOST}:cpu.graph_vlabel %
{TEST_GROUP};{TEST_HOST}:cpu.graph_category system
{TEST_GROUP};{TEST_HOST}:cpu.user.label User
{TEST_GROUP};{TEST_HOST}:cpu.user.type GAUGE
{TEST_GROUP};{TEST_HOST}:cpu.user.min 0
{TEST_GROUP};{TEST_HOST}:cpu.user.warning 80
{TEST_GROUP};{TEST_HOST}:cpu.user.critical 95
{TEST_GROUP};{TEST_HOST}:cpu.system.label System
{TEST_GROUP};{TEST_HOST}:cpu.system.type GAUGE
{TEST_GROUP};{TEST_HOST}:cpu.idle.label Idle
{TEST_GROUP};{TEST_HOST}:cpu.idle.type GAUGE
"""
    (tmp_path / "datafile").write_text(datafile_content)
    return tmp_path


SADF_AVAILABLE = shutil.which("sadf") is not None


def _find_sadc() -> str | None:
    found = shutil.which("sadc")
    if found:
        return found
    candidate = Path("/usr/lib/sysstat/sadc")
    return str(candidate) if candidate.exists() else None


SAR_GROUP = "sargroup"
SAR_HOST = "sarhost.example.com"


@pytest.fixture
def sar_root(tmp_path: Path) -> Path:
    if not SADF_AVAILABLE:
        pytest.skip("sadf command not available")
    sadc = _find_sadc()
    if sadc is None:
        pytest.skip("sadc command not available")

    host_dir = tmp_path / SAR_GROUP / SAR_HOST
    host_dir.mkdir(parents=True)
    today = time.strftime("%d")
    sa_file = host_dir / f"sa{today}"
    # -S DISK でディスクアクティビティも収集対象に含める。2サンプル
    # (ベースライン + 1インターバル分)採ることで cpu-load 等の
    # 平均値レコードが1件以上できる。
    subprocess.run(
        [sadc, "-S", "DISK", "1", "2", str(sa_file)],
        check=True,
        capture_output=True,
    )
    return tmp_path
