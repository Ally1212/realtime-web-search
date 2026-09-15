"""Restore visited-query priority without changing code, results or page cooldowns."""
import argparse
import json
import sqlite3
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    path = args.directory / 'experiment.sqlite3'
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30)
    now = time.time()
    try:
        settings = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings')}
        visits, baselines = {}, []
        for directory in settings.get('baseline_run_paths', []):
            previous = sqlite3.connect((Path(directory) / 'experiment.sqlite3').as_uri() + '?mode=ro', uri=True)
            try:
                count = 0
                for key, finished in previous.execute('SELECT query_id,max(finished) FROM searches '
                                                       'WHERE finished>? AND finished<=? GROUP BY query_id', (now-86400, now)):
                    visits[key] = max(visits.get(key, 0), finished)
                    count += 1
                baselines.append({'directory': directory, 'visited_queries': count})
            finally:
                previous.close()
        with db:
            db.execute('CREATE TEMP TABLE inherited_visits(id TEXT PRIMARY KEY,finished REAL)')
            db.executemany('INSERT INTO inherited_visits VALUES(?,?)', visits.items())
            cursor = db.execute('UPDATE queries SET last_served=(SELECT finished FROM inherited_visits v WHERE v.id=queries.id) '
                                'WHERE EXISTS(SELECT 1 FROM inherited_visits v WHERE v.id=queries.id AND v.finished>queries.last_served)')
            audit = {'at': now, 'queries_updated': cursor.rowcount, 'baselines': baselines,
                     'change': 'restore last_served for historical attempts including failures; page due times unchanged'}
            db.execute('INSERT INTO settings(key,value) VALUES(?,?)',
                       ('frontier_visit_repair_' + str(time.time_ns()), json.dumps(audit)))
        print(json.dumps(audit))
    finally:
        db.close()


if __name__ == '__main__':
    main()
