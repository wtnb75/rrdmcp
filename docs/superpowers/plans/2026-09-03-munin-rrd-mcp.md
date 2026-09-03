# Munin RRD MCPサーバ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MuninのRRDメトリックデータをLLMに公開するstdio MCPサーバ(`rrdmcp`)を構築する。

**Architecture:** Python製、`uv`管理のstdioMCPサーバ。`rrdtool`公式CLIをsubprocessで呼び出し、`/var/lib/munin/datafile`をパースしてhost/plugin/fieldの階層とメタデータ(タイトル・単位・閾値)を組み立てる。MCPツールは生データ取得に専念し、分析ロジックは持たない。

**Tech Stack:** Python 3.11+, `uv`, `mcp`(公式Python SDK, FastMCP), `pytest`, `rrdtool` CLI(subprocess経由、ビルド依存なし)

**Spec:** `docs/superpowers/specs/2026-09-03-munin-rrd-mcp-design.md`

## Global Constraints

- 対象RRDアクセスはローカルファイルシステムのみ。`rrdtool`本体はsubprocess呼び出し(Pythonバインディングやネイティブビルドは使わない)
- トランスポートはstdioのみ(v1)
- 設定はすべて環境変数: `MUNIN_RRD_BASE_PATH`(既定 `/var/lib/munin`)、`MUNIN_DATAFILE_PATH`(既定 `${MUNIN_RRD_BASE_PATH}/datafile`)
- LLMが書いた任意コードをサーバ側で実行する機能は持たない
- `rrdtool`のsubprocess呼び出しには30秒のタイムアウトを設定する
- `rrdtool`コマンドが実行環境に無い場合、テストは`pytest.skip`し、テストスイート自体は落とさない
- パッケージ管理は`uv`(`uv sync` / `uv run pytest` / `uv run rrdmcp`)

---

### Task 1: プロジェクト初期化 + エラー階層

**Files:**
- Create: `pyproject.toml`
- Create: `src/rrdmcp/__init__.py`
- Create: `src/rrdmcp/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `rrdmcp.errors.RrdMcpError`(基底)、`RrdToolNotFoundError`、`RrdToolTimeoutError`、`RrdFileNotAvailableError`、`HostNotFoundError`、`PluginNotFoundError`、`FieldNotFoundError`(すべて`RrdMcpError`のサブクラス) — 以降の全タスクがこれらを使う

- [ ] **Step 1: `pyproject.toml`を作成**

```toml
[project]
name = "rrdmcp"
version = "0.1.0"
description = "MCP server exposing Munin RRD metrics to LLMs"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.2.0",
]

[project.scripts]
rrdmcp = "rrdmcp.server:main"

[dependency-groups]
dev = [
    "pytest>=8.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/rrdmcp"]
```

- [ ] **Step 2: パッケージ雛形を作成**

`src/rrdmcp/__init__.py`:
```python
```

(空ファイルでよい)

- [ ] **Step 3: `errors.py`を作成**

```python
class RrdMcpError(Exception):
    """Base class for all rrdmcp domain errors."""


class RrdToolNotFoundError(RrdMcpError):
    """The `rrdtool` executable is not on PATH."""


class RrdToolTimeoutError(RrdMcpError):
    """An `rrdtool` subprocess call exceeded the timeout."""


class RrdFileNotAvailableError(RrdMcpError):
    """The RRD file for a resolved field does not exist or rrdtool failed on it."""


class HostNotFoundError(RrdMcpError):
    """No (group, host) matches the given identifiers."""


class PluginNotFoundError(RrdMcpError):
    """No plugin matches the given identifiers under the resolved host."""


class FieldNotFoundError(RrdMcpError):
    """No field matches the given identifiers under the resolved plugin."""
```

- [ ] **Step 4: 依存関係を同期し、失敗するテストを書く**

`tests/test_errors.py`:
```python
from rrdmcp.errors import (
    FieldNotFoundError,
    HostNotFoundError,
    PluginNotFoundError,
    RrdFileNotAvailableError,
    RrdMcpError,
    RrdToolNotFoundError,
    RrdToolTimeoutError,
)


