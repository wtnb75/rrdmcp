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
