FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY realtime ./realtime
RUN pip install --no-cache-dir .

ENTRYPOINT ["realtime-web-search"]
CMD ["serve"]
