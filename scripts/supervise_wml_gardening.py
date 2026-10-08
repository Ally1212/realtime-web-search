"""Host systemd service: replenish WML work and save a report every minute."""
import subprocess
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run_once():
    code=(ROOT/"scripts/wml_gardening_continuous.py").read_text()
    result=subprocess.run(["docker","compose","exec","-T","web","python","-"],
                          input=code,text=True,capture_output=True,cwd=ROOT,timeout=180)
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
          result.stdout.strip() if result.returncode==0 else result.stderr[-2000:],flush=True)
    if result.returncode:
        raise RuntimeError("WML control tick failed")
    status=subprocess.run(["docker","inspect","realtime-web-search-openserp-local-worker-1",
                           "--format","{{.State.Running}}"],text=True,capture_output=True,timeout=15)
    if status.stdout.strip()!="true":
        subprocess.run(["docker","compose","up","-d","--no-deps","local-worker"],
                       cwd=ROOT,check=True,timeout=120)


if __name__=="__main__":
    while True:
        run_once()
        time.sleep(60)
