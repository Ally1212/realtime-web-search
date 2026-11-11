"""Complete typed delivery candidates and synthetic pinned Consumer responses."""
import copy
import json
import os
import traceback
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4
from datetime import timedelta
import requests

from realtime.data_capability_delivery import observation_candidate,validate_candidate,prepare_candidate,CapabilityReadbackClient,read_authorized_observation
from realtime.data_capability_export import build_bundle,verify_bundle
from realtime.data_capability_jobs import digest
from realtime.data_capability_fact_retention import FactRetention
from realtime.google_whale_readback import ReadbackTarget
from realtime.google_domain import DomainError
from realtime.product import ProductError
from tests import test_data_capability_export as exports
from tests import test_data_capability_checkpoint as fixture


def observation(cap='trends_history'):
    value=exports.observation(cap);value['entity_id']=digest('synthetic-private-entity');exports.rehash(value);return value


def streamed(value,status=200,*,raw=None,headers=None):
    response=Mock(status_code=status);response.headers=headers or {}
    data=raw if raw is not None else json.dumps(value,ensure_ascii=False).encode()
    response.iter_content.return_value=[data[i:i+512] for i in range(0,len(data),512)]
    return response


def read_fixture(candidate):
    target=ReadbackTarget('doc-1','version-2','dataset-3',candidate['source_platform'])
    document=dict(document=dict(id='doc-1',dataset_id='dataset-3',source_platform=target.source_platform,
                                external_content_id=candidate['object_key'],current_version_id='different-current-version',
                                metadata=dict(discovery=dict(private='current metadata is not authority'))))
    item=dict(document_id='doc-1',version_id='version-2',version=dict(document_id='doc-1',version_id='version-2',raw_payload_hash=candidate['payload_hash']),platform_payload=copy.deepcopy(candidate['payload']))
    session=Mock();session.request.side_effect=[streamed(document),streamed(dict(versions=[item]))]
    client=CapabilityReadbackClient('https://platform.invalid','synthetic-consumer-key',session=session)
    return client,target,session,document,item


