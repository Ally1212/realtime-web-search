from contextlib import redirect_stdout,redirect_stderr
import ast
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
import os
import sys

spec=importlib.util.spec_from_file_location('once_remote',Path(__file__).parents[1]/'scripts/google_web_once_remote.py')
remote=importlib.util.module_from_spec(spec);spec.loader.exec_module(remote)


class RemoteBoundaryTests(unittest.TestCase):
    def test_unapproved_advance_never_reads_or_connects(self):
        with patch.object(remote.subprocess,'run') as run,patch.object(remote.Path,'read_text') as read:
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):remote.main(['advance'])
        run.assert_not_called();read.assert_not_called()

    def test_default_plan_transmits_no_consumer_material(self):
        with tempfile.TemporaryDirectory() as td:
            review=Path(td)/'review.json'
            review.write_text(json.dumps(dict(results=dict(plan=dict(response=dict(manifest_hash=remote.PLAN_HASH))),cli_args=['--journal','/app/state/test/run'])))
            reply=SimpleNamespace(returncode=0,stdout=json.dumps(dict(exit_code=0,response=dict(manifest_hash=remote.PLAN_HASH))))
            with patch.object(remote,'REVIEW',review),patch.object(remote.subprocess,'run',return_value=reply) as run,redirect_stdout(io.StringIO()):
                self.assertEqual(remote.main([]),0)
            outer=ast.parse(run.call_args.kwargs['input'])
            call=outer.body[1].value
            inner=next(k.value.value for k in call.keywords if k.arg=='input')
            packet=next(ast.literal_eval(node.value) for node in ast.parse(inner).body
                if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='packet' for t in node.targets))
            self.assertNotIn('consumer_key',packet)
            self.assertEqual(run.call_args.args[0][-1],'python3 -')

    def test_failed_consumer_precheck_never_enters_mutating_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            key=root/'.config/searchflow/whale-consumer-read-key'
            key.parent.mkdir(parents=True);key.write_text('synthetic-consumer-key');key.chmod(0o600)
            review=root/'review.json'
            review.write_text(json.dumps(dict(results=dict(plan=dict(response=dict(manifest_hash=remote.PLAN_HASH))),
                cli_args=['--platform-origin','https://platform.example'])))
            reply=SimpleNamespace(returncode=0,stdout=json.dumps(dict(exit_code=2,response=dict(phase='blocked_consumer_read_preflight'))))
            with patch.object(remote,'EXECUTION_REVIEW_SUSPENDED',False),patch.object(remote,'REVIEW',review),patch.object(remote.Path,'home',return_value=root),patch.object(
                    remote.subprocess,'run',return_value=reply) as run,redirect_stdout(io.StringIO()):
                self.assertEqual(remote.main(['advance','--authorized-manifest-hash',remote.PLAN_HASH]),2)
            outer=ast.parse(run.call_args.kwargs['input']);call=outer.body[1].value
            inner=next(k.value.value for k in call.keywords if k.arg=='input')
            output=io.StringIO();reader=Mock();reader.verify.side_effect=RuntimeError('fixture credit unavailable')
            with patch('realtime.google_web_once_cli.main') as cli,patch('realtime.google_whale_readback.WhaleReadbackClient',return_value=reader),patch(
                    'realtime.config.Config',return_value=SimpleNamespace(whale_dataset_id='test')),patch.dict(os.environ),patch.object(
                    sys,'path',list(sys.path)),redirect_stdout(output),self.assertRaises(SystemExit):
                exec(inner,{})
            cli.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['response']['phase'],'blocked_consumer_read_preflight')

    def test_suspended_review_blocks_even_matching_authorization(self):
        with patch.object(remote.Path,'read_text') as read,patch.object(remote.subprocess,'run') as run,redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):remote.main(['advance','--authorized-manifest-hash',remote.PLAN_HASH])
        read.assert_not_called();run.assert_not_called()


if __name__=='__main__':unittest.main()
