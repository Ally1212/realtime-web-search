"""Run keyword production until the UTC deadline, then stop and save the final report."""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEADLINE=datetime.fromisoformat('2026-10-07T01:00:00+00:00')


def control(action='tick'):
    code=(ROOT/'scripts/google_kitchen_until_morning.py').read_text()
    p=subprocess.run(['docker','compose','exec','-T','web','python','-',action],
                     input=code,text=True,capture_output=True,cwd=ROOT,timeout=180)
    if p.returncode:
        raise RuntimeError(p.stderr[-2000:])
    print(p.stdout.strip(),flush=True)
    return json.loads(p.stdout)


def finish():
    subprocess.run(['docker','compose','stop','-t','10','local-worker'],cwd=ROOT,check=True,timeout=45)
    control('finish')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='finish':
        finish()
    else:
        while datetime.now(timezone.utc)<DEADLINE:
            result=control()
            if result.get('status')=='finished':
                break
            state=subprocess.run(['docker','inspect','realtime-web-search-openserp-local-worker-1',
                                  '--format','{{.State.Running}}'],text=True,capture_output=True,timeout=15)
            if state.stdout.strip()!='true' and datetime.now(timezone.utc)<DEADLINE:
                subprocess.run(['docker','compose','up','-d','--no-deps','local-worker'],cwd=ROOT,check=True,timeout=120)
            time.sleep(max(0,min(60,DEADLINE.timestamp()-time.time())))
        finish()
