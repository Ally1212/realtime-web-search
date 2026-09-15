"""Sample receipt throughput and container resources without full family reports."""
import argparse
import json
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('key')
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', args.key):
    raise SystemExit('Invalid experiment key')
container = 'realtime-google-' + args.key
probe = '''
import json,sqlite3,sys,time,shutil
from pathlib import Path
p=Path('/app/state/experiments')/sys.argv[1]
c=sqlite3.connect((p/'experiment.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=5)
c.execute('BEGIN')
t=time.time()
settings={k:json.loads(v) for k,v in c.execute("SELECT key,value FROM settings WHERE key IN ('pipeline','deadline','started_at')")}
deadline=settings['deadline']
counts={str(seconds):c.execute("SELECT count(*) FROM outbox WHERE status='accepted' AND finished BETWEEN ? AND ?",(t-seconds,min(t,deadline))).fetchone()[0] for seconds in (60,300)}
result={'observed_at':t,'accepted_last_60s':counts['60'],'accepted_last_300s':counts['300'],'accepted_per_minute_last_5m':round(counts['300']/5,2),'pending_bodies':c.execute("SELECT count(*) FROM urls WHERE state='pending'").fetchone()[0],'pipeline':settings.get('pipeline'),'disk_free_bytes':shutil.disk_usage(p).free,'ledger_bytes':sum(f.stat().st_size for f in p.glob('experiment.sqlite3*')),'experiment_age_seconds':round(t-settings['started_at'],2)}
try:
 result['cpu_usage_usec']=int(dict(line.split() for line in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())['usage_usec'])
except (OSError,KeyError,ValueError): pass
workers=[]
for status in Path('/proc').glob('[0-9]*/status'):
 try:
  cmd=(status.parent/'cmdline').read_bytes().split(bytes([0]))
  if b'realtime.fast_experiment' not in cmd or b'fetch' not in cmd: continue
  rss=next(int(line.split()[1]) for line in status.read_text().splitlines() if line.startswith('VmRSS:'))
  workers.append((int(status.parent.name),rss))
 except (OSError,StopIteration,ValueError): pass
result['worker_pids']=[pid for pid,_ in workers]
result['worker_rss_mib_sum']=round(sum(rss for _,rss in workers)/1024,1)
result['worker_rss_mib_max']=round(max((rss for _,rss in workers),default=0)/1024,1)
print(json.dumps(result));c.close()
'''
result = subprocess.run(['docker', 'exec', '-i', container, 'python', '-', args.key],
                        input=probe, text=True, capture_output=True, check=True, timeout=10)
record = json.loads(result.stdout)
record['experiment'] = args.key
stats = subprocess.check_output(['docker', 'stats', '--no-stream', '--format',
                                 '{"cpu":"{{.CPUPerc}}","memory":"{{.MemUsage}}"}', container], text=True, timeout=10)
record['resources'] = json.loads(stats)
root = Path(__file__).resolve().parents[1]/'docs/research'
history = root/f'{args.key}-health.jsonl'
if history.exists():
    previous = json.loads(history.read_text().splitlines()[-1])
    seconds = record['observed_at'] - previous['observed_at']
    if seconds > 0 and 'cpu_usage_usec' in previous and record.get('cpu_usage_usec', 0) >= previous['cpu_usage_usec']:
        record['cpu_cores_since_previous_sample'] = round((record['cpu_usage_usec']-previous['cpu_usage_usec'])/1e6/seconds, 2)
    if 'worker_pids' in previous:
        record['new_worker_pids_since_previous_sample'] = len(set(record['worker_pids'])-set(previous['worker_pids']))
with history.open('a') as f:
    f.write(json.dumps(record)+'\n')
print(json.dumps(record))
