from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

from realtime.google_raw_capture import PrivateResponseVault
from realtime.free_google import GoogleTransport
from realtime.discovery import GoogleBlocked


class RawCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'vault'
        self.key=b'x'*32
        self.vault=PrivateResponseVault(self.root,key=self.key,max_bytes=1024,quota_bytes=16384)
        self.now=datetime.now(timezone.utc);self.rid=str(uuid4())
        self.args=dict(tenant_id='tenant-one',request_id=self.rid,http_status=503,
            expires_at=self.now+timedelta(hours=1),authorization_ref='fixture-policy-only',now=self.now)

    def test_exact_binary_roundtrip_and_plaintext_never_on_disk(self):
        raw=b'private-password-value\x00\xff\xfe{bad-json'
        ref=self.vault.put(raw,**self.args)
        self.assertEqual(self.vault.read(tenant_id='tenant-one',request_id=self.rid,now=self.now),raw)
        for path in self.root.iterdir():
            self.assertNotIn(b'private-password-value',path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(ref['representation'],'http_entity_bytes_after_content_decoding')

    def test_identical_replay_preserves_ciphertext_and_conflict_is_rejected(self):
        ref=self.vault.put(b'one',**self.args);path=self.root/(ref['capture_id']+'.sealed');before=path.read_bytes()
        self.vault.put(b'one',**self.args);self.assertEqual(before,path.read_bytes())
        with self.assertRaisesRegex(ValueError,'conflict'):self.vault.put(b'two',**self.args)
        self.assertEqual(before,path.read_bytes())

    def test_expired_or_other_tenant_or_wrong_key_cannot_read(self):
        self.vault.put(b'secret',**self.args)
        with self.assertRaisesRegex(ValueError,'expired'):
            self.vault.read(tenant_id='tenant-one',request_id=self.rid,now=self.now+timedelta(hours=2))
        with self.assertRaises(ValueError):self.vault.read(tenant_id='other',request_id=self.rid,now=self.now)
        wrong=PrivateResponseVault(self.root,key=b'y'*32)
        with self.assertRaises(ValueError):wrong.read(tenant_id='tenant-one',request_id=self.rid,now=self.now)

    def test_metadata_or_ciphertext_tamper_is_rejected(self):
        ref=self.vault.put(b'secret',**self.args);path=self.root/(ref['capture_id']+'.sealed');before=path.read_bytes()
        value=json.loads(before);value['binding']['expires_at']=(self.now+timedelta(hours=3)).isoformat();path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'authentication'):self.vault.read(tenant_id='tenant-one',request_id=self.rid,now=self.now)
        value=json.loads(before);value['ciphertext']='a'*24;path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.vault.read(tenant_id='tenant-one',request_id=self.rid,now=self.now)

    def test_size_policy_and_quota_fail_without_publishing(self):
        with self.assertRaises(ValueError):self.vault.put(b'x'*1025,**self.args)
        for changes in ({'expires_at':self.now-timedelta(seconds=1)},
                        {'expires_at':self.now+timedelta(days=2)},{'authorization_ref':''}):
            with self.assertRaises(ValueError):self.vault.put(b'one',**{**self.args,**changes})
        tiny=PrivateResponseVault(self.root,key=self.key,max_bytes=1024,quota_bytes=1024)
        with self.assertRaisesRegex(ValueError,'quota'):tiny.put(b'x'*1024,**self.args)
        self.assertEqual(list(self.root.glob('*.sealed')),[])

    def test_symlink_and_public_directory_refused(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):PrivateResponseVault(self.root,key=self.key)
        self.root.chmod(0o700)
        link=self.root.parent/'link';link.symlink_to(self.root)
        with self.assertRaises(ValueError):PrivateResponseVault(link,key=self.key)

    def test_failure_and_nonjson_are_captured_before_validation(self):
        session=Mock();response=session.get.return_value
        response.status_code=503;response.content=b'failure secret\xff\x00';response.headers={}
        response.json.side_effect=ValueError('not-json')
        def capture(request_id,body,http_status):
            self.args['request_id']=request_id;self.args['http_status']=http_status
            return self.vault.put(body,**self.args)
        with patch('realtime.free_google.requests.Session',return_value=session):
            transport=GoogleTransport(5,'en','',validate_target_dns=False)
            transport.raw_response_recorder=capture
            with self.assertRaises(GoogleBlocked):transport.openserp('test',1,None,None)
        self.assertEqual(self.vault.read(tenant_id='tenant-one',request_id=self.args['request_id'],now=self.now),response.content)
        self.assertNotIn('private_response_capture',str(transport.last_serp_response))
        response.close.assert_called_once()

    def test_no_capture_sink_keeps_existing_behavior(self):
        session=Mock();response=session.get.return_value
        response.status_code=503;response.content=b'not-json';response.headers={};response.json.side_effect=ValueError()
        with patch('realtime.free_google.requests.Session',return_value=session):
            transport=GoogleTransport(5,'en','',validate_target_dns=False)
            with self.assertRaises(GoogleBlocked):transport.openserp('test',1,None,None)
        self.assertEqual(list(self.root.glob('*.sealed')),[])
        self.assertIsNone(transport.local.last_private_response_capture)

    def test_expiry_cleanup_only_removes_authenticated_expired_files(self):
        first=self.vault.put(b'expired',**self.args)
        other={**self.args,'request_id':str(uuid4()),'expires_at':self.now+timedelta(hours=3)}
        second=self.vault.put(b'live',**other)
        result=self.vault.purge_expired(now=self.now+timedelta(hours=2))
        self.assertEqual(result['deleted'],1)
        self.assertFalse((self.root/(first['capture_id']+'.sealed')).exists())
        self.assertTrue((self.root/(second['capture_id']+'.sealed')).exists())

    def test_encrypted_orphan_temp_counts_against_quota(self):
        (self.root/'.sealed-orphan').write_bytes(b'x'*16000)
        with self.assertRaisesRegex(ValueError,'quota'):self.vault.put(b'one',**self.args)
        self.assertEqual(list(self.root.glob('*.sealed')),[])


if __name__=='__main__':unittest.main()
