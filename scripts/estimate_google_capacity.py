"""Read-only receipt-based capacity scenarios, never a 24-hour proof."""
import argparse
import json
import sqlite3
import time
from pathlib import Path


def peak_window(timestamps, seconds, started, end):
    left = 0
    best = 0
    for right, stamp in enumerate(timestamps):
        while timestamps[left] <= stamp-seconds:
            left += 1
        if stamp >= started+seconds and stamp <= end:
            best = max(best, right-left+1)
    return best


def estimate(directory, observed_at=None):
    now = time.time() if observed_at is None else observed_at
    db = sqlite3.connect((directory/'experiment.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    try:
        db.execute('BEGIN')
        settings = {k: json.loads(v) for k,v in db.execute('SELECT key,value FROM settings')}
        started, deadline = settings['started_at'], settings['deadline']
        end = min(now, deadline, settings.get('finished_at') or now)
        stamps = [r[0] for r in db.execute(
            "SELECT o.finished FROM outbox o JOIN documents d ON d.id=o.document_id "
            "WHERE o.status='accepted' AND d.classification='new' AND d.quality='[]' "
            "AND d.finished BETWEEN ? AND ? AND o.finished BETWEEN ? AND ? ORDER BY o.finished",
            (started,end,started,end))]
        inherited = db.execute(
            "SELECT count(*) FROM outbox o JOIN documents d ON d.id=o.document_id JOIN urls u ON u.url=d.url "
            "WHERE o.status='accepted' AND d.classification='new' AND d.quality='[]' AND u.first_seen<? "
            "AND d.finished BETWEEN ? AND ? AND o.finished BETWEEN ? AND ?", (started,started,end,started,end)).fetchone()[0]
        requests = successes = results = captchas = 0
        first_http = last_http = None
        for finish, raw in db.execute('SELECT finished,attempts FROM searches WHERE finished BETWEEN ? AND ?', (started,end)):
            rows = json.loads(raw)
            for row in rows:
                requests += 1
                successes += bool(row.get('success'))
                results += int(row.get('results') or 0)
                captchas += row.get('error') == 'google_captcha'
                first_http = min(first_http or finish,finish)
                last_http = max(last_http or finish,finish)
        measured_yield = (len(stamps)-inherited)/requests if requests else 0
        report = dict(observed_at=now, started_at=started, deadline=deadline,
            elapsed_minutes=(end-started)/60, state=settings['state'],
            strict_accepted=len(stamps), inherited_url_accepted=inherited,
            new_discovery_accepted=len(stamps)-inherited, requests=requests,
            successful_requests=successes, returned_links_with_repeats=results, captchas=captchas,
            first_http_record_at=first_http,last_http_record_at=last_http,
            accepted_per_request_excluding_inherited_urls=measured_yield,
            full_window_elapsed=now>=deadline,
            observed_last_5m=sum(t>end-300 for t in stamps),
            observed_last_30m=sum(t>end-1800 for t in stamps),
            observed_peak_5m=peak_window(stamps,300,started,end),
            observed_peak_15m=peak_window(stamps,900,started,end),
            arithmetic_daily_average=len(stamps)*86400/max(1,end-started))
        report['conditional_scenarios'] = {
            '80_hosts_30s_no_cooldown_same_yield': 80*86400/30*measured_yield,
            '4rps_no_cooldown_same_yield': 4*86400*measured_yield,
            '50000_serper_queries_same_yield_unvalidated': 50000*measured_yield,
            'requests_needed_for_million_at_same_yield': 1000000/measured_yield if measured_yield else None,
        }
        report['limitations'] = [
            'No verified daily maximum: current observation is shorter than 24 hours.',
            'Yield excludes inherited pending URLs; remains cohort- and provider-dependent, with completion lag.',
            'No-cooldown scenarios contradict current blocked exits and are not sustainable forecasts.',
            'Peak windows include draining and inherited work; cannot be multiplied into a promised daily capacity.',
            'SERP API scenario has not been tested; 50000 queries is a total credit budget, not a daily entitlement.',
        ]
        return report
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(estimate(args.directory), ensure_ascii=False, indent=2))
