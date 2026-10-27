FROM ghcr.io/astral-sh/uv:0.11.9 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" RECALL_RUNTIME=/app/runtime
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY data ./data
COPY evals ./evals
COPY scripts ./scripts
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home recall && mkdir -p /app/runtime && chown -R recall:recall /app/runtime /app/evals
USER recall
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)"
CMD ["uvicorn", "recall.web:app", "--host", "0.0.0.0", "--port", "8000"]
