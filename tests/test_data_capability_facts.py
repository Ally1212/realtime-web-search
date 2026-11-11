import copy
import unittest
from unittest.mock import patch

from realtime.data_capability_facts import VERSION, validate_result
from realtime.product import ProductError


def parameters(capability='trends_history'):
    common = dict(capability=capability, country='US', language='en')
    if capability == 'trends_history':
        return dict(**common, terms=['苹果', 'apple'], start_date='2026-01-01', end_date='2026-01-02', category=0, search_type='web')
    if capability == 'keyword_volume':
        return dict(**common, keywords=['苹果', 'apple', 'apple'], start_month='2026-01', end_month='2026-02', network='google')
    return dict(**common, prompt='Question 苹果', device='desktop')


def metric(value='1', status='available', **kwargs):
    return dict(value=value, status=status, **kwargs)


def result(p=None, provider='fixture'):
    p = p or parameters()
    base = dict(schema_version=VERSION,
                source=dict(provider=provider, api_version='fixture-1', parser_version='fixture-1', source_request_id='req-fixture',
                            source_observed_at=None, unknown_time_reason='fixture source gives no time'),
                requested_at='2026-11-10T12:00:00Z', received_at='2026-11-10T12:00:00Z',
                effective_parameters=copy.deepcopy(p), is_partial=False, is_truncated=False,
                termination_reason='fixture source end marker', warnings=[],
                raw=dict(status='stored', payload={'fixture': '源数据'}, redacted_paths=[], reason=None))
    if p['capability'] == 'trends_history':
        series = dict(id='series-fixture', term_or_topic='苹果', resolved_subject=None,
                      effective_window=dict(start='2026-01-01T00:00:00Z', end='2026-01-03T00:00:00Z'),
                      granularity='day', source_timezone='UTC', scale_id='batch-fixture',
                      measurement_type='relative_index', normalization='source_within_request',
                      comparison_group='compare-fixture', revision='r1', source_fields={}, points=[
                          dict(id='day-1', period_start='2026-01-01T00:00:00Z', period_end='2026-01-02T00:00:00Z',
                               metric=metric('0', 'true_zero'), partial=False, revision='r1', source_ref='rows/0', source_fields={}),
                          dict(id='day-2', period_start='2026-01-02T00:00:00Z', period_end='2026-01-03T00:00:00Z',
                               metric=metric(None, 'below_threshold', reason='fixture low volume'), partial=False, revision='r1',
                               source_ref='rows/1', source_fields={})])
        base['data'] = dict(series=[series], input_mapping=[
            dict(input_index=0, record_id=series['id'], status='available', reason=None),
            dict(input_index=1, record_id=None, status='not_observed', reason='fixture no data')])
    elif p['capability'] == 'keyword_volume':
        group = dict(id='group-fixture', returned_text='apple', close_variant_group='source-group',
                     measurement_type='rounded', unit='searches', statistic_window=dict(start_month='2025-01', end_month='2025-12'),
                     average=metric('100', rounded=True), monthly=[
                         dict(year=2026, month=1, metric=metric('0', 'true_zero'), source_fields={}),
                         dict(year=2026, month=2, metric=metric(None, 'not_observed', reason='fixture absent month'), source_fields={})],
                     commercial_fields={'cpc': dict(metric=metric('1230000'), currency='USD', unit='micros')}, source_fields={})
        base['data'] = dict(groups=[group], input_mapping=[
            dict(input_index=0, record_id=None, status='not_observed', reason='fixture no data'),
            dict(input_index=1, record_id=group['id'], status='available', reason=None),
            dict(input_index=2, record_id=group['id'], status='available', reason=None)])
    else:
        base['data'] = dict(answer=dict(id='answer-fixture', channel='aio' if p['capability'] == 'aio_answer' else 'ai_mode',
                                      state='complete', answer_text='苹果 answer', model=None,
                                      blocks=[dict(id='block-1', position=0, type='paragraph', text='苹果 answer', structure={}, source_fields={})],
                                      citations=[dict(id='citation-1', url='https://example.com/a', title=None, role='citation',
                                                      source_ref='fixture/citation/1', mapping_status='source_explicit',
                                                      block_ids=['block-1'], text_ranges=[dict(start=0, end=2)], source_fields={})],
                                      links=[dict(url='https://example.com/ordinary', title='Ordinary link', source_fields={})],
                                      expansion=dict(state='complete', end_reason='fixture expansion end'), source_fields={}))
    return base