def test_all_errors_are_rrdmcp_errors():
    for cls in (
        RrdToolNotFoundError,
        RrdToolTimeoutError,
        RrdFileNotAvailableError,
        HostNotFoundError,
        PluginNotFoundError,
        FieldNotFoundError,
    ):
        assert issubclass(cls, RrdMcpError)


def test_error_message_is_preserved():
    err = HostNotFoundError("host not found: group='g' host='h'")
    assert str(err) == "host not found: group='g' host='h'"
```

Run: `uv sync && uv run pytest tests/test_errors.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'rrdmcp'`) because `src/rrdmcp/errors.py`とパッケージ配置はできているが、まだ`uv sync`していない/エディタブルインストールされていない状態を確認する。

- [ ] **Step 5: テストを実行して通ることを確認**

Run: `uv run pytest tests/test_errors.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: コミット**

```bash
git add pyproject.toml src/rrdmcp/__init__.py src/rrdmcp/errors.py tests/test_errors.py
git commit -m "feat: プロジェクト初期化とエラー階層を追加"
```

---

### Task 2: テストfixture(Munin風ディレクトリ生成)

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/test_conftest_fixture.py`

**Interfaces:**
- Consumes: なし(標準の`rrdtool`CLIのみ)
- Produces: pytest fixture `munin_root(tmp_path) -> Path` — 以降の全モジュールテストがこのfixtureに依存する。`rrdtool`が見つからない場合は`pytest.skip`する。fixtureは`testgroup`ディレクトリ配下に`testhost.example.com`ホストの`cpu`プラグイン(`user`/`system`/`idle`フィールド、いずれもGAUGE)のRRDファイルと、対応する`<tmp_path>/datafile`を作成する。

- [ ] **Step 1: `conftest.py`を作成**

```python
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
        rrd_file = group_dir / f"{TEST_HOST}-{TEST_PLUGIN}-{field}-{ds_type[0].lower()}.rrd"
        subprocess.run(
            [
                "rrdtool", "create", str(rrd_file),
                "--start", str(start),
                "--step", "10",
                f"DS:42:{ds_type}:20:0:100",
                "RRA:AVERAGE:0.5:1:200",
            ],
            check=True, capture_output=True,
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
```

- [ ] **Step 2: fixtureを検証する失敗するテストを書く**

`tests/test_conftest_fixture.py`:
```python
from pathlib import Path


def test_munin_root_creates_rrd_files_and_datafile(munin_root: Path):
    group_dir = munin_root / "testgroup"
    assert (group_dir / "testhost.example.com-cpu-user-g.rrd").exists()
    assert (group_dir / "testhost.example.com-cpu-system-g.rrd").exists()
    assert (group_dir / "testhost.example.com-cpu-idle-g.rrd").exists()
    datafile_text = (munin_root / "datafile").read_text()
    assert "cpu.graph_title CPU usage" in datafile_text
    assert "cpu.user.warning 80" in datafile_text
```

Run: `uv run pytest tests/test_conftest_fixture.py -v`
Expected: FAIL if `rrdtool`がPATHに無ければSKIP、あれば現時点では`conftest.py`未作成分のimportエラー等がないことを事前確認(このステップはStep1のコード作成前に実行しないこと。Step1完了後に実行する)。

- [ ] **Step 3: テストを実行して通ることを確認**

Run: `uv run pytest tests/test_conftest_fixture.py -v`
Expected: PASS(1 passed)、または`rrdtool`が無い環境ではSKIPPED

- [ ] **Step 4: コミット**

```bash
git add tests/conftest.py tests/test_conftest_fixture.py
git commit -m "test: Munin風ディレクトリを生成するpytest fixtureを追加"
```

---

### Task 3: `munin_datafile.py` — datafileパーサ

**Files:**
- Create: `src/rrdmcp/munin_datafile.py`
- Test: `tests/test_munin_datafile.py`

**Interfaces:**
- Consumes: なし(純粋なテキストパース)
- Produces:
  - `FieldMeta`(dataclass): `label, type, min, max, warning, critical, info: str | None = None`、`extra: dict[str, str]`
  - `PluginMeta`(dataclass): `graph_title, graph_vlabel, graph_category, graph_info: str | None = None`、`extra_graph_attrs: dict[str, str]`、`fields: dict[str, FieldMeta]`
  - `DatafileIndex = dict[tuple[str, str], dict[str, PluginMeta]]`(キーは`(group, host)`)
  - `parse_datafile(text: str) -> DatafileIndex`
  - `load_datafile(path: Path) -> DatafileIndex`
  - 以降のタスクはこれらの型・関数を使う

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_munin_datafile.py`:
```python
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
```

Run: `uv run pytest tests/test_munin_datafile.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'rrdmcp.munin_datafile'`)

