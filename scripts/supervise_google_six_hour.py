"""Host supervisor: durable 6 hour deadline, minute reports, terminal stop."""
import json
import subprocess
import time
from pathlib import Path
ROOT=Path('/home/azureuser/projects/realtime-web-search-openserp')
STATE=ROOT/'state/google-web-travel-6h-20261005'
def run(action):
    code=(ROOT/'scripts/google_web_six_hour.py').read_text()
    return subprocess.run(['docker','compose','exec','-T','web','python','-',action],input=code,text=True,capture_output=True,cwd=ROOT,timeout=90)
def event(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),message,flush=True)
m=json.loads((STATE/'manifest.json').read_text())
from datetime import datetime
deadline=datetime.fromisoformat(m['deadline']).timestamp()
event('supervisor running')
while time.time()<deadline:
    try:
        r=run('report')
        event('report saved' if r.returncode==0 else r.stderr[-1000:])
    except Exception as e: event(repr(e))
    time.sleep(max(0,min(60,deadline-time.time())))
# Stop the worker so no in-flight body continues beyond the experiment.
subprocess.run(['docker','compose','stop','-t','30','local-worker'],cwd=ROOT,timeout=90,check=True)
r=run('finish')
event(r.stdout if r.returncode==0 else r.stderr)
raise SystemExit(r.returncode)
