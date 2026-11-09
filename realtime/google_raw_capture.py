"""Opt-in private encrypted response entity storage, independent of public APIs.

The caller owns source/tenant authorization and expiry determination. A policy
reference is mandatory, but this module does not grant or verify source rights.
Keys are injected, never generated/persisted here. No production default exists.
"""
from __future__ import annotations

import base64
from datetime import datetime,timezone,timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


class MissingPrivateCapture(ValueError):
    """The requested private record is absent, not a failed authentication."""


class PrivateResponseVault:
    def __init__(self,directory,*,key,max_bytes=64*1024*1024,quota_bytes=2*1024**3):
        if not isinstance(key,bytes) or len(key)!=32:raise ValueError('explicit 256-bit key required')
        if type(max_bytes) is not int or not 1<=max_bytes<=64*1024*1024:raise ValueError('invalid response limit')
        if type(quota_bytes) is not int or quota_bytes<max_bytes:raise ValueError('invalid quota')
        self.root=Path(directory).absolute()
        if self.root.resolve()!=self.root:raise ValueError('symlink ancestor refused')
        self.root.mkdir(mode=0o700,exist_ok=True)
        if self.root.stat().st_mode & 0o077:raise ValueError('private directory required')
        self.key_id=hashlib.sha256(key).hexdigest()[:24]
        self.cipher=AESGCM(key);self.max_bytes=max_bytes;self.quota_bytes=quota_bytes

    @staticmethod
    def _identity(tenant_id,request_id):
        if not isinstance(tenant_id,str) or not tenant_id.strip() or len(tenant_id)>256:
            raise ValueError('explicit tenant identity required')
        request_id=str(UUID(str(request_id)))
        return hashlib.sha256(canonical([tenant_id,request_id])).hexdigest(),request_id

    def _load(self,path):
        if not path.exists() and not path.is_symlink():raise MissingPrivateCapture('private capture unavailable')
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError('invalid private ciphertext file')
        if path.stat().st_size>self.max_bytes*2+8192:raise ValueError('invalid ciphertext size')
        return json.loads(path.read_bytes())

    def put(self,body,*,tenant_id,request_id,http_status,expires_at,authorization_ref,now=None):
        if type(http_status) is not int or not 100<=http_status<=599:
            raise ValueError('complete bounded response entity required')
        return self._put(body,tenant_id=tenant_id,request_id=request_id,http_status=http_status,
            expires_at=expires_at,authorization_ref=authorization_ref,now=now,
            representation='http_entity_bytes_after_content_decoding')

    def put_private_event(self,event,*,tenant_id,request_id,expires_at,authorization_ref,
                          now=None,exclusive=False):
        # Local journal JSON is never labelled as an upstream HTTP entity.
        if not isinstance(event,dict):raise ValueError('private event object required')
        body=canonical(event)
        if len(body)>65536:raise ValueError('private event exceeds limit')
        return self._put(body,tenant_id=tenant_id,request_id=request_id,http_status=None,
            expires_at=expires_at,authorization_ref=authorization_ref,now=now,
            representation='private_feed_journal_json',exclusive=exclusive)

    def _put(self,body,*,tenant_id,request_id,http_status,expires_at,authorization_ref,
             representation,now=None,exclusive=False):
        now=now or datetime.now(timezone.utc)
        if not isinstance(body,bytes) or len(body)>self.max_bytes:
            raise ValueError('complete bounded response entity required')
        if (not isinstance(expires_at,datetime) or expires_at.utcoffset() is None
                or not now<expires_at<=now+timedelta(hours=24)):
            raise ValueError('explicit live expiry within 24 hours required')
        if not isinstance(authorization_ref,str) or not authorization_ref.strip() or len(authorization_ref)>256:
            raise ValueError('explicit authorization reference required')
        capture_id,request_id=self._identity(tenant_id,request_id)
        binding=dict(version='private-response-1',capture_id=capture_id,key_id=self.key_id,
            tenant_id=tenant_id,request_id=request_id,http_status=http_status,
            expires_at=expires_at.astimezone(timezone.utc).isoformat(),authorization_ref=authorization_ref,
            body_sha256=hashlib.sha256(body).hexdigest(),body_bytes=len(body),
            representation=representation)
        path=self.root/(capture_id+'.sealed')
        lock=os.open(self.root/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if path.exists() or path.is_symlink():
                if exclusive:raise ValueError('private event already exists; replay forbidden')
                stored=self._load(path)
                if stored['binding']!=binding:raise ValueError('capture identity conflict')
                self.read(tenant_id=tenant_id,request_id=request_id,now=now)
                return binding
            nonce=os.urandom(12)
            ciphertext=self.cipher.encrypt(nonce,body,canonical(binding))
            sealed=canonical(dict(binding=binding,nonce=base64.b64encode(nonce).decode(),
                ciphertext=base64.b64encode(ciphertext).decode()))
            entries=list(self.root.iterdir())
            if any(p.is_symlink() for p in entries):raise ValueError('unexpected symlink in vault')
            # Interrupted encrypted temp files also consume quota.
            used=sum(p.stat().st_size for p in entries if p.is_file() and p.name!='lock')
            if used+len(sealed)>self.quota_bytes:raise ValueError('private capture quota exhausted')
            fd,name=tempfile.mkstemp(prefix='.sealed-',dir=self.root)
            try:
                with os.fdopen(fd,'wb') as stream:
                    os.fchmod(stream.fileno(),0o600);stream.write(sealed);stream.flush();os.fsync(stream.fileno())
                os.link(name,path)  # exclusive publish; never replace a prior observation
                directory=os.open(self.root,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            finally:
                if os.path.exists(name):os.unlink(name)
            return binding
        finally:os.close(lock)

    def read(self,*,tenant_id,request_id,now=None):
        return self.read_record(tenant_id=tenant_id,request_id=request_id,now=now)['body']

    def read_record(self,*,tenant_id,request_id,now=None):
        now=now or datetime.now(timezone.utc)
        capture_id,request_id=self._identity(tenant_id,request_id)
        value=self._load(self.root/(capture_id+'.sealed'));binding=value['binding']
        if (binding['tenant_id']!=tenant_id or binding['request_id']!=request_id
                or binding['capture_id']!=capture_id or binding['key_id']!=self.key_id):
            raise ValueError('private capture scope differs')
        if now>=datetime.fromisoformat(binding['expires_at']):raise ValueError('private capture expired')
        try:
            raw=self.cipher.decrypt(base64.b64decode(value['nonce'],validate=True),
                base64.b64decode(value['ciphertext'],validate=True),canonical(binding))
        except (InvalidTag,ValueError):raise ValueError('private capture authentication failed') from None
        if len(raw)!=binding['body_bytes'] or hashlib.sha256(raw).hexdigest()!=binding['body_sha256']:
            raise ValueError('private capture checksum differs')
        return dict(binding=binding,body=raw)

    def remove_records(self,*,tenant_id,request_ids,authorization_ref,required_events=None):
        """Privileged bounded cleanup primitive; caller must authorize deletion.

        Authenticates all selected published/temp ciphertexts before unlinking.
        Does not read or erase backups, unrelated records or unknown orphans.
        """
        ids=list(request_ids)
        if not 1<=len(ids)<=5:raise ValueError('bounded explicit private cleanup required')
        selected={self._identity(tenant_id,rid)[0]:self._identity(tenant_id,rid)[1] for rid in ids}
        if len(selected)!=len(ids):raise ValueError('duplicate cleanup identity')
        required_events=dict(required_events or {})
        if not set(required_events)<=set(selected.values()):raise ValueError('cleanup event outside scope')
        lock=os.open(self.root/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX)
            targets=[];missing=0;unknown=0;verified_events=set()
            def authenticate(path,value):
                binding=value['binding'];capture_id=binding.get('capture_id')
                if (capture_id not in selected or binding.get('tenant_id')!=tenant_id
                        or binding.get('request_id')!=selected[capture_id]
                        or binding.get('key_id')!=self.key_id
                        or binding.get('authorization_ref')!=authorization_ref):
                    raise ValueError('private cleanup scope differs')
                try:
                    body=self.cipher.decrypt(base64.b64decode(value['nonce'],validate=True),
                        base64.b64decode(value['ciphertext'],validate=True),canonical(binding))
                except (InvalidTag,ValueError):raise ValueError('private cleanup authentication failed') from None
                if len(body)!=binding['body_bytes'] or hashlib.sha256(body).hexdigest()!=binding['body_sha256']:
                    raise ValueError('private cleanup checksum differs')
                if binding['request_id'] in required_events:
                    if (binding['representation']!='private_feed_journal_json'
                            or body!=canonical(required_events[binding['request_id']])):
                        raise ValueError('private cleanup ownership differs')
                    verified_events.add(binding['request_id'])
                targets.append((path,binding['request_id']))
            for capture_id in selected:
                path=self.root/(capture_id+'.sealed')
                try:value=self._load(path)
                except MissingPrivateCapture:missing+=1;continue
                authenticate(path,value)
            published=len(targets)
            for path in self.root.glob('.sealed-*'):
                try:value=self._load(path)
                except (ValueError,OSError):unknown+=1;continue
                if not isinstance(value,dict) or not isinstance(value.get('binding'),dict):unknown+=1;continue
                binding=value['binding']
                if binding.get('capture_id') not in selected or binding.get('tenant_id')!=tenant_id:
                    # Unauthenticated metadata cannot prove this orphan is
                    # unrelated. Keep it and report incomplete attribution.
                    unknown+=1;continue
                authenticate(path,value)
            if targets and set(required_events)!=verified_events:
                raise ValueError('private cleanup ownership unavailable')
            # Keep ownership evidence until every other selected copy is gone,
            # so a failed unlink can be explicitly retried without widening scope.
            targets.sort(key=lambda item:item[1] in required_events)
            # No selected file is removed if any selected authentication failed.
            # Unlink may still fail partway; fsync partial progress and let an
            # explicit retry handle absent files. Never return success on error.
            try:
                for path,_ in targets:path.unlink()
            finally:
                directory=os.open(self.root,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            return dict(deleted=len(targets),deleted_published=published,
                deleted_orphans=len(targets)-published,missing_published=missing,
                unclassified_orphans=unknown,
                status='partial_unclassified_orphans' if unknown else 'current_scope_removed',
                storage='current_vault_only_no_backup_erasure_claim')
        finally:os.close(lock)

    def purge_expired(self,*,now=None):
        now=now or datetime.now(timezone.utc)
        lock=os.open(self.root/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        deleted=0
        try:
            fcntl.flock(lock,fcntl.LOCK_EX)
            for path in self.root.glob('*.sealed'):
                value=self._load(path);binding=value['binding']
                if now<datetime.fromisoformat(binding['expires_at']):continue
                if path.name!=binding['capture_id']+'.sealed' or binding['key_id']!=self.key_id:
                    raise ValueError('expired capture scope differs')
                try:
                    self.cipher.decrypt(base64.b64decode(value['nonce'],validate=True),
                        base64.b64decode(value['ciphertext'],validate=True),canonical(binding))
                except (InvalidTag,ValueError):raise ValueError('expired capture authentication failed') from None
                path.unlink();deleted+=1
            directory=os.open(self.root,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
            return dict(deleted=deleted,storage='current_vault_files_only')
        finally:os.close(lock)
