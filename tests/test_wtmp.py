import gzip
import subprocess
from pathlib import Path

import pytest

from rrdmcp import wtmp
from rrdmcp.errors import WtmpToolNotFoundError
from rrdmcp.wtmp import (
    _list_kind_files,
    _parse_line,
    _parse_utmpdump_output,
    _run_utmpdump,
    require_utmpdump,
)

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


def test_run_utmpdump_returns_none_on_truncated_gz(tmp_path: Path):
    path = tmp_path / "wtmp.1.gz"
    # Create a valid gzip file then truncate it mid-stream to trigger EOFError
    original = b"raw-wtmp-bytes" * 100
    compressed = gzip.compress(original)
    path.write_bytes(compressed[:15])  # truncate to first 15 bytes
    assert _run_utmpdump("/usr/bin/utmpdump", path) is None


def test_run_utmpdump_returns_none_on_corrupted_gz_stream(tmp_path: Path):
    path = tmp_path / "wtmp.1.gz"
    # Create a valid gzip file then corrupt a byte mid-stream to trigger zlib.error
    original = b"raw-wtmp-bytes" * 100
    compressed = bytearray(gzip.compress(original))
    # Flip a bit in the middle of the compressed data (not in the header)
    compressed[30] ^= 0xFF
    path.write_bytes(bytes(compressed))
    assert _run_utmpdump("/usr/bin/utmpdump", path) is None
