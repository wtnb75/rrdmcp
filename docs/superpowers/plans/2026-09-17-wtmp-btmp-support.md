# wtmp/btmp(ログイン履歴)データソース対応 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third data source (wtmp/btmp login-history logs, read via `utmpdump`) exposed through two new, independent MCP tools (`list_login_sources`, `list_login_events`) that return raw per-record login events — no numeric time-series, no session pairing.

**Architecture:** Two new modules (`wtmp_index.py` for discovery, `wtmp.py` for fetching) mirror the existing `sar_index.py`/`sar.py` pair, but are kept fully separate from `discovery.NormalizedField` (the munin/sar plugin-field model) because event records don't fit that shape. `server.py` gains a `WTMP_BASE_PATH` env var and the two new `@mcp.tool()` functions; no existing tool signature changes. A small shared-code cleanup moves `sar.py`'s epoch-only time normalizer into `timeutil.py` so `wtmp.py` can reuse it.

**Tech Stack:** Python 3.11+, `utmpdump` (util-linux, external subprocess — no Python binary-parsing library), `gzip` (stdlib, for rotated compressed logs), pytest/pytest-cov, ruff.

**Spec:** `docs/superpowers/specs/2026-09-17-wtmp-btmp-support-design.md`

## Global Constraints

- `WTMP_BASE_PATH` unset → wtmp/btmp support is fully disabled, no error (same pattern as `SAR_BASE_PATH`).
- Directory layout: `WTMP_BASE_PATH/<group>/<host>/{wtmp,btmp}`, plus rotated generations `{kind}.<digits>` and `{kind}.<digits>.gz` (generation count is not fixed — glob, don't hardcode a range).
- Never parse the wtmp/btmp binary struct directly in Python — always go through the `utmpdump` CLI (util-linux). Verified real output format (util-linux 2.38, Debian 12, captured via Docker during design): one record per line, `[type] [pid] [id] [user] [line] [host] [addr] [ISO8601 timestamp]`, e.g. `[7] [01234] [ts/0] [alice   ] [pts/0       ] [10.0.0.5            ] [10.0.0.5       ] [2023-11-14T22:15:00,000000+00:00]`. Timestamp format is `%Y-%m-%dT%H:%M:%S,%f%z` (colon-separated UTC offset, parses directly with `datetime.strptime`). `utmpdump` reads stdin when given no filename argument; its `Utmp dump of <file>` banner goes to stderr, never stdout.
- `start`/`end` accept only a unix timestamp or an ISO 8601 string (no rrdtool relative expressions), same constraint as the sar data source.
- No login/logout session pairing or duration computation in v1 — raw per-record events only, sorted ascending by timestamp.
- Individual rotated-file `utmpdump` execution failures (non-zero exit, timeout, unparseable/corrupt `.gz`) are skipped silently, never raised. Only a missing `utmpdump` executable is fatal (raised immediately, once, at the start of `fetch()`).
- `utmpdump` subprocess timeout: 30 seconds (`WTMPDUMP_TIMEOUT_SECONDS`), matching the existing `SADF_TIMEOUT_SECONDS` convention.
- `ut_type == 0` (`EMPTY`, an unused utmp slot) records must be filtered out of returned events.

---

## File Structure

```
src/rrdmcp/
├── errors.py        # MODIFY: add WtmpToolNotFoundError, WtmpSourceNotFoundError, WtmpInvalidTimeError
├── timeutil.py       # MODIFY: add normalize_time_to_epoch (shared by sar.py and wtmp.py)
├── sar.py             # MODIFY: _normalize_time_to_epoch delegates to timeutil.normalize_time_to_epoch
├── wtmp_index.py        # NEW: LoginSource dataclass + build_index() (WTMP_BASE_PATH scan)
├── wtmp.py                # NEW: utmpdump wrapper, rotation-aware fetch()
└── server.py                # MODIFY: WTMP_BASE_PATH env var + list_login_sources/list_login_events tools

tests/
├── test_timeutil.py   # MODIFY: tests for normalize_time_to_epoch
├── test_wtmp_index.py  # NEW
├── test_wtmp.py          # NEW
├── test_server.py         # MODIFY: wtmp tool tests + autouse env fixture
└── conftest.py              # MODIFY: wtmp_root fixture (builds a real binary wtmp file via `utmpdump -r`)

README.md   # MODIFY: config table, tools list, known limitations
Dockerfile   # MODIFY: install util-linux (for utmpdump)
```

---

### Task 1: Shared time normalizer + wtmp error classes

**Files:**
- Modify: `src/rrdmcp/errors.py`
- Modify: `src/rrdmcp/timeutil.py`
- Modify: `src/rrdmcp/sar.py:1-22,34-44`
- Test: `tests/test_timeutil.py`
- Test: `tests/test_sar.py` (regression only — no edits, must keep passing)

**Interfaces:**
- Consumes: `rrdmcp.timeutil.try_parse_iso8601(value: str) -> int | None` (existing)
- Produces: `rrdmcp.timeutil.normalize_time_to_epoch(value: str) -> int` (raises `ValueError` if `value` is neither a unix timestamp nor ISO 8601 — callers wrap this into their own domain error). `rrdmcp.errors.WtmpToolNotFoundError`, `rrdmcp.errors.WtmpSourceNotFoundError`, `rrdmcp.errors.WtmpInvalidTimeError` (all subclass `RrdMcpError`).

- [ ] **Step 1: Write the failing tests for `normalize_time_to_epoch`**

Add to `tests/test_timeutil.py` (append; also add `import pytest` and extend the existing import line):

```python
from datetime import UTC, datetime

import pytest

from rrdmcp.timeutil import normalize_time_to_epoch, try_parse_iso8601


def test_normalize_time_to_epoch_accepts_unix_timestamp_string():
    assert normalize_time_to_epoch("1757246400") == 1757246400


def test_normalize_time_to_epoch_accepts_iso8601():
    result = normalize_time_to_epoch("2026-09-07T12:00:00Z")
    assert result == int(datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC).timestamp())


def test_normalize_time_to_epoch_raises_value_error_for_relative_expressions():
    with pytest.raises(ValueError):
        normalize_time_to_epoch("-1h")
    with pytest.raises(ValueError):
        normalize_time_to_epoch("now")
```

The full file's import block becomes:

```python
from datetime import UTC, datetime

import pytest

from rrdmcp.timeutil import normalize_time_to_epoch, try_parse_iso8601
```

(keep the existing `test_parses_*`/`test_returns_none_*` tests below unchanged).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_timeutil.py -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_time_to_epoch'`

- [ ] **Step 3: Implement `normalize_time_to_epoch` in `timeutil.py`**

Append to `src/rrdmcp/timeutil.py`:

```python


def normalize_time_to_epoch(value: str) -> int:
    """Convert a unix timestamp string or an ISO 8601 string to a Unix epoch second.

    Raises ValueError if `value` is neither (e.g. rrdtool AT-STYLE
    expressions like "-1h"/"now", which only rrd.py's rrdtool-backed path
    understands). Callers that need a domain-specific error message should
    catch ValueError and re-raise their own exception.
    """
    epoch = try_parse_iso8601(value)
    if epoch is not None:
        return epoch
    return int(value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_timeutil.py -v`
Expected: PASS

- [ ] **Step 5: Add the wtmp error classes**

Append to `src/rrdmcp/errors.py`:

```python


class WtmpToolNotFoundError(RrdMcpError):
    """The `utmpdump` executable is not on PATH."""


class WtmpSourceNotFoundError(RrdMcpError):
    """No (group, host, kind) matches the given identifiers among discovered wtmp/btmp sources."""


class WtmpInvalidTimeError(RrdMcpError):
    """A start/end time could not be interpreted as a unix timestamp or ISO 8601 string."""
```

- [ ] **Step 6: Refactor `sar.py` to delegate to the shared normalizer**

In `src/rrdmcp/sar.py`, change the import block (currently `from .timeutil import try_parse_iso8601`) to:

```python
from .timeutil import normalize_time_to_epoch
```

And replace the existing `_normalize_time_to_epoch` function body:

```python
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
```

with:

```python
def _normalize_time_to_epoch(value: str) -> int:
    try:
        return normalize_time_to_epoch(value)
    except ValueError as exc:
        raise SarInvalidTimeError(
            "sar data source requires a unix timestamp or an ISO 8601 "
            f"string (rrdtool-style relative expressions are not supported), got: {value!r}"
        ) from exc
```

- [ ] **Step 7: Run the full sar test suite to confirm no regression**

Run: `uv run pytest tests/test_sar.py tests/test_timeutil.py -v`
Expected: PASS (all existing `test_normalize_time_to_epoch_*` tests in `test_sar.py` must still pass unchanged — they exercise `sar._normalize_time_to_epoch` directly and its behavior/error type is unchanged by this refactor)

- [ ] **Step 8: Commit**

```bash
git add src/rrdmcp/errors.py src/rrdmcp/timeutil.py src/rrdmcp/sar.py tests/test_timeutil.py
git commit -m "$(cat <<'EOF'
timeutilに時刻正規化の共通関数を切り出し、wtmp用エラークラスを追加

sar.pyの_normalize_time_to_epochをtimeutil.normalize_time_to_epochに
委譲するよう変更し、後続タスクで追加するwtmp.pyからも同じロジックを
再利用できるようにする。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 2: wtmp/btmp discovery (`wtmp_index.py`)

**Files:**
- Create: `src/rrdmcp/wtmp_index.py`
- Test: `tests/test_wtmp_index.py`

**Interfaces:**
- Consumes: nothing from other new modules.
- Produces: `rrdmcp.wtmp_index.LoginSource` (dataclass: `group: str`, `host: str`, `kind: Literal["wtmp", "btmp"]`); `rrdmcp.wtmp_index.build_index(base_path: Path) -> list[LoginSource]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wtmp_index.py`:

```python
from pathlib import Path

from rrdmcp.wtmp_index import LoginSource, build_index


def test_build_index_returns_empty_list_for_missing_base_path(tmp_path: Path):
    assert build_index(tmp_path / "does-not-exist") == []


def test_build_index_finds_plain_wtmp_file(tmp_path: Path):
    host_dir = tmp_path / "web" / "app01"
    host_dir.mkdir(parents=True)
    (host_dir / "wtmp").write_bytes(b"")
    assert build_index(tmp_path) == [LoginSource(group="web", host="app01", kind="wtmp")]


def test_build_index_finds_rotated_and_compressed_files(tmp_path: Path):
    host_dir = tmp_path / "web" / "app01"
    host_dir.mkdir(parents=True)
    (host_dir / "btmp.1").write_bytes(b"")
    (host_dir / "btmp.2.gz").write_bytes(b"")
    assert build_index(tmp_path) == [LoginSource(group="web", host="app01", kind="btmp")]


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wtmp_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rrdmcp.wtmp_index'`

- [ ] **Step 3: Implement `wtmp_index.py`**

Create `src/rrdmcp/wtmp_index.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass
class LoginSource:
    group: str
    host: str
    kind: Literal["wtmp", "btmp"]


def _has_kind_files(host_dir: Path, kind: str) -> bool:
    if (host_dir / kind).is_file():
        return True
    return any(host_dir.glob(f"{kind}.[0-9]*"))


def build_index(base_path: Path) -> list[LoginSource]:
    """Discover (group, host, kind) triples with at least one wtmp/btmp file.

    Returns an empty list (never raises) if `base_path` doesn't exist, so
    wtmp/btmp support degrades to a no-op when unconfigured — matching how
    sar_index.build_index behaves when SAR_BASE_PATH is missing.
    """
    if not base_path.is_dir():
        return []
    entries: list[LoginSource] = []
    for group_dir in sorted(p for p in base_path.iterdir() if p.is_dir()):
        for host_dir in sorted(p for p in group_dir.iterdir() if p.is_dir()):
            for kind in ("wtmp", "btmp"):
                if _has_kind_files(host_dir, kind):
                    entries.append(
                        LoginSource(group=group_dir.name, host=host_dir.name, kind=kind)
                    )
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wtmp_index.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/wtmp_index.py tests/test_wtmp_index.py
git commit -m "$(cat <<'EOF'
wtmp/btmpログのディスカバリ(wtmp_index.py)を追加

WTMP_BASE_PATH/<group>/<host>/配下でwtmp/btmp(ローテート・gz圧縮分含む)
の存在を走査し、(group, host, kind)の組を発見する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 3: `utmpdump` output parsing (`wtmp.py`, part A)

**Files:**
- Create: `src/rrdmcp/wtmp.py`
- Test: `tests/test_wtmp.py`

**Interfaces:**
- Consumes: nothing from other new modules yet.
- Produces: `rrdmcp.wtmp.UTMP_TYPE_NAMES: dict[int, str]`; `rrdmcp.wtmp._parse_line(line: str) -> dict | None` (keys: `type_num: int`, `timestamp: int`, `type: str`, `user: str`, `line: str`, `host: str`, `pid: int`); `rrdmcp.wtmp._parse_utmpdump_output(text: str) -> list[dict]` (same keys minus `type_num`, EMPTY-type records dropped).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wtmp.py`:

```python
from rrdmcp.wtmp import _parse_line, _parse_utmpdump_output

# Real `utmpdump` output (util-linux 2.38, Debian 12 bookworm), captured by
# writing a synthetic `struct utmp` via a small C program compiled inside a
# `debian:bookworm-slim` container and running the real `utmpdump` binary on
# it — not hand-guessed. Field order: type, pid, id, user, line, host, addr,
# timestamp. Record 1 = a reboot (BOOT_TIME), record 2 = a remote login
# (USER_PROCESS) for "alice" from 10.0.0.5, record 3 = that session's logout
# (DEAD_PROCESS, user/host blanked out by the kernel on logout).
SAMPLE_UTMPDUMP_OUTPUT = (
    "[2] [00000] [~~  ] [reboot  ] [~           ] "
    "[5.10.0-linux        ] [0.0.0.0        ] [2023-11-14T22:13:20,000000+00:00]\n"
    "[7] [01234] [ts/0] [alice   ] [pts/0       ] "
    "[10.0.0.5            ] [10.0.0.5       ] [2023-11-14T22:15:00,000000+00:00]\n"
    "[8] [01234] [ts/0] [        ] [pts/0       ] "
    "[                    ] [0.0.0.0        ] [2023-11-14T23:13:20,000000+00:00]\n"
)


def test_parse_utmpdump_output_extracts_all_non_empty_records():
    records = _parse_utmpdump_output(SAMPLE_UTMPDUMP_OUTPUT)
    assert len(records) == 3


def test_parse_utmpdump_output_decodes_boot_time_record():
    records = _parse_utmpdump_output(SAMPLE_UTMPDUMP_OUTPUT)
    boot = records[0]
    assert boot["type"] == "BOOT_TIME"
    assert boot["user"] == "reboot"
    assert boot["line"] == "~"
    assert boot["host"] == "5.10.0-linux"
    assert boot["pid"] == 0
    assert boot["timestamp"] == 1700000000


def test_parse_utmpdump_output_decodes_user_process_record():
    records = _parse_utmpdump_output(SAMPLE_UTMPDUMP_OUTPUT)
    login = records[1]
    assert login["type"] == "USER_PROCESS"
    assert login["user"] == "alice"
    assert login["line"] == "pts/0"
    assert login["host"] == "10.0.0.5"
    assert login["pid"] == 1234
    assert login["timestamp"] == 1700000100


def test_parse_utmpdump_output_decodes_dead_process_record_with_empty_user():
    records = _parse_utmpdump_output(SAMPLE_UTMPDUMP_OUTPUT)
    logout = records[2]
    assert logout["type"] == "DEAD_PROCESS"
    assert logout["user"] == ""
    assert logout["timestamp"] == 1700003600


def test_parse_utmpdump_output_excludes_empty_type_records():
    text = (
        "[0] [00000] [    ] [        ] [            ] "
        "[                    ] [0.0.0.0        ] [2023-11-14T22:13:20,000000+00:00]\n"
    )
    assert _parse_utmpdump_output(text) == []


def test_parse_line_returns_none_for_malformed_line():
    assert _parse_line("not a valid utmpdump line") is None


def test_parse_line_returns_none_for_unparseable_timestamp():
    line = (
        "[7] [00001] [ts/0] [bob     ] [pts/1       ] "
        "[host                ] [0.0.0.0        ] [not-a-timestamp]"
    )
    assert _parse_line(line) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wtmp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rrdmcp.wtmp'`

- [ ] **Step 3: Implement the parsing logic**

Create `src/rrdmcp/wtmp.py`:

```python
import re
from datetime import datetime

UTMP_TYPE_NAMES: dict[int, str] = {
    0: "EMPTY",
    1: "RUN_LVL",
    2: "BOOT_TIME",
    3: "NEW_TIME",
    4: "OLD_TIME",
    5: "INIT_PROCESS",
    6: "LOGIN_PROCESS",
    7: "USER_PROCESS",
    8: "DEAD_PROCESS",
    9: "ACCOUNTING",
}

_BRACKET_RE = re.compile(r"\[([^\]]*)\]")


def _parse_line(line: str) -> dict | None:
    """Parse one `utmpdump` output line into a raw record dict, or None if unparseable.

    `utmpdump` (util-linux) prints one utmp/wtmp/btmp record per line in a
    fixed bracketed form:
    [type] [pid] [id] [user] [line] [host] [addr] [ISO 8601 timestamp]
    """
    groups = _BRACKET_RE.findall(line)
    if len(groups) != 8:
        return None
    type_raw, pid_raw, _id, user, line_field, host, _addr, ts_raw = (
        g.strip() for g in groups
    )
    try:
        type_num = int(type_raw)
        pid = int(pid_raw)
        dt = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%S,%f%z")
    except ValueError:
        return None
    return {
        "type_num": type_num,
        "timestamp": int(dt.timestamp()),
        "type": UTMP_TYPE_NAMES.get(type_num, str(type_num)),
        "user": user,
        "line": line_field,
        "host": host,
        "pid": pid,
    }


def _parse_utmpdump_output(text: str) -> list[dict]:
    """Parse full `utmpdump` stdout into event records, dropping EMPTY slots."""
    records = []
    for raw_line in text.splitlines():
        record = _parse_line(raw_line)
        if record is None or record["type_num"] == 0:
            continue
        del record["type_num"]
        records.append(record)
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wtmp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/wtmp.py tests/test_wtmp.py
git commit -m "$(cat <<'EOF'
utmpdump出力のパースロジック(wtmp.py)を追加

実機で採取したutmpdump実出力形式(type/pid/id/user/line/host/addr/timestamp
のbracket区切り)を固定fixtureとして検証する。EMPTY(未使用スロット)は
除外する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 4: Rotated-file enumeration (`wtmp.py`, part B)

**Files:**
- Modify: `src/rrdmcp/wtmp.py`
- Test: `tests/test_wtmp.py`

**Interfaces:**
- Consumes: `pathlib.Path` only (stdlib).
- Produces: `rrdmcp.wtmp._list_kind_files(host_dir: Path, kind: str) -> list[Path]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wtmp.py` (add `from pathlib import Path` to the top imports, and `from rrdmcp.wtmp import _list_kind_files, _parse_line, _parse_utmpdump_output`):

```python
def test_list_kind_files_finds_plain_file_only(tmp_path: Path):
    (tmp_path / "wtmp").write_bytes(b"")
    assert _list_kind_files(tmp_path, "wtmp") == [tmp_path / "wtmp"]


def test_list_kind_files_finds_rotated_and_gz_generations(tmp_path: Path):
    (tmp_path / "wtmp").write_bytes(b"")
    (tmp_path / "wtmp.1").write_bytes(b"")
    (tmp_path / "wtmp.2.gz").write_bytes(b"")
    result = _list_kind_files(tmp_path, "wtmp")
    assert set(result) == {
        tmp_path / "wtmp",
        tmp_path / "wtmp.1",
        tmp_path / "wtmp.2.gz",
    }


def test_list_kind_files_ignores_other_kind(tmp_path: Path):
    (tmp_path / "btmp").write_bytes(b"")
    assert _list_kind_files(tmp_path, "wtmp") == []


def test_list_kind_files_returns_empty_for_missing_directory(tmp_path: Path):
    assert _list_kind_files(tmp_path / "does-not-exist", "wtmp") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wtmp.py -v -k list_kind_files`
Expected: FAIL with `ImportError: cannot import name '_list_kind_files'`

- [ ] **Step 3: Implement `_list_kind_files`**

Append to `src/rrdmcp/wtmp.py` (add `from pathlib import Path` to the top imports):

```python


def _list_kind_files(host_dir: Path, kind: str) -> list[Path]:
    """Enumerate the base file and any rotated/compressed generations for `kind`.

    Matches `{kind}`, `{kind}.1`, `{kind}.2.gz`, etc. Rotation generation
    counts aren't fixed (depends on the deployment's logrotate config), so
    this globs rather than assuming a bound.
    """
    files = []
    plain = host_dir / kind
    if plain.is_file():
        files.append(plain)
    files.extend(sorted(host_dir.glob(f"{kind}.[0-9]*")))
    return files
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wtmp.py -v`
Expected: PASS (all tests in the file, including Task 3's)

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/wtmp.py tests/test_wtmp.py
git commit -m "$(cat <<'EOF'
wtmp.pyにローテートファイル列挙(_list_kind_files)を追加

{kind}, {kind}.N, {kind}.N.gz を世代数を決め打ちせずglobで発見する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 5: `utmpdump` execution wrapper (`wtmp.py`, part C)

**Files:**
- Modify: `src/rrdmcp/wtmp.py`
- Test: `tests/test_wtmp.py`

**Interfaces:**
- Consumes: `rrdmcp.errors.WtmpToolNotFoundError` (Task 1); `rrdmcp.wtmp._parse_utmpdump_output(text: str) -> list[dict]` (Task 3).
- Produces: `rrdmcp.wtmp.WTMPDUMP_TIMEOUT_SECONDS = 30`; `rrdmcp.wtmp.require_utmpdump() -> str` (raises `WtmpToolNotFoundError`); `rrdmcp.wtmp._run_utmpdump(exe: str, path: Path) -> list[dict] | None` (returns `None` on any execution failure, never raises).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wtmp.py` (add these imports at the top: `import gzip`, `import subprocess`, `import pytest`, `from rrdmcp import wtmp`, `from rrdmcp.errors import WtmpToolNotFoundError`, and extend the `from rrdmcp.wtmp import ...` line with `_run_utmpdump, require_utmpdump`):

```python
def test_require_utmpdump_returns_path_when_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wtmp.shutil, "which", lambda name: "/usr/bin/utmpdump")
    assert require_utmpdump() == "/usr/bin/utmpdump"


def test_require_utmpdump_raises_when_not_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wtmp.shutil, "which", lambda name: None)
    with pytest.raises(WtmpToolNotFoundError):
        require_utmpdump()


def test_run_utmpdump_passes_plain_file_path_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "wtmp"
    path.write_bytes(b"")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=SAMPLE_UTMPDUMP_OUTPUT, stderr="")

    monkeypatch.setattr(wtmp.subprocess, "run", fake_run)
    records = _run_utmpdump("/usr/bin/utmpdump", path)
    assert calls == [["/usr/bin/utmpdump", str(path)]]
    assert len(records) == 3


def test_run_utmpdump_decompresses_gz_and_pipes_via_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "wtmp.1.gz"
    original = b"raw-wtmp-bytes"
    path.write_bytes(gzip.compress(original))
    captured = {}

    def fake_run(args, input=None, **kwargs):
        captured["args"] = args
        captured["input"] = input
        return subprocess.CompletedProcess(
            args, 0, stdout=SAMPLE_UTMPDUMP_OUTPUT.encode(), stderr=b""
        )

    monkeypatch.setattr(wtmp.subprocess, "run", fake_run)
    records = _run_utmpdump("/usr/bin/utmpdump", path)
    assert captured["args"] == ["/usr/bin/utmpdump"]
    assert captured["input"] == original
    assert len(records) == 3


def test_run_utmpdump_returns_none_on_called_process_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "wtmp"
    path.write_bytes(b"")

    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr="corrupt")

    monkeypatch.setattr(wtmp.subprocess, "run", fake_run)
    assert _run_utmpdump("/usr/bin/utmpdump", path) is None


def test_run_utmpdump_returns_none_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "wtmp"
    path.write_bytes(b"")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=wtmp.WTMPDUMP_TIMEOUT_SECONDS)

    monkeypatch.setattr(wtmp.subprocess, "run", fake_run)
    assert _run_utmpdump("/usr/bin/utmpdump", path) is None


def test_run_utmpdump_returns_none_on_corrupt_gz(tmp_path: Path):
    path = tmp_path / "wtmp.1.gz"
    path.write_bytes(b"not-actually-gzip-data")
    assert _run_utmpdump("/usr/bin/utmpdump", path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wtmp.py -v -k "utmpdump and not output"`
Expected: FAIL with `ImportError: cannot import name '_run_utmpdump'`

- [ ] **Step 3: Implement `require_utmpdump` and `_run_utmpdump`**

Append to `src/rrdmcp/wtmp.py` (add `import gzip`, `import shutil`, `import subprocess` and `from .errors import WtmpToolNotFoundError` to the top imports):

```python


WTMPDUMP_TIMEOUT_SECONDS = 30


def require_utmpdump() -> str:
    exe = shutil.which("utmpdump")
    if exe is None:
        raise WtmpToolNotFoundError("utmpdump command not found in PATH")
    return exe


def _run_utmpdump(exe: str, path: Path) -> list[dict] | None:
    """Run utmpdump on one wtmp/btmp file and parse its output.

    Returns None (never raises) on any execution failure — a non-zero
    exit, a timeout, or an OS-level error (including a corrupt .gz) — so
    one bad rotated file doesn't abort a fetch spanning several files.
    """
    try:
        if path.suffix == ".gz":
            data = gzip.decompress(path.read_bytes())
            proc = subprocess.run(
                [exe],
                input=data,
                capture_output=True,
                timeout=WTMPDUMP_TIMEOUT_SECONDS,
                check=True,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
        else:
            proc = subprocess.run(
                [exe, str(path)],
                capture_output=True,
                text=True,
                timeout=WTMPDUMP_TIMEOUT_SECONDS,
                check=True,
            )
            stdout = proc.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return _parse_utmpdump_output(stdout)
```

`Path` is already imported (added in Task 4's top-imports change), so no new import is needed here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wtmp.py -v`
Expected: PASS (all tests in the file so far)

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/wtmp.py tests/test_wtmp.py
git commit -m "$(cat <<'EOF'
wtmp.pyにutmpdump実行ラッパー(require_utmpdump/_run_utmpdump)を追加

.gzは解凍して標準入力経由でutmpdumpに渡す。個別ファイルの実行失敗
(非0終了・タイムアウト・破損gz)は例外にせずNoneを返して呼び出し側で
スキップできるようにする。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 6: `fetch()` — merge, filter, sort, limit (`wtmp.py`, part D)

**Files:**
- Modify: `src/rrdmcp/wtmp.py`
- Test: `tests/test_wtmp.py`

**Interfaces:**
- Consumes: `rrdmcp.timeutil.normalize_time_to_epoch` (Task 1); `rrdmcp.errors.WtmpInvalidTimeError` (Task 1); `rrdmcp.wtmp._list_kind_files` (Task 4); `rrdmcp.wtmp.require_utmpdump`, `rrdmcp.wtmp._run_utmpdump` (Task 5).
- Produces: `rrdmcp.wtmp.FetchResult` (dataclass: `total_events: int`, `events: list[dict]`); `rrdmcp.wtmp.fetch(host_dir: Path, kind: str, start: str, end: str, limit: int | None = None) -> FetchResult`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wtmp.py` (extend imports: `from rrdmcp.errors import WtmpInvalidTimeError, WtmpToolNotFoundError`; extend the `from rrdmcp.wtmp import ...` line with `fetch`):

```python
def test_fetch_returns_empty_when_no_files_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(wtmp, "require_utmpdump", lambda: "/usr/bin/utmpdump")
    result = fetch(tmp_path, "wtmp", "0", "9999999999")
    assert result == wtmp.FetchResult(total_events=0, events=[])


def test_fetch_raises_when_utmpdump_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fail():
        raise WtmpToolNotFoundError("utmpdump command not found in PATH")

    monkeypatch.setattr(wtmp, "require_utmpdump", fail)
    with pytest.raises(WtmpToolNotFoundError):
        fetch(tmp_path, "wtmp", "0", "9999999999")


def test_fetch_merges_and_sorts_events_across_rotated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "wtmp").write_bytes(b"")
    (tmp_path / "wtmp.1").write_bytes(b"")
    monkeypatch.setattr(wtmp, "require_utmpdump", lambda: "/usr/bin/utmpdump")

    def fake_run_utmpdump(exe, path):
        if path.name == "wtmp":
            return [
                {"timestamp": 300, "type": "USER_PROCESS", "user": "b", "line": "", "host": "", "pid": 2}
            ]
        return [
            {"timestamp": 100, "type": "USER_PROCESS", "user": "a", "line": "", "host": "", "pid": 1}
        ]

    monkeypatch.setattr(wtmp, "_run_utmpdump", fake_run_utmpdump)
    result = fetch(tmp_path, "wtmp", "0", "9999999999")
    assert [e["timestamp"] for e in result.events] == [100, 300]
    assert result.total_events == 2


def test_fetch_filters_events_outside_start_end_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "wtmp").write_bytes(b"")
    monkeypatch.setattr(wtmp, "require_utmpdump", lambda: "/usr/bin/utmpdump")
    monkeypatch.setattr(
        wtmp,
        "_run_utmpdump",
        lambda exe, path: [
            {"timestamp": 50, "type": "USER_PROCESS", "user": "a", "line": "", "host": "", "pid": 1},
            {"timestamp": 150, "type": "USER_PROCESS", "user": "b", "line": "", "host": "", "pid": 2},
        ],
    )
    result = fetch(tmp_path, "wtmp", "100", "200")
    assert [e["timestamp"] for e in result.events] == [150]
    assert result.total_events == 1


