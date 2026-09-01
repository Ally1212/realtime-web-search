FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY realtime ./realtime
RUN --mount=type=cache,target=/root/.cache/pip pip install .

ENTRYPOINT ["realtime-web-search"]
CMD ["serve"]
