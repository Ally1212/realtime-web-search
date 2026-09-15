import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from realtime.experiment_store import ExperimentStore
from realtime.google_recovery import next_delay, transition, lease_query, write_summary, recovery_query, ready_hosts


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires TEST_DATABASE_URL')
class RecoveryDatabaseTests(unittest.TestCase):
    def test_half_open_preserves_circuit_cooldown_and_alias_exclusivity(self):
        from concurrent.futures import ThreadPoolExecutor
        from uuid import uuid4
        from realtime.campaign_store import CampaignStore
        from realtime.google_recovery import reserve_probe
        store=CampaignStore(os.environ['TEST_DATABASE_URL'])
        source='recovery_test_'+uuid4().hex
        aliases=[uuid4().hex.ljust(64,'0') for _ in range(2)]
        try:
            store.acquire_discovery_slot(source,1)
            with store.connect() as db:
                db.execute("UPDATE discovery_source_runtime SET circuit_until=now()+interval '30 minutes',next_request_at=now()+interval '10 seconds' WHERE source=%s",(source,))
                before=db.execute('SELECT circuit_until,next_request_at FROM discovery_source_runtime WHERE source=%s',(source,)).fetchone()
            for key in aliases:store.record_google_proxy_result(key,success=False,cooldown_seconds=21600,error_code='google_captcha')
            with store.connect() as db:
                cooldowns={r['proxy_key_hash']:r['cooldown_until'] for r in db.execute('SELECT proxy_key_hash,cooldown_until FROM google_proxy_sessions WHERE proxy_key_hash=ANY(%s)',(aliases,)).fetchall()}
            with ThreadPoolExecutor(max_workers=4) as ex:
                results=list(ex.map(lambda _:reserve_probe(store,aliases,source_name=source),range(4)))
            self.assertEqual(sum(r['allowed'] for r in results),1)
            allowed=next(r for r in results if r['allowed'])
            self.assertTrue(allowed['half_open_global'])
            self.assertGreater(allowed['wait'],8)
            with store.connect() as db:
                after=db.execute('SELECT circuit_until,next_request_at FROM discovery_source_runtime WHERE source=%s',(source,)).fetchone()
                self.assertEqual(after['circuit_until'],before['circuit_until'])
                self.assertGreater(after['next_request_at'],before['next_request_at'])
                for r in db.execute('SELECT * FROM google_proxy_sessions WHERE proxy_key_hash=ANY(%s)',(aliases,)).fetchall():
                    self.assertEqual(r['cooldown_until'],cooldowns[r['proxy_key_hash']])
                    self.assertEqual(r['captcha_count'],1)
            self.assertFalse(store.reserve_google_proxy(aliases[0],'zh-CN',30)[0])
        finally:
            with store.connect() as db:
                db.execute('DELETE FROM discovery_source_runtime WHERE source=%s',(source,))
                db.execute('DELETE FROM google_proxy_sessions WHERE proxy_key_hash=ANY(%s)',(aliases,))


class RecoveryTests(unittest.TestCase):
    def test_recovered_host_is_not_starved_by_confirmations(self):
        states=[{'phase':'recovered','due':0}, {'phase':'confirming','due':190},
                {'phase':'waiting','timed_challenge':True,'due':195}]
        self.assertIs(ready_hosts(states,200)[0],states[0])

    def test_timed_short_trial_precedes_old_initial_checks_and_confirmations(self):
        states=[{'phase':'initial','due':0}, {'phase':'confirming','due':10},
                {'phase':'waiting','timed_challenge':True,'due':60},
                {'phase':'waiting','timed_challenge':True,'due':200}]
        ready=ready_hosts(states,100)
        self.assertEqual(ready,[states[2],states[1],states[0]])

    def test_probe_control_does_not_enable_duplicate_production_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ExperimentStore(Path(directory),create=True)
            try:
                row=recovery_query(store,self.state())
                self.assertEqual(row['page'],1)
                self.assertEqual(recovery_query(store,self.state()|{'streak':2})['page'],3)
                self.assertIsNone(store.due_query('site',time.time()))
                self.assertEqual(store.db.execute('SELECT enabled FROM queries WHERE id=?',(row['id'],)).fetchone()[0],0)
            finally:store.db.close()

    def state(self):
        return {'phase':'waiting','streak':0,'base_delay':60,'delay':60,'last_challenge_at':100}

    def test_recovery_requires_three_successes_and_keeps_initial_wait(self):
        state=transition(self.state(),160,True,'')
        self.assertEqual(state['phase'],'confirming')
        self.assertNotIn('confirmed_recovery_seconds',state)
        state=transition(state,220,True,'')
        state=transition(state,280,True,'')
        self.assertEqual(state['phase'],'recovered')
        self.assertEqual(state['confirmed_recovery_seconds'],60)

    def test_new_challenge_resets_confirmation_and_increases_delay(self):
        state=transition(self.state(),160,True,'')
        state=transition(state,220,False,'google_captcha')
        self.assertEqual(state['streak'],0)
        self.assertEqual(state['last_challenge_at'],220)
        self.assertEqual(state['due'],280)
        self.assertEqual(state['delay'],120)
        self.assertEqual(next_delay(21600),21600)

    def test_timeout_does_not_invent_a_new_captcha_timestamp(self):
        state=transition(self.state(),160,False,'google_timeout')
        self.assertEqual(state['last_challenge_at'],100)
        self.assertEqual(state['due'],460)

    def test_recovered_host_restarts_its_assigned_cohort_after_new_captcha(self):
        state=self.state()|{'phase':'recovered','streak':4,'delay':3600}
        updated=transition(state,200,False,'google_captcha')
        self.assertEqual(updated['due'],260)
        self.assertEqual(updated['delay'],120)

    def test_query_lease_excludes_concurrent_main_worker_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ExperimentStore(Path(directory),create=True)
            try:
                store.add_query('site','site:example.com AI','zh','AI',pages=1)
                now=time.time()
                self.assertIsNotNone(lease_query(store,now))
                self.assertIsNone(store.due_query('site',now))
                self.assertIsNone(lease_query(store,now))
            finally:store.db.close()

    def test_summary_keeps_incomplete_reservations_out_of_success_rate(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db=sqlite3.connect(':memory:')
            db.executescript('CREATE TABLE attempts(id INTEGER PRIMARY KEY,data TEXT); CREATE TABLE hosts(data TEXT);')
            db.execute('INSERT INTO attempts(data) VALUES(?)',(json.dumps({'started':100}),))
            result=write_summary(db,Path(directory)/'summary.json')
            self.assertEqual(result['reserved_attempts'],1)
            self.assertEqual(result['completed_attempts'],0)
            self.assertIsNone(result['minimum_confirmed_observed_seconds'])
            db.close()


if __name__=='__main__':unittest.main()