def test_fetch_skips_file_when_run_utmpdump_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "wtmp").write_bytes(b"")
    (tmp_path / "wtmp.1").write_bytes(b"")
    monkeypatch.setattr(wtmp, "require_utmpdump", lambda: "/usr/bin/utmpdump")

    def fake_run_utmpdump(exe, path):
        if path.name == "wtmp.1":
            return None
        return [
            {"timestamp": 100, "type": "USER_PROCESS", "user": "a", "line": "", "host": "", "pid": 1}
        ]

    monkeypatch.setattr(wtmp, "_run_utmpdump", fake_run_utmpdump)
    result = fetch(tmp_path, "wtmp", "0", "9999999999")
    assert result.total_events == 1


def test_fetch_applies_limit_keeping_most_recent_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "wtmp").write_bytes(b"")
    monkeypatch.setattr(wtmp, "require_utmpdump", lambda: "/usr/bin/utmpdump")
    monkeypatch.setattr(
        wtmp,
        "_run_utmpdump",
        lambda exe, path: [
            {"timestamp": ts, "type": "USER_PROCESS", "user": "u", "line": "", "host": "", "pid": 1}
            for ts in (100, 200, 300)
        ],
    )
    result = fetch(tmp_path, "wtmp", "0", "9999999999", limit=2)
    assert [e["timestamp"] for e in result.events] == [200, 300]
    assert result.total_events == 3


