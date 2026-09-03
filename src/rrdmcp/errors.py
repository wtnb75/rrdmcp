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