- [ ] **Step 2: `munin_datafile.py`を実装**

```python
import re
from dataclasses import dataclass, field as dc_field
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
```

- [ ] **Step 3: テストを実行して通ることを確認**

Run: `uv run pytest tests/test_munin_datafile.py -v`
Expected: PASS(6 passed)

- [ ] **Step 4: コミット**

```bash
git add src/rrdmcp/munin_datafile.py tests/test_munin_datafile.py
git commit -m "feat: munin datafileパーサを追加"
```

---

### Task 4: `rrd.py` — rrdtool CLIラッパー

**Files:**
- Create: `src/rrdmcp/rrd.py`
- Test: `tests/test_rrd.py`

**Interfaces:**
- Consumes: `rrdmcp.errors.{RrdToolNotFoundError, RrdToolTimeoutError, RrdFileNotAvailableError}`、fixture `munin_root`
- Produces:
  - `RRDTOOL_TIMEOUT_SECONDS: int = 30`
  - `sanitize_name(name: str) -> str`
  - `type_letter(ds_type: str) -> str`
  - `rrd_path(base_path: Path, group: str, host: str, plugin: str, field: str, ds_type: str) -> Path`
  - `require_rrdtool() -> str`
  - `FetchResult`(dataclass): `step: int, ds_names: list[str], points: list[tuple[int, float | None]]`
  - `fetch(path: Path, start: str, end: str, cf: str = "AVERAGE") -> FetchResult`
  - `info(path: Path) -> dict[str, str]`
  - `render_graph(paths_and_labels: list[tuple[Path, str]], start: str, end: str, title: str, vlabel: str, width: int = 800, height: int = 300) -> bytes`
  - 以降のタスクはこれらを使う

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_rrd.py`:
```python
from pathlib import Path

import pytest

from rrdmcp.errors import RrdFileNotAvailableError, RrdToolNotFoundError
from rrdmcp.rrd import fetch, info, rrd_path, render_graph, sanitize_name, type_letter


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
        [(rrd_file, "User")], "-2h", "now", "CPU usage", "%",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
```

Run: `uv run pytest tests/test_rrd.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'rrdmcp.rrd'`)

- [ ] **Step 2: `rrd.py`を実装**

```python
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import RrdFileNotAvailableError, RrdToolNotFoundError, RrdToolTimeoutError

RRDTOOL_TIMEOUT_SECONDS = 30

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.]")
_GRAPH_COLORS = ["#0000FF", "#FF0000", "#00AA00", "#FF8800", "#AA00AA", "#00AAAA"]


def sanitize_name(name: str) -> str:
    return _SANITIZE_RE.sub("_", name)


def type_letter(ds_type: str) -> str:
    return ds_type[:1].lower()


def rrd_path(base_path: Path, group: str, host: str, plugin: str, field: str, ds_type: str) -> Path:
    filename = f"{host}-{sanitize_name(plugin)}-{sanitize_name(field)}-{type_letter(ds_type)}.rrd"
    return base_path / group / filename


def require_rrdtool() -> str:
    exe = shutil.which("rrdtool")
    if exe is None:
        raise RrdToolNotFoundError("rrdtool command not found in PATH")
    return exe