class CapabilityDeliveryTests(unittest.TestCase):
    def test_four_domains_lossless_candidate_matches_export_counts_and_preserves_relations(self):
        for cap in ('trends_history','keyword_volume','aio_answer','ai_mode_answer'):
            with self.subTest(cap=cap):
                saved=observation(cap);before=copy.deepcopy(saved)
                candidate=observation_candidate('tenant-private',saved,source_platform='candidate_'+cap)
                self.assertEqual(saved,before);self.assertEqual(candidate,observation_candidate('tenant-private',saved,source_platform='candidate_'+cap))
                summary=validate_candidate(candidate);bundle=build_bundle(saved)
                manifest=verify_bundle(bundle['files']['bundle.zip'],**exports.pin(saved))
                self.assertEqual(summary['record_counts'],manifest['record_counts'])
                self.assertEqual(candidate['payload']['observation'],saved)
                self.assertEqual(candidate['admission_status'],'candidate_not_approved')
                for forbidden in ('dataset_id','content','receipt','accepted'):self.assertNotIn(forbidden,candidate)

    def test_event_keys_keep_tenants_new_observations_and_revisions_distinct_without_query_keys(self):
        saved=observation();first=observation_candidate('one',saved,source_platform='candidate')
        foreign=observation_candidate('two',saved,source_platform='candidate')
        self.assertNotEqual(first['object_key'],foreign['object_key'])
        again=copy.deepcopy(saved);again['observation_id']=str(uuid4());exports.rehash(again)
        second=observation_candidate('one',again,source_platform='candidate')
        self.assertEqual(saved['payload_hash'],again['payload_hash']);self.assertNotEqual(first['event_key'],second['event_key'])
        revision=copy.deepcopy(again);revision['supersedes']=saved['observation_id'];revision['revision']='r2';exports.rehash(revision)
        revised=observation_candidate('one',revision,source_platform='candidate')
        self.assertNotEqual(revised['event_key'],second['event_key'])
        self.assertEqual(first,observation_candidate('one',saved,source_platform='candidate'))
        self.assertNotIn('苹果',first['object_key']+first['event_key'])

    def test_candidate_identity_counts_and_corrupt_snapshots_are_not_silently_repaired(self):
        candidate=observation_candidate('tenant',observation('ai_mode_answer'),source_platform='candidate')
        for field in ['object_key','event_key','payload_hash','record_counts','admission_status']:
            with self.subTest(field=field):
                bad=copy.deepcopy(candidate);bad[field]='tampered'
                with self.assertRaises(ProductError):validate_candidate(bad)
        bad=copy.deepcopy(candidate);bad['payload']['observation']['payload']['data']['answer']['blocks'][0]['text']+='changed'
        with self.assertRaises(ProductError):validate_candidate(bad)
        bad=observation();bad['parameters']['api_key']='synthetic';exports.rehash(bad)
        with self.assertRaises(ProductError):observation_candidate('tenant',bad,source_platform='candidate')
        with patch('realtime.data_capability_delivery.MAX_BYTES',20):
            with self.assertRaises(ProductError) as error:observation_candidate('tenant',observation(),source_platform='candidate')
            self.assertEqual(error.exception.status,413)

    def test_pinned_readback_all_domains_keeps_platform_receipt_schema_and_current_metadata_separate(self):
        for cap in ('trends_history','keyword_volume','aio_answer','ai_mode_answer'):
            with self.subTest(cap=cap):
                candidate=observation_candidate('tenant',observation(cap),source_platform='candidate_'+cap)
                client,target,session,_,_=read_fixture(candidate)
                result=client.read_observation(target,expected_candidate=candidate,tenant_id='tenant')
                self.assertEqual(result['payload'],candidate['payload']);proof=result['proof']
                self.assertTrue(proof['version_readable']);self.assertTrue(proof['payload_roundtrip_verified'])
                self.assertFalse(proof['terminal_receipt_verified']);self.assertFalse(proof['platform_schema_admitted'])
                self.assertFalse(proof['current_document_version_matches']);self.assertTrue(proof['candidate_contract_only'])
                self.assertEqual(proof['record_counts'],candidate['record_counts'])
                for call in session.request.call_args_list:
                    self.assertTrue(call.kwargs['stream']);self.assertFalse(call.kwargs['allow_redirects'])
                self.assertEqual(session.request.call_args_list[1].kwargs['json']['items'],[dict(document_id=target.document_id,version_id=target.version_id)])

    def test_cross_tenant_or_source_rejected_before_network(self):
        candidate=observation_candidate('tenant',observation(),source_platform='candidate')
        client,target,session,_,_=read_fixture(candidate)
        with self.assertRaises(ProductError):client.read_observation(target,expected_candidate=candidate,tenant_id='foreign')
        with self.assertRaises(ProductError):client.read_observation(ReadbackTarget('doc-1','version-2','dataset-3','foreign'),expected_candidate=candidate,tenant_id='tenant')
        session.request.assert_not_called()

    def test_missing_or_modified_fixed_version_never_uses_current_document_payload(self):
        candidate=observation_candidate('tenant',observation('ai_mode_answer'),source_platform='candidate')
        for mutation in ['missing','body','raw_hash','version','object','dataset','tenant']:
            with self.subTest(mutation=mutation):
                client,target,session,document,item=read_fixture(candidate)
                if mutation=='missing':item.pop('platform_payload');document['document']['platform_payload']=candidate['payload']
                elif mutation=='body':item['platform_payload']['observation']['payload']['data']['answer']['blocks'][0]['text']+='changed'
                elif mutation=='raw_hash':item['version']['raw_payload_hash']='sha256:'+'0'*64
                elif mutation=='version':item['version']['version_id']='another-version'
                elif mutation=='object':document['document']['external_content_id']='other'
                elif mutation=='dataset':document['document']['dataset_id']='foreign'
                else:item['platform_payload']['tenant_id']='foreign'
                session.request.side_effect=[streamed(document),streamed(dict(versions=[item]))]
                with self.assertRaises(DomainError):client.read_observation(target,expected_candidate=candidate,tenant_id='tenant')

    def test_stream_bounds_duplicates_nonfinite_and_failures_close_responses_without_leaking_key(self):
        for raw in [b'{"a":1,"a":2}',b'{"a":NaN}',b'not json',b'\xff']:
            with self.subTest(raw=repr(raw)):
                session=Mock();response=streamed({},raw=raw);session.request.return_value=response
                client=CapabilityReadbackClient('https://platform.invalid','synthetic-key',session=session)
                with self.assertRaises(DomainError):client._request('GET','/v1/documents/doc')
                response.close.assert_called_once()
        for status in [401,403,404,302,500]:
            session=Mock();response=streamed({},status=status);session.request.return_value=response
            client=CapabilityReadbackClient('https://platform.invalid','synthetic-key',session=session)
            with self.assertRaises(DomainError):client._request('GET','/v1/documents/doc')
            response.close.assert_called_once();response.iter_content.assert_not_called()
        for mode in ('length','stream'):
            session=Mock();response=streamed({},headers={'Content-Length':'70000'} if mode=='length' else {},raw=b'x'*70000)
            session.request.return_value=response;client=CapabilityReadbackClient('https://platform.invalid','synthetic-key',session=session)
            with patch('realtime.data_capability_delivery.MAX_BYTES',20):
                with self.assertRaises(DomainError):client._request('POST','/v1/versions/batch-get',body={})
            response.close.assert_called_once()
        session=Mock();response=streamed({});session.request.return_value=response
        client=CapabilityReadbackClient('https://platform.invalid','synthetic-key',session=session)
        with patch('realtime.data_capability_delivery.time.monotonic',side_effect=[0,0,31]):
            with self.assertRaises(DomainError):client._request('GET','/v1/documents/doc')
        response.close.assert_called_once()
        session=Mock();session.request.side_effect=requests.Timeout('synthetic-key')
        client=CapabilityReadbackClient('https://platform.invalid','synthetic-key',session=session)
        try:client._request('GET','/v1/documents/doc')
        except DomainError as error:self.assertNotIn('synthetic-key',''.join(traceback.format_exception(error)))
        else:self.fail('timeout was accepted')


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class AuthorizedDeliveryTests(unittest.TestCase):
    setUp=fixture.CheckpointTests.setUp
    tearDown=fixture.CheckpointTests.tearDown
    store=fixture.CheckpointTests.store
    start=fixture.CheckpointTests.start
    finish=fixture.CheckpointTests.finish
    saved=fixture.CheckpointTests.saved
    error=fixture.CheckpointTests.error

    def test_saved_four_domains_current_rights_and_fixed_version_readback(self):
        for cap in self.grants:
            with self.subTest(cap=cap):
                job=self.saved(cap);candidate=prepare_candidate(self.runtime,self.tenant,job['result_ref'],source_platform='candidate_'+cap)
                client,target,session,_,_=read_fixture(candidate)
                result=read_authorized_observation(self.runtime,self.tenant,job['result_ref'],client,target,source_platform='candidate_'+cap)
                self.assertTrue(result['proof']['current_local_rights_rechecked'])
                self.assertFalse(result['proof']['terminal_receipt_verified'])
                client=Mock();self.error('not_found',lambda:read_authorized_observation(self.runtime,str(uuid4()),job['result_ref'],client,target,source_platform='candidate_'+cap))
                client.read_observation.assert_not_called()

    def test_revocation_erasure_and_expiry_during_readback_prevent_returning_candidate_or_proof(self):
        for mutation in ['revocation','erasure','expiry']:
            with self.subTest(mutation=mutation):
                job=self.saved();candidate=prepare_candidate(self.runtime,self.tenant,job['result_ref'],source_platform='candidate')
                client=Mock();target=ReadbackTarget('doc','version','dataset','candidate')
                def read(**kwargs):
                    if mutation=='revocation':self.grants['trends_history']['status']='revoked'
                    elif mutation=='erasure':FactRetention(self.data).erase(self.tenant,job['result_ref'],reason='operator_erased')
                    else:self.now+=timedelta(days=2)
                    return dict(proof={},payload=candidate['payload'])
                client.read_observation.side_effect=lambda *args,**kwargs:read(**kwargs)
                self.error('source_not_admitted' if mutation=='revocation' else 'expired',lambda:read_authorized_observation(self.runtime,self.tenant,job['result_ref'],client,target,source_platform='candidate'))
                self.grants['trends_history']['status']='approved'
