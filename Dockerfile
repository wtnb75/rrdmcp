FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Install dependencies first so this layer is cached independently of source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
COPY README.md ./
RUN uv sync --frozen --no-dev --no-editable


FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim

# rrdtool is a runtime dependency invoked as a subprocess (see src/rrdmcp/rrd.py),
# not a Python package — it must be installed via apt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rrdtool \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 rrdmcp

WORKDIR /app
COPY --from=builder /app/.venv ./.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MUNIN_RRD_BASE_PATH=/var/lib/munin

USER rrdmcp

ENTRYPOINT ["rrdmcp"]
