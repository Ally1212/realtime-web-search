"""One direct existing OpenSERP attempt, saved under a permanently paused task.

No schema initialization, scheduler, queue insertion, or platform call. The
caller must supply a current idle/authorization guard; construction does no I/O.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import UUID
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from .campaign_store import CampaignStore
from .serp_console import ConsoleStore
from .serp_store import SerpStore
from .serp_worker import make_discovery


class OneBackendSession(requests.Session):
    def __init__(self, origin):
        super().__init__()
        self.trust_env=False
        parsed=urlsplit(origin)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in ('http','https') or not parsed.hostname
                or parsed.path not in ('','/')):
            raise ValueError('invalid OpenSERP origin')
        self.endpoint=origin.rstrip('/')+'/google/search'
        self.calls=0
        self.mount('http://',HTTPAdapter(max_retries=0))
        self.mount('https://',HTTPAdapter(max_retries=0))

    def request(self, method, url, **kwargs):
        if method.upper()!='GET' or url!=self.endpoint or self.calls:
            raise ValueError('additional or redirected source request forbidden')
        self.calls+=1
        kwargs['allow_redirects']=False
        return super().request(method,url,**kwargs)


class SingleWebSource:
    def __init__(self, config, *, idle_guard, discovery_factory=make_discovery, evidence_path=None,
                 raw_response_recorder=None):
        if not callable(idle_guard):
            raise ValueError('independent idle and stop guard required')
        self.config, self.idle_guard = config, idle_guard
        self.discovery_factory = discovery_factory
        self.campaign_id = None
        self.request_id = None
        self.attempted = False
        self.evidence = None
        self.raw_response_recorder=raw_response_recorder
        self.evidence_path=Path(evidence_path).absolute() if evidence_path is not None else None
        if self.evidence_path is not None:
            parent=self.evidence_path.parent
            if (parent.resolve()!=parent or not parent.is_dir() or parent.stat().st_mode & 0o077
                    or self.evidence_path.exists() or self.evidence_path.is_symlink()):
                raise ValueError('new evidence path in private journal required')

    def _save_evidence(self):
        if self.evidence_path is not None:
            from .google_web_once import _write
            _write(self.evidence_path,self.evidence)

    def assert_idle(self, plan):
        self.idle_guard(deepcopy(plan))

    def _paused(self, base):
        with base.connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            row=db.execute("SELECT c.status,o.started,EXISTS(SELECT 1 FROM whale_serp_task_uploads u WHERE u.campaign_id=c.id) upload FROM campaigns c JOIN serp_task_options o ON o.campaign_id=c.id WHERE c.id=%s",(self.campaign_id,)).fetchone()
        if not row or row['status']!='paused' or row['started'] or row['upload']:
            raise ValueError('one-shot campaign was activated or upload-enabled')

    def acquire_once(self, plan):
        if self.attempted:
            raise ValueError('source attempt already consumed')
        if (plan['source']!='google_web' or plan['provider']!='openserp'
                or plan['page']!=1 or plan['max_source_calls']!=1
                or plan['country']!='US' or plan['language']!='en'):
            raise ValueError('unsupported single source scope')
        self.assert_idle(plan)
        self.attempted=True
        base=CampaignStore(self.config.database_url,initialize=False)
        console=ConsoleStore(self.config.database_url)
        serp=SerpStore(self.config.database_url)
        task=console.create_task(dict(query=plan['query'],sources=['google_web'],start=False,
            upload_to_whale=False,mode='once',max_successful_searches=1,
            options=dict(web_pages=1,web_region='US',web_language='en')),self.config)
        self.campaign_id=task['campaign_id']
        self._paused(base)
        campaign=base.campaign(self.campaign_id)
        campaign['serp_options']=task['options']
        discovery=self.discovery_factory(self.config,base,serp,campaign)
        request_ids=[]
        def still_allowed():
            self.assert_idle(plan)
            self._paused(base)
            return True
        def save(**event):
            if request_ids:
                raise ValueError('second source result forbidden')
            rid=serp.record(self.campaign_id,**event)
            request_ids.append(rid)
            return rid
        discovery.should_continue=still_allowed
        discovery.serp_response_recorder=save
        if self.raw_response_recorder is not None:
            discovery.transport.raw_response_recorder=self.raw_response_recorder
        session=OneBackendSession(self.config.openserp_url)
        discovery.transport.local.openserp_session=session
        failed=False
        try:
            # Bypass discover_many/page retry loops and query/page leases. The
            # existing attempt still respects shared rate/proxy cooldown slots.
            discovery._attempt('openserp',plan['query'],1)
        except Exception:
            failed=True
        finally:
            try:
                attempts=discovery.attempts
                allowed=('provider','page','success','results','seconds','error','http_status',
                         'page_classification','upstream_request_id','upstream_version',
                         'upstream_attempts','upstream_cache_status','network_bytes')
                self.evidence=dict(campaign_id=self.campaign_id,source_calls=len(attempts),
                    backend_http_calls=session.calls,
                    private_response_capture=getattr(discovery.transport.local,'last_private_response_capture',None),
                    attempt_failed=failed,attempts=[{k:a.get(k) for k in allowed} for a in attempts],
                    saved_request_ids=request_ids[:],campaign_remains_paused=None)
                self._save_evidence()
            finally:
                try:discovery.close()
                finally:session.close()
        self._paused(base)
        self.evidence['campaign_remains_paused']=True
        self._save_evidence()
        if len(request_ids)!=1 or len(discovery.attempts)!=1:
            raise ValueError('source attempt has no unique saved outcome')
        self.request_id=str(UUID(str(request_ids[0])))
        return self.request_id

    def saved_public_request(self, request_id):
        request_id=str(UUID(str(request_id)))
        base=CampaignStore(self.config.database_url,initialize=False)
        with base.connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout='5s'")
            row=db.execute("""SELECT r.request_id,r.source,r.query,r.page,r.classification,
                r.completed_at,r.response FROM serp_requests r WHERE r.request_id=%s
                AND r.source='google_web' AND NOT EXISTS(SELECT 1 FROM serp_task_options o
                WHERE o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')""",
                (request_id,)).fetchone()
        if row is None:
            raise ValueError('public saved Web request unavailable')
        return row