def _run_rrdtool(args: list[str], text: bool = True) -> subprocess.CompletedProcess:
    exe = require_rrdtool()
    try:
        return subprocess.run(
            [exe, *args],
            capture_output=True,
            text=text,
            timeout=RRDTOOL_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RrdToolTimeoutError(
            f"rrdtool {args[0]} timed out after {RRDTOOL_TIMEOUT_SECONDS}s"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise RrdFileNotAvailableError(
            f"rrdtool {args[0]} failed: {(stderr or '').strip()}"
        ) from exc


@dataclass
class FetchResult:
    step: int
    ds_names: list[str]
    points: list[tuple[int, float | None]]


def _infer_step(points: list[tuple[int, float | None]]) -> int:
    if len(points) < 2:
        return 0
    return points[1][0] - points[0][0]


def fetch(path: Path, start: str, end: str, cf: str = "AVERAGE") -> FetchResult:
    if not path.exists():
        raise RrdFileNotAvailableError(f"RRD file not found: {path}")
    proc = _run_rrdtool(["fetch", str(path), cf, "--start", str(start), "--end", str(end)])
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    ds_names = lines[0].split()
    points: list[tuple[int, float | None]] = []
    for line in lines[1:]:
        ts_str, _, rest = line.partition(":")
        values = rest.split()
        # Munin RRD files always have exactly one DS, literally named "42".
        raw_value = values[0] if values else "nan"
        parsed_value = None if raw_value.lower() == "nan" else float(raw_value)
        points.append((int(ts_str), parsed_value))
    return FetchResult(step=_infer_step(points), ds_names=ds_names, points=points)


def info(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RrdFileNotAvailableError(f"RRD file not found: {path}")
    proc = _run_rrdtool(["info", str(path)])
    result: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"')
    return result


def render_graph(
    paths_and_labels: list[tuple[Path, str]],
    start: str,
    end: str,
    title: str,
    vlabel: str,
    width: int = 800,
    height: int = 300,
) -> bytes:
    for path, _ in paths_and_labels:
        if not path.exists():
            raise RrdFileNotAvailableError(f"RRD file not found: {path}")
    args = [
        "graph", "-",
        "--start", str(start),
        "--end", str(end),
        "--title", title,
        "--vlabel", vlabel,
        "--width", str(width),
        "--height", str(height),
        "--imgformat", "PNG",
    ]
    for idx, (path, label) in enumerate(paths_and_labels):
        ds_name = f"v{idx}"
        color = _GRAPH_COLORS[idx % len(_GRAPH_COLORS)]
        args.append(f"DEF:{ds_name}={path}:42:AVERAGE")
        args.append(f"LINE1:{ds_name}{color}:{label}")
    proc = _run_rrdtool(args, text=False)
    return proc.stdout
```

- [ ] **Step 3: テストを実行して通ることを確認**

Run: `uv run pytest tests/test_rrd.py -v`
Expected: PASS(7 passed)、`rrdtool`が無い環境ではmunin_root依存分がSKIPPED

- [ ] **Step 4: コミット**

```bash
git add src/rrdmcp/rrd.py tests/test_rrd.py
git commit -m "feat: rrdtool CLIラッパーを追加"
```

---

### Task 5: `discovery.py` — host/plugin/field発見ロジック

**Files:**
- Create: `src/rrdmcp/discovery.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `rrdmcp.munin_datafile.{DatafileIndex, FieldMeta, PluginMeta, load_datafile}`、`rrdmcp.rrd.rrd_path`、`rrdmcp.errors.{HostNotFoundError, PluginNotFoundError, FieldNotFoundError}`
- Produces:
  - `NormalizedField`(dataclass): `group: str, host: str, plugin: str, field: str, meta: FieldMeta, plugin_meta: PluginMeta, path: Path, rrd_available: bool, metadata_available: bool`
  - `fallback_scan(base_path: Path) -> list[dict]`(キー: `group, host_plugin, field, type, path, metadata_available`)
  - `build_index(base_path: Path, datafile_index: DatafileIndex | None) -> list[NormalizedField]`
  - `list_hosts(entries: list[NormalizedField]) -> list[dict]`
  - `list_plugins(entries: list[NormalizedField], group: str, host: str) -> list[dict]`
  - `list_fields(entries: list[NormalizedField], group: str, host: str, plugin: str) -> list[dict]`
  - `resolve_field(entries: list[NormalizedField], group: str, host: str, plugin: str, field: str) -> NormalizedField`
  - server.pyがこれらを直接使う

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_discovery.py`:
```python
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
```

Run: `uv run pytest tests/test_discovery.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'rrdmcp.discovery'`)

- [ ] **Step 2: `discovery.py`を実装**

```python
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import FieldNotFoundError, HostNotFoundError, PluginNotFoundError
from .munin_datafile import DatafileIndex, FieldMeta, PluginMeta
from .rrd import rrd_path

_FALLBACK_RE = re.compile(r"^(?P<host_plugin>.+)-(?P<field>[^-]+)-(?P<type>[a-z])\.rrd$")


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


def fallback_scan(base_path: Path) -> list[dict]:
    """Best-effort discovery when no datafile is available.

    Host/plugin boundaries within `host_plugin` cannot be determined
    reliably from the filename alone (both may contain hyphens), so they
    are reported as a single combined string with no metadata.
    """
    entries: list[dict] = []
    for rrd_file in sorted(base_path.rglob("*.rrd")):
        group = rrd_file.parent.name
        match = _FALLBACK_RE.match(rrd_file.name)
        if not match:
            continue
        entries.append(
            {
                "group": group,
                "host_plugin": match.group("host_plugin"),
                "field": match.group("field"),
                "type": match.group("type"),
                "path": str(rrd_file),
                "metadata_available": False,
            }
        )
    return entries


def _build_from_datafile(base_path: Path, datafile_index: DatafileIndex) -> list[NormalizedField]:
    entries: list[NormalizedField] = []
    for (group, host), plugins in datafile_index.items():
        for plugin_name, plugin_meta in plugins.items():
            for field_name, field_meta in plugin_meta.fields.items():
                ds_type = field_meta.type or "GAUGE"
                path = rrd_path(base_path, group, host, plugin_name, field_name, ds_type)
                entries.append(
                    NormalizedField(
                        group=group,
                        host=host,
                        plugin=plugin_name,
                        field=field_name,
                        meta=field_meta,
                        plugin_meta=plugin_meta,
                        path=path,
                        rrd_available=path.exists(),
                        metadata_available=True,
                    )
                )
    return entries


def _build_from_fallback(base_path: Path) -> list[NormalizedField]:
    entries: list[NormalizedField] = []
    for raw in fallback_scan(base_path):
        entries.append(
            NormalizedField(
                group=raw["group"],
                host=raw["host_plugin"],
                plugin="",
                field=raw["field"],
                meta=FieldMeta(),
                plugin_meta=PluginMeta(),
                path=Path(raw["path"]),
                rrd_available=True,
                metadata_available=False,
            )
        )
    return entries


def build_index(base_path: Path, datafile_index: DatafileIndex | None) -> list[NormalizedField]:
    if datafile_index:
        return _build_from_datafile(base_path, datafile_index)
    return _build_from_fallback(base_path)


def list_hosts(entries: list[NormalizedField]) -> list[dict]:
    seen = sorted({(e.group, e.host) for e in entries})
    return [{"group": group, "host": host} for group, host in seen]


def _require_host(entries: list[NormalizedField], group: str, host: str) -> None:
    if not any(e.group == group and e.host == host for e in entries):
        raise HostNotFoundError(f"host not found: group={group!r} host={host!r}")


def list_plugins(entries: list[NormalizedField], group: str, host: str) -> list[dict]:
    _require_host(entries, group, host)
    seen: dict[str, PluginMeta] = {}
    for e in entries:
        if e.group == group and e.host == host:
            seen.setdefault(e.plugin, e.plugin_meta)
    return [
        {
            "plugin": plugin,
            "graph_title": meta.graph_title,
            "graph_category": meta.graph_category,
            "graph_vlabel": meta.graph_vlabel,
        }
        for plugin, meta in sorted(seen.items())
    ]


def _require_plugin(entries: list[NormalizedField], group: str, host: str, plugin: str) -> None:
    _require_host(entries, group, host)
    if not any(e.group == group and e.host == host and e.plugin == plugin for e in entries):
        raise PluginNotFoundError(
            f"plugin not found: group={group!r} host={host!r} plugin={plugin!r}"
        )


def list_fields(entries: list[NormalizedField], group: str, host: str, plugin: str) -> list[dict]:
    _require_plugin(entries, group, host, plugin)
    matched = [e for e in entries if e.group == group and e.host == host and e.plugin == plugin]
    return [
        {
            "field": e.field,
            "label": e.meta.label,
            "type": e.meta.type,
            "min": e.meta.min,
            "max": e.meta.max,
            "warning": e.meta.warning,
            "critical": e.meta.critical,
            "info": e.meta.info,
            "rrd_available": e.rrd_available,
            "metadata_available": e.metadata_available,
        }
        for e in sorted(matched, key=lambda e: e.field)
    ]


def resolve_field(
    entries: list[NormalizedField], group: str, host: str, plugin: str, field: str
) -> NormalizedField:
    _require_plugin(entries, group, host, plugin)
    for e in entries:
        if e.group == group and e.host == host and e.plugin == plugin and e.field == field:
            return e
    raise FieldNotFoundError(
        f"field not found: group={group!r} host={host!r} plugin={plugin!r} field={field!r}"
    )
```

- [ ] **Step 3: テストを実行して通ることを確認**

Run: `uv run pytest tests/test_discovery.py -v`
Expected: PASS(10 passed)

- [ ] **Step 4: コミット**

```bash
git add src/rrdmcp/discovery.py tests/test_discovery.py
git commit -m "feat: host/plugin/field発見ロジックを追加"
```

---

### Task 6: `server.py` — MCPツール登録

**Files:**
- Create: `src/rrdmcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `rrdmcp.discovery.*`、`rrdmcp.rrd.*`、`rrdmcp.munin_datafile.load_datafile`、`rrdmcp.errors.RrdMcpError`
- Produces: モジュールレベルの`mcp = FastMCP("rrdmcp")`と6つのツール関数(`list_hosts, list_plugins, list_fields, get_metadata, fetch_series, render_graph`)、および`main() -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_server.py`:
```python
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _configure_env(monkeypatch: pytest.MonkeyPatch, munin_root: Path):
    monkeypatch.setenv("MUNIN_RRD_BASE_PATH", str(munin_root))
    monkeypatch.setenv("MUNIN_DATAFILE_PATH", str(munin_root / "datafile"))


def test_list_hosts_tool():
    from rrdmcp import server

    result = server.list_hosts()
    assert result == [{"group": "testgroup", "host": "testhost.example.com"}]


def test_list_plugins_tool():
    from rrdmcp import server

    result = server.list_plugins("testgroup", "testhost.example.com")
    assert result[0]["plugin"] == "cpu"


def test_list_plugins_tool_returns_error_dict_for_unknown_host():
    from rrdmcp import server

    result = server.list_plugins("testgroup", "no-such-host")
    assert "error" in result


def test_get_metadata_tool_for_single_field():
    from rrdmcp import server

    result = server.get_metadata("testgroup", "testhost.example.com", "cpu", "user")
    assert result["warning"] == "80"
    assert result["rrd_available"] is True


def test_get_metadata_tool_for_whole_plugin():
    from rrdmcp import server

    result = server.get_metadata("testgroup", "testhost.example.com", "cpu")
    assert result["graph_title"] == "CPU usage"
    assert {f["field"] for f in result["fields"]} == {"user", "system", "idle"}


def test_fetch_series_tool():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup", "testhost.example.com", "cpu", "user", "-2h", "now"
    )
    assert "points" in result
    assert len(result["points"]) > 0
    assert result["ds_names"] == ["42"]


def test_fetch_series_tool_returns_error_dict_for_unknown_field():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup", "testhost.example.com", "cpu", "no-such-field", "-2h", "now"
    )
    assert "error" in result


def test_render_graph_tool_returns_image():
    from mcp.server.fastmcp import Image

    from rrdmcp import server

    result = server.render_graph(
        "testgroup", "testhost.example.com", "cpu", ["user", "system"], "-2h", "now"
    )
    assert isinstance(result, Image)
    assert result.data[:8] == b"\x89PNG\r\n\x1a\n"
```

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'rrdmcp.server'`)

- [ ] **Step 2: `server.py`を実装**

```python
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from . import discovery, rrd
from .errors import RrdMcpError
from .munin_datafile import load_datafile

mcp = FastMCP("rrdmcp")


def _base_path() -> Path:
    return Path(os.environ.get("MUNIN_RRD_BASE_PATH", "/var/lib/munin"))


def _datafile_path() -> Path:
    default = str(_base_path() / "datafile")
    return Path(os.environ.get("MUNIN_DATAFILE_PATH", default))


def _load_entries() -> list[discovery.NormalizedField]:
    base_path = _base_path()
    datafile_path = _datafile_path()
    datafile_index = load_datafile(datafile_path) if datafile_path.exists() else None
    return discovery.build_index(base_path, datafile_index)


@mcp.tool()
def list_hosts() -> list[dict] | dict:
    """List all (group, host) pairs discovered from the Munin datafile."""
    try:
        return discovery.list_hosts(_load_entries())
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_plugins(group: str, host: str) -> list[dict] | dict:
    """List plugins for a given host."""
    try:
        return discovery.list_plugins(_load_entries(), group, host)
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_fields(group: str, host: str, plugin: str) -> list[dict] | dict:
    """List fields for a given plugin."""
    try:
        return discovery.list_fields(_load_entries(), group, host, plugin)
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_metadata(group: str, host: str, plugin: str, field: str | None = None) -> dict:
    """Get metadata for a plugin, or a single field within it if `field` is given."""
    try:
        entries = _load_entries()
        if field is None:
            fields = discovery.list_fields(entries, group, host, plugin)
            plugins = discovery.list_plugins(entries, group, host)
            plugin_info = next(p for p in plugins if p["plugin"] == plugin)
            return {**plugin_info, "fields": fields}
        resolved = discovery.resolve_field(entries, group, host, plugin, field)
        return {
            "field": resolved.field,
            "label": resolved.meta.label,
            "type": resolved.meta.type,
            "min": resolved.meta.min,
            "max": resolved.meta.max,
            "warning": resolved.meta.warning,
            "critical": resolved.meta.critical,
            "info": resolved.meta.info,
            "rrd_available": resolved.rrd_available,
            "metadata_available": resolved.metadata_available,
        }
    except RrdMcpError as exc:
        return {"error": str(exc)}


@mcp.tool()
def fetch_series(group: str, host: str, plugin: str, field: str, start: str, end: str) -> dict:
    """Fetch raw time series data for a single field.

    `start`/`end` accept a unix timestamp or any string rrdtool understands
    (e.g. "-1d", "now").
    """
    try:
        entries = _load_entries()
        resolved = discovery.resolve_field(entries, group, host, plugin, field)
        if not resolved.rrd_available:
            return {"error": f"RRD file not available for {group}/{host}/{plugin}/{field}"}
        result = rrd.fetch(resolved.path, start, end)
        return {
            "step": result.step,
            "ds_names": result.ds_names,
            "points": [{"timestamp": ts, "value": val} for ts, val in result.points],
        }
    except RrdMcpError as exc:
        return {"error": str(exc)}


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
    """Render a PNG graph overlaying the given fields of a plugin."""
    try:
        entries = _load_entries()
        paths_and_labels = []
        for field in fields:
            resolved = discovery.resolve_field(entries, group, host, plugin, field)
            if not resolved.rrd_available:
                return {"error": f"RRD file not available for {group}/{host}/{plugin}/{field}"}
            label = resolved.meta.label or field
            paths_and_labels.append((resolved.path, label))
        plugins = discovery.list_plugins(entries, group, host)
        plugin_info = next(p for p in plugins if p["plugin"] == plugin)
        title = plugin_info["graph_title"] or f"{host} {plugin}"
        vlabel = plugin_info["graph_vlabel"] or ""
        png_bytes = rrd.render_graph(paths_and_labels, start, end, title, vlabel, width, height)
        return Image(data=png_bytes, format="png")
    except RrdMcpError as exc:
        return {"error": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: テストを実行**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS(8 passed)。もし`@mcp.tool()`がデコレータで関数を`Tool`オブジェクトにラップして直接呼び出せない場合や、`Image`のPNGバイト列を保持する属性名が`.data`と異なる場合は、`ImportError`/`AttributeError`/`TypeError`が出る。その場合、インストール済み`mcp`パッケージのバージョンを`uv run python -c "import mcp; print(mcp.__version__)"`で確認し、`uv run python -c "import mcp.server.fastmcp as m; print(dir(m)); print(dir(m.Image))"`で実際のエクスポート内容・属性名を確認して、importパスや属性名のみを実際のSDKに合わせて調整すること(ツール関数のロジック自体は変更しない)。

- [ ] **Step 4: テストが通ることを確認してからコミット**

```bash
git add src/rrdmcp/server.py tests/test_server.py
git commit -m "feat: MCPツール(list_hosts/list_plugins/list_fields/get_metadata/fetch_series/render_graph)を追加"
```

---

### Task 7: README・設定例・手動確認

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: Task 1-6の全成果物
- Produces: なし(ドキュメントのみ)

- [ ] **Step 1: `README.md`を作成**

```markdown
# rrdmcp

MuninのRRDメトリックデータをLLMに公開するMCPサーバ(stdio transport)。

## セットアップ

\`\`\`bash
uv sync
\`\`\`

実行環境に`rrdtool`コマンドがPATH上にあること(Muninが動いているホストであれば、通常は依存パッケージとして既に入っています)。

## 設定(環境変数)

| 変数名 | 既定値 | 説明 |
|---|---|---|
| `MUNIN_RRD_BASE_PATH` | `/var/lib/munin` | RRDファイルのルートディレクトリ |
| `MUNIN_DATAFILE_PATH` | `${MUNIN_RRD_BASE_PATH}/datafile` | Muninのdatafile(設定キャッシュ)の場所 |

## 起動

\`\`\`bash
uv run rrdmcp
\`\`\`

## MCPクライアント設定例

\`\`\`json
{
  "mcpServers": {
    "rrdmcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/rrdmcp", "rrdmcp"],
      "env": {
        "MUNIN_RRD_BASE_PATH": "/var/lib/munin"
      }
    }
  }
}
\`\`\`

## ツール

- `list_hosts` — 発見された全`(group, host)`を列挙
- `list_plugins(group, host)` — ホストのプラグイン一覧
- `list_fields(group, host, plugin)` — プラグインのフィールド一覧(ラベル・型・閾値等)
- `get_metadata(group, host, plugin, field?)` — プラグイン全体、または単一フィールドの詳細メタデータ
- `fetch_series(group, host, plugin, field, start, end)` — 生の時系列データ取得。`start`/`end`はunixタイムスタンプまたは`rrdtool`が解釈できる文字列(`-1d`, `now`等)
- `render_graph(group, host, plugin, fields, start, end, width?, height?)` — 指定フィールドを重ね描きしたPNGグラフ

## 既知の制約

- `datafile`が存在しない場合はファイル名からのベストエフォート推定にフォールバックし、host/plugin境界の精度とメタデータが失われる
- グラフ描画はMunin本家のような閾値バンド・stack・cdef等は再現しない簡易版
- stdio transportのみ(v1)
```

- [ ] **Step 2: 全テストスイートを実行**

Run: `uv run pytest -v`
Expected: 全テストPASS(`rrdtool`が無い環境ではmunin_root依存分がSKIPPED)

- [ ] **Step 3: サーバが起動することを手動確認**

Run: `MUNIN_RRD_BASE_PATH=/tmp/does-not-matter uv run rrdmcp &` を実行し、プロセスがクラッシュせず起動待機状態になることを確認後、`kill %1`で終了する。

実際のMuninデータを使った動作確認(本物のホスト/プラグイン/フィールドに対する`list_hosts`〜`render_graph`一連の呼び出し)は、Munin稼働ホスト上でこのユーザー自身が行う必要がある(この開発環境には実データが無いため)。

- [ ] **Step 4: コミット**

```bash
git add README.md
git commit -m "docs: README(セットアップ・設定・ツール一覧)を追加"
```
