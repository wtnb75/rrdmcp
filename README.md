# rrdmcp

An MCP server (stdio transport) that exposes Munin's RRD metric data to an
LLM, so you can ask questions about your monitored hosts in plain language
instead of writing `rrdtool` incantations or one-off scripts.

## What it's for

Munin collects rich time-series data (CPU, memory, disk, network, custom
plugins...) but analyzing it usually means digging through graph images or
writing throwaway scripts against the RRD files. `rrdmcp` exposes that data
directly to an LLM through a handful of tools, so you can just ask:

- "Which days this month had the highest CPU usage?"
- "Free memory has been dropping — is that a trend or a one-off?"
- "fail2ban's ban count spiked a few days ago — does that line up with
  higher process counts or TCP resets on this host?"
- "Is disk usage on this host trending toward full, and how soon?"

The server deliberately stays "dumb" about interpretation: it discovers
hosts/plugins/fields, fetches raw or lightly-aggregated series (bucketed
averages, whole-range summaries, top-N rankings), and renders graphs — but
leaves judgment calls (what counts as "high", whether two metrics are
actually related, what to do about it) to the LLM doing the analysis. This
keeps the tool surface small and lets the LLM reason over real numbers
instead of a pre-baked interpretation.

## Setup

```bash
uv sync
```

Requires the `rrdtool` command on `PATH` (already present on any host
running Munin, since it's a dependency of `munin-node`/`munin`).

## Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `MUNIN_RRD_BASE_PATH` | `/var/lib/munin` | Root directory of the RRD files |
| `MUNIN_DATAFILE_PATH` | `${MUNIN_RRD_BASE_PATH}/datafile` | Location of Munin's `datafile` (config cache) |

## Running

```bash
uv run rrdmcp
```

## MCP client configuration example

```json
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
```

## Running in Docker

```bash
docker build -t rrdmcp .
```

Since this is a stdio transport, mount the directory holding the real
Munin data read-only and keep stdin open with `-i`:

```bash
docker run --rm -i -v /var/lib/munin:/var/lib/munin:ro rrdmcp
```

MCP client configuration example (Docker):

```json
{
  "mcpServers": {
    "rrdmcp": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/var/lib/munin:/var/lib/munin:ro",
        "rrdmcp"
      ]
    }
  }
}
```

If you mount `MUNIN_RRD_BASE_PATH` at a different path, add
`-e MUNIN_RRD_BASE_PATH=...` to `docker run` (the image default is
`/var/lib/munin`).

## Tools

- `list_hosts` — list every discovered `(group, host)` pair
- `list_plugins(group, host)` — list plugins for a host
- `list_fields(group, host, plugin)` — list a plugin's fields (label, type, thresholds, etc.)
- `get_metadata(group, host, plugin, field?)` — detailed metadata for a whole plugin, or a single field
- `fetch_series(group, host, plugin, field, start, end, resolution?, summary?, top_n?, top_by?, order?)` — fetch time series data. `start`/`end` accept a unix timestamp or any string `rrdtool` understands (`-1d`, `now`, etc.)
  - With no options, returns raw `points` as-is
  - `resolution` (seconds) aggregates into UTC-epoch-aligned `buckets` (avg/min/max/count) instead of raw points
  - `summary=true` aggregates the whole range into a single `summary` (avg/min/max/count); cannot be combined with `resolution`
  - `top_n` (requires `resolution`) returns only the top N buckets sorted by `top_by` ("avg"/"min"/"max", default "avg") in `order` ("desc"/"asc", default "desc"); `total_buckets` reports the count before filtering, so you can ask things like "the 10 days with the highest average" or "the 5 days with the lowest minimum" directly
- `render_graph(group, host, plugin, fields, start, end, width?, height?)` — render a PNG graph overlaying the given fields

## Known limitations

- If `datafile` is unavailable, discovery falls back to best-effort parsing of RRD filenames, losing precision on host/plugin boundaries and all metadata
- Graph rendering is a simplified version — it doesn't reproduce Munin's own threshold bands, stacking, CDEFs, etc.
- stdio transport only (v1)
