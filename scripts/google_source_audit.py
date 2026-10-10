"""Read-only Google source report for an explicitly enabled audit revision."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict

from realtime.config import Config
from realtime.discovery_audit_metrics import read_google_discovery_window


def audit_availability(manifest, start, end):
    """Enablement metadata is an operator assertion recorded at deployment."""
    evidence = manifest.get('auxiliary_audit')
    if not isinstance(evidence, dict) or not evidence.get('revision'):
        return 'unavailable', 'No recorded auxiliary audit enablement for this run.'
    try:
        enabled = datetime.fromisoformat(evidence['enabled_since'])
        if enabled.tzinfo is None or start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError('invalid window')
    except (KeyError, TypeError, ValueError):
        return 'unknown', 'Invalid audit enablement timestamp or report window.'
    if enabled > start:
        return 'unavailable', 'Audit was not enabled for the entire report window.'
    return 'available', 'Enablement asserted by run manifest; event correlation checked separately.'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory', required=True, type=Path)
    parser.add_argument('--report', type=Path, help='Exact frozen report; default audited-final.json')
    args = parser.parse_args(argv)
    root = args.run_directory
    manifest = json.loads((root / 'manifest.json').read_text())
    report_path = args.report or root / 'audited-final.json'
    report = json.loads(report_path.read_text())
    if report['run'] != manifest['run']:
        raise ValueError('report run differs from manifest')
    start = datetime.fromisoformat(report['started_at'])
    end = datetime.fromisoformat(report['measured_until'])
    status, reason = audit_availability(manifest, start, end)
    result = dict(at=datetime.now(timezone.utc).isoformat(), run=manifest['run'],
                  window=[start.isoformat(), end.isoformat()], status=status, reason=reason,
                  metrics=None, database_read_only=None)
    if status == 'available':
        baseline = set((root / 'baseline-url-hashes.txt').read_text().splitlines())
        dsn = Config().database_url
        options = conninfo_to_dict(dsn).get('options', '') + ' -c default_transaction_read_only=on'
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row, options=options) as db:
            with db.transaction():
                db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                result['database_read_only'] = db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
                required = ('discovery_aux_audit_events', 'discovery_known_result_urls',
                            'google_known_result_urls', 'google_serp_attempts', 'wml_continuous_campaigns')
                missing = [table for table in required if db.execute(
                    'SELECT to_regclass(%s) present', (table,)).fetchone()['present'] is None]
                if missing:
                    result.update(status='unavailable', reason='Missing required audit tables: ' + ', '.join(missing))
                else:
                    campaigns = [str(row['campaign_id']) for row in db.execute(
                        'SELECT campaign_id FROM wml_continuous_campaigns WHERE run_id=%s', (manifest['run'],))]
                    if not campaigns:
                        result.update(status='unknown', reason='No retained campaign association for this run.')
                    else:
                        result['metrics'] = read_google_discovery_window(db, campaigns, start, end, baseline)
                        result['web_exclusive_run_verified'] = report.get('exclusive_provider') is True and report.get('valid_for_comparison') is True
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'available' else 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, psycopg.Error) as exc:
        # Keep credentials and database exception payloads out of report output.
        print(json.dumps(dict(status='unknown', error_type=type(exc).__name__, metrics=None)))
        raise SystemExit(2)
