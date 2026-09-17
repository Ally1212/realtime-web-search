"""Audit the fixed experiment window against durable new-content receipts."""
import argparse
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from realtime.experiment_audit import audit_database


def audit_directory(directory: Path) -> dict:
    return audit_database(directory / 'experiment.sqlite3')


def zero_receipt_fallback(directory: Path, error: Exception) -> dict | None:
    """A damaged body table cannot hide accepted receipts when outbox proves zero."""
    path = (directory.resolve()/'experiment.sqlite3').as_uri()+'?mode=ro'
    db = sqlite3.connect(path, uri=True)
    try:
        accepted = db.execute("SELECT count(*) FROM outbox WHERE status='accepted'").fetchone()[0]
        if accepted:
            return None
        return {
            'experiment': directory.name,
            'strict_new_accepted_before_deadline': 0,
            'audit_complete': False,
            'maximum_bounded_by_receipts': True,
            'error': type(error).__name__,
        }
    except sqlite3.Error:
        return None
    finally:
        db.close()


def audit_all(root: Path) -> dict:
    reports, unavailable = [], []
    for directory in sorted(root.iterdir() if root.is_dir() else []):
        if not directory.is_dir() or not (directory/'experiment.sqlite3').is_file():
            continue
        try:
            report = audit_directory(directory)
        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            report = zero_receipt_fallback(directory, exc)
            if report is None:
                unavailable.append({'experiment': directory.name, 'error': type(exc).__name__})
                continue
        report['directory'] = directory.name
        report['evidence'] = 'live_ledger'
        reports.append(report)
    live_experiments = {row.get('experiment') for row in reports}
    archive_roots = {
        root.resolve() / 'experiment-audits',
        root.resolve().parent / 'benchmarks' / 'experiment-audits',
    }
    for archive_root in sorted(archive_roots):
        for path in sorted(archive_root.glob('*.json') if archive_root.is_dir() else []):
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
                if (
                    report.get('evidence') != 'archived_strict_snapshot'
                    or report.get('audit_complete') is not True
                    or not isinstance(report.get('violations'), dict)
                    or not isinstance(report.get('strict_new_accepted_before_deadline'), int)
                    or not isinstance(report.get('experiment'), str)
                ):
                    continue
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if report['experiment'] in live_experiments:
                continue
            report['directory'] = report.get('source_directory') or path.stem
            reports.append(report)
            live_experiments.add(report['experiment'])
    candidates = [row for row in reports if row.get('audit_complete') and not any(row['violations'].values())]
    maximum = max(candidates, key=lambda row: row['strict_new_accepted_before_deadline'], default=None)
    content_candidates = [
        row for row in candidates
        if isinstance(row.get('valid_new_content_before_deadline'), int)
    ]
    content_maximum = max(
        content_candidates,
        key=lambda row: row['valid_new_content_before_deadline'],
        default=None,
    )
    reports.sort(key=lambda row: row['strict_new_accepted_before_deadline'], reverse=True)
    return {
        'scope': 'strict accepted, valid new content, document and receipt inside each fixed window',
        'experiments': reports,
        'verified_maximum': maximum,
        'verified_valid_content_maximum': content_maximum,
        'unavailable': unavailable,
        'maximum_proven_across_ledgers': not unavailable,
    }


def parse_container_log(container: str, output: str) -> list[dict]:
    """Read timestamped metric lines without treating them as ledger proof."""
    experiments: dict[str, dict] = {}
    for line in output.splitlines():
        try:
            timestamp, raw = line.split(' ', 1)
            payload = json.loads(raw)
            experiment = str(payload['experiment'])
            observed_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp()
            new = int(payload['new'])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not experiment.startswith('google-experiment-') or new < 0:
            continue
        item = experiments.setdefault(experiment, {
            'experiment': experiment,
            'containers': set(),
            'first_observed_at': observed_at,
            'last_observed_at': observed_at,
            'new': 0,
            'requests': 0,
            'whale_accepted': 0,
            'state': '',
        })
        item['containers'].add(container)
        item['first_observed_at'] = min(item['first_observed_at'], observed_at)
        if observed_at >= item['last_observed_at']:
            item['last_observed_at'] = observed_at
            item['state'] = str(payload.get('state') or '')
        item['new'] = max(item['new'], new)
        item['requests'] = max(item['requests'], int(payload.get('requests') or 0))
        item['whale_accepted'] = max(
            item['whale_accepted'], int(payload.get('whale_accepted') or 0)
        )
    rows = []
    for item in experiments.values():
        item['containers'] = sorted(item['containers'])
        item['observed_seconds'] = round(
            item['last_observed_at'] - item['first_observed_at'], 3
        )
        rows.append(item)
    return rows


def audit_docker_containers(prefix: str = 'realtime-google-') -> dict:
    names = subprocess.check_output(
        ['docker', 'ps', '-a', '--format', '{{.Names}}'], text=True,
    ).splitlines()
    combined: dict[str, dict] = {}
    for name in sorted(item for item in names if item.startswith(prefix)):
        try:
            output = subprocess.check_output(
                ['docker', 'logs', '-t', name],
                stderr=subprocess.STDOUT,
                text=True,
            )
        except subprocess.CalledProcessError:
            continue
        for row in parse_container_log(name, output):
            previous = combined.get(row['experiment'])
            if previous is None:
                combined[row['experiment']] = row
                continue
            previous['containers'] = sorted(set(previous['containers'] + row['containers']))
            previous['first_observed_at'] = min(
                previous['first_observed_at'], row['first_observed_at']
            )
            if row['last_observed_at'] >= previous['last_observed_at']:
                previous['last_observed_at'] = row['last_observed_at']
                previous['state'] = row['state']
            for field in ('new', 'requests', 'whale_accepted'):
                previous[field] = max(previous[field], row[field])
            previous['observed_seconds'] = round(
                previous['last_observed_at'] - previous['first_observed_at'], 3
            )
    rows = sorted(combined.values(), key=lambda row: row['new'], reverse=True)
    return {
        'container_evidence_scope': (
            'observed process counters only; uniqueness, quality and receipt integrity '
            'require the strict ledger audit'
        ),
        'observed_containers': rows,
        'observed_container_maximum': rows[0] if rows else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--all-experiments', action='store_true')
    parser.add_argument('--docker-containers', action='store_true')
    args = parser.parse_args()
    if args.docker_containers and not args.all_experiments:
        parser.error('--docker-containers requires --all-experiments')
    report = audit_all(args.directory) if args.all_experiments else audit_directory(args.directory)
    if args.docker_containers:
        try:
            report.update(audit_docker_containers())
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            report['docker_container_error'] = type(exc).__name__
    print(json.dumps(report))


if __name__ == '__main__':
    main()
