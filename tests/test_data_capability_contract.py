import copy
import unittest
from datetime import datetime, timezone

from realtime.data_capability_contract import admit_request, request_digest, validate_request
from realtime.product import ProductError


class DataCapabilityContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = dict(capability='keyword_volume', country='us', language='en',
                            keywords=['apple', 'apple', '苹果'], start_month='2025-01',
                            end_month='2026-01', network='google')
        self.grant = dict(capability='keyword_volume', status='approved', provider='fixture',
                          license_id='fixture-only', allowed_use='isolated_test', retention_days=1,
                          billing_unit='keyword', currency='USD', maximum_cost='0.20',
                          expires_at='2027-01-01T00:00:00Z', validate_parameters=lambda p: None)
        self.now = datetime(2026, 11, 10, tzinfo=timezone.utc)

    def test_input_mapping_and_canonical_hash(self):
        before = copy.deepcopy(self.payload)
        p = validate_request(self.payload)
        self.assertEqual(p['keywords'], ['apple', 'apple', '苹果'])
        self.assertEqual(p['country'], 'US')
        self.assertEqual(request_digest(p), request_digest(self.payload))
        self.assertEqual(self.payload, before)
        self.assertNotEqual(request_digest(p), request_digest({**p, 'keywords': ['苹果', 'apple']}))

    def test_preserves_original_query_and_prompt(self):
        p = {**self.payload, 'keywords': [' apple ', '苹果']}
        self.assertEqual(validate_request(p)['keywords'][0], ' apple ')
        self.assertNotEqual(request_digest(p), request_digest({**p, 'keywords': ['apple', '苹果']}))
        ai = dict(capability='aio_answer', country='US', language='en', prompt='  question\n', device='desktop')
        self.assertEqual(validate_request(ai)['prompt'], '  question\n')

    def test_rejects_silent_downgrades_and_invalid_windows(self):
        for change in ({'device': 'desktop'}, {'start_month': '2026-02'},
                       {'end_month': '2026-13'}, {'keywords': []}, {'keywords': ['']},
                       {'keywords': ['x'] * 101}, {'country': 'USA'}, {'language': None},
                       {'network': 'unknown'}, {'capability': []}):
            with self.subTest(change=change), self.assertRaises(ProductError):
                validate_request({**self.payload, **change})

    def test_separate_ai_channels_and_explicit_trends_scale_conditions(self):
        ai = dict(capability='aio_answer', country='US', language='en', prompt='Question', device='mobile')
        self.assertNotEqual(request_digest(ai), request_digest({**ai, 'capability': 'ai_mode_answer'}))
        with self.assertRaises(ProductError):
            validate_request({**ai, 'model': 'invented'})
        trends = dict(capability='trends_history', country='US', language='en', terms=['topic:/m/abc'],
                      start_date='2026-01-01', end_date='2026-02-01', category=0, search_type='web')
        self.assertEqual(validate_request(trends), trends)
        for change in ({'category': True}, {'start_date': '2026-02-30'}, {'search_type': 'rss'}):
            with self.subTest(change=change), self.assertRaises(ProductError):
                validate_request({**trends, **change})

    def test_admission_expiry_budget_currency_and_provider_checks(self):
        result = admit_request(self.payload, self.grant, '0.30', 'USD', now=self.now)
        self.assertEqual(result['maximum_cost'], '0.20')
        for grant, budget, currency in (
            (None, '1', 'USD'), ({**self.grant, 'allowed_use': ''}, '1', 'USD'), ({**self.grant, 'status': 'candidate'}, '1', 'USD'),
            ({**self.grant, 'capability': 'aio_answer'}, '1', 'USD'),
            ({**self.grant, 'expires_at': '2026-11-10T00:00:00Z'}, '1', 'USD'),
            ({**self.grant, 'expires_at': '2027-01-01'}, '1', 'USD'),
            ({**self.grant, 'retention_days': 0}, '1', 'USD'),
            (self.grant, 'NaN', 'USD'), (self.grant, '0.19', 'USD'),
            (self.grant, '1', 'EUR'),
        ):
            with self.subTest(grant=grant, budget=budget), self.assertRaises(ProductError):
                admit_request(self.payload, grant, budget, currency, now=self.now)
        def reject(p):
            raise ProductError('unsupported_parameter', 'market not supported')
        with self.assertRaises(ProductError):
            admit_request(self.payload, {**self.grant, 'validate_parameters': reject}, '1', 'USD', now=self.now)
