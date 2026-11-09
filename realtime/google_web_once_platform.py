"""Single-message Writer and independent pinned Consumer adapter components.

Construction does no I/O. No registration, task claiming, bulk retry, or source
request exists here. Source/DB adapter wiring remains separate.
"""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from .google_whale_readback import WhaleReadbackClient
from .whale_serp import processing_receipts, receipt_query


class PlatformCallError(RuntimeError):
    def __init__(self, status=None, retry_after=None):
        super().__init__('platform request did not complete successfully')
        self.status_code = status
        self.retry_after = retry_after


def _endpoint(base_url, allow_loopback):
    parsed = urlsplit(base_url)
    loopback = False
    try:
        loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        pass
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('','/') or not parsed.hostname
            or not (parsed.scheme == 'https' or
                    (allow_loopback and loopback and parsed.scheme == 'http'))):
        raise ValueError('explicit HTTPS platform origin required')
    return base_url.rstrip('/')


def _session():
    session = requests.Session()
    session.trust_env = False
    session.mount('http://', HTTPAdapter(max_retries=0))
    session.mount('https://', HTTPAdapter(max_retries=0))
    return session


class SingleRunPlatform:
    def __init__(self, base_url, *, writer_key, consumer_key, allow_loopback=False):
        self.base_url = _endpoint(base_url, allow_loopback)
        if not all(isinstance(v,str) and v.strip() for v in (writer_key,consumer_key)):
            raise ValueError('separate explicit Writer and Consumer credentials required')
        if writer_key == consumer_key:
            raise ValueError('Consumer credential must be independent')
        self._writer_key = writer_key
        self._writer = _session()
        self._consumer_session = _session()
        # Consumer constructor intentionally accepts only production HTTPS. A
        # loopback-only test origin is substituted after the same origin guard.
        self._reader = WhaleReadbackClient(
            self.base_url if self.base_url.startswith('https:') else 'https://loopback.invalid',
            consumer_key, session=self._consumer_session)
        self._reader.base_url = self.base_url

    def close(self):
        self._writer.close()
        self._consumer_session.close()

    def _post(self, path, body):
        try:
            with self._writer.post(self.base_url+path, json=body,
                    headers={'Authorization':'Bearer '+self._writer_key},
                    timeout=(5,20), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise PlatformCallError(response.status_code,response.headers.get('Retry-After'))
                raw=bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw)>1024*1024:
                        raise PlatformCallError()
                return json.loads(raw)
        except (requests.RequestException,ValueError,UnicodeError):
            # Never propagate request URLs, response bodies or credentials.
            raise PlatformCallError() from None

    def validate(self, message):
        reply = self._post('/v1/documents/validate',[message])
        return (isinstance(reply,dict) and reply.get('dry_run') is True
            and reply.get('valid') is True and isinstance(reply.get('results'),list)
            and len(reply['results'])==1 and isinstance(reply['results'][0],dict)
            and type(reply['results'][0].get('index')) is int and reply['results'][0].get('index')==0
            and reply['results'][0].get('valid') is True)

    def bulk_once(self, message):
        reply = self._post('/v1/documents/bulk',[message])
        if (not isinstance(reply,list) or len(reply)!=1 or not isinstance(reply[0],dict)
                or reply[0].get('receipt_status') not in {'queued','accepted','duplicate'}):
            raise PlatformCallError()
        result=reply[0]
        if any(not isinstance(result.get(k),str) or not result[k] or len(result[k])>256
                for k in ('document_id','version_id')):
            raise PlatformCallError()
        return {k:result[k] for k in ('document_id','version_id')}

    def receipt(self, message, acknowledgment):
        row=dict(payload=message,dataset_id=message['dataset_id'],
            record_key=message['source']['source_record_key'],receipt=acknowledgment)
        reply=self._post('/v1/ingest/receipts',[receipt_query(row)])
        return processing_receipts([row],reply)[0]

    def readback(self, target, expected_payload):
        return self._reader.read_full_run(target,expected_payload=expected_payload)
