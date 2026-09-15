"""Read-only campaign progress; throughput is observation, never daily proof."""
import argparse
import datetime
import json
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('key', nargs='?', default='million-yield-v5-20260915')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
data = json.load(urllib.request.urlopen('http://127.0.0.1:8091/api/experiments/'+args.key, timeout=30))
t, m = data['timing'], data['metrics']
elapsed = t['elapsed_seconds']
if t.get('finished_at') and t['state'] in {'paused', 'complete', 'stopped', 'storage_stopped'}:
    elapsed = min(elapsed, max(0, t['finished_at'] - t['started_at']))
record = {'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'experiment': args.key, 'state': t['state'], 'elapsed_minutes': round(elapsed/60, 2),
          'new': m.get('new', 0), 'accepted': m.get('whale_accepted', 0),
          'pending': m.get('whale_pending', 0), 'missing_date': m.get('whale_blocked_missing_publication', 0),
          'duplicate_receipts': m.get('whale_duplicate', 0), 'rejected': m.get('whale_rejected', 0),
          'requests': m['google_requests'], 'captchas': m['google_captchas'],
          'heartbeat_age': round(t['heartbeat_age_seconds'] or 0, 2),
          'whale_error': data.get('whale_last_error'),
          'observed_accepted_per_minute': round(m.get('whale_accepted', 0)*60/max(elapsed, 1), 2),
          'target_per_minute': round(1_000_000/1440, 2)}
with (root/'docs/research/million-campaign-progress.jsonl').open('a') as f:
    f.write(json.dumps(record, ensure_ascii=False)+'\n')
(root/'docs/research'/f'{args.key}-latest.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(record, ensure_ascii=False))
