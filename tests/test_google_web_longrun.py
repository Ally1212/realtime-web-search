import unittest
from scripts.google_web_longrun import result,best,PLANS

class LongRunMetricTests(unittest.TestCase):
    def fixture(self):
        p=PLANS[0]
        return dict(at='2026-10-10T11:30:00+00:00',started_at='2026-10-10T10:00:00+00:00',requests=[dict(source='google_web',attempts=1000,successful=950,captcha=5,limited=20)],new_links=9000,hourly=[],pages={'n':3},effective={**p,'providers':['openserp']},settings=dict(content_fetch_enabled=False,continuous_sources=['google_web']),active_manual_tasks=0,active_supply=[dict(sources=['google_web'])],worker=dict(running=True,oom=False,restarts=0),source_runtime=[],queue={},host={})

    def test_elapsed_includes_waits_and_retries(self):
        d=self.fixture();r=result(d,PLANS[0],{'n':3})
        self.assertEqual(r['new_links_per_hour'],6000)
        self.assertEqual(r['elapsed_seconds'],5400)
        self.assertTrue(r['eligible'])

    def test_peak_short_window_and_contaminated_windows_cannot_win(self):
        base=self.fixture();good=result(base,PLANS[0],{'n':3})
        for change in [dict(at='2026-10-10T10:30:00+00:00'),dict(pages={'n':4}),dict(active_manual_tasks=1),dict(settings=dict(content_fetch_enabled=True,continuous_sources=['google_web'])),dict(requests=[dict(source='google_news',attempts=10)])]:
            d=dict(base,**change);d['new_links']=999999
            candidate=result(d,PLANS[0],{'n':3})
            self.assertFalse(candidate['eligible'])
            compared=best([candidate,good])
            if change.get('at'):
                # A short observation cannot qualify as a long test by itself.
                self.assertIsNone(best([candidate]))
            else:
                self.assertEqual(compared['new_links_per_hour'],good['new_links_per_hour'])

    def test_repeated_configuration_uses_weighted_average_not_best_segment(self):
        first=result(self.fixture(),PLANS[0],{'n':3})
        d=self.fixture();d['new_links']=3000
        repeated=result(d,dict(PLANS[0],key='baseline-repeat'),{'n':3})
        winner=best([first,repeated])
        self.assertEqual(winner['new_links_per_hour'],4000)
        self.assertEqual(winner['elapsed_seconds'],10800)
        self.assertEqual(len(winner['segments']),2)

    def test_mismatched_configuration_and_unhealthy_worker_are_ineligible(self):
        for key,value in [('slots',12),('pages',2),('max_rps',4),('providers',['wml'])]:
            d=self.fixture();d['effective'][key]=value
            self.assertFalse(result(d,PLANS[0],{'n':3})['eligible'])
        d=self.fixture();d['worker']['restarts']=1
        self.assertFalse(result(d,PLANS[0],{'n':3})['eligible'])
