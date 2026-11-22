import unittest
from unittest.mock import Mock
import requests
from realtime.middle_maps_collector import MapsCollector
from realtime.product import ProductError


class MapsCollectorTests(unittest.TestCase):
    def test_dedicated_registration_and_realtime_upload(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200)
        session.post.return_value.json.return_value = dict(agent_id='agent_maps')
        client = MapsCollector('https://middle.test', 'private-key', session=session)
        client.register()
        payload = session.post.call_args.kwargs['json']
        self.assertEqual(payload['supported_platforms'], ['google_maps'])
        self.assertEqual(payload['supported_task_types'], ['live_query'])
        self.assertIn('condition:location', payload['declared_capabilities'])
        session.post.return_value.json.return_value = [{'status': 'queued'}]
        client.upload_realtime([{'event': 'stable'}])
        self.assertEqual(session.post.call_args.kwargs['headers']['X-Ingest-Class'], 'realtime')
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])

    def test_timeout_does_not_repeat_upload_or_disclose_secret(self):
        session = Mock()
        session.post.side_effect = requests.Timeout('private-key')
        client = MapsCollector('https://middle.test', 'private-key', session=session)
        with self.assertRaises(ProductError) as caught:
            client.upload_realtime([{}])
        session.post.assert_called_once()
        self.assertNotIn('private-key', str(caught.exception))

    def test_rate_limit_retains_retry_after_without_retrying_mutation(self):
        session = Mock()
        session.post.return_value = Mock(status_code=429, headers={'Retry-After': '45'})
        client = MapsCollector('https://middle.test', 'private-key', session=session)
        with self.assertRaises(ProductError) as caught:
            client.upload_realtime([{}])
        self.assertEqual(caught.exception.retry_after, 45)
        session.post.assert_called_once()
