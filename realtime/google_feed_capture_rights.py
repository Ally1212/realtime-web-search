"""Read-only registry enforcement around the opt-in private feed journal.

No registrations, approvals, migrations, source calls or automatic activation.
The two rights fields below are local storage contracts, not a Whale schema.
"""
from __future__ import annotations

from .google_domain import DomainError,parse_time
from .google_rights import authorize

ENDPOINTS={'google_news':'https://news.google.com/rss/search',
           'google_trends':'https://trends.google.com/trending/rss'}
FIELDS=('raw_response_entity','capture_context')


class RightsBoundFeedCapture:
    def __init__(self,journal,*,registry,principal,grant_id,adapter_id,adapter_version,
                 source,query,locale,production=True):
        if (source not in ENDPOINTS or type(production) is not bool
                or not isinstance(query,str) or not query.strip()
                or not isinstance(locale,str) or not locale
                or source=='google_trends' and (query!='trending-now' or len(locale)!=2 or not locale.isalpha())):
            raise ValueError('explicit feed source and production mode required')
        if principal.tenant_id!=journal.tenant_id or not principal.subject_ref:
            raise DomainError('not_authorized','Explicit tenant subject required',403)
        self.journal=journal;self.registry=registry;self.principal=principal
        self.grant_id=grant_id;self.adapter_id=adapter_id;self.adapter_version=adapter_version
        self.source=source;self.query=query;self.locale=locale;self.production=production
        self.attempt_id=journal.attempt_id

    def check(self,*,reading=False):
        now=self.journal.clock()
        with self.registry.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            db.execute("SET LOCAL statement_timeout='5s'")
            principal=self.registry.assert_current(self.principal,now=now,db=db)
            if not principal.roles or not reading and 'manager' not in principal.roles:
                raise DomainError('not_authorized','Subject role does not permit private capture operation',403)
            source=self.registry.source(principal.tenant_id,self.adapter_id,self.adapter_version,db=db)
            grant=self.registry.grant(principal.tenant_id,self.grant_id,db=db)
            if (grant.source_adapter!=self.adapter_id or grant.source_version!=self.adapter_version
                    or source.endpoint!=ENDPOINTS[self.source]
                    or source.approval_ref!=self.journal.authorization_ref):
                raise DomainError('not_authorized','Feed source or approval binding differs',403)
            parts=self.locale.split(':',1)
            if (parts[0].upper() not in source.coverage.get('countries',())
                    or self.source=='google_news' and (len(parts)!=2 or parts[1] not in source.coverage.get('languages',()))):
                raise DomainError('not_covered','Feed country/language scope is not registered',403)
            rights=[]
            for field in FIELDS:
                right=authorize(principal,grant,source,field,'raw_store',now=now,production=self.production)
                if reading:
                    authorize(principal,grant,source,field,'internal_display',now=now,production=self.production)
                rights.append(right)
            deadlines=[parse_time(grant.expires_at),*(r.retention_deadline for r in rights if r.retention_deadline is not None)]
            if self.journal.expires_at>min(deadlines):
                raise DomainError('use_not_permitted','Private capture expiry exceeds live rights',403)
            if parse_time(now)>=self.journal.expires_at:
                raise DomainError('expired','Private capture has expired',410)
            return self._ownership(grant.asset_id)

    def _ownership(self,asset_id):
        return dict(tenant_id=self.principal.tenant_id,asset_id=asset_id,grant_id=self.grant_id,
            adapter_id=self.adapter_id,adapter_version=self.adapter_version,
            source=self.source,query=self.query,locale=self.locale)

    def _assert_ownership(self,scope):
        if self.journal._read_event(self.attempt_id,'rights')!=scope:
            raise DomainError('not_authorized','Private feed ownership differs',403)

    def _context(self,kw):
        if any(kw.get(k)!=v for k,v in dict(source=self.source,query=self.query,locale=self.locale).items()):
            raise DomainError('not_authorized','Private feed execution context differs',403)

    def begin(self,**kw):
        self._context(kw);scope=self.check()
        self.journal._event(self.attempt_id,'rights',scope,exclusive=True)
        self.journal.begin(**kw)
        self.check()

    def __call__(self,**kw):
        self._context(kw);self._assert_ownership(self.check())
        result=self.journal(**kw)
        self.check()
        return result

    def saved(self,**kw):
        self._assert_ownership(self.check());self.journal.saved(**kw);self.check()

    def reconcile(self,request_id,*,read_saved):
        self._assert_ownership(self.check(reading=True))
        result=self.journal.reconcile(request_id,read_saved=read_saved)
        self.check(reading=True)
        return result

    def cleanup(self,*,reason):
        """Explicit operator cleanup, never an automatic read-failure action.

        A tenant-rights shared row lock serializes this operation with registry
        writers. Revoked storage rights are not needed to erase the same scope;
        the current manager subject must still explicitly own its grant scope.
        """
        if reason not in {'revoked','expired'}:raise ValueError('explicit cleanup cause required')
        now=self.journal.clock()
        with self.registry.connect() as db:
            db.execute("SET LOCAL statement_timeout='5s'")
            db.execute("SET LOCAL lock_timeout='3s'")
            state=db.execute('SELECT epoch FROM sf_google_tenant_rights WHERE tenant_id=%s FOR SHARE',
                             (self.principal.tenant_id,)).fetchone()
            if state is None:raise DomainError('not_authorized','Cleanup tenant unavailable',403)
            principal=self.registry.assert_current(self.principal,now=now,db=db)
            subject=self.registry._read(principal.tenant_id,'subject',principal.subject_ref,db=db)
            if 'manager' not in principal.roles or self.grant_id not in subject.get('grant_ids',()):
                raise DomainError('not_authorized','Cleanup scope is not assigned to this manager',403)
            source=self.registry.source(principal.tenant_id,self.adapter_id,self.adapter_version,db=db)
            grant=self.registry.grant(principal.tenant_id,self.grant_id,db=db)
            if (grant.source_adapter!=self.adapter_id or grant.source_version!=self.adapter_version
                    or source.endpoint!=ENDPOINTS[self.source]
                    or source.approval_ref!=self.journal.authorization_ref):
                raise DomainError('not_authorized','Cleanup source scope differs',403)
            deadlines=[self.journal.expires_at,parse_time(grant.expires_at),
                *(r.retention_deadline for r in source.field_rights if r.field in FIELDS and r.retention_deadline is not None)]
            if reason=='revoked' and not (grant.revoked or source.admission=='revoked'):
                raise DomainError('not_authorized','No explicit source/grant revocation',403)
            if reason=='expired' and parse_time(now)<min(deadlines):
                raise DomainError('not_authorized','Private scope has not expired',403)
            ids=[self.attempt_id,*(self.journal._event_id(self.attempt_id,stage)
                                  for stage in ('intent','captured','saved','rights'))]
            result=self.journal.vault.remove_records(tenant_id=principal.tenant_id,request_ids=ids,
                authorization_ref=self.journal.authorization_ref,required_events={
                    self.journal._event_id(self.attempt_id,'rights'):dict(version='private-feed-journal-1',
                        request_id=self.attempt_id,stage='rights',value=self._ownership(grant.asset_id))})
            return dict(result,reason=reason,request_id=self.attempt_id,registry_mutations=0,
                backup_erasure_verified=False,platform_erasure_verified=False)
