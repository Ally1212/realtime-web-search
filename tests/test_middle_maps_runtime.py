import unittest
from unittest.mock import Mock, patch
from realtime.middle_maps_runtime import MapsPhases
from realtime.product import ProductError


class MapsPhasesTests(unittest.TestCase):
    def fixture(self):
        middle, collector, save, search = Mock(), Mock(), Mock(), Mock()
        runtime = MapsPhases(middle=middle, collector=collector, save=save, search=search)
        job = dict(id='job', account_id='tenant', parameters={}, idempotency_key='retry')
        message = dict(dataset_id='google_maps_raw', source=dict(source_platform='google_maps',
                       source_name='searchflow.maps.query', source_record_key='maps-query:job', payload_hash='hash'),
                       content=dict(platform_payload={'places': []}))
        receipt = dict(dataset_id='google_maps_raw', source_platform='google_maps',
                       source_name='searchflow.maps.query', source_record_key='maps-query:job',
                       status='accepted', document_id='doc', version_id='ver')
        state = dict(middle_query_id='lq', platform_task_ids=['task'], task={}, message=message)
        state['task'] = {'id': 'task'}
        middle.status_for_request.return_value = dict(platform_status='collecting')
        collector.post.return_value = dict(receipts=[receipt])
        return runtime, job, state, receipt

    def test_receipt_is_saved_before_complete_and_restart_does_not_recollect(self):
        runtime, job, state, receipt = self.fixture()
        saved = []
        runtime.save.side_effect = lambda j, s: saved.append(dict(s))
        self.assertIsNone(runtime.advance(job, state))
        self.assertEqual(saved[0]['receipt'], receipt)
        runtime.search.assert_not_called()
        runtime.collector.upload_realtime.assert_not_called()
        runtime.middle.status_for_request.return_value = dict(platform_status='succeeded')
        runtime.middle.results_page.return_value = dict(items=[dict(document=dict(id='doc'))])
        runtime.middle.read_snapshot.return_value = {'places': []}
        result = runtime.advance(job, saved[0])
        self.assertTrue(result['middle_verified'])
        runtime.middle.read_snapshot.assert_called_once_with('doc', 'ver', expected_payload={'places': []}, expected_hash='hash')

    def test_queued_never_completes_or_reads(self):
        runtime, job, state, receipt = self.fixture()
        receipt['status'] = 'queued'
        self.assertIsNone(runtime.advance(job, state))
        runtime.save.assert_not_called()
        runtime.middle.read_snapshot.assert_not_called()
        self.assertEqual(runtime.collector.post.call_count, 2)

    def test_wrong_result_never_unlocks(self):
        runtime, job, state, receipt = self.fixture()
        state['receipt'] = receipt
        runtime.middle.status_for_request.return_value = dict(platform_status='succeeded')
        runtime.middle.results_page.return_value = dict(items=[dict(document=dict(id='other'))])
        with self.assertRaises(ProductError):
            runtime.advance(job, state)
        runtime.middle.read_snapshot.assert_not_called()

    def test_initial_control_signal_prevents_collection_and_upload(self):
        for signal in ('cancel', 'stop', 'pause'):
            with self.subTest(signal=signal):
                runtime, job, state, _ = self.fixture()
                state.pop('message')
                runtime.collector.post.return_value = {'control_signal': signal}
                with patch('realtime.middle_maps_runtime.task_parameters', return_value={'location': {'lat': 1, 'lng': 2}}):
                    with self.assertRaises(ProductError) as error:
                        runtime.advance(job, state)
                self.assertEqual(error.exception.code, 'middle_controlled')
                runtime.search.assert_not_called()
                runtime.collector.upload_realtime.assert_not_called()
                runtime.save.assert_not_called()

    def test_provider_failure_is_durable_and_reported_without_zero_snapshot(self):
        for code in ('maps_timeout', 'maps_invalid_response'):
            with self.subTest(code=code):
                runtime, job, state, _ = self.fixture()
                state.pop('message')
                runtime.collector.post.return_value = {}
                runtime.search.side_effect = ProductError(code, 'provider failure', 502)
                with patch('realtime.middle_maps_runtime.task_parameters', return_value={'location': {'lat': 1, 'lng': 2}}):
                    self.assertIsNone(runtime.advance(job, state))
                saved = runtime.save.call_args.args[1]
                self.assertEqual(saved['collection_error'], code)
                self.assertNotIn('message', saved)
                # Unknown delivery of /fail preserves the durable failure and
                # never repeats the source search or submits a zero snapshot.
                runtime.collector.post.side_effect = ProductError('middle_transport_unknown', 'unknown', 503)
                with self.assertRaises(ProductError):
                    runtime.advance(job, saved)
                runtime.collector.post.side_effect = None
                with self.assertRaises(ProductError) as error:
                    runtime.advance(job, saved)
                self.assertEqual(error.exception.code, code)
                self.assertTrue(runtime.collector.post.call_args.args[0].endswith('/task/fail'))
                self.assertFalse(runtime.collector.post.call_args.args[1]['retry'])
                runtime.search.assert_called_once()
                runtime.collector.upload_realtime.assert_not_called()
