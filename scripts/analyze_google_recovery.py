"""Read-only recovery evidence, separating old cooldowns from timed challenges."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
import time


def analyze(directory):
    path=directory/'recovery/audit.sqlite3'
    if not path.exists():return {'present':False}
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        db.execute('BEGIN')
        rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM attempts ORDER BY id')]
    finally:db.close()
    runs=defaultdict(list);streaks=defaultdict(list);episodes=[];captcha_seen={};timed_trials=[]
    for row in rows:
        group=row['group_hash'];runs[group].append(row)
        if not row.get('finished'):
            streaks[group]=[];continue
        # A confirmation request is not a fresh trial of the cooldown interval.
        # Count actual request-start waits after directly observed CAPTCHAs only.
        if group in captcha_seen and row.get('phase_before')=='waiting':
            timed_trials.append({'group_hash':group,'wait_seconds':row['started']-captcha_seen[group],
                                 'success':bool(row['success']),'error':row.get('error',''),
                                 'dispatch_lateness_seconds':row.get('dispatch_lateness_seconds')})
        if row.get('error')=='google_captcha':captcha_seen[group]=row['finished']
        if not row.get('success'):
            streaks[group]=[];continue
        streaks[group].append(row)
        if len(streaks[group])==3:
            first=streaks[group][0]
            episodes.append({'group_hash':group,'first_success_at':first['started'],
                             'confirmed_at':row['finished'],
                             'wait_seconds':first['seconds_since_last_challenge'],
                             'challenge_timed_in_probe':group in captcha_seen,
                             'request_spacing_seconds':[streaks[group][i]['started']-streaks[group][i-1]['started'] for i in (1,2)]})
    timed=[e for e in episodes if e['challenge_timed_in_probe']]
    for episode in episodes:
        followup=[r for r in runs[episode['group_hash']] if r.get('finished') and r['started']>episode['confirmed_at']]
        reblock=next((r for r in followup if r.get('error') in {'google_captcha','google_http_403','google_http_429'}),None)
        episode['followup_requests']=len(followup)
        episode['reblocked_at']=reblock['finished'] if reblock else None
        episode['observed_followup_seconds']=max((r['finished']-episode['confirmed_at'] for r in followup),default=0)
    complete=[r for r in rows if r.get('finished')]
    main=sqlite3.connect((directory/'experiment.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        main.execute('BEGIN')
        deadline=json.loads(main.execute("SELECT value FROM settings WHERE key='deadline'").fetchone()[0])
        introduced=set()
        for search_id in {r['search_id'] for r in complete if r.get('search_id')}:
            search=main.execute('SELECT finished,results FROM searches WHERE id=?',(search_id,)).fetchone()
            if not search:continue
            for item in json.loads(search[1]):
                found=main.execute('SELECT first_seen FROM urls WHERE url=?',(item['url'],)).fetchone()
                if found and found[0]==search[0]:introduced.add(item['url'])
        introduced=sorted(introduced);delivered=0
        for offset in range(0,len(introduced),500):
            batch=introduced[offset:offset+500];marks=','.join('?' for _ in batch)
            delivered+=main.execute("SELECT count(*) FROM documents d JOIN outbox o ON o.document_id=d.id "
                "WHERE d.classification='new' AND d.quality='[]' AND o.status='accepted' AND d.finished<=? AND o.finished<=? "
                f"AND d.url IN ({marks})",[deadline,deadline,*batch]).fetchone()[0]
    finally:main.close()
    return {'present':True,'observed_at':time.time(),'reserved_attempts':len(rows),
            'completed_attempts':len(complete),'successful_searches':sum(r['success'] for r in complete),
            'returned_links_with_repeats':sum(r.get('results',0) for r in complete),
            'whale_accepted_from_urls_first_introduced_by_probe':delivered,
            'errors':dict(Counter(r['error'] for r in complete if r.get('error'))),
            'confirmed_episodes':episodes,
            'timed_cooldown_trials':timed_trials,
            'timed_cooldown_trial_count':len(timed_trials),
            'minimum_confirmed_wait_seconds':min((e['wait_seconds'] for e in episodes),default=None),
            'maximum_observed_confirmed_wait_seconds':max((e['wait_seconds'] for e in episodes),default=None),
            'minimum_confirmed_after_timed_captcha_seconds':min((e['wait_seconds'] for e in timed),default=None),
            'default_cooldown_proven':False,
            'interpretation':'An observed success bounds recovery from above; it does not prove a shorter interval fails. Initial cooldown ages are inferred from legacy six-hour records. No universal recovery guarantee or statistically validated default yet.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);args=parser.parse_args()
    print(json.dumps(analyze(args.directory),ensure_ascii=False,indent=2))
