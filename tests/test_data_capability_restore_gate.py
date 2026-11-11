"""Startup admission must follow this process's successful committed replay."""
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_jobs import digest
from realtime.data_capability_restore_gate import RestoreGate
from realtime.data_capability_store import CapabilityData
from realtime.data_capability_responses import ResponseStore
from realtime.data_capability_executor import SourceResult
from realtime.data_capability_subscriptions import CapabilitySubscriptions
from realtime.product import ProductError
from tests import test_data_capability_checkpoint as fixture
from tests import test_data_capability_executor as execution
from tests.test_data_capability_facts import parameters


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class RestoreGateTests(unittest.TestCase):
    setUp=fixture.CheckpointTests.setUp
    store=fixture.CheckpointTests.store
    start=fixture.CheckpointTests.start
    finish=fixture.CheckpointTests.finish
    saved=fixture.CheckpointTests.saved
    export=fixture.CheckpointTests.export
    error=fixture.CheckpointTests.error
    sign=fixture.CheckpointTests.sign
    executor=execution.ExecutorTests.executor
    facts=execution.ExecutorTests.facts

    def tearDown(self):
        for e in getattr(self,'executors',[]):self.assertTrue(e.stop())
        fixture.CheckpointTests.tearDown(self)

    def bind(self,value=None):
        value=value or self.export()
        gate=RestoreGate(self.data,checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
        return gate,value

    def replay(self,value):return self.recovery.replay(value,self.key,expected_hash=digest(value))

    def test_existing_receipt_never_admits_new_service_and_all_data_ports_close(self):
        job=self.saved();value=self.export();self.replay(value)
        gate,_=self.bind(value);self.assertFalse(gate.ready())
        for call in [lambda:self.runtime.job(self.tenant,job['id']),lambda:self.runtime.progress(self.tenant,job['id']),
                     lambda:self.runtime.observations(self.tenant,{}),lambda:self.spool.get(self.tenant,job['id']),
                     lambda:self.data.job_data(self.tenant,job['id'],self.grants['trends_history']),
                     lambda:self.data.observation(self.tenant,job['result_ref'],self.grants['trends_history']),
                     lambda:self.data.create(self.tenant,parameters(),'new-private-key',self.grants['trends_history'],'0.5','USD'),
                     lambda:self.data.claim('trends_history',self.tenant,lambda *args:self.grants['trends_history']),
                     lambda:CapabilitySubscriptions(self.runtime,self.tenant).list()]:
            self.error('restore_required',call)
        self.assertEqual(self.data.get(self.tenant,job['id'])['actual_cost'],job['actual_cost'])
        self.replay(value);self.assertTrue(gate.ready());self.assertEqual(self.runtime.job(self.tenant,job['id'])['id'],job['id'])
        clone=CapabilityData(self.data.dsn,clock=lambda:self.now)
        clone_spool=ResponseStore(clone,lambda t,c:self.grants[c])
        clone_gate=RestoreGate(clone,checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
        self.assertFalse(clone_gate.ready())
        other=DataErasureCheckpoint(clone,clone_spool);other.initialize();other.replay(value,self.key,expected_hash=digest(value))
        self.assertTrue(clone_gate.ready())

    def test_wrong_pin_id_or_legacy_scope_never_unlocks_and_requirement_is_immutable(self):
        value=self.export();gate,_=self.bind(value)
        other=self.export()
        self.error('restore_required',lambda:self.replay(other))
        self.assertFalse(gate.ready())
        with self.assertRaises(ValueError):RestoreGate(self.data,checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_erasure_replays').fetchone()['n'],0)
        self.replay(value);self.assertTrue(gate.ready())

    def test_bad_replay_closes_previously_ready_gate_until_fresh_replay(self):
        value=self.export();gate,_=self.bind(value);self.replay(value);self.assertTrue(gate.ready())
        bad=copy.deepcopy(value);bad['signature']='0'*64
        self.error('invalid_erasure_checkpoint',lambda:self.replay(bad));self.assertFalse(gate.ready())
        self.replay(value);self.assertTrue(gate.ready())
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'bad';path.write_bytes(b'{}')
            self.error('invalid_erasure_checkpoint',lambda:self.recovery.replay_file(path,self.key,expected_hash=digest(value)))
        self.assertFalse(gate.ready())

    def test_missing_receipt_latches_closed_even_when_row_is_put_back(self):
        value=self.export();gate,_=self.bind(value);self.replay(value)
        with self.data.connect() as db:
            saved=db.execute('SELECT * FROM sf_capability_erasure_replays').fetchone()
            db.execute('DELETE FROM sf_capability_erasure_replays')
        self.assertFalse(gate.ready())
        with self.data.connect() as db:
            db.execute('INSERT INTO sf_capability_erasure_replays VALUES(%s,%s,%s,%s,%s)',tuple(saved.values()))
        self.assertFalse(gate.ready())
        self.replay(value);self.assertTrue(gate.ready())

    def test_transaction_failure_or_missing_auxiliary_receipt_cannot_admit(self):
        value=self.export();gate,_=self.bind(value);connect=self.data.connect
        @contextmanager
        def fail_commit():
            with connect() as db:
                yield db
                raise RuntimeError('fixture failure before commit')
        with patch.object(self.data,'connect',side_effect=fail_commit):
            with self.assertRaises(RuntimeError):self.replay(value)
        self.assertFalse(gate.ready())
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_erasure_replays').fetchone()['n'],0)
        self.replay(value);self.assertTrue(gate.ready())
        with self.data.connect() as db:db.execute('DELETE FROM sf_capability_job_erasure_replays')
        self.assertFalse(gate.ready())

    def test_stale_finishing_replay_cannot_unlock_after_a_newer_attempt_closed_gate(self):
        value=self.export();gate,_=self.bind(value);entered,release=threading.Event(),threading.Event()
        original=self.recovery._facts
        def wait(*args):
            result=original(*args);entered.set();self.assertTrue(release.wait(3));return result
        with patch.object(self.recovery,'_facts',side_effect=wait):
            with ThreadPoolExecutor(1) as pool:
                future=pool.submit(self.replay,value);self.assertTrue(entered.wait(2))
                gate.close();release.set()
                with self.assertRaises(ProductError) as error:future.result(timeout=3)
                self.assertEqual(error.exception.code,'restore_required')
        self.assertFalse(gate.ready())
        with self.data.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_erasure_replays').fetchone()['n'],1)
        self.replay(value);self.assertTrue(gate.ready())

    def test_worker_start_and_synchronous_run_cannot_claim_or_call_source_before_replay(self):
        calls=[]
        e=self.executor(lambda ctx:(calls.append(ctx.job_id) or SourceResult(self.facts(ctx),'0.1')),response_store=self.spool)
        job=self.data.create(self.tenant,parameters(),str(uuid4()),self.grants['trends_history'],'0.5','USD')
        gate,value=self.bind()
        self.error('restore_required',e.start);self.error('restore_required',e.run_once)
        self.assertFalse(e.ready(self.tenant,'trends_history',self.tenant));self.assertEqual(calls,[])
        self.assertEqual(self.data.get(self.tenant,job['id'])['status'],'queued')
        self.replay(value);e.start()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and self.data.get(self.tenant,job['id'])['status']!='succeeded':time.sleep(.01)
        self.assertEqual(self.data.get(self.tenant,job['id'])['status'],'succeeded');self.assertEqual(calls,[job['id']])
        self.assertTrue(e.stop());gate.close();self.assertFalse(e.run_once())
        self.assertEqual(calls,[job['id']])

    def test_gate_closing_during_source_call_keeps_known_charge_but_quarantines_facts(self):
        entered,release=threading.Event(),threading.Event()
        def execute(ctx):
            entered.set();self.assertTrue(release.wait(3));return SourceResult(self.facts(ctx),'0.2')
        e=self.executor(execute,response_store=self.spool)
        job=self.data.create(self.tenant,parameters(),str(uuid4()),self.grants['trends_history'],'0.5','USD')
        gate,value=self.bind();self.replay(value)
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(e.run_once);self.assertTrue(entered.wait(2))
            gate.close();release.set();self.assertTrue(future.result(timeout=3))
        raw=self.data.get(self.tenant,job['id']);self.assertEqual(raw['status'],'reconciliation_required');self.assertIsNone(raw['actual_cost'])
        with self.data.connect() as db:
            self.assertEqual(str(db.execute('SELECT actual_cost FROM sf_capability_dispatches').fetchone()['actual_cost']),'0.2')
            self.assertEqual(db.execute('SELECT count(*) n FROM sf_capability_observations').fetchone()['n'],0)
