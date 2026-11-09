import unittest
from unittest.mock import Mock

import requests

from realtime.google_domain import DomainError
from realtime.google_whale_readback import ReadbackTarget, WhaleReadbackClient


TARGET = ReadbackTarget("doc-1", "version-2", "dataset-3", "google_search")


def response(status, payload):
    value = Mock(status_code=status)
    value.json.return_value = payload
    return value


class WhaleReadbackTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = WhaleReadbackClient(
            "https://example.test/", "secret-consumer-key", session=self.session)
        self.document = {"document": {"id": "doc-1", "dataset_id": "dataset-3",
                                      "source_platform": "google_search"}}
        self.version = {"versions": [{"document_id": "doc-1", "version_id": "version-2",
                                      "version": {"document_id": "doc-1",
                                                  "version_id": "version-2",
                                                  "content_hash": "hash-1"},
                                      "platform_payload": {"private": "do not return"}}]}

    def test_pinned_readback_returns_only_safe_proof(self):
        self.session.request.side_effect = [response(200, self.document),
                                            response(200, self.version)]
        result = self.client.verify(TARGET)
        self.assertTrue(result["version_readable"])
        self.assertFalse(result["run_item_schema_verified"])
        self.assertFalse(result["terminal_receipt_verified"])
        self.assertNotIn("private", str(result))
        first, second = self.session.request.call_args_list
        self.assertEqual(first.args, ("GET", "https://example.test/v1/documents/doc-1"))
        self.assertEqual(second.args, ("POST", "https://example.test/v1/versions/batch-get"))
        self.assertEqual(second.kwargs["json"]["items"],
                         [{"document_id": "doc-1", "version_id": "version-2"}])
        for call in (first, second):
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertEqual(call.kwargs["headers"]["Authorization"],
                             "Bearer secret-consumer-key")

    def test_wrong_document_scope_and_version_fail_closed(self):
        for key, bad in (("id", "other"), ("dataset_id", "other"),
                         ("source_platform", "other")):
            with self.subTest(key=key):
                document = {"document": {**self.document["document"], key: bad}}
                self.session.request.side_effect = [response(200, document)]
                with self.assertRaises(DomainError):
                    self.client.verify(TARGET)
        for change in ({"document_id": "other"}, {"version_id": "other"},
                       {"version": {"document_id": "other", "version_id": "version-2"}},
                       {"version": {"document_id": "doc-1", "version_id": "other"}}):
            with self.subTest(change=change):
                version = {"versions": [{**self.version["versions"][0], **change}]}
                self.session.request.side_effect = [response(200, self.document),
                                                    response(200, version)]
                with self.assertRaises(DomainError):
                    self.client.verify(TARGET)

    def test_auth_missing_item_redirect_and_network_failure(self):
        for status in (401, 403, 302):
            with self.subTest(status=status):
                self.session.request.side_effect = [response(status, {})]
                with self.assertRaises(DomainError) as error:
                    self.client.verify(TARGET)
                self.assertNotIn("secret-consumer-key", str(error.exception))
        self.session.request.side_effect = [response(200, self.document),
                                            response(200, {"versions": [{"error": "missing"}]})]
        with self.assertRaises(DomainError) as error:
            self.client.verify(TARGET)
        self.assertEqual(error.exception.code, "not_covered")
        self.session.request.side_effect = requests.Timeout("secret-consumer-key")
        with self.assertRaises(DomainError) as error:
            self.client.verify(TARGET)
        self.assertNotIn("secret-consumer-key", str(error.exception))

    def test_content_and_payload_hashes_must_match_pinned_version(self):
        from dataclasses import replace
        from realtime.google_data import digest
        content_hash = 'a' * 64
        self.version['versions'][0]['version']['content_hash'] = content_hash
        target = replace(TARGET, expected_content_hash=content_hash,
                         expected_platform_payload_hash=digest({'private': 'do not return'}))
        self.session.request.side_effect = [response(200, self.document), response(200, self.version)]
        result = self.client.verify(target)
        self.assertEqual(result['content_verification'], 'matched')
        self.assertEqual(result['verified_hashes'], ['content_hash', 'platform_payload_hash'])
        for target in (replace(TARGET, expected_content_hash='b' * 64),
                       replace(TARGET, expected_platform_payload_hash='b' * 64)):
            self.session.request.side_effect = [response(200, self.document), response(200, self.version)]
            with self.assertRaises(DomainError):
                self.client.verify(target)

    def test_prefixed_sha256_matches_unprefixed_expectation(self):
        from dataclasses import replace
        self.version['versions'][0]['version']['raw_payload_hash'] = 'sha256:' + 'b' * 64
        self.session.request.side_effect = [response(200, self.document), response(200, self.version)]
        self.assertEqual(self.client.verify(replace(TARGET, expected_raw_payload_hash='b' * 64))['content_verification'], 'matched')

    def test_batch_checks_scope_and_identity_without_dropping_bad_items(self):
        other = ReadbackTarget('doc-2', 'version-3', 'dataset-3', 'google_search')
        document = {**self.document['document'], 'current_version_id': 'version-2', 'status': 'published'}
        self.session.request.side_effect = [
            response(200, {'documents': [{'document_id': 'doc-1', 'document': document},
                                        {'document_id': 'doc-2', 'error': {'code': 'forbidden'}}]}),
            response(200, {'versions': [self.version['versions'][0],
                {'document_id': 'doc-2', 'version_id': 'version-3', 'error': {'code': 'forbidden'}}]})]
        result = self.client.verify_many([TARGET, other])
        self.assertTrue(result[0]['proof']['version_readable'])
        self.assertTrue(result[0]['proof']['current_document_version_matches'])
        self.assertEqual(result[1]['error'], 'not_authorized')
        self.assertEqual(self.session.request.call_count, 2)
        for call in self.session.request.call_args_list:
            self.assertFalse(call.kwargs['allow_redirects'])

    def test_batch_bounds_missing_and_unexpected_identities(self):
        for targets in ([], [TARGET] * 101, ['bad']):
            with self.assertRaises(DomainError): self.client.verify_many(targets)
        self.session.request.side_effect = [response(200, {'documents': []}), response(200, {'versions': []})]
        self.assertEqual(self.client.verify_many([TARGET])[0]['error'], 'not_covered')
        self.session.request.side_effect = [response(200, {'documents': [{'document_id': 'wrong'}]})]
        with self.assertRaises(DomainError): self.client.verify_many([TARGET])

    def test_batch_document_reads_split_at_fifty(self):
        targets = [ReadbackTarget(f'doc-{i}', f'v-{i}', 'data', 'google_search') for i in range(51)]
        docs = [{'document_id': t.document_id, 'document': {'id': t.document_id,
                 'dataset_id': t.dataset_id, 'source_platform': t.source_platform}} for t in targets]
        versions = [{'document_id': t.document_id, 'version_id': t.version_id,
                     'version': {'document_id': t.document_id, 'version_id': t.version_id}} for t in targets]
        self.session.request.side_effect = [response(200, {'documents': docs[:50]}),
            response(200, {'documents': docs[50:]}), response(200, {'versions': versions})]
        self.assertEqual(len(self.client.verify_many(targets)), 51)
        self.assertEqual([len(c.kwargs['json']['document_ids']) for c in self.session.request.call_args_list[:2]], [50, 1])

    def test_full_run_uses_only_pinned_payload_and_checks_occurrences(self):
        import copy
        payload={'schema_version':'google-serp-run-envelope-1','run_id':'run-1',
                 'observation':{'request_id':'run-1','source':'google_web','occurrences':[
                     {'request_id':'run-1','occurrence_id':'item-1','rank':1.0}]}}
        document=copy.deepcopy(self.document)
        document['document']['external_content_id']='serp-run:run-1'
        version=copy.deepcopy(self.version)
        version['versions'][0]['platform_payload']=payload
        self.session.request.side_effect=[response(200,document),response(200,version)]
        expected=copy.deepcopy(payload);expected['observation']['occurrences'][0]['rank']=1
        result=self.client.read_full_run(TARGET,expected_payload=expected)
        self.assertTrue(result['proof']['payload_roundtrip_verified'])
        self.assertEqual(result['proof']['payload_binding'],'pinned_version')
        self.assertEqual(result['proof']['occurrence_count'],1)
        for change in ({'platform_payload':None},{'platform_payload':{**payload,'run_id':'other'}}):
            self.session.request.side_effect=[response(200,document),response(200,{'versions':[{**version['versions'][0],**change}]})]
            with self.assertRaises(DomainError):self.client.read_full_run(TARGET)
        expected['observation']['occurrences'][0]['rank']=True
        self.session.request.side_effect=[response(200,document),response(200,version)]
        with self.assertRaises(DomainError):self.client.read_full_run(TARGET,expected_payload=expected)

    def test_rejects_untrusted_endpoint_and_target(self):
        for base in ("http://example.test", "https://example.test/prefix",
                     "https://user:password@example.test", "https://example.test/?key=secret"):
            with self.subTest(base=base), self.assertRaises(DomainError):
                WhaleReadbackClient(base, "secret", session=self.session)
        with self.assertRaises(DomainError):
            ReadbackTarget("other/path", "version-2", "dataset-3", "google_search")


if __name__ == "__main__":
    unittest.main()
