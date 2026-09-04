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
    assert result["extra"] == {}


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


def test_fetch_series_tool_with_resolution_returns_aggregated_buckets():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
    )
    assert "buckets" in result
    assert "points" not in result
    assert result["resolution"] == 100
    buckets = result["buckets"]
    assert len(buckets) > 0
    for bucket in buckets:
        assert bucket["min"] <= bucket["avg"] <= bucket["max"]
        assert bucket["count"] >= 1


def test_fetch_series_tool_rejects_non_positive_resolution():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=0,
    )
    assert "error" in result


def test_aggregate_points_computes_avg_min_max_count():
    from rrdmcp.server import _aggregate_points

    points = [
        (1000, 10.0),
        (1010, 20.0),
        (1020, 30.0),
        (1100, 5.0),
        (1110, None),
        (1120, 15.0),
    ]
    buckets = _aggregate_points(points, resolution=100)
    assert buckets == [
        {"start": 1000, "avg": 20.0, "min": 10.0, "max": 30.0, "count": 3},
        {"start": 1100, "avg": 10.0, "min": 5.0, "max": 15.0, "count": 2},
    ]


def test_aggregate_points_omits_bucket_with_only_none_values():
    from rrdmcp.server import _aggregate_points

    points = [(1000, None), (1000, None)]
    buckets = _aggregate_points(points, resolution=100)
    assert buckets == []


def test_fetch_series_tool_with_summary_returns_aggregate_stats():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        summary=True,
    )
    assert "summary" in result
    assert "points" not in result
    assert "buckets" not in result
    summary = result["summary"]
    assert summary["min"] <= summary["avg"] <= summary["max"]
    assert summary["count"] > 0


def test_fetch_series_tool_summary_and_resolution_together_is_error():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
        summary=True,
    )
    assert "error" in result


def test_fetch_series_tool_top_n_requires_resolution():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        top_n=1,
    )
    assert "error" in result


def test_fetch_series_tool_top_n_rejects_invalid_top_by():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
        top_n=1,
        top_by="median",
    )
    assert "error" in result


def test_fetch_series_tool_top_n_rejects_invalid_order():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
        top_n=1,
        order="sideways",
    )
    assert "error" in result


def test_fetch_series_tool_rejects_non_positive_top_n():
    from rrdmcp import server

    result = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
        top_n=-1,
    )
    assert "error" in result


def test_fetch_series_tool_top_n_larger_than_total_buckets_returns_all():
    from rrdmcp import server

    full = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
    )
    total = len(full["buckets"])

    top = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
        top_n=total + 1000,
    )
    assert len(top["buckets"]) == total
    assert top["total_buckets"] == total


def test_fetch_series_tool_top_n_returns_sorted_top_buckets():
    from rrdmcp import server

    full = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
    )
    total = len(full["buckets"])
    assert total > 2

    top = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
        top_n=2,
    )
    assert len(top["buckets"]) == 2
    assert top["total_buckets"] == total
    assert top["buckets"][0]["avg"] >= top["buckets"][1]["avg"]
    expected_top2 = sorted(full["buckets"], key=lambda b: b["avg"], reverse=True)[:2]
    assert [b["start"] for b in top["buckets"]] == [b["start"] for b in expected_top2]


def test_fetch_series_tool_top_n_ascending_order_returns_lowest_first():
    from rrdmcp import server

    full = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
    )
    top = server.fetch_series(
        "testgroup",
        "testhost.example.com",
        "cpu",
        "user",
        "-2h",
        "now",
        resolution=100,
        top_n=2,
        order="asc",
    )
    expected = sorted(full["buckets"], key=lambda b: b["avg"])[:2]
    assert [b["start"] for b in top["buckets"]] == [b["start"] for b in expected]


def test_summarize_points_computes_avg_min_max_count():
    from rrdmcp.server import _summarize_points

    points = [(1000, 10.0), (1010, 20.0), (1020, None), (1030, 30.0)]
    summary = _summarize_points(points)
    assert summary == {"avg": 20.0, "min": 10.0, "max": 30.0, "count": 3}


def test_summarize_points_with_no_values_returns_none_stats():
    from rrdmcp.server import _summarize_points

    summary = _summarize_points([(1000, None), (1010, None)])
    assert summary == {"avg": None, "min": None, "max": None, "count": 0}


def test_render_graph_tool_returns_image():
    from mcp.server.mcpserver import Image

    from rrdmcp import server

    result = server.render_graph(
        "testgroup", "testhost.example.com", "cpu", ["user", "system"], "-2h", "now"
    )
    assert isinstance(result, Image)
    assert result.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_graph_tool_returns_error_dict_for_unknown_plugin_with_empty_fields():
    from rrdmcp import server

    result = server.render_graph(
        "testgroup", "testhost.example.com", "no-such-plugin", [], "-2h", "now"
    )
    assert isinstance(result, dict)
    assert "error" in result
