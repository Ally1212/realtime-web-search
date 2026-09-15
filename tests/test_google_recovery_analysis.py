import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from realtime.experiment_store import ExperimentStore
from scripts.analyze_google_recovery import analyze


class RecoveryAnalysisTests(unittest.TestCase):
    def test_timed_recovery_and_receipts_exclude_preexisting_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);store=ExperimentStore(directory,create=True)
            try:
                store.set('deadline',1000)
                with store.db:
                    store.db.execute("INSERT INTO searches(id,finished,results) VALUES(1,100,?)",(json.dumps([{'url':'https://new.example/ai'},{'url':'https://old.example/ai'}]),))
                    for i,(url,first) in enumerate([('https://new.example/ai',100),('https://old.example/ai',20)],1):
                        store.db.execute('INSERT INTO urls(url,first_seen) VALUES(?,?)',(url,first))
                        store.db.execute("INSERT INTO documents(id,url,finished,quality,classification) VALUES(?,?,200,'[]','new')",(i,url))
                        store.db.execute("INSERT INTO outbox(document_id,status,finished) VALUES(?,'accepted',210)",(i,))
            finally:store.db.close()
            (directory/'recovery').mkdir()
            audit=sqlite3.connect(directory/'recovery/audit.sqlite3')
            audit.execute('CREATE TABLE attempts(id INTEGER PRIMARY KEY,data TEXT)')
            rows=[{'group_hash':'a','started':5,'finished':10,'success':False,'error':'google_captcha'}]
            for started in (70,140,210):
                rows.append({'group_hash':'a','started':started,'finished':started+2,'success':True,'error':'',
                             'phase_before':'waiting' if started==70 else 'confirming',
                             'seconds_since_last_challenge':started-10,'results':2,'search_id':1})
            for row in rows:audit.execute('INSERT INTO attempts(data) VALUES(?)',(json.dumps(row),))
            audit.commit();audit.close()
            report=analyze(directory)
            self.assertEqual(report['minimum_confirmed_after_timed_captcha_seconds'],60)
            self.assertEqual(report['whale_accepted_from_urls_first_introduced_by_probe'],1)
            self.assertFalse(report['default_cooldown_proven'])
            self.assertEqual(len(report['confirmed_episodes']),1)
            self.assertEqual(report['timed_cooldown_trial_count'],1)
            self.assertEqual(report['timed_cooldown_trials'][0]['wait_seconds'],60)
            self.assertEqual(report['confirmed_episodes'][0]['followup_requests'],0)


if __name__=='__main__':unittest.main()
