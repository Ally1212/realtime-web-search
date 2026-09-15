"""Persist a minute-by-minute record while an experiment is running."""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('key')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    while True:
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, str(root/'scripts/million_progress.py'), args.key],
                                    capture_output=True, text=True, timeout=45)
            if result.returncode:
                raise RuntimeError(result.stderr.strip()[-500:])
            record = json.loads(result.stdout)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if record['state'] in {'complete', 'paused', 'stopped', 'storage_stopped'}:
                # The collector may already have exited. Read the shared volume
                # through the web container so the final audit still executes.
                final = subprocess.run([sys.executable, str(root/'scripts/report_google_day.py'), args.key],
                                       capture_output=True, text=True, timeout=100)
                if final.returncode:
                    raise RuntimeError('final audit failed: ' + final.stderr.strip()[-500:])
                print(json.dumps({'day_report': final.stdout.strip()}), flush=True)
                return
            health = subprocess.run([sys.executable, str(root/'scripts/million_health.py'), args.key],
                                    capture_output=True, text=True, timeout=25)
            if health.returncode:
                raise RuntimeError(health.stderr.strip()[-500:])
            print(health.stdout.strip(), flush=True)
        except Exception as exc:
            print(json.dumps({'observed_at': datetime.now(timezone.utc).isoformat(),
                              'experiment': args.key, 'monitor_error': str(exc)}, ensure_ascii=False), flush=True)
        time.sleep(max(1, 60 - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
