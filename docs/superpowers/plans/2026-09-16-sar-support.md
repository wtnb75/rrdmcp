# sysstat(sar)データソース対応 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Munin RRDに加えて、sysstat(`sar`)が収集した時系列データも既存の`group/host/plugin/field` MCPツール群から横断的に扱えるようにする。

**Architecture:** `rrd.py`(munin)と対になる`sar.py`/`sar_index.py`を新設し、`sadf -j`のJSON出力をパースしてMuninと同じ`NormalizedField`モデルに正規化する。`discovery.NormalizedField`に`source`フィールドを追加し、`server.py`側でmunin/sarのエントリを単純に結合、`fetch_series`/`render_graph`は`source`で処理を分岐する。

**Tech Stack:** Python 3.11+、`sadf`(sysstatパッケージ)をsubprocessで呼び出し、グラフ描画はmatplotlib(Aggバックエンド)。

**Spec:** `docs/superpowers/specs/2026-09-16-sar-support-design.md`

## Global Constraints

- `sadf -j`の出力する`timestamp.utc`は常に`1`(実機のDebian 12 + sysstat 12.6.1で確認済み)なので、`date`+`time`は常にUTCとして解釈する
- `sadf -s`/`-e`は`hh:mm[:ss]`形式のみを受け付ける(日付を跨いだ指定は不可)。日付を跨ぐ範囲は複数回の`sadf`呼び出しに分割する
- sarのインスタンスキーのホワイトリストは`cpu`, `disk-device`, `iface`, `filesystem`, `number`の5つ(v1でサポートする既知の構造のみ)
- sarログのディレクトリ構成は`SAR_BASE_PATH/<group>/<host>/saXX`を前提とする(`SAR_BASE_PATH`が未設定ならsar機能全体を無効化する)
- `sadf`/`sadc`がローカル環境に無いテストは`shutil.which`でスキップする(既存の`rrdtool`まわりのテストパターンを踏襲)
- 既存の`rrdtool`ベースのMunin機能(`rrd.py`, `munin_datafile.py`のロジック自体)は変更しない。触るのは`discovery.py`(フィールド追加のみ)と`server.py`(分岐追加)のみ

---

### Task 1: 時刻正規化ロジックの共通化(`timeutil.py`)

**Files:**
- Create: `src/rrdmcp/timeutil.py`
- Modify: `src/rrdmcp/rrd.py:1-6,31-45`(`_normalize_time`の実装を`timeutil.try_parse_iso8601`使用に置き換え、不要になった`datetime`/`UTC`のimportを削除)
- Test: `tests/test_timeutil.py`

**Interfaces:**
- Produces: `timeutil.try_parse_iso8601(value: str) -> int | None` — ISO 8601文字列をUnix epoch秒(int)に変換する。ISO 8601として解釈できない場合は`None`を返す。tzなしの場合はUTC扱い。後続タスク(`sar.py`)もこの関数を使う。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_timeutil.py
from datetime import UTC, datetime

from rrdmcp.timeutil import try_parse_iso8601


def test_parses_aware_iso8601_to_epoch():
    result = try_parse_iso8601("2026-09-07T12:00:00Z")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_parses_naive_iso8601_as_utc():
    result = try_parse_iso8601("2026-09-07T12:00:00")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_returns_none_for_non_iso8601_strings():
    assert try_parse_iso8601("-1h") is None
    assert try_parse_iso8601("now") is None
    assert try_parse_iso8601("1757246400") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_timeutil.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rrdmcp.timeutil'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rrdmcp/timeutil.py
from datetime import UTC, datetime


