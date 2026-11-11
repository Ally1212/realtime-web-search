import os
from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4

from realtime.data_capability_deployment import CapabilityDeployment
from realtime.data_capability_checkpoint import DataErasureCheckpoint
from realtime.data_capability_executor import CapabilityExecutor,SourceResult
from realtime.data_capability_retention import write_checkpoint
from realtime.data_capability_jobs import digest
from tests import test_data_capability_http as fixture
from tests.test_data_capability_facts import result

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class DeploymentHTTPTests(unittest.TestCase):
    setUp=fixture.CapabilityHTTPTests.setUp
    tearDown=fixture.CapabilityHTTPTests.tearDown
    work=fixture.CapabilityHTTPTests.work
    request=fixture.CapabilityHTTPTests.request
    json=fixture.CapabilityHTTPTests.json
    submit=fixture.CapabilityHTTPTests.submit
    wait=fixture.CapabilityHTTPTests.wait

    def test_explicit_assembly_serves_four_domains_through_existing_auth_and_stops_closed(self):
        self.stop_worker.set();self.worker.join(3);self.assertFalse(self.worker.is_alive())
        grant_for=self.runtime.grant_for;calls=[]
        def execute(ctx):
            calls.append(ctx.job_id);facts=result(ctx.parameters,self.provider)
            facts['requested_at']=facts['received_at']=self.now.isoformat()
            return SourceResult(facts,'0.1')
        executors=[CapabilityExecutor(self.data,grant_for,capability=cap,provider=self.provider,scope='assembly-fixture-account',
                                     execute=execute,interval_seconds=0,poll_seconds=.01) for cap in self.grants]
        recovery=DataErasureCheckpoint(self.data);recovery.initialize();key=b'assembly-isolated-http-signing-key-0001';value=recovery.export(key)
        with tempfile.TemporaryDirectory(prefix='sf-deployment-http-') as directory:
            path=Path(directory)/'v3.json';write_checkpoint(path,value)
            d=CapabilityDeployment(self.data,grant_for,executors=executors,cursor_secret=b'isolated-assembly-http-cursor-secret'*2,
                                   checkpoint_id=value['checkpoint_id'],checkpoint_hash=digest(value))
            try:
                d.initialize();d.recover_file(path,key);d.start()
                # Fixture explicitly relinquishes its manual runtime; assembly never overwrites one.
                del self.server.data_capability_runtime;d.mount(self.server)
                for cap in self.grants:
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline and not d.ready(str(self.account['account_id']),cap,self.provider):time.sleep(.01)
                    self.assertTrue(d.ready(str(self.account['account_id']),cap,self.provider))
                    job=self.wait(self.submit(cap,idempotency=str(uuid4())))
                    self.assertEqual(job['status'],'succeeded')
                    self.assertEqual(self.json('/v1/jobs/'+job['id']+'/data')[0],200)
                    self.assertEqual(self.request('/v1/jobs/'+job['id']+'/export?format=zip')[0],200)
                    self.assertEqual(self.json('/v1/jobs/'+job['id'],key=self.other['api_key'])[0],404)
                    self.assertEqual(self.json('/v1/jobs/'+job['id'],site=False)[0],401)
                self.assertEqual(len(calls),4);self.assertEqual(self.json('/v1/jobs')[0],200)
                self.assertTrue(d.stop()['stopped'])
                code,error=self.json('/v1/jobs?domain=data');self.assertEqual(code,503);self.assertEqual(error['code'],'restore_required')
                self.assertEqual(self.json('/v1/jobs')[0],200);self.assertEqual(len(calls),4)
            finally:self.assertTrue(d.stop()['stopped'])
