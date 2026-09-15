"""Recover unprocessed Google discoveries from configured experiment baselines."""
import argparse
import json
import sqlite3
import time
from pathlib import Path


def inherit(directory):
    db = sqlite3.connect((directory/'experiment.sqlite3').as_uri()+'?mode=rw', uri=True, timeout=30)
    changes = []
    try:
        settings = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings')}
        for baseline in settings.get('baseline_run_paths', []):
            db.execute('ATTACH DATABASE ? AS previous', ((Path(baseline)/'experiment.sqlite3').as_uri()+'?mode=ro',))
            try:
                with db:
                    db.execute("CREATE TEMP TABLE pending_inherit AS SELECT u.* FROM previous.urls u "
                               "WHERE u.last_fetch=0 AND u.state IN ('pending','fetching') "
                               "AND EXISTS(SELECT 1 FROM previous.discoveries d WHERE d.url=u.url) "
                               "AND NOT EXISTS(SELECT 1 FROM baseline_urls b WHERE b.url=u.url)")
                    db.execute('CREATE INDEX pending_inherit_url ON pending_inherit(url)')
                    # Unknown queries are provenance-only; they do not alter the search plan.
                    queries = db.execute('INSERT OR IGNORE INTO queries(id,family,query,language,topic,enabled,last_served) '
                                         'SELECT DISTINCT q.id,q.family,q.query,q.language,q.topic,0,q.last_served '
                                         'FROM previous.queries q JOIN previous.discoveries d ON d.query_id=q.id '
                                         'JOIN pending_inherit u ON u.url=d.url').rowcount
                    urls = db.execute('INSERT OR IGNORE INTO urls(url,title,first_seen,last_seen) '
                                      'SELECT url,title,first_seen,last_seen FROM pending_inherit').rowcount
                    discoveries = db.execute('INSERT OR IGNORE INTO discoveries '
                                             'SELECT d.* FROM previous.discoveries d JOIN pending_inherit p ON p.url=d.url '
                                             "JOIN urls u ON u.url=d.url WHERE u.last_fetch=0 AND u.state<>'baseline_skipped'").rowcount
                    db.execute('DROP TABLE pending_inherit')
                    record = {'baseline': baseline, 'new_pending_urls': urls,
                              'new_provenance_queries': queries, 'new_discoveries': discoveries}
                    db.execute('INSERT INTO settings VALUES(?,?)',
                               ('pending_frontier_inheritance_'+str(time.time_ns()), json.dumps(dict(record, at=time.time()))))
                    changes.append(record)
            finally:
                db.execute('DETACH DATABASE previous')
        return {'at': time.time(), 'new_pending_urls': sum(r['new_pending_urls'] for r in changes),
                'baselines': changes, 'search_attempts_added': 0, 'documents_or_receipts_added': 0}
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    print(json.dumps(inherit(parser.parse_args().directory)))
