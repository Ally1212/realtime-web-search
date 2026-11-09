"""Opt-in encrypted feed attempt evidence. Recovery reads; it never replays.

Source permission, a live authorization guard, keys and expiry belong to the
caller. This sink provides no source/API calls, schema changes or scheduler.
"""
from __future__ import annotations

from datetime import datetime,timezone
import json
from uuid import UUID,uuid5

from .google_data import digest
from .google_raw_capture import MissingPrivateCapture


class PrivateFeedCaptureJournal:
    def __init__(self,vault,*,tenant_id,attempt_id,expires_at,authorization_ref,clock=None):
        self.vault=vault;self.tenant_id=tenant_id
        self.attempt_id=str(UUID(str(attempt_id)))
        if not isinstance(expires_at,datetime) or expires_at.utcoffset() is None:
            raise ValueError('aware private journal expiry required')
        self.expires_at=expires_at.astimezone(timezone.utc);self.authorization_ref=authorization_ref
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    @staticmethod
    def _event_id(request_id,stage):
        return str(uuid5(UUID(str(request_id)),'private-feed-journal-1:'+stage))

    def _scope(self,request_id):
        if str(UUID(str(request_id)))!=self.attempt_id:
            raise ValueError('private feed attempt scope differs')

    def _event(self,request_id,stage,value,*,exclusive=False):
        self._scope(request_id)
        event=dict(version='private-feed-journal-1',request_id=str(UUID(str(request_id))),
            stage=stage,value=value)
        return self.vault.put_private_event(event,tenant_id=self.tenant_id,
            request_id=self._event_id(request_id,stage),expires_at=self.expires_at,
            authorization_ref=self.authorization_ref,now=self.clock(),exclusive=exclusive)

    def _read_event(self,request_id,stage,*,optional=False):
        self._scope(request_id)
        try:
            record=self.vault.read_record(tenant_id=self.tenant_id,
                request_id=self._event_id(request_id,stage),now=self.clock())
        except (MissingPrivateCapture,FileNotFoundError):
            if optional:return None
            raise ValueError('private feed intent unavailable; replay forbidden') from None
        value=json.loads(record['body'])
        if (record['binding']['expires_at']!=self.expires_at.isoformat()
                or record['binding']['authorization_ref']!=self.authorization_ref
                or record['binding']['representation']!='private_feed_journal_json'
                or value.get('version')!='private-feed-journal-1'
                or value.get('request_id')!=str(UUID(str(request_id))) or value.get('stage')!=stage):
            raise ValueError('private feed journal scope differs')
        return value['value']

    def begin(self,*,request_id,source,query,locale,campaign_id,requested_at):
        if source not in {'google_news','google_trends'}:raise ValueError('unsupported feed source')
        if not isinstance(query,str) or not isinstance(locale,str) or not locale:
            raise ValueError('explicit feed context required')
        timestamp=datetime.fromisoformat(requested_at.replace('Z','+00:00'))
        if timestamp.utcoffset() is None:raise ValueError('aware request time required')
        context=dict(source=source,query=query,locale=locale,
            campaign_id=str(UUID(str(campaign_id))),requested_at=timestamp.isoformat())
        # Exclusive under the vault lock, even for identical content. A second
        # begin is not allowed to issue another source request after a crash.
        self._event(request_id,'intent',context,exclusive=True)

    def __call__(self,*,request_id,body,http_status,source,query,locale):
        intent=self._read_event(request_id,'intent')
        if any(intent[k]!=v for k,v in dict(source=source,query=query,locale=locale).items()):
            raise ValueError('private feed response context differs')
        capture=self.vault.put(body,tenant_id=self.tenant_id,request_id=request_id,
            http_status=http_status,expires_at=self.expires_at,
            authorization_ref=self.authorization_ref,now=self.clock())
        # A crash here leaves raw bytes plus intent. Reconcile discovers that
        # deterministic raw identity without reconstructing or fetching it.
        self._event(request_id,'captured',dict(intent_hash=digest(intent),capture=capture))
        return capture

    def saved(self,*,request_id,saved_request_id):
        if str(UUID(str(saved_request_id)))!=str(UUID(str(request_id))):
            raise ValueError('saved feed request identity differs')
        intent=self._read_event(request_id,'intent')
        captured=self._read_event(request_id,'captured')
        raw=self.vault.read_record(tenant_id=self.tenant_id,request_id=request_id,now=self.clock())
        self._check_capture(intent,captured,raw)
        self._event(request_id,'saved',dict(intent_hash=digest(intent),
            capture_hash=digest(raw['binding']),saved_request_id=str(UUID(str(saved_request_id)))))

    @staticmethod
    def _check_capture(intent,captured,raw):
        if raw['binding']['representation']!='http_entity_bytes_after_content_decoding':
            raise ValueError('raw feed record kind differs')
        if captured is not None and (captured.get('intent_hash')!=digest(intent)
                or captured.get('capture')!=raw['binding']):
            raise ValueError('private feed capture event differs')

    def reconcile(self,request_id,*,read_saved):
        """Inspect one intent against an injected read-only DB lookup; no writes.

        The reader returns the saved raw request row or None. A missing row,
        missing marker or unknown outcome never authorizes source/DB replay.
        """
        request_id=str(UUID(str(request_id)))
        intent=self._read_event(request_id,'intent')
        captured=self._read_event(request_id,'captured',optional=True)
        saved=self._read_event(request_id,'saved',optional=True)
        report=dict(request_id=request_id,source=intent['source'],replay_authorized=False,
            journal_changed=False,formal_admission=False,database_writes=0,
            captured_marker_present=captured is not None,saved_marker_present=saved is not None)
        try:
            raw=self.vault.read_record(tenant_id=self.tenant_id,request_id=request_id,now=self.clock())
        except (MissingPrivateCapture,FileNotFoundError):
            if captured is not None or saved is not None:raise ValueError('journal raw response unavailable') from None
            report.update(status='source_outcome_unknown',database_reads=0)
            return report
        self._check_capture(intent,captured,raw)
        if saved is not None and (saved.get('intent_hash')!=digest(intent)
                or saved.get('capture_hash')!=digest(raw['binding'])
                or saved.get('saved_request_id')!=request_id):
            raise ValueError('private feed saved event differs')
        row=read_saved(request_id);report['database_reads']=1
        if row is None:
            report.update(status='capture_present_database_missing',
                raw_body_sha256=raw['binding']['body_sha256'])
            return report
        parameters=row.get('response',{}).get('search_parameters',{})
        checks=dict(request_id=str(row.get('request_id'))==request_id,
            campaign_id=str(row.get('campaign_id'))==intent['campaign_id'],
            source=row.get('source')==intent['source'],query=row.get('query')==intent['query'],
            page=row.get('page')==1,locale=parameters.get('locale')==intent['locale'],
            http_status=row.get('http_status')==raw['binding']['http_status'])
        if not all(checks.values()):raise ValueError('saved feed request context differs')
        # Context/status is intentionally narrower than a parsed payload diff.
        report.update(status='capture_and_saved_context_verified',context_checks=checks,
            raw_body_sha256=raw['binding']['body_sha256'],payload_roundtrip_verified=False)
        return report
