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
