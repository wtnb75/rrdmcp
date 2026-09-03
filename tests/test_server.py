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
    from mcp.server.mcpserver import Image

    from rrdmcp import server

    result = server.render_graph(
        "testgroup", "testhost.example.com", "cpu", ["user", "system"], "-2h", "now"
    )
    assert isinstance(result, Image)
    assert result.data[:8] == b"\x89PNG\r\n\x1a\n"
