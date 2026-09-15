"""Audit the fixed experiment window against durable new-content receipts."""
import argparse
import json
import sqlite3
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    db = sqlite3.connect((args.directory/'experiment.sqlite3').as_uri()+'?mode=ro', uri=True)
    try:
        db.execute('BEGIN')  # All audit counters see the same WAL snapshot.
        settings = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings')}
        deadline, started = settings['deadline'], settings['started_at']
        now = time.time()
        accepted = db.execute("SELECT count(*) FROM outbox o JOIN documents d ON d.id=o.document_id "
                              "WHERE o.status='accepted' AND d.classification='new' AND d.quality='[]' "
                              "AND d.finished BETWEEN ? AND ? AND o.finished BETWEEN ? AND ?",
                              (started, deadline, started, deadline)).fetchone()[0]
        violations = {
            'accepted_not_valid_new': db.execute("SELECT count(*) FROM outbox o JOIN documents d ON d.id=o.document_id "
                                               "WHERE o.status='accepted' AND (d.classification<>'new' OR d.quality<>'[]')").fetchone()[0],
            'new_matches_baseline': db.execute("SELECT count(*) FROM documents d WHERE d.classification='new' AND "
                                             "(EXISTS(SELECT 1 FROM baseline_hashes b WHERE b.hash=d.hash) OR "
                                             "EXISTS(SELECT 1 FROM baseline_urls b WHERE b.url=d.url OR b.url=d.canonical))").fetchone()[0],
            'repeated_new_hashes': db.execute("SELECT count(*) FROM (SELECT hash FROM documents WHERE classification='new' "
                                             "GROUP BY hash HAVING count(*)>1)").fetchone()[0],
            'repeated_new_canonicals': db.execute("SELECT count(*) FROM (SELECT canonical FROM documents WHERE classification='new' "
                                                  "GROUP BY canonical HAVING count(*)>1)").fetchone()[0],
        }
        report = {'observed_at': now, 'experiment': settings['id'], 'state': settings['state'],
                  'started_at': started, 'deadline': deadline, 'strict_new_accepted_before_deadline': accepted,
                  'accepted_last_60s': db.execute("SELECT count(*) FROM outbox WHERE status='accepted' AND finished BETWEEN ? AND ?",
                                                (now-60, min(now, deadline))).fetchone()[0],
                  'window_seconds': deadline-started, 'full_window_elapsed': now >= deadline,
                  'violations': violations,
                  'outbox_states': dict(db.execute('SELECT status,count(*) FROM outbox GROUP BY status')),
                  'receipt_scope': 'Whale accepted/queued response; downstream indexing is not verified',
                  'content_scope': 'Existing automatic relevance/body/publication rules; no independent manual review'}
        paused_seconds = db.execute("SELECT coalesce(sum(seconds),0) FROM runtime WHERE kind='paused'").fetchone()[0]
        report['completed_full_window'] = (settings['state'] == 'complete' and settings.get('stop_reason') == 'deadline'
                                           and settings.get('finished_at', 0) >= deadline and paused_seconds == 0)
        report['million_goal_proven'] = (report['completed_full_window'] and now >= deadline and abs(deadline-started-86400)<1
                                         and accepted >= 1000000 and not any(violations.values()))
        print(json.dumps(report))
    finally:
        db.close()


if __name__ == '__main__':
    main()
