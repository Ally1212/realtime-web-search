"""Archive completed experiments and exact continuous hours without changing collection."""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


GRACE = timedelta(seconds=60)


def completed_windows(start, now, deadline):
    measured = min(now - GRACE, deadline)
    count = max(0, int((measured - start).total_seconds() // 3600))
    return [(start + timedelta(hours=i), start + timedelta(hours=i+1)) for i in range(count)]


def interruptions_in_window(events, left, right):
    # The supervisor notices changes at minute probes. Conservatively invalidate
    # both adjacent hours when a change could have happened across their boundary.
    found = []
    for event in events:
        if event.get('affected_from') and event.get('affected_until'):
            start = datetime.fromisoformat(event['affected_from'])
            end = datetime.fromisoformat(event['affected_until'])
            if end < start:
                raise ValueError('Interruption interval ends before it starts')
            if start < right and end > left:
                found.append(event)
        elif left - GRACE <= datetime.fromisoformat(event['at']) < right + GRACE:
            found.append(event)
    return found


def worker_window_verified(manifest, right):
    latest = manifest.get('last_verified_running_at')
    return bool(latest and datetime.fromisoformat(latest) >= right)


def summarize(hours):
    observed = sorted(hours, key=lambda row: row['started_at'])
    valid = [row for row in observed if row['valid_for_comparison']]
    consecutive = 0
    previous = None
    for row in observed:
        good = row['valid_for_comparison'] and row['new_qualified_bodies'] > 0 and row['successful_searches'] > 0
        consecutive = (consecutive + 1 if previous == row['started_at'] else 1) if good else 0
        previous = row['measured_until']
    latest_day = observed[-24:]
    day_complete = (len(latest_day) == 24 and all(row['valid_for_comparison'] for row in latest_day)
                    and all(a['measured_until'] == b['started_at'] for a, b in zip(latest_day, latest_day[1:])))
    return dict(complete_hours=len(observed), valid_hours=len(valid),
                consecutive_nonzero_qualified_hours=consecutive,
                best_complete_hour_bodies=max((row['new_qualified_bodies'] for row in valid), default=None),
                last_24_hours_verified=day_complete,
                last_24_hour_bodies=sum(row['new_qualified_bodies'] for row in latest_day) if day_complete else None,
                last_24_hour_searches=sum(row['google_searches'] for row in latest_day) if day_complete else None,
                last_24_hour_new_links=sum(row['valid_new_links'] for row in latest_day) if day_complete else None,
                last_24_hour_google_keyword_searches=sum(row['completed_google_keyword_searches'] for row in latest_day)
                    if day_complete and all('completed_google_keyword_searches' in row for row in latest_day) else None,
                last_24_hour_all_source_new_links=sum(row['all_source_valid_new_links'] for row in latest_day)
                    if day_complete and all('all_source_valid_new_links' in row for row in latest_day) else None,
                last_24_hour_extended_audit_complete=all(row['extended_audit_complete'] for row in latest_day)
                    if day_complete and all('extended_audit_complete' in row for row in latest_day) else None)


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def container_archive(series):
    import psycopg
    from psycopg.rows import dict_row
    from realtime.config import Config
    root = Path('/app/state')
    state = root / series
    archive = state / 'hourly-archive'
    archive.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location('frozen_hour_metrics', state / 'control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    experiments = []
    for directory in sorted(root.glob(series + '-*')):
        final = directory / 'audited-final.json'
        if final.exists():
            report = json.loads(final.read_text())
            target = archive / (directory.name + '.json')
            if not target.exists():
                save(target, report)
            experiments.append(dict(run=report['run'], valid=report['valid_for_comparison'],
                google_searches=report['google_searches'], valid_new_links=report['valid_new_links'],
                captcha_rate=report['captcha_rate'], new_qualified_bodies=report['new_qualified_bodies']))
    continuous = root / (series + '-continuous-best')
    manifest_path = continuous / 'manifest.json'
    now = datetime.now(timezone.utc)
    rows = []
    status = 'waiting_for_experiments'
    with psycopg.connect(Config().database_url, autocommit=True, row_factory=dict_row,
                        options='-c default_transaction_read_only=on', connect_timeout=10) as db:
        read_only = db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
        if not read_only:
            raise RuntimeError('Audit connection must be read-only')
        if manifest_path.exists():
            m = json.loads(manifest_path.read_text())
            if m.get('worker_started_at') and m.get('status') == 'running':
                status = 'archiving_continuous_hours'
                start = datetime.fromisoformat(m['worker_started_at'])
                deadline = datetime.fromisoformat(m['deadline'])
                for index, (left, right) in enumerate(completed_windows(start, now, deadline)):
                    target = archive / ('continuous-hour-%06d.json' % index)
                    if not target.exists():
                        if not worker_window_verified(m, right):
                            # Elapsed wall time alone does not prove the worker
                            # survived this hour. Wait for a post-boundary probe.
                            continue
                        report = control.metrics(db, continuous, m, right)
                        interruptions = interruptions_in_window(m.get('interruptions', []), left, right)
                        report['interruptions'] = interruptions
                        report['valid_for_comparison'] = bool(report['full_hour_observed']
                            and report['exclusive_provider'] and report['result_url_audit_complete']
                            and report.get('body_admission_audit_complete', False)
                            and report.get('body_integrity_valid', True)
                            and report.get('auxiliary_enablement_valid', True)
                            and not interruptions)
                        report['archived_at'] = now
                        report['window_index'] = index
                        report['worker_verified_through'] = m['last_verified_running_at']
                        save(target, report)
                    report = json.loads(target.read_text())
                    late_interruptions = interruptions_in_window(m.get('interruptions', []), left, right)
                    if late_interruptions and report['valid_for_comparison']:
                        # An observation can arrive late after a slow supervisor probe.
                        # Preserve frozen counts, but revoke a subsequently disproved valid hour.
                        report['valid_for_comparison'] = False
                        report['interruptions'] = late_interruptions
                        report['validation_revised_at'] = now
                        save(target, report)
                    rows.append(report)
    result = dict(series=series, as_of=now, status=status, database_read_only=read_only,
                  grace_seconds=int(GRACE.total_seconds()), experiments=experiments,
                  continuous=summarize(rows), continuous_hours=rows,
                  definition='Frozen full hours anchored to the selected worker start, with 60s commit grace. '
                  'Counts alone do not prove this configuration is globally optimal or stable indefinitely.')
    save(archive / 'summary.json', result)
    print(json.dumps({key: value for key, value in result.items() if key != 'continuous_hours'}, default=str))


def main():
    series = os.getenv('GOOGLE_HOURLY_SERIES', 'google-hourly-20261009')
    if len(sys.argv) > 1 and sys.argv[1] == '--container':
        container_archive(sys.argv[2])
        return
    root = Path(os.getenv('GOOGLE_PROJECT_ROOT', str(Path(__file__).resolve().parents[1])))
    result = subprocess.run(['docker', 'compose', 'exec', '-T', '-w', '/tmp',
        '-e', 'PYTHONPATH=/app/state/' + series + '/code',
        'web', 'python', '-', '--container', series], cwd=root,
        input=Path(__file__).read_text(), text=True, capture_output=True, timeout=180)
    if result.returncode:
        print(json.dumps(dict(archive_error='container_command_failed', returncode=result.returncode)), file=sys.stderr)
        raise SystemExit(result.returncode)
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
