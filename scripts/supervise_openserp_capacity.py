"""Durable capacity test with a lane-lock watchdog and a hard stop deadline."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = os.getenv("CAPACITY_RUN_ID", "openserp-capacity-20261007")
STATE = ROOT / "state" / RUN


def control(action="tick"):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-e", "CAPACITY_RUN_ID=" + RUN, "web", "python", "-", action],
        input=(ROOT / "scripts/openserp_capacity_run.py").read_text(),
        text=True, capture_output=True, cwd=ROOT, timeout=180,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:])
    print(result.stdout.strip(), flush=True)
    return json.loads(result.stdout)


def finish():
    subprocess.run(["docker", "compose", "stop", "-t", "10", "local-worker"], cwd=ROOT, check=True, timeout=60)
    control("finish")
    if os.getenv("CAPACITY_AUTOTUNE") == "1":
        evidence = subprocess.run(
            ["docker", "compose", "exec", "-T", "web", "python", "-", RUN],
            input=(ROOT / "scripts/verify_openserp_capacity.py").read_text(),
            text=True, capture_output=True, cwd=ROOT, check=True, timeout=90,
        )
        (STATE / "final-hour-verification.json").write_text(evidence.stdout)


def service_responsive():
    # /ready does not acquire the lane mutex and can falsely report healthy.
    with urllib.request.urlopen("http://127.0.0.1:7001/stats/proxy", timeout=5) as response:
        return isinstance(json.load(response).get("lanes"), dict)


def supervise():
    deadline = datetime.fromisoformat(json.loads((STATE / "manifest.json").read_text())["deadline"]).timestamp()
    failures = 0
    while time.time() < deadline:
        try:
            failures = 0 if service_responsive() else failures + 1
        except Exception:
            failures += 1
        if failures >= 3 and time.time() < deadline:
            # CAPTCHA and 429 keep this endpoint responsive; they cannot trigger a restart.
            subprocess.run(["docker", "compose", "restart", "openserp"], cwd=ROOT, check=True, timeout=90)
            print("OpenSERP restarted after three failed mutex health probes", flush=True)
            failures = 0
        if time.time() >= deadline:
            break
        result = control()
        if result.get("status") == "finished":
            break
        state = subprocess.run(["docker", "inspect", "realtime-web-search-openserp-local-worker-1", "--format", "{{.State.Running}}"], text=True, capture_output=True, timeout=15)
        if state.stdout.strip() != "true" and time.time() < deadline:
            subprocess.run(["docker", "compose", "up", "-d", "--no-deps", "local-worker"], cwd=ROOT, check=True, timeout=120)
        if os.getenv("CAPACITY_AUTOTUNE") == "1" and time.time() < deadline:
            from autotune_openserp_capacity import tick
            tick(ROOT, STATE)
        (STATE / "heartbeat.json").write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "health_failures": failures}))
        time.sleep(max(0, min(60, deadline - time.time())))
    finish()


if __name__ == "__main__":
    finish() if len(sys.argv) > 1 and sys.argv[1] == "finish" else supervise()
