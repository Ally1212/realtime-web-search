FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml README.md ./

# Keep the large dependency and browser layers independent from application
# source so a code-only fix can be deployed without downloading Chromium again.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c "import subprocess,sys,tomllib; dependencies=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; subprocess.check_call([sys.executable,'-m','pip','install',*dependencies])"
RUN python -m playwright install --with-deps chromium

COPY realtime ./realtime
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .

ENTRYPOINT ["realtime-web-search"]
CMD ["serve"]
