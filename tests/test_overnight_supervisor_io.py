import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('supervisor',Path(__file__).parents[1]/'scripts/overnight_source_supervisor.py')
supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)

class SupervisorInputTests(unittest.TestCase):
    def test_large_identity_list_never_becomes_command_argument(self):
        ids=['12345678-1234-1234-1234-123456789012']*10000
        with patch.object(supervisor,'cmd',return_value='{}') as command:
            self.assertEqual(supervisor.sample('2026-11-17T10:00:00+00:00',ids),{})
        args,stdin,timeout=command.call_args.args
        self.assertGreater(len(stdin),128*1024)
        self.assertLess(max(map(len,args)),32768)
        self.assertEqual(json.loads(stdin)['ids'],ids)

if __name__=='__main__':unittest.main()