def try_parse_iso8601(value: str) -> int | None:
    """Convert an ISO 8601 timestamp string to a Unix epoch second.

    Returns None if `value` is not a valid ISO 8601 string (e.g. rrdtool
    AT-STYLE expressions like "-1h"/"now", or a bare unix timestamp).
    Naive (timezone-less) timestamps are assumed to be UTC.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_timeutil.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Refactor `rrd.py` to use the shared helper**

In `src/rrdmcp/rrd.py`, remove the `from datetime import UTC, datetime` import (no longer needed) and replace `_normalize_time`:

```python
from .timeutil import try_parse_iso8601


def _normalize_time(value: str) -> str:
    """Convert an ISO 8601 timestamp to a Unix epoch string for rrdtool.

    Unix timestamps and rrdtool AT-STYLE expressions (e.g. "-1d", "now") are
    not valid ISO 8601 and fail to parse, so they pass through unchanged.
    """
    epoch = try_parse_iso8601(value)
    return str(epoch) if epoch is not None else value
```

- [ ] **Step 6: Run existing rrd.py tests to verify no regression**

Run: `uv run pytest tests/test_rrd.py -v`
Expected: PASS (all existing tests pass unchanged; skipped if `rrdtool` is not on PATH)

- [ ] **Step 7: Commit**

```bash
git add src/rrdmcp/timeutil.py src/rrdmcp/rrd.py tests/test_timeutil.py
git commit -m "refactor: extract ISO 8601 time parsing into timeutil module"
```

---

### Task 2: `discovery.NormalizedField`に`source`フィールドを追加

**Files:**
- Modify: `src/rrdmcp/discovery.py:1-25`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: なし(既存モジュール内の変更)
- Produces: `NormalizedField.source: Literal["munin", "sar"]`(デフォルト値`"munin"`)。Task 4以降、`sar_index.build_index()`が`source="sar"`のエントリを生成する際に使う。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discovery.py に追記
def test_build_index_sets_source_to_munin(munin_root: Path):
    datafile_index = load_datafile(munin_root / "datafile")
    entries = build_index(munin_root, datafile_index)
    assert all(e.source == "munin" for e in entries)


def test_build_index_falls_back_sets_source_to_munin(munin_root: Path):
    entries = build_index(munin_root, None)
    assert all(e.source == "munin" for e in entries)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_discovery.py -v -k source`
Expected: FAIL with `AttributeError: 'NormalizedField' object has no attribute 'source'`

- [ ] **Step 3: Write minimal implementation**

In `src/rrdmcp/discovery.py`, add the `Literal` import and the field:

```python
from typing import Literal
```

```python
@dataclass
class NormalizedField:
    group: str
    host: str
    plugin: str
    field: str
    meta: FieldMeta
    plugin_meta: PluginMeta
    path: Path
    rrd_available: bool
    metadata_available: bool
    source: Literal["munin", "sar"] = "munin"
```

(`_build_from_datafile`/`_build_from_fallback`はデフォルト値のおかげで変更不要)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_discovery.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/discovery.py tests/test_discovery.py
git commit -m "feat: add source field to NormalizedField for multi-backend support"
```

---

### Task 3: sar統計JSONの発見ロジック(`sar_index.py` — `walk_statistics`と静的メタデータ)

**Files:**
- Create: `src/rrdmcp/sar_index.py`
- Test: `tests/test_sar_index.py`

**Interfaces:**
- Consumes: `rrd.sanitize_name(name: str) -> str`(既存関数)
- Produces:
  - `sar_index.walk_statistics(node: dict, path: str = "") -> dict[str, dict[str, float | int]]` — `sadf -j`の1統計ブロック(`statistics[i]`、`json.loads`済みのdict)を`{plugin: {field: value}}`に変換する純粋関数。Task 4(`build_index`)とTask 5(`sar.fetch`)の両方から使う。
  - `sar_index.SAR_ACTIVITY_META: dict[str, dict[str, str | None]]` — activityキー(インスタンス部分を除いたplugin名)→`{"graph_title", "graph_vlabel", "graph_category"}`
  - `sar_index.SAR_FIELD_META: dict[str, dict[str, str]]` — activityキー→`{field名: label}`
  - `sar_index.activity_key(plugin: str) -> str` — `walk_statistics`が生成したplugin名(例: `"cpu-load.0"`, `"network.net-dev.eth0"`)から、`SAR_ACTIVITY_META`/`SAR_FIELD_META`を引くためのキー(例: `"cpu-load"`, `"network.net-dev"`)を求める

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sar_index.py
from rrdmcp.sar_index import SAR_ACTIVITY_META, SAR_FIELD_META, activity_key, walk_statistics

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sar_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rrdmcp.sar_index'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rrdmcp/sar_index.py
from .rrd import sanitize_name

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sar_index.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/sar_index.py tests/test_sar_index.py
git commit -m "feat: add sar statistics-block walker and static metadata tables"
```

---

### Task 4: sarエラークラスとディスカバリ(`sar_index.build_index`)

**Files:**
- Modify: `src/rrdmcp/errors.py`
- Modify: `src/rrdmcp/sar_index.py`
- Modify: `tests/conftest.py`(sarログディレクトリを作る`sar_root`フィクスチャを追加)
- Test: `tests/test_sar_index.py`

**Interfaces:**
- Consumes: `discovery.NormalizedField`、`munin_datafile.FieldMeta`/`PluginMeta`、`sar_index.walk_statistics`/`SAR_ACTIVITY_META`/`SAR_FIELD_META`/`activity_key`(Task 3)
- Produces:
  - `errors.SarToolNotFoundError`, `errors.SarFileNotAvailableError`, `errors.SarToolTimeoutError`(`RrdMcpError`のサブクラス)
  - `sar_index.build_index(base_path: Path) -> list[discovery.NormalizedField]` — `SAR_BASE_PATH`配下をスキャンし、`source="sar"`のエントリ一覧を返す。`sadf`がPATHに無い場合や`base_path`が存在しない場合は空リストを返す(例外を投げない)

- [ ] **Step 1: Write the failing test**

まず`tests/conftest.py`に`sar_root`フィクスチャを追加する(`sadf`/`sadc`が無い環境では自動スキップ):

```python
# tests/conftest.py に追記
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
```

必要なimportを`tests/conftest.py`先頭に追加する(`shutil`, `time`は新規、`subprocess`, `Path`, `pytest`は既存を流用):

```python
import shutil
import time
```

次にテストを追記:

```python
# tests/test_sar_index.py に追記
from pathlib import Path

from rrdmcp.errors import SarFileNotAvailableError, SarToolNotFoundError, SarToolTimeoutError
from rrdmcp.sar_index import build_index


def test_build_index_returns_empty_list_when_sadf_unavailable(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert build_index(tmp_path) == []


def test_build_index_returns_empty_list_for_missing_base_path(tmp_path: Path):
    assert build_index(tmp_path / "does-not-exist") == []


def test_build_index_discovers_entries_from_real_sar_log(sar_root: Path):
    from tests.conftest import SAR_GROUP, SAR_HOST

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sar_index.py -v -k build_index`
Expected: FAIL with `ImportError: cannot import name 'build_index' from 'rrdmcp.sar_index'`

- [ ] **Step 3: Write minimal implementation**

`src/rrdmcp/errors.py`に追記:

```python
class SarToolNotFoundError(RrdMcpError):
    """The `sadf` executable is not on PATH."""


class SarFileNotAvailableError(RrdMcpError):
    """A sar log file for a resolved field does not exist or sadf failed on it."""


class SarToolTimeoutError(RrdMcpError):
    """A `sadf` subprocess call exceeded the timeout."""


class SarInvalidTimeError(RrdMcpError):
    """A start/end time could not be interpreted as a unix timestamp or ISO 8601 string."""
```

`src/rrdmcp/sar_index.py`の先頭に追記:

```python
import json
import shutil
import subprocess
from pathlib import Path

from .discovery import NormalizedField
from .munin_datafile import FieldMeta, PluginMeta

SADF_TIMEOUT_SECONDS = 30
```

末尾に追記:

```python
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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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
            if data is None:
                continue
            hosts = data.get("sysstat", {}).get("hosts", [])
            if not hosts:
                continue
            statistics = hosts[0].get("statistics", [])
            if not statistics:
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
```

`NormalizedField`の循環importを避けるため、`discovery.py`は`sar_index.py`をimportしない(既存の一方向依存のまま: `sar_index.py`が`discovery.py`からdataclassを借りる)。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sar_index.py -v`
Expected: PASS。`sadf`/`sadc`がローカルに無い環境では`test_build_index_discovers_entries_from_real_sar_log`のみSKIPPED、他はPASS。

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/errors.py src/rrdmcp/sar_index.py tests/conftest.py tests/test_sar_index.py
git commit -m "feat: add sar log directory discovery via sadf"
```

---

### Task 5: `sar.py` — `fetch`(複数日ファイルの結合)

**Files:**
- Create: `src/rrdmcp/sar.py`
- Test: `tests/test_sar.py`

**Interfaces:**
- Consumes: `timeutil.try_parse_iso8601`(Task 1)、`sar_index.walk_statistics`(Task 3)、`errors.SarInvalidTimeError`/`SarToolNotFoundError`/`SarFileNotAvailableError`/`SarToolTimeoutError`(Task 4)
- Produces:
  - `sar.FetchResult`(`step: int`, `ds_names: list[str]`, `points: list[tuple[int, float | None]]`) — `rrd.FetchResult`と同じ形。`server.py`の`_aggregate_points`/`_summarize_points`をそのまま使い回せる
  - `sar.fetch(host_dir: Path, plugin: str, field: str, start: str, end: str) -> FetchResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sar.py
from pathlib import Path

import pytest

from rrdmcp.errors import SarInvalidTimeError
from rrdmcp.sar import _date_range, _normalize_time_to_epoch, fetch


def test_normalize_time_to_epoch_accepts_unix_timestamp_string():
    assert _normalize_time_to_epoch("1757246400") == 1757246400


def test_normalize_time_to_epoch_accepts_iso8601():
    from datetime import UTC, datetime

    result = _normalize_time_to_epoch("2026-09-07T12:00:00Z")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_normalize_time_to_epoch_rejects_relative_expressions():
    with pytest.raises(SarInvalidTimeError):
        _normalize_time_to_epoch("-1h")
    with pytest.raises(SarInvalidTimeError):
        _normalize_time_to_epoch("now")


def test_date_range_spans_multiple_days():
    from datetime import UTC, datetime

    start = datetime(2026, 9, 14, 23, 0, 0, tzinfo=UTC)
    end = datetime(2026, 9, 16, 1, 0, 0, tzinfo=UTC)
    days = _date_range(start, end)
    assert [d.day for d in days] == [14, 15, 16]


def test_date_range_single_day():
    from datetime import UTC, datetime

    start = datetime(2026, 9, 16, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 9, 16, 23, 0, 0, tzinfo=UTC)
    days = _date_range(start, end)
    assert [d.day for d in days] == [16]


def test_fetch_returns_empty_points_when_no_sa_files_exist(tmp_path: Path):
    result = fetch(tmp_path, "cpu-load.all", "usr", "1757246400", "1757250000")
    assert result.points == []
    assert result.ds_names == ["usr"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rrdmcp.sar'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rrdmcp/sar.py
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .errors import (
    SarFileNotAvailableError,
    SarInvalidTimeError,
    SarToolNotFoundError,
    SarToolTimeoutError,
)
from .sar_index import walk_statistics
from .timeutil import try_parse_iso8601

SADF_TIMEOUT_SECONDS = 30


@dataclass
class FetchResult:
    step: int
    ds_names: list[str]
    points: list[tuple[int, float | None]]


def _normalize_time_to_epoch(value: str) -> int:
    epoch = try_parse_iso8601(value)
    if epoch is not None:
        return epoch
    try:
        return int(value)
    except ValueError as exc:
        raise SarInvalidTimeError(
            "sar data source requires a unix timestamp or an ISO 8601 "
            f"string (rrdtool-style relative expressions are not supported), got: {value!r}"
        ) from exc


def require_sadf() -> str:
    exe = shutil.which("sadf")
    if exe is None:
        raise SarToolNotFoundError("sadf command not found in PATH")
    return exe


def _sa_file_for_date(host_dir: Path, day: date) -> Path:
    return host_dir / f"sa{day.day:02d}"


def _date_range(start_dt: datetime, end_dt: datetime) -> list[date]:
    days = []
    current = start_dt.date()
    while current <= end_dt.date():
        days.append(current)
        current += timedelta(days=1)
    return days


def _run_sadf(sa_file: Path, start_hms: str | None, end_hms: str | None) -> dict:
    exe = require_sadf()
    args = [exe, "-j"]
    if start_hms is not None:
        args += ["-s", start_hms]
    if end_hms is not None:
        args += ["-e", end_hms]
    args += ["--", "-A", str(sa_file)]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=SADF_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise SarToolTimeoutError(
            f"sadf timed out after {SADF_TIMEOUT_SECONDS}s"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise SarFileNotAvailableError(f"sadf failed on {sa_file}: {stderr}") from exc
    return json.loads(proc.stdout)


def _extract_points(
    data: dict, plugin: str, field: str
) -> list[tuple[int, float | None]]:
    hosts = data.get("sysstat", {}).get("hosts", [])
    if not hosts:
        return []
    points: list[tuple[int, float | None]] = []
    for stat in hosts[0].get("statistics", []):
        ts = stat.get("timestamp", {})
        dt = datetime.strptime(
            f"{ts['date']} {ts['time']}", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=UTC)
        metrics = walk_statistics(stat)
        value = metrics.get(plugin, {}).get(field)
        points.append((int(dt.timestamp()), value))
    return points


def _infer_step(points: list[tuple[int, float | None]]) -> int:
    if len(points) < 2:
        return 0
    return points[1][0] - points[0][0]


def fetch(host_dir: Path, plugin: str, field: str, start: str, end: str) -> FetchResult:
    """Fetch one field's time series by reading and merging daily sar log files.

    `host_dir` is `SAR_BASE_PATH/<group>/<host>/`. `plugin`/`field` must be
    the same names `sar_index.build_index` assigned (e.g. plugin="cpu-load.0",
    field="usr"). Missing daily files are skipped as gaps, never an error.
    """
    start_epoch = _normalize_time_to_epoch(start)
    end_epoch = _normalize_time_to_epoch(end)
    start_dt = datetime.fromtimestamp(start_epoch, tz=UTC)
    end_dt = datetime.fromtimestamp(end_epoch, tz=UTC)

    all_points: list[tuple[int, float | None]] = []
    for day in _date_range(start_dt, end_dt):
        sa_file = _sa_file_for_date(host_dir, day)
        if not sa_file.exists():
            continue
        start_hms = start_dt.strftime("%H:%M:%S") if day == start_dt.date() else None
        end_hms = end_dt.strftime("%H:%M:%S") if day == end_dt.date() else None
        data = _run_sadf(sa_file, start_hms, end_hms)
        all_points.extend(_extract_points(data, plugin, field))

    all_points.sort(key=lambda p: p[0])
    return FetchResult(
        step=_infer_step(all_points), ds_names=[field], points=all_points
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sar.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Add an integration test against a real sar log**

```python
# tests/test_sar.py に追記
def test_fetch_returns_points_from_real_sar_log(sar_root: Path):
    from tests.conftest import SAR_GROUP, SAR_HOST

    host_dir = sar_root / SAR_GROUP / SAR_HOST
    yesterday_epoch = int(__import__("time").time()) - 60
    now_epoch = int(__import__("time").time()) + 60
    result = fetch(host_dir, "cpu-load.all", "usr", str(yesterday_epoch), str(now_epoch))
    assert result.ds_names == ["usr"]
    assert len(result.points) > 0
    assert all(isinstance(ts, int) for ts, _ in result.points)
```

Run: `uv run pytest tests/test_sar.py -v`
Expected: PASS (7 tests total); the real-log test SKIPPED if `sadf`/`sadc` unavailable locally, PASS where sysstat is installed (verified manually in the Docker sysstat container used to derive this plan's JSON samples).

- [ ] **Step 6: Commit**

```bash
git add src/rrdmcp/sar.py tests/test_sar.py
git commit -m "feat: implement sar fetch() merging multiple daily log files"
```

---

### Task 6: `sar.py` — `render_graph`(matplotlib)と`matplotlib`依存の追加

**Files:**
- Modify: `pyproject.toml`(依存追加)
- Modify: `src/rrdmcp/rrd.py`(`_GRAPH_COLORS`を公開名`GRAPH_COLORS`にリネームして再利用可能にする)
- Modify: `src/rrdmcp/sar.py`
- Test: `tests/test_sar.py`

**Interfaces:**
- Consumes: `rrd.GRAPH_COLORS`(リネーム後)
- Produces: `sar.render_graph(points_and_labels: list[tuple[list[tuple[int, float | None]], str]], start: str, end: str, title: str, vlabel: str, width: int = 800, height: int = 300) -> bytes`

- [ ] **Step 1: Add the dependency**

`pyproject.toml`の`dependencies`に追記:

```toml
dependencies = [
    "mcp>=1.2.0",
    "matplotlib>=3.8",
]
```

Run: `uv sync`
Expected: `matplotlib`とその依存(`numpy`等)がインストールされる

- [ ] **Step 2: Rename `_GRAPH_COLORS` to `GRAPH_COLORS` in rrd.py**

`src/rrdmcp/rrd.py`内の`_GRAPH_COLORS`を`GRAPH_COLORS`に変更する(定義箇所と`render_graph`内の参照箇所の2箇所)。

Run: `uv run pytest tests/test_rrd.py -v`
Expected: PASS(既存挙動に変化なし)

- [ ] **Step 3: Write the failing test**

```python
# tests/test_sar.py に追記
from rrdmcp.sar import render_graph


def test_render_graph_returns_png_bytes():
    points_a = [(1000, 10.0), (1010, 20.0), (1020, 15.0)]
    points_b = [(1000, 5.0), (1010, 8.0), (1020, 6.0)]
    png = render_graph(
        [(points_a, "User"), (points_b, "System")],
        "1000",
        "1020",
        "CPU usage",
        "%",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_graph_handles_none_values():
    points = [(1000, 10.0), (1010, None), (1020, 15.0)]
    png = render_graph([(points, "User")], "1000", "1020", "CPU usage", "%")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_sar.py -v -k render_graph`
Expected: FAIL with `ImportError: cannot import name 'render_graph' from 'rrdmcp.sar'`

- [ ] **Step 5: Write minimal implementation**

`src/rrdmcp/sar.py`のimportブロック(Task 5で書いたもの)の先頭に`io`と`matplotlib`まわりの3行を挿入する。`matplotlib.use("Agg")`は`matplotlib.pyplot`をimportする前に呼ぶ必要があるため、import順序に注意する(`# noqa: E402`はこの順序制約のため)。最終的なファイル冒頭は以下になる:

```python
# src/rrdmcp/sar.py の import 部分(最終形)
import io
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .errors import (
    SarFileNotAvailableError,
    SarInvalidTimeError,
    SarToolNotFoundError,
    SarToolTimeoutError,
)
from .rrd import GRAPH_COLORS
from .sar_index import walk_statistics
from .timeutil import try_parse_iso8601
```

そして`src/rrdmcp/sar.py`の末尾(Task 5で定義した`fetch`関数の後)に`render_graph`を追加する:

```python
def render_graph(
    points_and_labels: list[tuple[list[tuple[int, float | None]], str]],
    start: str,
    end: str,
    title: str,
    vlabel: str,
    width: int = 800,
    height: int = 300,
) -> bytes:
    fig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    for idx, (points, label) in enumerate(points_and_labels):
        times = [datetime.fromtimestamp(ts, tz=UTC) for ts, _ in points]
        values = [v for _, v in points]
        color = GRAPH_COLORS[idx % len(GRAPH_COLORS)]
        ax.plot(times, values, label=label, color=color)
    ax.set_title(title)
    ax.set_ylabel(vlabel)
    if points_and_labels:
        ax.legend()
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_sar.py -v`
Expected: PASS (all tests including the 2 new ones)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/rrdmcp/rrd.py src/rrdmcp/sar.py tests/test_sar.py
git commit -m "feat: implement sar render_graph via matplotlib"
```

---

### Task 7: `server.py` — `SAR_BASE_PATH`統合と`source`による分岐

**Files:**
- Modify: `src/rrdmcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `sar_index.build_index(base_path: Path) -> list[NormalizedField]`(Task 4)、`sar.fetch(host_dir, plugin, field, start, end) -> sar.FetchResult`(Task 5)、`sar.render_graph(points_and_labels, start, end, title, vlabel, width, height) -> bytes`(Task 6)

- [ ] **Step 1: Write the failing test**

`tests/conftest.py`の`sar_root`フィクスチャ(Task 4)を使い、`tests/test_server.py`に追記:

```python
# tests/test_server.py に追記
from pathlib import Path

from rrdmcp.rrd import FetchResult as RrdFetchResult


def test_load_entries_merges_munin_and_sar(
    monkeypatch: pytest.MonkeyPatch, sar_root: Path
):
    from rrdmcp import server
    from tests.conftest import SAR_GROUP, SAR_HOST

    monkeypatch.setenv("SAR_BASE_PATH", str(sar_root))
    entries = server._load_entries()
    sources = {e.source for e in entries}
    assert sources == {"munin", "sar"}
    assert any(e.group == SAR_GROUP and e.host == SAR_HOST for e in entries)


def test_load_entries_skips_sar_when_env_var_unset():
    from rrdmcp import server

    entries = server._load_entries()
    assert all(e.source == "munin" for e in entries)


def test_fetch_series_tool_dispatches_to_sar_backend(
    monkeypatch: pytest.MonkeyPatch, sar_root: Path
):
    from rrdmcp import server
    from tests.conftest import SAR_GROUP, SAR_HOST

    monkeypatch.setenv("SAR_BASE_PATH", str(sar_root))
    entries = server._load_entries()
    cpu_field = next(
        e
        for e in entries
        if e.source == "sar" and e.group == SAR_GROUP and e.host == SAR_HOST
        and e.plugin.startswith("cpu-load.") and e.field == "usr"
    )
    import time

    result = server.fetch_series(
        SAR_GROUP,
        SAR_HOST,
        cpu_field.plugin,
        "usr",
        str(int(time.time()) - 60),
        str(int(time.time()) + 60),
    )
    assert "points" in result
    assert result["ds_names"] == ["usr"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -v -k "merges_munin_and_sar or skips_sar or dispatches_to_sar"`
Expected: FAIL — `test_load_entries_merges_munin_and_sar`はsourcesが`{"munin"}`のみで失敗、`dispatches_to_sar`は`cpu_field`が見つからず`StopIteration`

- [ ] **Step 3: Write minimal implementation**

`src/rrdmcp/server.py`の先頭に追記:

```python
from . import discovery, rrd, sar, sar_index
```

(既存の`from . import discovery, rrd`を置き換え)

`_base_path`の下に追記:

```python
def _sar_base_path() -> Path | None:
    raw = os.environ.get("SAR_BASE_PATH")
    return Path(raw) if raw else None
```

`_load_entries`を変更:

```python
def _load_entries() -> list[discovery.NormalizedField]:
    base_path = _base_path()
    datafile_path = _datafile_path()
    datafile_index = load_datafile(datafile_path) if datafile_path.exists() else None
    entries = discovery.build_index(base_path, datafile_index)
    sar_base = _sar_base_path()
    if sar_base is not None:
        entries = entries + sar_index.build_index(sar_base)
    return entries
```

`fetch_series`内、`result = rrd.fetch(resolved.path, start, end)`の行を置き換え:

```python
        if resolved.source == "munin":
            result = rrd.fetch(resolved.path, start, end)
        else:
            result = sar.fetch(resolved.path, resolved.plugin, resolved.field, start, end)
```

`fetch_series`のdocstringに1文追記:

```python
    `start`/`end` accept a unix timestamp, an ISO 8601 timestamp (e.g.
    "2026-09-07T12:00:00Z"; naive timestamps are treated as UTC), or any
    string rrdtool understands (e.g. "-1d", "now"). Fields from the sar
    data source only accept a unix timestamp or an ISO 8601 timestamp
    (no rrdtool-style relative expressions).
```

`render_graph`ツールの実装を変更:

```python
@mcp.tool()
def render_graph(
    group: str,
    host: str,
    plugin: str,
    fields: list[str],
    start: str,
    end: str,
    width: int = 800,
    height: int = 300,
) -> "Image | dict":
    """Render a PNG graph overlaying the given fields of a plugin.

    `start`/`end` accept a unix timestamp, an ISO 8601 timestamp (e.g.
    "2026-09-07T12:00:00Z"; naive timestamps are treated as UTC), or any
    string rrdtool understands (e.g. "-1d", "now"). Fields from the sar
    data source only accept a unix timestamp or an ISO 8601 timestamp
    (no rrdtool-style relative expressions).
    """
    try:
        entries = _load_entries()
        discovery._require_plugin(entries, group, host, plugin)
        resolved_fields = []
        for field in fields:
            resolved = discovery.resolve_field(entries, group, host, plugin, field)
            if not resolved.rrd_available:
                return {
                    "error": f"RRD file not available for {group}/{host}/{plugin}/{field}"
                }
            resolved_fields.append(resolved)
        plugins = discovery.list_plugins(entries, group, host)
        plugin_info = next(p for p in plugins if p["plugin"] == plugin)
        title = plugin_info["graph_title"] or f"{host} {plugin}"
        vlabel = plugin_info["graph_vlabel"] or ""
        source = resolved_fields[0].source if resolved_fields else "munin"
        if source == "munin":
            paths_and_labels = [
                (r.path, r.meta.label or r.field) for r in resolved_fields
            ]
            png_bytes = rrd.render_graph(
                paths_and_labels, start, end, title, vlabel, width, height
            )
        else:
            points_and_labels = []
            for r in resolved_fields:
                fetched = sar.fetch(r.path, r.plugin, r.field, start, end)
                points_and_labels.append((fetched.points, r.meta.label or r.field))
            png_bytes = sar.render_graph(
                points_and_labels, start, end, title, vlabel, width, height
            )
        return Image(data=png_bytes, format="png")
    except RrdMcpError as exc:
        return {"error": str(exc)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS(sar系テストは`sadf`/`sadc`が無い環境では`sar_root`フィクスチャによりSKIP、他は全てPASS)

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS(全体、環境依存でSKIPが混じるのは許容)

- [ ] **Step 6: Commit**

```bash
git add src/rrdmcp/server.py tests/test_server.py
git commit -m "feat: wire sar backend into MCP tools via SAR_BASE_PATH"
```

---

### Task 8: README更新

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the configuration table**

`## Configuration (environment variables)`テーブルに1行追加:

```markdown
| `SAR_BASE_PATH` | (unset — sar support disabled) | Root directory of sar logs, laid out as `<group>/<host>/saXX` (requires the `sadf` command on `PATH`, from the `sysstat` package) |
```

- [ ] **Step 2: Note the sar time-argument restriction under `fetch_series`**

`## Tools`セクションの`fetch_series`の説明に1文追記:

```markdown
- `fetch_series(...)` — ... Fields backed by the sar data source only accept a unix timestamp or an ISO 8601 timestamp for `start`/`end` (rrdtool-style relative expressions like `-1d` are munin-only).
```

- [ ] **Step 3: Add a note under "Known limitations"**

```markdown
- sar support requires log files pre-aggregated under `SAR_BASE_PATH/<group>/<host>/saXX` (e.g. via `rsync` from each host's `/var/log/sa`); it does not read `/var/log/sa` directly or collect data itself
- sar plugin/field discovery only recognizes a fixed set of `sadf -j` structures (see `sar_index.SAR_ACTIVITY_META`); uncommon activities still work but show up without a human-friendly title/label
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document SAR_BASE_PATH and sar data source limitations"
```
