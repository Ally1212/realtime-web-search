"""Read the shared experiment volume after the collector exits and save audits."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def write_report(key, reader='realtime-web-search-web-1'):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
        raise ValueError('invalid experiment key')
    root = Path(__file__).resolve().parents[1]
    report = {'experiment': key, 'evidence': {}, 'script_hashes': {}}
    for source, label in [('audit_million.py', 'strict_audit'),
                          ('estimate_google_capacity.py', 'capacity_scenarios'),
                          ('analyze_google_recovery.py', 'recovery_probe_evidence')]:
        code = (root/'scripts'/source).read_text()
        result = subprocess.run(
            ['docker', 'exec', '-i', reader, 'python', '-', '/app/state/experiments/'+key],
            input=code, text=True, capture_output=True, check=True, timeout=45)
        report['evidence'][label] = json.loads(result.stdout)
        report['script_hashes'][source] = hashlib.sha256(code.encode()).hexdigest()
    path = root/'docs/research'/f'{key}-day-report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('key')
    parser.add_argument('--reader-container', default='realtime-web-search-web-1')
    args = parser.parse_args()
    print(write_report(args.key, args.reader_container))