def test_fetch_raises_invalid_time_for_relative_expression(tmp_path: Path):
    with pytest.raises(WtmpInvalidTimeError):
        fetch(tmp_path, "wtmp", "-1h", "now")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wtmp.py -v -k test_fetch`
Expected: FAIL with `ImportError: cannot import name 'fetch'`

- [ ] **Step 3: Implement `FetchResult` and `fetch`**

Append to `src/rrdmcp/wtmp.py` (add `from dataclasses import dataclass` and `from .errors import WtmpInvalidTimeError, WtmpToolNotFoundError` to the top imports — combine with the existing `from .errors import WtmpToolNotFoundError` line from Task 5 into one `from .errors import WtmpInvalidTimeError, WtmpToolNotFoundError`; add `from .timeutil import normalize_time_to_epoch`):

```python


@dataclass
class FetchResult:
    total_events: int
    events: list[dict]


def _normalize_time_to_epoch(value: str) -> int:
    try:
        return normalize_time_to_epoch(value)
    except ValueError as exc:
        raise WtmpInvalidTimeError(
            "wtmp/btmp data source requires a unix timestamp or an ISO 8601 "
            f"string, got: {value!r}"
        ) from exc


def fetch(
    host_dir: Path, kind: str, start: str, end: str, limit: int | None = None
) -> FetchResult:
    """Fetch raw wtmp/btmp event records for one host, across all rotated files.

    `host_dir` is `WTMP_BASE_PATH/<group>/<host>/`. `kind` is "wtmp" or
    "btmp". Missing/corrupt rotated files are skipped as gaps, never an
    error; only a missing `utmpdump` executable is fatal.

    Events are sorted ascending by timestamp. If `limit` is given, only the
    most recent `limit` events (by timestamp) are returned, but
    `FetchResult.total_events` always reports the count before truncation.
    """
    start_epoch = _normalize_time_to_epoch(start)
    end_epoch = _normalize_time_to_epoch(end)
    exe = require_utmpdump()

    all_events: list[dict] = []
    for path in _list_kind_files(host_dir, kind):
        records = _run_utmpdump(exe, path)
        if records is None:
            continue
        all_events.extend(records)

    all_events = [e for e in all_events if start_epoch <= e["timestamp"] <= end_epoch]
    all_events.sort(key=lambda e: e["timestamp"])
    total_events = len(all_events)
    if limit is not None:
        all_events = all_events[-limit:]
    return FetchResult(total_events=total_events, events=all_events)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wtmp.py tests/test_wtmp_index.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/rrdmcp/wtmp.py tests/test_wtmp.py
