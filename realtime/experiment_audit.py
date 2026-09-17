"""Strict, bounded experiment audit and durable snapshot helpers."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


def audit_connection(db: sqlite3.Connection, *, observed_at: float | None = None) -> dict:
    settings = {key: json.loads(value) for key, value in db.execute(
        'SELECT key,value FROM settings'
    )}
    deadline, started = settings['deadline'], settings['started_at']
    now = time.time() if observed_at is None else observed_at
    accepted = db.execute(
        "SELECT count(*) FROM outbox o JOIN documents d ON d.id=o.document_id "
        "WHERE o.status='accepted' AND d.classification='new' AND d.quality='[]' "
        "AND d.finished BETWEEN ? AND ? AND o.finished BETWEEN ? AND ?",
        (started, deadline, started, deadline),
    ).fetchone()[0]
    violations = {
        'accepted_not_valid_new': db.execute(
            "SELECT count(*) FROM outbox o JOIN documents d ON d.id=o.document_id "
            "WHERE o.status='accepted' AND (d.classification<>'new' OR d.quality<>'[]')"
        ).fetchone()[0],
        'new_matches_baseline': db.execute(
            "SELECT count(*) FROM documents d WHERE d.classification='new' AND "
            "(EXISTS(SELECT 1 FROM baseline_hashes b WHERE b.hash=d.hash) OR "
            "EXISTS(SELECT 1 FROM baseline_urls b WHERE b.url=d.url OR b.url=d.canonical))"
        ).fetchone()[0],
        'repeated_new_hashes': db.execute(
            "SELECT count(*) FROM (SELECT hash FROM documents WHERE classification='new' "
            "GROUP BY hash HAVING count(*)>1)"
        ).fetchone()[0],
        'repeated_new_canonicals': db.execute(
            "SELECT count(*) FROM (SELECT canonical FROM documents WHERE classification='new' "
            "GROUP BY canonical HAVING count(*)>1)"
        ).fetchone()[0],
    }
    report = {
        'schema_version': 1,
        'observed_at': now,
        'experiment': settings['id'],
        'state': settings['state'],
        'started_at': started,
        'deadline': deadline,
        'strict_new_accepted_before_deadline': accepted,
        'valid_new_content_before_deadline': db.execute(
            "SELECT count(*) FROM documents WHERE classification='new' AND quality='[]' "
            "AND finished BETWEEN ? AND ?", (started, deadline),
        ).fetchone()[0],
        'accepted_last_60s': db.execute(
            "SELECT count(*) FROM outbox WHERE status='accepted' AND finished BETWEEN ? AND ?",
            (now - 60, min(now, deadline)),
        ).fetchone()[0],
        'window_seconds': deadline - started,
        'full_window_elapsed': now >= deadline,
        'violations': violations,
        'outbox_states': dict(db.execute('SELECT status,count(*) FROM outbox GROUP BY status')),
        'receipt_scope': 'Whale accepted/queued response; downstream indexing is not verified',
        'content_scope': (
            'Existing automatic relevance/body/publication rules; no independent manual review'
        ),
    }
    paused_seconds = db.execute(
        "SELECT coalesce(sum(seconds),0) FROM runtime WHERE kind='paused'"
    ).fetchone()[0]
    report['completed_full_window'] = (
        settings['state'] == 'complete'
        and settings.get('stop_reason') == 'deadline'
        and settings.get('finished_at', 0) >= deadline
        and paused_seconds == 0
    )
    report['million_goal_proven'] = (
        report['completed_full_window']
        and now >= deadline
        and abs(deadline - started - 86400) < 1
        and accepted >= 1_000_000
        and not any(violations.values())
    )
    report['audit_complete'] = True
    return report


def audit_database(path: Path, *, observed_at: float | None = None) -> dict:
    uri = path.resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        db.execute('BEGIN')
        return audit_connection(db, observed_at=observed_at)
    finally:
        db.close()


def default_archive_root(database_path: Path) -> Path:
    run_directory = database_path.resolve().parent
    if run_directory.parent.name == 'experiments':
        return run_directory.parent.parent / 'benchmarks' / 'experiment-audits'
    return run_directory.parent / 'experiment-audits'


def write_audit_snapshot(database_path: Path, archive_root: Path | None = None) -> Path:
    report = audit_database(database_path)
    report['evidence'] = 'archived_strict_snapshot'
    report['source_directory'] = database_path.resolve().parent.name
    root = (archive_root or default_archive_root(database_path)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{database_path.resolve().parent.name}.json"
    temporary = target.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target
