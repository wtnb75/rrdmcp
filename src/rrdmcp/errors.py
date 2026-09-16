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


class SarToolNotFoundError(RrdMcpError):
    """The `sadf` executable is not on PATH."""


class SarFileNotAvailableError(RrdMcpError):
    """A sar log file for a resolved field does not exist or sadf failed on it."""


class SarToolTimeoutError(RrdMcpError):
    """A `sadf` subprocess call exceeded the timeout."""


class SarInvalidTimeError(RrdMcpError):
    """A start/end time could not be interpreted as a unix timestamp or ISO 8601 string."""
