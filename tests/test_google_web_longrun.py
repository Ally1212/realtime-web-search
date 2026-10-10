import unittest
from scripts.google_web_longrun import result,best,PLANS

class LongRunMetricTests(unittest.TestCase):
    def fixture(self):
        p=PLANS[1]
        return dict(at='2026-10-10T11:30:00+00:00',started_at='2026-10-10T10:00:00+00:00',requests=[dict(source='google_web',attempts=1000,successful=950,captcha=5,limited=20)],request_pages=[dict(page=1,attempts=1000,successful=950)],new_links=9000,hourly=[],pages={'n':3},effective={**p,'providers':['openserp']},settings=dict(content_fetch_enabled=False,continuous_sources=['google_web']),active_manual_tasks=0,active_supply=[dict(sources=['google_web'])],worker=dict(running=True,oom=False,restarts=0),source_runtime=[],queue={},host={})

    def test_depth_trial_requires_actual_successful_deep_pages_and_batch_matches(self):
        plan=PLANS[4];d=self.fixture();d['effective']={**plan,'providers':['openserp']}
        unseen=result(d,plan,{'n':3})
        self.assertTrue(unseen['configuration_matches'])
        self.assertFalse(unseen['page_depth_observed'])
        self.assertFalse(unseen['eligible'])
        self.assertIsNone(best([unseen]))
        d['request_pages']=[dict(page=1,attempts=900,successful=850),dict(page=2,attempts=100,successful=100)]
        actual=result(d,plan,{'n':3})
        self.assertTrue(actual['eligible'])
        weighted=best([unseen,actual])
        self.assertTrue(weighted['page_depth_observed'])
        self.assertEqual(sum(p['attempts'] for p in weighted['page_coverage']),weighted['requests']['attempts'])
        self.assertEqual(sum(p['successful'] for p in weighted['page_coverage']),weighted['requests']['successful'])
        d['effective']['pages_per_batch']=1
        self.assertFalse(result(d,plan,{'n':3})['configuration_matches'])

    def test_elapsed_includes_waits_and_retries(self):
        d=self.fixture();r=result(d,PLANS[1],{'n':3})
        self.assertEqual(r['new_links_per_hour'],6000)
        self.assertEqual(r['elapsed_seconds'],5400)
        self.assertTrue(r['eligible'])

    def test_legacy_baseline_data_is_retained_but_cannot_restore_unbounded_queue(self):
        good=result(self.fixture(),PLANS[1],{'n':3})
        d=self.fixture();d['new_links']=900000
        legacy=result(d,PLANS[0],{'n':3})
        self.assertEqual(legacy['new_links'],900000)
        self.assertTrue(legacy['pure_google_web'])
        self.assertFalse(legacy['queue_safe'])
        self.assertFalse(legacy['eligible'])
        self.assertEqual(best([legacy,good])['new_links_per_hour'],6000)
        # Pre-gate artifacts cannot bypass the rule with their old eligible flag.
        legacy.pop('queue_safe');legacy['eligible']=True
        self.assertIsNone(best([legacy]))

    def test_peak_short_window_and_contaminated_windows_cannot_win(self):
        base=self.fixture();good=result(base,PLANS[1],{'n':3})
        for change in [dict(at='2026-10-10T10:30:00+00:00'),dict(pages={'n':4}),dict(active_manual_tasks=1),dict(settings=dict(content_fetch_enabled=True,continuous_sources=['google_web'])),dict(requests=[dict(source='google_news',attempts=10)])]:
            d=dict(base,**change);d['new_links']=999999
            candidate=result(d,PLANS[1],{'n':3})
            self.assertFalse(candidate['eligible'])
            compared=best([candidate,good])
            if change.get('at'):
                # A short observation cannot qualify as a long test by itself.
                self.assertIsNone(best([candidate]))
            else:
                self.assertEqual(compared['new_links_per_hour'],good['new_links_per_hour'])

    def test_repeated_configuration_uses_weighted_average_not_best_segment(self):
        first=result(self.fixture(),PLANS[1],{'n':3})
        d=self.fixture();d['new_links']=3000
        repeated=result(d,dict(PLANS[1],key='baseline-repeat'),{'n':3})
        winner=best([first,repeated])
        self.assertEqual(winner['new_links_per_hour'],4000)
        self.assertEqual(winner['elapsed_seconds'],10800)
        self.assertEqual(len(winner['segments']),2)

    def test_mismatched_configuration_and_unhealthy_worker_are_ineligible(self):
        for key,value in [('slots',12),('pages',2),('max_rps',4),('providers',['wml'])]:
            d=self.fixture();d['effective'][key]=value
            self.assertFalse(result(d,PLANS[1],{'n':3})['eligible'])
        d=self.fixture();d['worker']['restarts']=1
        self.assertFalse(result(d,PLANS[1],{'n':3})['eligible'])
