FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY realtime ./realtime
RUN --mount=type=cache,target=/root/.cache/pip pip install .
RUN python -m playwright install --with-deps chromium
RUN apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["realtime-web-search"]
CMD ["serve"]