class FactContractTests(unittest.TestCase):
    def test_all_domains_detach_and_keep_source_facts(self):
        for capability in ('trends_history', 'keyword_volume', 'aio_answer', 'ai_mode_answer'):
            with self.subTest(capability=capability):
                p = parameters(capability)
                source = result(p)
                clean = validate_result(p, source)
                self.assertEqual(clean, source)
                clean['raw']['payload']['fixture'] = 'changed'
                self.assertEqual(source['raw']['payload']['fixture'], '源数据')

    def test_trend_zero_scale_window_order_and_partial(self):
        p = parameters()
        mutations = [
            lambda s: s['points'][0]['metric'].update(value=None),
            lambda s: s['points'][1]['metric'].update(value='0'),
            lambda s: s['points'][0]['metric'].update(value='101', status='available'),
            lambda s: s['points'][0].update(period_end='2026-01-03T00:00:00Z'),
            lambda s: s['points'].reverse(),
            lambda s: s['points'][0].update(partial=True),
            lambda s: s.update(source_timezone='imaginary/zone'),
            lambda s: s['effective_window'].update(start='2025-12-31T00:00:00Z'),
        ]
        for mutate in mutations:
            r = result(p)
            mutate(r['data']['series'][0])
            with self.subTest(r=r), self.assertRaises(ProductError):
                validate_result(p, r)
        r = result(p)
        r['data']['series'][0]['points'][0]['partial'] = True
        r['is_partial'] = True
        self.assertTrue(validate_result(p, r)['is_partial'])

    def test_volume_mapping_month_missing_zero_and_units(self):
        p = parameters('keyword_volume')
        mutations = [
            lambda r: r['data']['input_mapping'].pop(),
            lambda r: r['data']['input_mapping'][1].update(input_index=2),
            lambda r: r['data']['groups'][0]['monthly'].append(copy.deepcopy(r['data']['groups'][0]['monthly'][0])),
            lambda r: r['data']['groups'][0].update(unit='relative_index'),
            lambda r: r['data']['groups'][0]['monthly'][1]['metric'].update(value='0'),
            lambda r: r['data']['groups'][0]['commercial_fields']['cpc'].update(currency=None),
            lambda r: r['data']['groups'][0]['commercial_fields'].update(seo_difficulty='invented'),
        ]
        for mutate in mutations:
            r = result(p)
            mutate(r)
            with self.subTest(r=r), self.assertRaises(ProductError):
                validate_result(p, r)

    def test_ai_channels_citations_and_expansion_evidence(self):
        p = parameters('aio_answer')
        mutations = [
            lambda a: a.update(channel='ai_mode'),
            lambda a: a['citations'][0].update(role='ordinary_link'),
            lambda a: a['citations'][0].update(block_ids=['unknown']),
            lambda a: a['citations'][0].update(mapping_status='not_covered'),
            lambda a: a['citations'][0]['text_ranges'][0].update(end=100),
            lambda a: a['citations'][0].update(url='https://secret:password@example.com'),
            lambda a: a['expansion'].update(state='expired'),
            lambda a: a['expansion'].update(end_reason=''),
            lambda a: a.update(state='not_observed'),
        ]
        for mutate in mutations:
            r = result(p)
            mutate(r['data']['answer'])
            with self.subTest(r=r), self.assertRaises(ProductError):
                validate_result(p, r)
        r = result(p)
        r['data']['answer'].update(state='partial')
        r['data']['answer']['expansion']['state'] = 'expired'
        r['is_partial'] = True
        self.assertEqual(validate_result(p, r)['data']['answer']['state'], 'partial')
        r['is_partial'] = False
        with self.assertRaises(ProductError):
            validate_result(p, r)

    def test_raw_redaction_secret_size_and_effective_parameters(self):
        p = parameters()
        for raw in ({'token': 'must-not-be-stored'}, {'nested': {'apiKey': 'must-not-be-stored'}}, {'number': float('nan')}):
            r = result(p)
            r['raw']['payload'] = raw
            with self.subTest(raw=raw), self.assertRaises(ProductError):
                validate_result(p, r)
        r = result(p)
        r['effective_parameters']['category'] = False
        with self.assertRaises(ProductError):
            validate_result(p, r)
        r = result(p)
        with patch('realtime.data_capability_facts.MAX_BYTES', 100):
            with self.assertRaises(ProductError) as e:
                validate_result(p, r)
            self.assertEqual(e.exception.status, 413)
        r['raw'] = dict(status='redacted', payload={'safe': 'value'}, redacted_paths=['/token'], reason='short-lived handle removed')
        self.assertEqual(validate_result(p, r)['raw']['status'], 'redacted')
        r['raw'] = dict(status='not_retained', payload=None, redacted_paths=[], reason='fixture license disallows raw retention')
        self.assertIsNone(validate_result(p, r)['raw']['payload'])

    def test_absence_refusal_expansion_failure_and_empty_numeric_sets(self):
        p = parameters('aio_answer')
        for state, expansion in (('not_observed', 'not_required'), ('refused', 'complete'),
                                 ('expired', 'expired'), ('expansion_failed', 'failed'), ('source_error', 'failed')):
            r = result(p)
            a = r['data']['answer']
            a.update(state=state, answer_text=None, blocks=[], citations=[])
            a['expansion']['state'] = expansion
            r['is_partial'] = state in ('expired', 'expansion_failed', 'source_error')
            with self.subTest(state=state):
                self.assertEqual(validate_result(p, r)['data']['answer']['state'], state)
        r = result(p)
        r['data']['answer'].update(state='refused', citations=[])
        self.assertEqual(validate_result(p, r)['data']['answer']['answer_text'], '苹果 answer')
        for capability, key in (('trends_history', 'series'), ('keyword_volume', 'groups')):
            p = parameters(capability)
            r = result(p)
            r['data'][key] = []
            for m in r['data']['input_mapping']:
                m.update(record_id=None, status='not_observed', reason='fixture source empty')
            with self.subTest(capability=capability):
                self.assertEqual(validate_result(p, r)['data'][key], [])

    def test_non_json_keys_nul_and_implicit_type_coercion_rejected(self):
        p = parameters()
        for raw in ({1: 'cannot silently stringify a key'}, {'text': 'NUL\x00'}, {'tuple': (1, 2)}):
            r = result(p)
            r['raw']['payload'] = raw
            with self.subTest(raw=raw), self.assertRaises(ProductError):
                validate_result(p, r)

    def test_source_rounded_zero_is_not_promoted_to_true_zero(self):
        p = parameters('keyword_volume')
        r = result(p)
        r['data']['groups'][0]['average'] = metric('0', 'available', rounded=True)
        clean = validate_result(p, r)
        self.assertEqual(clean['data']['groups'][0]['average'], metric('0', 'available', rounded=True))
