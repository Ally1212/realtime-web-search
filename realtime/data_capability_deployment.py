"""Explicit candidate assembly, never automatically discovered or production-mounted.

Trusted callers supply already approved adapters/grants, budgets and an independently
retained v3 pin. Construction performs no DB/network/thread work. Every lifecycle
transition keeps public admission closed until recovery and supervised workers agree.
"""
from __future__ import annotations

import threading
import time

from .data_capability_checkpoint import DataErasureCheckpoint
from .data_capability_executor import CapabilityExecutor
from .data_capability_queries import CapabilityRuntime
from .data_capability_restore_gate import RestoreGate
from .data_capability_store import CapabilityData
from .product import ProductError


class CapabilityDeployment:
    def __init__(self, service, grant_for, *, executors, cursor_secret,
                 checkpoint_id, checkpoint_hash, response_store=None,
                 queue_timeout_seconds=300):
        if not isinstance(service,CapabilityData) or not callable(grant_for):
            raise ValueError('one capability service and trusted current grant resolver required')
        if not isinstance(executors,(list,tuple)) or not 1<=len(executors)<=16:
            raise ValueError('explicit bounded executor list required')
        seen=set()
        for e in executors:
            if (not isinstance(e,CapabilityExecutor) or e.service is not service
                    or e.grant_for is not grant_for or e.response_store is not response_store
                    or e.is_alive() or e.admission_ready is not None):
                raise ValueError('stopped executors must share service, resolver and response store')
            identity=(e.capability,e.provider)
            if identity in seen:raise ValueError('ambiguous capability/provider executor')
            seen.add(identity)
            if getattr(e.execute,'requires_response_store',False) and response_store is None:
                raise ValueError('adapter requires an explicitly configured response store')
        if response_store is not None and (response_store.service is not service or response_store.grant_for is not grant_for):
            raise ValueError('response store must share service and current resolver')
        self.service=service;self.executors=tuple(executors)
        self._pin=checkpoint_hash;self._lifecycle=threading.Lock();self._state=threading.RLock()
        self._phase='constructed';self._initialized=False;self._mounts=[]
        # Validate runtime arguments BEFORE binding the immutable gate.
        self.runtime=CapabilityRuntime(service,grant_for,cursor_secret=cursor_secret,
                                       worker_ready=self.ready,response_store=response_store,
                                       queue_timeout_seconds=queue_timeout_seconds,access_ready=lambda:self._phase_is('running'))
        self.recovery=DataErasureCheckpoint(service,response_store)
        self.gate=RestoreGate(service,checkpoint_id=checkpoint_id,checkpoint_hash=checkpoint_hash)
        self.response_store=response_store
        for e in self.executors:e.admission_ready=lambda:self._phase_is('running')

    def _phase_is(self, value):
        with self._state:return self._phase==value

    def _set(self, value):
        with self._state:self._phase=value

    def _quiescent(self):
        if any(e.is_alive() for e in self.executors):
            raise ProductError('deployment_busy','执行器尚未退出，不能初始化或重放恢复',409)

    def initialize(self):
        """Only independent new-domain schemas/policies; no threads or calls."""
        with self._lifecycle:
            self._quiescent();self.gate.close();self._initialized=False;self._set('initializing')
            try:
                self.service.initialize()
                if self.response_store is not None:self.response_store.initialize()
                self.recovery.initialize()
                for e in self.executors:e.initialize()
                self._initialized=True;self._set('initialized')
            except Exception:
                self._set('failed')
                raise ProductError('deployment_initialization_failed','新数据域初始化失败，未启动来源',503) from None

    def recover_file(self, path, signing_key):
        """Fixed independent pin only; every attempt closes admission first."""
        with self._lifecycle:
            self._quiescent()
            self.gate.close()
            if not self._initialized:
                raise ProductError('deployment_not_initialized','请先显式初始化新数据域',503)
            self._set('recovering')
            try:
                proof=self.recovery.replay_file(path,signing_key,expected_hash=self._pin)
                self._set('recovered')
                return proof
            except Exception as exc:
                self.gate.close();self._set('failed')
                if isinstance(exc,ProductError):raise
                raise ProductError('deployment_recovery_failed','恢复文件核验失败，未启动来源',503) from None

    def ready(self, tenant, capability, provider):
        # Do not hold the state mutex during DB checks or joining workers.
        if not self._phase_is('running'):return False
        try:
            self.gate.require()
            return any(e.ready(tenant,capability,provider) for e in self.executors
                       if (e.capability,e.provider)==(capability,provider))
        except Exception:return False

    def _stop_workers(self, timeout):
        deadline=time.monotonic()+timeout
        for e in self.executors:
            try:e.stop(timeout=max(0,deadline-time.monotonic()))
            except Exception:pass
        return not any(e.is_alive() for e in self.executors)

    @staticmethod
    def _timeout(timeout):
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 0<=timeout<=30:
            raise ValueError('shutdown timeout must be 0–30 seconds')

    def start(self):
        with self._lifecycle:
            self._quiescent()
            if not self._initialized or not self._phase_is('recovered'):
                raise ProductError('restore_required','启动前必须完成本实例固定v3恢复',503)
            self.gate.require();self._set('starting')
            try:
                for e in self.executors:
                    e.start()
                    if not e.is_alive():raise RuntimeError('executor exited during startup')
                self.gate.require();self._set('running')
            except Exception:
                # Stop admission before stopping any partially started workers.
                self._set('failed');self.gate.close();self._stop_workers(5)
                raise ProductError('deployment_start_failed','执行器启动未完成，已关闭新域准入',503) from None

    def stop(self, timeout=5):
        self._timeout(timeout)
        with self._lifecycle:
            self._set('stopping');self.gate.close()
            stopped=self._stop_workers(timeout)
            self._set('stopped' if stopped else 'stopping')
            return dict(stopped=stopped,alive_workers=sum(e.is_alive() for e in self.executors))

    def mount(self, server):
        """Explicit existing authenticated HTTP-server integration; no listener."""
        with self._lifecycle:
            if not self._phase_is('running'):
                raise ProductError('deployment_not_running','先恢复并启动执行器，再挂载新域API',503)
            self.gate.require()
            current=getattr(server,'data_capability_runtime',None)
            if current is not None and current is not self.runtime:
                raise ProductError('deployment_mount_conflict','目标已有其他新域运行时，不覆盖',409)
            if not any(s is server for s in self._mounts):
                if len(self._mounts)>=8:raise ValueError('at most eight explicit server mounts')
                self._mounts.append(server)
            server.data_capability_runtime=self.runtime

    def unmount(self, server):
        with self._lifecycle:
            self._quiescent()
            if getattr(server,'data_capability_runtime',None) is self.runtime:
                del server.data_capability_runtime
            self._mounts=[s for s in self._mounts if s is not server]

    def status(self):
        with self._state:phase=self._phase
        return dict(phase=phase,initialized=self._initialized,recovered=self.gate.ready(),
                    workers=[dict(capability=e.capability,provider=e.provider,alive=e.is_alive()) for e in self.executors],
                    mounts=len(self._mounts),source_acceptance='not_proven_by_assembly')