git commit -m "$(cat <<'EOF'
wtmp.pyにfetch()を追加し、複数ローテートファイルを横断した
イベント取得を実装

start/endで絞り込み、timestamp昇順にソートし、limit指定時は
直近側からtotal_eventsを保った上で切り詰める。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 7: Real `utmpdump` integration fixture

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_wtmp.py`

**Interfaces:**
- Consumes: `rrdmcp.wtmp.fetch` (Task 6).
- Produces: pytest fixture `wtmp_root(tmp_path) -> Path`, skipped when `utmpdump` isn't on `PATH`. Local constants `WTMP_GROUP = "wtmpgroup"`, `WTMP_HOST = "wtmphost.example.com"` (mirrors the existing `SAR_GROUP`/`SAR_HOST` local-constant convention documented at the top of `tests/test_sar.py`).

- [ ] **Step 1: Add the `wtmp_root` fixture to `conftest.py`**

Append to `tests/conftest.py`:

```python


UTMPDUMP_AVAILABLE = shutil.which("utmpdump") is not None

WTMP_GROUP = "wtmpgroup"
WTMP_HOST = "wtmphost.example.com"

# Same three records as tests/test_wtmp.py's SAMPLE_UTMPDUMP_OUTPUT, kept as
# the exact bracketed text `utmpdump` prints, so `utmpdump -r` (reverse:
# text -> binary) can round-trip it into a real wtmp file without needing
# to hand-craft the `struct utmp` binary layout in Python.
_SAMPLE_UTMPDUMP_TEXT = (
    "[2] [00000] [~~  ] [reboot  ] [~           ] "
    "[5.10.0-linux        ] [0.0.0.0        ] [2023-11-14T22:13:20,000000+00:00]\n"
    "[7] [01234] [ts/0] [alice   ] [pts/0       ] "
    "[10.0.0.5            ] [10.0.0.5       ] [2023-11-14T22:15:00,000000+00:00]\n"
    "[8] [01234] [ts/0] [        ] [pts/0       ] "
    "[                    ] [0.0.0.0        ] [2023-11-14T23:13:20,000000+00:00]\n"
)


