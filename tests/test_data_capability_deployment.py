"""Synthetic source assembly, real isolated PostgreSQL, no external traffic."""
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from realtime.data_capability_deployment import CapabilityDeployment
from realtime.data_capability_executor import CapabilityExecutor,SourceResult
from realtime.data_capability_jobs import digest
from realtime.data_capability_retention import write_checkpoint
from realtime.product import ProductError
from tests import test_data_capability_checkpoint as fixture
from tests.test_data_capability_facts import parameters,result


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class DeploymentTests(unittest.TestCase):
    store=fixture.CheckpointTests.store
    error=fixture.CheckpointTests.error

    def setUp(self):
        fixture.CheckpointTests.setUp(self)
        self.grant_for=self.spool.grant_for;self.calls=[];self.deployments=[]
        self.value=self.recovery.export(self.key)
        self.temp=tempfile.TemporaryDirectory(prefix='sf-deployment-');self.path=Path(self.temp.name)/'checkpoint.json'
        write_checkpoint(self.path,self.value)

    def tearDown(self):
        for d in self.deployments:self.assertTrue(d.stop()['stopped'])
        self.temp.cleanup();fixture.CheckpointTests.tearDown(self)

    def executor(self,cap='trends_history',execute=None):
        def default(ctx):
            self.calls.append((cap,ctx.job_id));facts=result(ctx.parameters,self.tenant)
            facts['requested_at']=facts['received_at']=self.now.isoformat()
            return SourceResult(facts,'0.1')
        return CapabilityExecutor(self.data,self.grant_for,capability=cap,provider=self.tenant,scope=self.tenant,
                                  execute=execute or default,interval_seconds=0,poll_seconds=.01,response_store=self.spool)

    def build(self,executors=None,**kwargs):
        d=CapabilityDeployment(self.data,self.grant_for,executors=executors or [self.executor(c) for c in self.grants],
                               cursor_secret=b'isolated-deployment-cursor-secret'*2,
                               checkpoint_id=self.value['checkpoint_id'],checkpoint_hash=digest(self.value),response_store=self.spool,**kwargs)
        self.deployments.append(d);return d

    def prepared(self,executors=None):
        d=self.build(executors);d.initialize();d.recover_file(self.path,self.key);return d

    def wait(self,call):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            value=call()
            if value:return value
            time.sleep(.01)
        self.fail('bounded synthetic worker wait expired')

    def test_construction_performs_no_database_work_and_enforces_initialize_recovery_start_order(self):
        with patch.object(self.data,'connect',side_effect=AssertionError('constructor must be pure')):d=self.build()
        self.assertEqual(d.status()['phase'],'constructed');self.assertEqual(self.calls,[])
        self.error('restore_required',d.start)
        self.error('deployment_not_initialized',lambda:d.recover_file(self.path,self.key))
        self.error('deployment_not_running',lambda:d.mount(SimpleNamespace()))
        d.initialize();self.assertEqual(d.status()['phase'],'initialized')
        self.error('restore_required',d.start)
        d.recover_file(self.path,self.key);self.assertEqual(d.status()['phase'],'recovered')
        self.assertFalse(d.ready(self.tenant,'trends_history',self.tenant))
        d.start();self.wait(lambda:d.ready(self.tenant,'trends_history',self.tenant))
        self.assertEqual(d.stop(),dict(stopped=True,alive_workers=0));self.error('restore_required',d.start)
        self.assertEqual(self.calls,[])

    def test_four_domains_mount_submission_actual_billing_and_restart(self):
        d=self.prepared();d.start();server=SimpleNamespace();d.mount(server);d.mount(server)
        self.assertIs(server.data_capability_runtime,d.runtime);self.assertEqual(d.status()['mounts'],1)
        jobs=[]
        for cap in self.grants:
            self.wait(lambda:d.ready(self.tenant,cap,self.tenant))
            body=dict(parameters(cap),budget=dict(maximum_cost='0.5',currency='USD'))
            job=d.runtime.submit(self.tenant,body,str(uuid4()));jobs.append(job)
        for job in jobs:
            row=self.wait(lambda j=job:self.data.get(self.tenant,j['id']) if self.data.get(self.tenant,j['id'])['status']=='succeeded' else None)
            self.assertEqual(row['actual_cost'],'0.1')
            self.assertEqual(d.runtime.job_data(self.tenant,row['id'])['observation']['capability'],row['capability'])
        self.assertEqual(len(self.calls),4)
        d.stop();self.assertIs(server.data_capability_runtime,d.runtime)
        self.error('restore_required',lambda:d.runtime.jobs(self.tenant,{}))
        d.recover_file(self.path,self.key)
        self.error('deployment_not_running',lambda:d.runtime.jobs(self.tenant,{}))
        d.start();self.wait(lambda:d.ready(self.tenant,'trends_history',self.tenant))
        self.assertEqual(len(self.calls),4)
        d.stop();d.unmount(server);self.assertFalse(hasattr(server,'data_capability_runtime'))

    def test_partial_start_failure_never_enters_source_and_failed_initialization_never_starts(self):
        es=[self.executor('trends_history'),self.executor('keyword_volume')];d=self.build(es)
        with patch.object(es[1],'initialize',side_effect=RuntimeError('synthetic private failure')):
            self.error('deployment_initialization_failed',d.initialize)
        self.assertFalse(d.status()['initialized']);self.assertEqual(self.calls,[])
        d.initialize();d.recover_file(self.path,self.key)
        job=self.data.create(self.tenant,parameters(),str(uuid4()),self.grants['trends_history'],'0.5','USD')
        with patch.object(es[1],'start',side_effect=RuntimeError('synthetic private failure')):
            self.error('deployment_start_failed',d.start)
        self.assertEqual(self.calls,[]);self.assertFalse(any(e.is_alive() for e in es))
        self.assertEqual(self.data.get(self.tenant,job['id'])['status'],'queued')
        self.assertFalse(d.status()['recovered']);d.recover_file(self.path,self.key);d.start()
        self.wait(lambda:self.data.get(self.tenant,job['id'])['status']=='succeeded');self.assertEqual(len(self.calls),1)

    def test_busy_inflight_stop_timeout_does_not_pretend_thread_exited_or_allow_recovery(self):
        entered=threading.Event();release=threading.Event()
        def execute(ctx):
            entered.set();release.wait(3)
            facts=result(ctx.parameters,self.tenant);facts['requested_at']=facts['received_at']=self.now.isoformat()
            return SourceResult(facts,'0.2')
        e=self.executor(execute=execute);d=self.prepared([e]);d.start()
        try:
            self.wait(lambda:d.ready(self.tenant,'trends_history',self.tenant))
            body=dict(parameters(),budget=dict(maximum_cost='0.5',currency='USD'))
            job=d.runtime.submit(self.tenant,body,str(uuid4()));self.assertTrue(entered.wait(3))
            self.assertEqual(d.stop(timeout=0),dict(stopped=False,alive_workers=1))
            self.assertTrue(e.is_alive());self.assertFalse(d.ready(self.tenant,'trends_history',self.tenant))
            self.error('deployment_busy',lambda:d.recover_file(self.path,self.key))
            self.error('deployment_busy',d.initialize)
            self.error('deployment_busy',lambda:d.unmount(SimpleNamespace()))
        finally:release.set()
        self.assertTrue(d.stop()['stopped'])
        row=self.data.get(self.tenant,job['id']);self.assertEqual(row['status'],'reconciliation_required')
        with self.data.connect() as db:
            charge=db.execute('SELECT actual_cost FROM sf_capability_dispatches WHERE job_id=%s',(job['id'],)).fetchone()
            self.assertEqual(str(charge['actual_cost']),'0.2');self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'],0)

    def test_wrong_file_pin_and_missing_receipt_keep_readiness_closed_and_never_mount(self):
        d=self.build();d.initialize();self.path.write_bytes(b'{}')
        self.error('invalid_erasure_checkpoint',lambda:d.recover_file(self.path,self.key));self.assertFalse(d.status()['recovered'])
        self.error('deployment_not_running',lambda:d.mount(SimpleNamespace()))
        write_checkpoint(self.path,self.value);d.recover_file(self.path,self.key);d.start()
        self.wait(lambda:d.ready(self.tenant,'trends_history',self.tenant))
        with self.data.connect() as db:db.execute('DELETE FROM sf_capability_subscription_erasure_replays')
        self.assertFalse(d.ready(self.tenant,'trends_history',self.tenant))
        self.error('restore_required',lambda:d.mount(SimpleNamespace()));self.assertEqual(self.calls,[])

    def test_mount_and_unmount_never_overwrite_or_delete_another_runtime(self):
        d=self.prepared();d.start();other=object();server=SimpleNamespace(data_capability_runtime=other)
        self.error('deployment_mount_conflict',lambda:d.mount(server));self.assertIs(server.data_capability_runtime,other)
        empty=SimpleNamespace();d.mount(empty)
        self.error('deployment_busy',lambda:d.unmount(empty))
        d.stop();empty.data_capability_runtime=other;d.unmount(empty);self.assertIs(empty.data_capability_runtime,other)

    def test_configuration_rejects_duplicates_mismatched_components_and_existing_owner_before_binding(self):
        e=self.executor()
        base=dict(executors=[e],cursor_secret=b'x'*32,checkpoint_id=self.value['checkpoint_id'],checkpoint_hash=digest(self.value),response_store=self.spool)
        for change in [dict(executors=[]),dict(executors=[e,e]),dict(executors=[self.executor()],response_store=None),dict(cursor_secret=b'short')]:
            with self.subTest(change=list(change)):
                with self.assertRaises(ValueError):CapabilityDeployment(self.data,self.grant_for,**dict(base,**change))
                self.assertIsNone(getattr(self.data,'restore_gate',None))
        wrong=self.executor();wrong.grant_for=lambda t,c:self.grants[c]
        with self.assertRaises(ValueError):CapabilityDeployment(self.data,self.grant_for,**dict(base,executors=[wrong]))
        d=self.build([e]);self.assertFalse(d.status()['recovered'])
        with self.assertRaises(ValueError):CapabilityDeployment(self.data,self.grant_for,**base)
