import argparse
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
from types import MethodType
import unittest
from unittest.mock import patch

from realtime.config import Config
from realtime.google_web_once_cli import main,manifest,IdleGuard
from tests import test_google_web_once_source as fixture


class CLIDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'new-journal'
        self.args=['--journal',str(self.root),'--dataset-id','test','--source-name','test',
            '--collector-id','test','--platform-origin','https://platform.example']

    def test_default_only_prints_plan_without_connection_or_files(self):
        output=io.StringIO()
        with patch('realtime.google_web_once_cli.psycopg.connect',side_effect=AssertionError('must not connect')),redirect_stdout(output):
            self.assertEqual(main(self.args),0)
        data=json.loads(output.getvalue())
        self.assertFalse(data['executed'])
        self.assertEqual(data['execution']['plan']['max_source_calls'],1)
        self.assertFalse(self.root.exists())
        self.assertNotIn('password',output.getvalue())

    def test_advance_without_exact_authorization_never_connects(self):
        with patch('realtime.google_web_once_cli.psycopg.connect',side_effect=AssertionError('must not connect')),redirect_stdout(io.StringIO()):
            self.assertEqual(main(['advance',*self.args]),2)
        self.assertFalse(self.root.exists())

    def test_manifest_binds_journal_origin_and_source_configuration(self):
        args=argparse.Namespace(journal=str(self.root),dataset_id='d',source_name='s',collector_id='c',platform_origin='https://platform.example')
        config=Config()
        first=manifest(config,args)
        self.assertNotEqual(first,manifest(replace(config,openserp_url='http://different:7000'),args))
        args.journal=str(self.root.parent/'different')
        self.assertNotEqual(first,manifest(config,args))


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class IdleGuardDatabaseTests(unittest.TestCase):
    setUpDatabase=fixture.SingleSourceTests.setUpDatabase
    tearDown=fixture.SingleSourceTests.tearDown
    record=fixture.SingleSourceTests.record

    def setUp(self):
        self.setUpDatabase()
        with self.base.connect() as db:
            db.execute('CREATE TABLE sf_jobs(status text)')
            db.execute('CREATE TABLE sf_monitors(enabled boolean)')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.guard=IdleGuard(self.dsn,Path(self.temp.name)/'STOP')

    def test_off_snapshot_is_read_only_and_stop_is_honored(self):
        result=self.guard({})
        self.assertTrue(result['source_read_only'])
        self.assertTrue(all(v==0 for v in result['live_work'].values()))
        self.guard.stop_path.touch()
        with self.assertRaisesRegex(ValueError,'stop'):self.guard({})

    def test_monitors_customer_work_or_enabled_flags_stop(self):
        with self.base.connect() as db:db.execute("INSERT INTO sf_jobs VALUES('running')")
        with self.assertRaises(ValueError):self.guard({})
        with self.base.connect() as db:
            db.execute('DELETE FROM sf_jobs');db.execute('INSERT INTO sf_monitors VALUES(true)')
        with self.assertRaises(ValueError):self.guard({})
        with self.base.connect() as db:db.execute('DELETE FROM sf_monitors')
        self.console.save_settings({'continuous_enabled':True},self.config)
        with self.assertRaises(ValueError):self.guard({})


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'requires isolated PostgreSQL')
class CLIFlowTests(unittest.TestCase):
    setUpDatabase=fixture.SingleSourceTests.setUpDatabase
    setUp=fixture.SingleSourceTests.setUp
    tearDown=fixture.SingleSourceTests.tearDown
    record=fixture.SingleSourceTests.record
    close_server=fixture.SingleSourceTests.close_server
    idle=fixture.SingleSourceTests.idle

    def test_cli_end_to_end_and_repeat_are_bounded(self):
        from tests.test_google_web_once_platform import PlatformHTTPTests
        platform=PlatformHTTPTests('test_writer_and_independent_pinned_consumer');platform.setUp()
        with self.base.connect() as db:
            db.execute('CREATE TABLE sf_jobs(status text)')
            db.execute('CREATE TABLE sf_monitors(enabled boolean)')
        original=platform.client.validate
        def validate(client,message):
            platform.message=message
            return original(message)
        platform.client.validate=MethodType(validate,platform.client)
        try:
            with tempfile.TemporaryDirectory() as td:
                root=Path(td).resolve()/'journal'
                args=['--journal',str(root),'--dataset-id','test','--source-name','test',
                    '--collector-id','test','--platform-origin','https://platform.example']
                output=io.StringIO()
                with patch('realtime.google_web_once_cli.Config',return_value=self.config),redirect_stdout(output):
                    self.assertEqual(main(args),0)
                approval=json.loads(output.getvalue())['manifest_hash']
                with patch('realtime.google_web_once_cli.Config',return_value=self.config),patch(
                        'realtime.google_web_once_cli.SingleRunPlatform',return_value=platform.client),redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['advance',*args,'--authorized-manifest-hash',approval]),0)
                    self.assertEqual(main(['advance',*args,'--authorized-manifest-hash',approval]),0)
                self.assertEqual(json.loads((root/'state.json').read_text())['phase'],'verified')
                self.assertEqual(len(self.calls),1)
                self.assertEqual(len(platform.calls),5)
        finally:platform.doCleanups()


if __name__=='__main__':unittest.main()