@pytest.fixture
def wtmp_root(tmp_path: Path) -> Path:
    if not UTMPDUMP_AVAILABLE:
        pytest.skip("utmpdump command not available")

    host_dir = tmp_path / WTMP_GROUP / WTMP_HOST
    host_dir.mkdir(parents=True)
    proc = subprocess.run(
        ["utmpdump", "-r"],
        input=_SAMPLE_UTMPDUMP_TEXT.encode(),
        capture_output=True,
        check=True,
    )
    (host_dir / "wtmp").write_bytes(proc.stdout)
    return tmp_path
```

`shutil`, `subprocess`, and `pytest` are already imported at the top of `conftest.py`; no new imports needed there.

- [ ] **Step 2: Write the integration test**

Append to `tests/test_wtmp.py`:

```python
WTMP_GROUP = "wtmpgroup"
WTMP_HOST = "wtmphost.example.com"


def test_fetch_returns_events_from_real_utmpdump_round_trip(wtmp_root: Path):
    host_dir = wtmp_root / WTMP_GROUP / WTMP_HOST
    result = fetch(host_dir, "wtmp", "1700000000", "1700003600")
    assert result.total_events == 3
    assert [e["type"] for e in result.events] == [
        "BOOT_TIME",
        "USER_PROCESS",
        "DEAD_PROCESS",
    ]
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_wtmp.py -v -k real_utmpdump`
Expected: SKIPPED on a machine without `utmpdump` on `PATH` (this dev sandbox); PASS on a Linux machine with `util-linux` installed. Either outcome is correct — verify by checking `shutil.which("utmpdump")` locally first, and if it's `None`, confirm the test result line says `SKIPPED` rather than erroring.

- [ ] **Step 4: Run the full test suite to confirm nothing else broke**

Run: `uv run pytest -v`
Expected: PASS (or SKIPPED for anything tool-gated), no FAIL/ERROR

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_wtmp.py
git commit -m "$(cat <<'EOF'
実機utmpdumpを使ったwtmp_rootフィクスチャと結合テストを追加

utmpdump -r(テキスト→バイナリ)でsample.wtmpを実際に生成し、fetch()を
実utmpdump経由で検証する。sar_rootと同様、utmpdumpが無い環境ではskip
する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 8: MCP tools (`server.py`)

**Files:**
- Modify: `src/rrdmcp/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `rrdmcp.wtmp_index.build_index`, `rrdmcp.wtmp_index.LoginSource` (Task 2); `rrdmcp.wtmp.fetch`, `rrdmcp.wtmp.FetchResult` (Task 6); `rrdmcp.errors.WtmpSourceNotFoundError` (Task 1); `wtmp_root`, `WTMP_GROUP`, `WTMP_HOST` fixture/constants (Task 7).
- Produces: `@mcp.tool() list_login_sources() -> list[dict]`; `@mcp.tool() list_login_events(group: str, host: str, kind: str, start: str, end: str, limit: int | None = None) -> dict` (returns `{"total_events": int, "events": list[dict]}` or `{"error": str}`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py`, update the top-of-file constants and the autouse fixture:

```python
SAR_GROUP = "sargroup"
SAR_HOST = "sarhost.example.com"
WTMP_GROUP = "wtmpgroup"
WTMP_HOST = "wtmphost.example.com"


@pytest.fixture(autouse=True)
def _configure_env(monkeypatch: pytest.MonkeyPatch, munin_root: Path):
    monkeypatch.setenv("MUNIN_RRD_BASE_PATH", str(munin_root))
    monkeypatch.setenv("MUNIN_DATAFILE_PATH", str(munin_root / "datafile"))
    # Hermetic by default: tests that need sar/wtmp set SAR_BASE_PATH/
    # WTMP_BASE_PATH themselves via monkeypatch.setenv, but the whole
    # file's isolation from an ambient value in the dev/CI shell depends on
    # this, not just the one test that asserts each source is skipped when
    # unset.
    monkeypatch.delenv("SAR_BASE_PATH", raising=False)
    monkeypatch.delenv("WTMP_BASE_PATH", raising=False)
```

Then append these test functions to the end of the file:

```python
def test_list_login_sources_returns_empty_list_when_env_var_unset():
    from rrdmcp import server

    assert server.list_login_sources() == []


def test_list_login_sources_lists_discovered_kinds(
    monkeypatch: pytest.MonkeyPatch, wtmp_root: Path
):
    from rrdmcp import server

    monkeypatch.setenv("WTMP_BASE_PATH", str(wtmp_root))
    result = server.list_login_sources()
    assert {"group": WTMP_GROUP, "host": WTMP_HOST, "kind": "wtmp"} in result


def test_list_login_events_returns_error_when_env_var_unset():
    from rrdmcp import server

    result = server.list_login_events("g", "h", "wtmp", "0", "9999999999")
    assert "error" in result


def test_list_login_events_returns_error_for_unknown_source(
    monkeypatch: pytest.MonkeyPatch, wtmp_root: Path
):
    from rrdmcp import server

    monkeypatch.setenv("WTMP_BASE_PATH", str(wtmp_root))
    result = server.list_login_events(
        WTMP_GROUP, "no-such-host", "wtmp", "0", "9999999999"
    )
    assert "error" in result


def test_list_login_events_returns_real_events(
    monkeypatch: pytest.MonkeyPatch, wtmp_root: Path
):
    from rrdmcp import server

    monkeypatch.setenv("WTMP_BASE_PATH", str(wtmp_root))
    result = server.list_login_events(
        WTMP_GROUP, WTMP_HOST, "wtmp", "1700000000", "1700003600"
    )
    assert result["total_events"] == 3
    assert [e["type"] for e in result["events"]] == [
        "BOOT_TIME",
        "USER_PROCESS",
        "DEAD_PROCESS",
    ]


def test_list_login_events_rejects_non_positive_limit(
    monkeypatch: pytest.MonkeyPatch, wtmp_root: Path
):
    from rrdmcp import server

    monkeypatch.setenv("WTMP_BASE_PATH", str(wtmp_root))
    result = server.list_login_events(
        WTMP_GROUP, WTMP_HOST, "wtmp", "1700000000", "1700003600", limit=0
    )
    assert "error" in result


def test_list_login_events_applies_limit(
    monkeypatch: pytest.MonkeyPatch, wtmp_root: Path
):
    from rrdmcp import server

    monkeypatch.setenv("WTMP_BASE_PATH", str(wtmp_root))
    result = server.list_login_events(
        WTMP_GROUP, WTMP_HOST, "wtmp", "1700000000", "1700003600", limit=1
    )
    assert result["total_events"] == 3
    assert len(result["events"]) == 1
    assert result["events"][0]["type"] == "DEAD_PROCESS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -v -k login`
Expected: FAIL with `AttributeError: module 'rrdmcp.server' has no attribute 'list_login_sources'`

- [ ] **Step 3: Implement the tools in `server.py`**

Change the import block at the top of `src/rrdmcp/server.py` from:

```python
from . import discovery, rrd, sar, sar_index
from .errors import RrdMcpError
from .munin_datafile import load_datafile
```

to:

```python
from . import discovery, rrd, sar, sar_index, wtmp, wtmp_index
from .errors import RrdMcpError, WtmpSourceNotFoundError
from .munin_datafile import load_datafile
```

Add `_wtmp_base_path` right after the existing `_sar_base_path` function:

```python
def _wtmp_base_path() -> Path | None:
    raw = os.environ.get("WTMP_BASE_PATH")
    return Path(raw) if raw else None
```

Add the two new tools after `render_graph` (before `_build_arg_parser`):

```python
@mcp.tool()
def list_login_sources() -> list[dict]:
    """List all (group, host, kind) triples discovered from wtmp/btmp logs.

    `kind` is "wtmp" (login/logout/reboot history) or "btmp" (failed login
    attempts). Returns an empty list if `WTMP_BASE_PATH` is not set.
    """
    base = _wtmp_base_path()
    if base is None:
        return []
    return [
        {"group": s.group, "host": s.host, "kind": s.kind}
        for s in wtmp_index.build_index(base)
    ]


@mcp.tool()
def list_login_events(
    group: str, host: str, kind: str, start: str, end: str, limit: int | None = None
) -> dict:
    """Fetch raw wtmp/btmp login-event records for one host.

    `kind` is "wtmp" or "btmp", as returned by `list_login_sources`.
    `start`/`end` accept a unix timestamp or an ISO 8601 timestamp only (no
    rrdtool-style relative expressions). Events are raw per-record data (no
    login/logout session pairing or duration is computed), sorted ascending
    by timestamp. If `limit` is given, only the most recent `limit` events
    are returned; `total_events` always reports the count before
    truncation.
    """
    try:
        if limit is not None and limit <= 0:
            return {"error": "limit must be a positive integer"}
        base = _wtmp_base_path()
        if base is None:
            return {"error": "WTMP_BASE_PATH is not set; wtmp/btmp support is disabled"}
        sources = wtmp_index.build_index(base)
        if not any(
            s.group == group and s.host == host and s.kind == kind for s in sources
        ):
            raise WtmpSourceNotFoundError(
                f"login source not found: group={group!r} host={host!r} kind={kind!r}"
            )
        result = wtmp.fetch(base / group / host, kind, start, end, limit)
        return {"total_events": result.total_events, "events": result.events}
    except RrdMcpError as exc:
        return {"error": str(exc)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS (all tests, including pre-existing munin/sar ones — the env-var fixture change must not break them)

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS (or SKIPPED for tool-gated tests)

- [ ] **Step 6: Commit**

```bash
git add src/rrdmcp/server.py tests/test_server.py
git commit -m "$(cat <<'EOF'
list_login_sources/list_login_eventsツールを追加

WTMP_BASE_PATH環境変数でwtmp/btmpデータソースを有効化する。既存の
list_hosts等(plugin/field前提の数値時系列ツール群)には一切手を
入れない、独立したツールとして追加する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```

---

### Task 9: Documentation and Docker image

**Files:**
- Modify: `README.md`
- Modify: `Dockerfile`

**Interfaces:**
- Consumes: nothing (docs/packaging only — no new code interfaces).
- Produces: nothing new; this task documents what Tasks 1–8 already built.

- [ ] **Step 1: Update the configuration table in `README.md`**

In the `## Configuration (environment variables)` table, add a row after the `SAR_BASE_PATH` row:

```markdown
| `WTMP_BASE_PATH` | (unset — wtmp/btmp support disabled) | Root directory of wtmp/btmp logs, laid out as `<group>/<host>/{wtmp,btmp}` (rotated generations like `wtmp.1`/`wtmp.2.gz` are also read; requires the `utmpdump` command on `PATH`, from the `util-linux` package) |
```

- [ ] **Step 2: Add the two new tools to the `## Tools` section**

After the existing `render_graph(...)` bullet, add:

```markdown
- `list_login_sources()` — list every discovered `(group, host, kind)` triple from wtmp/btmp logs (`kind` is `"wtmp"` or `"btmp"`)
- `list_login_events(group, host, kind, start, end, limit?)` — fetch raw login-event records for one host (no session pairing or duration computed), sorted ascending by timestamp. `start`/`end` accept a unix timestamp or an ISO 8601 timestamp only. If `limit` is given, only the most recent `limit` events are returned; `total_events` reports the count before truncation
```

- [ ] **Step 3: Add a "Known limitations" bullet**

At the end of the `## Known limitations` list, add:

```markdown
- wtmp/btmp support requires log files pre-aggregated under `WTMP_BASE_PATH/<group>/<host>/{wtmp,btmp}` (e.g. via `rsync`) and the `utmpdump` command on `PATH`; it returns raw per-record events only — no login/logout session pairing, duration computation, or `wtmpdb` (SQLite-based) support
```

- [ ] **Step 4: Install `util-linux` in the Docker image**

In `Dockerfile`, change:

```dockerfile
# rrdtool and sysstat (for sadf) are runtime dependencies invoked as
# subprocesses (see src/rrdmcp/rrd.py, src/rrdmcp/sar.py), not Python
# packages — they must be installed via apt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rrdtool sysstat \
    && rm -rf /var/lib/apt/lists/*
```

to:

```dockerfile
# rrdtool, sysstat (for sadf), and util-linux (for utmpdump) are runtime
# dependencies invoked as subprocesses (see src/rrdmcp/rrd.py,
# src/rrdmcp/sar.py, src/rrdmcp/wtmp.py), not Python packages — they must
# be installed via apt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rrdtool sysstat util-linux \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 5: Verify the edits landed correctly**

Run: `rg -n "WTMP_BASE_PATH|list_login_sources|list_login_events|util-linux" README.md Dockerfile`
Expected: matches in `README.md` for the config row, both tool bullets, and the limitations bullet; a match in `Dockerfile` for the `util-linux` package name

- [ ] **Step 6: Commit**

```bash
git add README.md Dockerfile
git commit -m "$(cat <<'EOF'
README/DockerfileにWTMP_BASE_PATH対応を記載

list_login_sources/list_login_eventsツールの説明・制約と、Docker
イメージへのutil-linux(utmpdump)インストールを追加する。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Fe6NynBcfQ1WNo8yvbvaCn
EOF
)"
```
