import importlib.util
import json
import os
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control = load('google_hourly_control')
series = load('google_hourly_series')


class HourlyEvidenceTests(unittest.TestCase):
    def test_fresh_keyword_slices_are_explicit_and_continuous_does_not_repeat_winner_slice(self):
        plan = dict(keyword_offset=90248)
        self.assertEqual(series.run_keyword_offset(plan,0),90248)
        self.assertEqual(series.run_keyword_offset(plan,3,continuous=True,
            series=dict(continuous_keyword_offset=105894)),105894)
        self.assertEqual(series.run_keyword_offset({},2),30000)
        self.assertEqual(series.run_keyword_offset(plan,3,continuous=True),45000)
        for invalid in (-1,120000,'90248',True):
            with self.assertRaises(ValueError):series.run_keyword_offset(dict(keyword_offset=invalid),0)

    def test_control_imports_frozen_package_instead_of_live_web_package(self):
        # Match python stdin import rules with a conflicting live cwd package.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / 'live'
            frozen = root / 'frozen'
            neutral = root / 'neutral'
            state = root / 'run'
            for directory in (live, frozen, neutral, state):
                directory.mkdir()
            for directory, marker in ((live, 'live'), (frozen, 'frozen')):
                (directory / 'probe_package.py').write_text('MARKER=' + repr(marker))
            (state / 'manifest.json').write_text(json.dumps(dict(provider='openserp', run='test-run')))
            (root / 'control.py').write_text(
                'import json,probe_package; print(json.dumps(dict(marker=probe_package.MARKER)))')

            def invoke(args, **kwargs):
                workdir = neutral if '-w' in args and args[args.index('-w') + 1] == '/tmp' else live
                return subprocess.run([sys.executable, '-'], input=kwargs['input'], text=True,
                                      capture_output=True, check=True, cwd=workdir,
                                      env={**os.environ, 'PYTHONPATH': str(frozen)})

            with patch.object(series, 'STATE', root), patch.object(series, 'command', side_effect=invoke):
                self.assertEqual(series.control(state, 'tick'), dict(marker='frozen'))

    def test_control_failure_keeps_location_without_credentials_or_output(self):
        error = subprocess.CalledProcessError(1, ['docker', 'secret-command-argument'],
            output='stdout-secret', stderr='''Traceback (most recent call last):
  File "/app/realtime/campaign_store.py", line 1042, in put_campaign
    sql_with_secret_password
psycopg.errors.DeadlockDetected: password=private-secret http://user:pass@proxy
''')
        result = series.control_failure_summary('test-run', 'tick', error)
        self.assertEqual(result['exception_types'], ['psycopg.errors.DeadlockDetected'])
        self.assertEqual(result['frames'], [dict(file='campaign_store.py', line=1042, function='put_campaign')])
        self.assertEqual(result['returncode'], 1)
        encoded = json.dumps(result)
        for secret in ['private-secret', 'user:pass', 'stdout-secret', 'secret-command-argument', 'sql_with_secret']:
            self.assertNotIn(secret, encoded)

    def test_timeout_failure_summary_handles_partial_byte_stderr(self):
        error = subprocess.TimeoutExpired(['secret-command'], 240, stderr=b'TimeoutError: secret-token')
        result = series.control_failure_summary('test-run', 'tick', error)
        self.assertEqual(result['exception_types'], ['TimeoutError'])
        self.assertEqual(result['timeout_seconds'], 240)
        self.assertIsNone(result['returncode'])
        self.assertNotIn('secret-token', json.dumps(result))

    def test_paused_or_restarted_worker_cannot_verify_collection_uptime(self):
        m = dict(worker_id='same', docker_started_at='2026-10-09T01:00:00Z', worker_restart_count=0)
        info = dict(Id='same', RestartCount=0,
                    State=dict(Running=True, Paused=False, Restarting=False, StartedAt=m['docker_started_at']))
        self.assertTrue(series.worker_matches_manifest(info, m))
        for key in ['Paused', 'Restarting']:
            with self.subTest(key=key):
                info['State'][key] = True
                self.assertFalse(series.worker_matches_manifest(info, m))
                info['State'][key] = False
        info['RestartCount'] = 1
        self.assertFalse(series.worker_matches_manifest(info, m))

    def test_prepared_candidate_cannot_replace_an_already_running_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state/'manifest.json').write_text(json.dumps(dict(status='prepared')))
            with patch.object(series, 'worker_state', return_value={'State': {'Running': True}}), \
                    patch.object(series, 'control') as control_call, patch.object(series, 'compose') as compose_call:
                with self.assertRaisesRegex(RuntimeError, 'refusing to replace'):
                    series.run_one(state)
                control_call.assert_not_called()
                compose_call.assert_not_called()

    def test_interruption_covers_the_gap_since_the_last_real_probe(self):
        from datetime import datetime, timezone
        m = dict(worker_started_at='2026-10-09T01:00:00+00:00',
                 last_verified_running_at='2026-10-09T01:30:00+00:00')
        now = datetime(2026, 10, 9, 4, tzinfo=timezone.utc)
        event = series.interruption_evidence(m, now, 'changed')
        self.assertEqual(event['affected_from'], m['last_verified_running_at'])
        self.assertEqual(event['affected_until'], now.isoformat())
        del m['last_verified_running_at']
        self.assertEqual(series.interruption_evidence(m, now, 'changed')['affected_from'], m['worker_started_at'])

    def test_original_plans_keep_the_original_collection_snapshot(self):
        self.assertEqual(series.collection_code(series.PLANS[0]), series.STATE / 'code' / 'realtime')

    def test_collection_revision_requires_exact_hashes_and_offline_evidence(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory, patch.object(series, 'STATE', Path(directory)):
            root = Path(directory) / 'revisions/browser-v3'
            code = root / 'code/realtime'
            code.mkdir(parents=True)
            source = code / 'probe.py'
            source.write_text('pass\n')
            (root / 'manifest.json').write_text(json.dumps(dict(revision='browser-v3', code_sha256={
                'code/realtime/probe.py': hashlib.sha256(source.read_bytes()).hexdigest()})))
            smoke = root / 'offline-smoke.json'
            smoke.write_text(json.dumps(dict(revision='browser-v3', checks=[dict(passed=True, external_requests=0)])))
            plan = dict(code_revision='browser-v3')
            self.assertEqual(series.collection_code(plan), code)
            smoke.write_text(json.dumps(dict(revision='browser-v3', checks=[dict(passed=True, external_requests=1)])))
            with self.assertRaisesRegex(ValueError, 'offline validation'):
                series.collection_code(plan)
            source.write_text('modified = True\n')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                series.collection_code(plan)

    def test_collection_revision_cannot_escape_the_revision_directory(self):
        with self.assertRaisesRegex(ValueError, 'Invalid collection revision'):
            series.collection_code(dict(code_revision='../../other-project'))

    def test_captcha_code_is_counted_when_old_browser_omitted_page_evidence(self):
        self.assertTrue(control.is_captcha_attempt(dict(classification='error', error_code='google_captcha')))
        self.assertTrue(control.is_captcha_attempt(dict(classification='captcha', error_code=None)))
        for code in ('google_browser_deadline', 'google_javascript_required', 'google_http_429', None):
            self.assertFalse(control.is_captcha_attempt(dict(classification='error', error_code=code)))

    def test_mixed_rss_environment_loads_the_real_config_and_only_google_feed(self):
        env = dict(os.environ, **series.worker_environment(series.PLANS[-1]))
        result = subprocess.run([sys.executable, '-c',
            'import json; from realtime.config import Config; print(json.dumps(Config().discovery_feeds))'],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [['google_news_rss', series.GOOGLE_RSS]])
    def test_docker_nanoseconds_are_portable_to_server_python(self):
        parsed = series.docker_time('2026-10-09T01:54:19.19146509Z')
        self.assertEqual(parsed.isoformat(), '2026-10-09T01:54:19.191465+00:00')
    def test_tracking_variants_and_old_links_cannot_inflate_novelty(self):
        attempts = [dict(result_urls=['https://example.org/a?utm_source=x#part',
                                      'https://example.org/a', 'https://example.org/new'], result_count=3),
                    dict(result_urls=['https://example.org/new#again'], result_count=1)]
        result = control.link_metrics(attempts, {control.url_hash('https://example.org/a')})
        self.assertEqual(result['unique_result_links'], 2)
        self.assertEqual(result['valid_new_links'], 1)

    def test_missing_result_audit_invalidates_a_successful_search(self):
        result = control.link_metrics([dict(result_urls=[], result_count=7)], set())
        self.assertFalse(result['result_url_audit_complete'])
        self.assertEqual(result['valid_new_links'], 0)

    def test_partial_hours_and_interruptions_do_not_enter_comparison(self):
        report = dict(full_hour_observed=False, exclusive_provider=True, result_url_audit_complete=True,
                      body_admission_audit_complete=True)
        self.assertFalse(series.valid_hour(report, []))
        report['full_hour_observed'] = True
        self.assertFalse(series.valid_hour(report, ['worker restarted']))
        self.assertTrue(series.valid_hour(report, []))

    def test_missing_original_body_metadata_cannot_support_a_valid_hour(self):
        report = dict(full_hour_observed=True, exclusive_provider=True, result_url_audit_complete=True)
        self.assertFalse(series.valid_hour(report, []))

    def test_new_revision_body_failures_and_missing_enablement_reject_comparison(self):
        report = dict(full_hour_observed=True, exclusive_provider=True, result_url_audit_complete=True,
                      body_admission_audit_complete=True, body_integrity_valid=True,
                      auxiliary_enablement_valid=True, extended_audit_complete=False)
        # An explicitly visible unfinished request does not falsify body counts.
        self.assertTrue(series.valid_hour(report, []))
        report['body_integrity_valid'] = False
        self.assertFalse(series.valid_hour(report, []))
        report['body_integrity_valid'] = True
        report['auxiliary_enablement_valid'] = False
        self.assertFalse(series.valid_hour(report, []))
        report['body_admission_audit_complete'] = False
        self.assertFalse(series.valid_hour(report, []))

    def test_body_winner_does_not_follow_search_or_link_volume(self):
        rows = [dict(index=0, valid_for_comparison=True, new_qualified_bodies=30,
                     valid_new_links=500, successful_searches=90),
                dict(index=1, valid_for_comparison=True, new_qualified_bodies=60,
                     valid_new_links=100, successful_searches=10),
                dict(index=2, valid_for_comparison=False, new_qualified_bodies=999,
                     valid_new_links=999, successful_searches=999)]
        plans = [dict(key=str(i), sources=['google_web']) for i in range(3)]
        plan, evidence = series.best_plan(rows, plans)
        self.assertEqual(plan['key'], '1')
        self.assertEqual(evidence['new_qualified_bodies'], 60)

    def test_pure_web_selection_excludes_mixed_google_sources(self):
        plans = [dict(key='web', sources=['google_web']),
                 dict(key='mixed', sources=['google_web', 'google_news'])]
        rows = [dict(index=i, valid_for_comparison=True, new_qualified_bodies=100*(i+1),
                     valid_new_links=50, successful_searches=5) for i in range(2)]
        self.assertEqual(series.best_plan(rows, plans)[0]['key'], 'mixed')
        self.assertEqual(series.best_plan(rows, plans, pure=True)[0]['key'], 'web')


if __name__ == '__main__':
    unittest.main()
