"""Read-only transition checks before retiring a finished series for an upgrade.

This command never stops, starts or migrates anything. Recheck immediately before
the separate migration/launch; a green report is not a lock against later changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKER = 'realtime-web-search-openserp-local-worker-1'
PREDECESSOR = 'google-hourly-20261009'
UNIT = 'realtime-google-hourly.service'
COUNT_KEYS = {'google_searches', 'successful_searches', 'captcha_attempts', 'raw_result_count',
              'result_urls_recorded', 'unique_result_links', 'valid_new_links',
              'new_qualified_bodies', 'missing_body_admission_rows'}
LEGACY_RUNS = {PREDECESSOR + '-' + key for key in ('curl-p1-s3','browser-p1-s3','wml-p1-s3')}
LEGACY_SOURCE = 'observer-revisions/uptime-interval-v1/supervisor.py.before'
LEGACY_SOURCE_SHA = '62a2626c7d2e40e61a680a2d5af198d0d214486e151e1578e8f0dcd8402579a2'


def timestamp(value):
    return datetime.fromisoformat(re.sub(r'(\.\d{6})\d+', r'\1', value).replace('Z', '+00:00'))


def full_hour(report, *, legacy_protocol_verified=False):
    return (report.get('valid_for_comparison') is True and report.get('full_hour_observed') is True
            and report.get('elapsed_seconds') == 3600 and report.get('interruptions') == []
            and report.get('exclusive_provider') is True
            and report.get('result_url_audit_complete') is True
            and report.get('body_admission_audit_complete') is True
            and (timestamp(report['measured_until']) - timestamp(report['started_at'])).total_seconds() == 3600
            and (timestamp(report['worker_verified_through']) >= timestamp(report['measured_until'])
                 if report.get('worker_verified_through') else
                 legacy_protocol_verified and report.get('run') in LEGACY_RUNS))


def verify(root: Path, independent: dict, worker: dict, supervisor: dict, *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    root = root.resolve()
    prior = root / 'state' / PREDECESSOR
    series = json.loads((prior / 'series.json').read_text())
    comparison = json.loads((prior / 'comparison.json').read_text())
    source = prior / LEGACY_SOURCE
    legacy_verified = source.exists() and hashlib.sha256(source.read_bytes()).hexdigest() == LEGACY_SOURCE_SHA
    checks = dict(nine_original_plans=len(series['plans']) == 9,
                  nine_completed=comparison.get('pending') == 0 and len(comparison['completed']) == 9,
                  original_worker_stopped=not worker.get('Running', True)
                      and not worker.get('Paused', True) and not worker.get('Restarting', True),
                  original_supervisor_stopped=supervisor.get('ActiveState') == 'inactive'
                      and supervisor.get('MainPID') == '0')
    reports, valid, sql_valid, windows = [], True, True, []
    for index, plan in enumerate(series['plans']):
        run = PREDECESSOR + '-' + plan['key']
        # Retry hours can replace an invalid initial run; follow the actual completed row.
        completed = next((r for r in comparison['completed'] if r.get('index') == index), None)
        if completed:
            run = completed['run']
        if not re.fullmatch(PREDECESSOR + r'-[a-z0-9-]+', run):
            raise ValueError('invalid predecessor run identity')
        path = root / 'state' / run / 'audited-final.json'
        if not path.resolve().is_relative_to(root):
            raise ValueError('predecessor report escapes project root')
        if not path.exists():
            valid = sql_valid = False
            reports.append(dict(run=run, final_present=False))
            continue
        raw = path.read_bytes();report = json.loads(raw);digest = hashlib.sha256(raw).hexdigest()
        good = bool(completed and report['run'] == run
                    and full_hour(report, legacy_protocol_verified=legacy_verified)
                    and report['provider'] == plan['provider'] and report['sources'] == plan['sources']
                    and timestamp(report['measured_until']) <= now)
        if good:
            windows.append((timestamp(report['started_at']),timestamp(report['measured_until'])))
        audit = independent.get(run, {})
        sql_good = (audit.get('matches_frozen_report') is True
                    and audit.get('database_read_only') == 'on'
                    and audit.get('frozen_report_sha256') == digest
                    and set(audit.get('checks', {})) == COUNT_KEYS
                    and all(v is True for v in audit['checks'].values())
                    and set(audit.get('independent_counts', {})) == COUNT_KEYS
                    and all(audit['independent_counts'][k] == report[k] for k in COUNT_KEYS))
        valid &= good;sql_valid &= sql_good
        reports.append(dict(run=run, final_present=True, exact_full_hour_valid=good,
                            independent_sql_verified=sql_good, report_sha256=digest,
                            explicit_worker_end_timestamp_present=bool(report.get('worker_verified_through')),
                            legacy_final_state_protocol_verified=bool(run in LEGACY_RUNS and legacy_verified)))
    checks.update(all_nine_exact_hours=valid, all_nine_independent_sql=sql_valid)
    windows.sort()
    checks['recorded_windows_nonoverlapping'] = all(a[1] <= b[0] for a,b in zip(windows,windows[1:]))
    continuous = root / 'state' / (PREDECESSOR + '-continuous-best')
    manifest = continuous / 'manifest.json'
    checks['continuous_stopped_at_completed_boundary'] = True
    if manifest.exists():
        checks['continuous_stopped_at_completed_boundary'] = False
        m = json.loads(manifest.read_text())
        if m.get('worker_started_at') and checks['original_worker_stopped']:
            start = timestamp(m['worker_started_at']);finished = timestamp(worker['FinishedAt'])
            index = int((finished - start).total_seconds() // 3600) - 1
            archive = prior / 'hourly-archive' / ('continuous-hour-%06d.json' % index)
            if index >= 0 and archive.exists():
                hour = json.loads(archive.read_text());right = start + timedelta(hours=index + 1)
                checks['continuous_stopped_at_completed_boundary'] = bool(
                    full_hour(hour) and timestamp(hour['measured_until']) == right
                    and timedelta(0) <= finished - right <= timedelta(seconds=120)
                    and finished <= now and (not windows or start >= windows[-1][1])
                    and worker.get('StartedAt') == m['docker_started_at']
                    and worker.get('Id') == m['worker_id']
                    and worker.get('RestartCount') == m['worker_restart_count']
                    and timestamp(m['last_verified_running_at']) >= right)
    return dict(at=now.isoformat(), predecessor=PREDECESSOR,
                ready=all(checks.values()), checks=checks, reports=reports,
                limits='Read-only snapshots and supplied independent SQL evidence. Recheck before migration; '
                       'this does not stop writers, serialize launch or certify old current body integrity. '
                       'A continuous worker must have ended within 120s after a verified complete hour; '
                       'the grace accommodates normal shutdown, not a mid-hour restart. '
                       'The three early legacy reports can use the SHA-verified original after-deadline final-state '
                       'control flow; their missing persisted end timestamps are explicitly reported, never invented. '
                       'Continuous-hour verification cannot use that exception.')


def inspect():
    def call(args):
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=20).stdout
    state = json.loads(call(['docker', 'inspect', '--format', '{{json .State}}', WORKER]))
    restarts = int(call(['docker', 'inspect', '--format', '{{.RestartCount}}', WORKER]).strip())
    allowed = {k:state[k] for k in ('Running','Paused','Restarting','StartedAt','FinishedAt')}
    allowed['RestartCount'] = restarts
    allowed['Id'] = call(['docker', 'inspect', '--format', '{{.Id}}', WORKER]).strip()
    unit = dict(line.split('=',1) for line in call(
        ['systemctl','show',UNIT,'-p','ActiveState','-p','MainPID']).splitlines())
    return allowed, unit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--independent-audits', type=Path, required=True)
    args = parser.parse_args()
    independent = json.loads(args.independent_audits.read_text())
    worker, supervisor = inspect()
    result = verify(args.project_root, independent, worker, supervisor)
    print(json.dumps(result, indent=2))
    return 0 if result['ready'] else 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(ready=False, observation_error=type(exc).__name__)))
        raise SystemExit(2)
