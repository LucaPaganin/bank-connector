# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.6.11 AS uv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
# Install exactly the production dependency set before copying application code.
RUN uv sync --frozen --no-dev --no-install-project \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin connector \
    && install --directory --owner=connector --group=connector /data

COPY bank_connector ./bank_connector
COPY connector.py ./

USER connector
EXPOSE 3000

CMD ["uv", "run", "--no-sync", "python", "connector.py"]
