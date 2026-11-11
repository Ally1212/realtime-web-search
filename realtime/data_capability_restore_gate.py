"""Opt-in per-service startup admission; a restored receipt never self-admits.

Bind every candidate service/process from an independently retained current pin.
Trusted schema/replay/fee-audit ports remain available while public reads close.
"""
import re
import threading
from uuid import UUID

from .product import ProductError


def closed():
    raise ProductError('restore_required','新数据域尚未完成本进程恢复核验，未提供数据或来源调用',503)


class RestoreGate:
    def __init__(self, service, *, checkpoint_id, checkpoint_hash):
        if (not isinstance(checkpoint_id,str) or str(UUID(checkpoint_id))!=checkpoint_id
                or not isinstance(checkpoint_hash,str) or not re.fullmatch('[0-9a-f]{64}',checkpoint_hash)):
            raise ValueError('independently pinned canonical checkpoint UUID and SHA256 required')
        if getattr(service,'restore_gate',None) is not None:
            raise ValueError('service already has an immutable restore requirement')
        self.service=service
        self._id,self._hash=checkpoint_id,checkpoint_hash
        self._lock=threading.RLock();self._ticket=object();self._admitted=False;self._proof=None
        service.restore_gate=self

    def close(self):
        with self._lock:
            self._ticket=object();self._admitted=False;self._proof=None

    def begin(self, service):
        with self._lock:
            if service is not self.service:raise ValueError('restore gate belongs to another service instance')
            self.close()
            return self._ticket

    def validate(self, value, expected_hash):
        if (value['checkpoint_id']!=self._id or expected_hash!=self._hash
                or value['schema_version']!='searchflow-data-erasure-3'):
            closed()

    def _receipt(self):
        try:
            with self.service.connect() as db:
                row=db.execute('SELECT r.checkpoint_hash,r.fact_entries,r.response_entries,j.job_entries,s.subscription_entries '
                               'FROM sf_capability_erasure_replays r LEFT JOIN sf_capability_job_erasure_replays j '
                               'ON j.checkpoint_id=r.checkpoint_id LEFT JOIN sf_capability_subscription_erasure_replays s '
                               'ON s.checkpoint_id=r.checkpoint_id WHERE r.checkpoint_id=%s',(self._id,)).fetchone()
            if (not row or row['checkpoint_hash']!=self._hash or self._proof is None
                    or any(row[k]!=self._proof[k] for k in ['fact_entries','response_entries','job_entries','subscription_entries'])):
                return False
            return True
        except Exception:
            return False

    def admit(self, ticket, proof):
        with self._lock:
            if ticket is not self._ticket:closed()
            if proof['checkpoint_id']!=self._id or proof['checkpoint_hash']!=self._hash:closed()
            self._proof={k:proof[k] for k in ['fact_entries','response_entries','job_entries','subscription_entries']}
            if not self._receipt():
                self.close();closed()
            self._admitted=True

    def fail(self, ticket):
        with self._lock:
            if ticket is self._ticket:self.close()

    def require(self):
        with self._lock:
            if not self._admitted:closed()
            if not self._receipt():
                self.close();closed()

    def ready(self):
        try:self.require();return True
        except ProductError:return False
