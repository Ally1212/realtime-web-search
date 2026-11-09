import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import searchflow_acceptance as qa
from scripts.searchflow_acceptance_report import summarize


class AcceptanceTests(unittest.TestCase):
    def test_watchdog_reconciles_concurrent_stop_without_replaying_post(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'journal.json'
            journal = dict(state='running', tasks=[dict(campaign_id='a')], started_at=datetime.now(timezone.utc).isoformat(), duration_minutes=30)
            qa.write_json(path, journal)
            task = dict(id='a', status='active', sources=['google_web'], requests=2,
                        successful=0, created_at='2020-01-01T00:00:00+00:00')
            replies = [dict(results=[dict(body=dict(tasks=[task]))]),
                       dict(results=[dict(status=400)]),
                       dict(results=[dict(body=dict(tasks=[dict(task, status='stopped')]))])]
            def stop_loop(_):
                qa.write_json(path, dict(journal, state='stopped'))
            with patch.object(qa, 'browser', side_effect=replies) as browser, patch.object(qa.time, 'sleep', side_effect=stop_loop):
                qa.watchdog(1, path)
            posts = [r for call in browser.call_args_list for r in call.args[1] if r.get('method') == 'POST']
            self.assertEqual(len(posts), 1)
            events = json.loads((Path(d)/'watchdog/events.json').read_text())
            self.assertEqual(events[0]['state'], 'already_stopped')

    def test_run_deadline_stops_owned_task_even_if_sampler_is_stuck_stopping(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'journal.json'
            qa.write_json(path,dict(state='stopping',tasks=[dict(campaign_id='a')],started_at='2020-01-01T00:00:00+00:00',duration_minutes=30))
            task=dict(id='a',status='active',sources=['google_web'],requests=0,successful=0,created_at=datetime.now(timezone.utc).isoformat())
            with patch.object(qa,'browser',side_effect=[dict(results=[dict(body=dict(tasks=[task]))]),dict(results=[dict(status=200)])]):
                qa.watchdog(1,path)
            result=json.loads((Path(d)/'watchdog/finished.json').read_text())
            self.assertEqual(result['reason'],'run_deadline')
            self.assertEqual(result['events'][0]['campaign_id'],'a')

    def test_ambiguous_create_is_owned_only_with_matching_creation_digest(self):
        import hashlib
        journal=dict(tasks=[],intents=[dict(idempotency_key='random-task-nonce')])
        tasks=[dict(id='ours',options=dict(_creation_key=hashlib.sha256(b'random-task-nonce').hexdigest())),dict(id='other',options={})]
        self.assertEqual(qa.owned_task_ids(journal,tasks),{'ours'})

    def test_report_excludes_cached_associations_and_unrelated_tasks(self):
        journal = dict(tasks=[dict(campaign_id='a'), dict(campaign_id='b')], started_at='2026-11-09T00:00:00Z')
        tasks = [dict(id='a', sources=['google_web'], status='stopped', requests=4, successful=3, cached=1, new_links=8),
                 dict(id='b', sources=['google_web'], status='stopped', requests=1, successful=1, cached=1, new_links=0),
                 dict(id='other', sources=['google_web'], status='active', requests=100, successful=100, cached=0, new_links=500)]
        result = summarize(journal, tasks)
        self.assertEqual(result['totals']['attempts_without_cache'], 3)
        self.assertEqual(result['totals']['successes_without_cache'], 2)
        self.assertEqual(result['totals']['new_links'], 8)
        self.assertEqual(result['sources']['google_web']['success_rate_without_cache'], 2/3)

    def test_watchdog_only_expires_owned_active_batches(self):
        now = datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc)
        tasks = [dict(id=ident, status=status, created_at=created) for ident, status, created in
                 [('owned', 'active', '2026-11-09T03:26:59+00:00'),
                  ('young', 'active', '2026-11-09T03:29:59+00:00'),
                  ('paused', 'paused', '2026-11-09T02:00:00+00:00'),
                  ('other', 'active', '2026-11-09T02:00:00+00:00')]]
        journal = dict(tasks=[dict(campaign_id=ident) for ident in ('owned', 'young', 'paused')])
        self.assertEqual([t['id'] for t in qa.expired_owned_tasks(journal, tasks, now)], ['owned'])

    def test_source_cadence_and_distinct_web_queries(self):
        plans = [qa.batch_plan(i) for i in range(30)]
        counts = {source: sum(s == source for plan in plans for s, _ in plan)
                  for source in ('google_web', 'google_news', 'google_images', 'google_trends')}
        self.assertEqual(counts, dict(google_web=30, google_news=6, google_images=6, google_trends=2))
        words = [w for plan in plans for s, words in plan if s == 'google_web' for w in words]
        self.assertEqual(len(set(words)), 60)

    def test_duration_and_operator_state_fail_before_post(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                qa.collect(1, 121, Path(d))
            baseline = dict(results=[dict(body=dict(continuous_enabled=True, content_fetch_enabled=False)),
                                     dict(body=dict(settings=dict(uploads_enabled=False, claims_enabled=False)))])
            with patch.object(qa, 'browser', return_value=baseline) as browser, patch.object(qa.signal, 'signal'):
                with self.assertRaisesRegex(RuntimeError, 'operator state'):
                    qa.collect(1, 1, Path(d))
                self.assertEqual(browser.call_count, 1)

    def test_deadline_stops_only_owned_tasks_and_keeps_bounded_batches(self):
        clock = [0.0]
        tasks = [dict(id='unrelated', status='active')]
        posts = []

        def browser(space, requests, destination):
            results = []
            for req in requests:
                path = req['path']
                if path == '/api/collector-settings':
                    body = dict(continuous_enabled=False, content_fetch_enabled=False)
                elif path == '/api/whale/status':
                    body = dict(settings=dict(uploads_enabled=False, claims_enabled=False))
                elif path.startswith('/api/serp/tasks?'):
                    body = dict(tasks=[dict(t) for t in tasks])
                elif req.get('method') == 'POST':
                    posts.append(req)
                    if path == '/api/serp/tasks':
                        self.assertFalse(req['payload']['upload_to_whale'])
                        self.assertEqual(req['payload']['mode'], 'once')
                        self.assertLessEqual(req['payload']['max_successful_searches'], 2)
                        body = dict(campaign_id=str(len(tasks)), status='active')
                        tasks.append(dict(id=body['campaign_id'], status='active'))
                    else:
                        ident = path.split('/')[-2]
                        self.assertNotEqual(ident, 'unrelated')
                        next(t for t in tasks if t['id'] == ident)['status'] = 'stopped'
                        body = dict(status='stopped')
                else:
                    body = {}
                results.append(dict(status=201 if path == '/api/serp/tasks' else 200, body=body))
            return dict(results=results)

        with tempfile.TemporaryDirectory() as d, patch.object(qa, 'browser', side_effect=browser), \
                patch.object(qa.signal, 'signal'), patch.object(qa.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(qa.time, 'sleep', side_effect=lambda n: clock.__setitem__(0, clock[0]+n)):
            journal = qa.collect(1, 1, Path(d))
            self.assertEqual(journal['state'], 'stopped')
            self.assertEqual(clock[0], 60)
            self.assertEqual(len(journal['tasks']), 4)
            self.assertEqual(tasks[0]['status'], 'active')
            self.assertTrue(all(t['status'] == 'stopped' for t in tasks[1:]))
