import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

spec = importlib.util.spec_from_file_location(
    "capacity_supervisor", Path(__file__).resolve().parents[1] / "scripts/supervise_openserp_capacity.py"
)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class CapacitySupervisorTests(unittest.TestCase):
    def exercise(self, deadline, health, reports):
        state = MagicMock()
        (state / "manifest.json").read_text.return_value = json.dumps({"deadline": deadline})
        with patch.object(supervisor, "STATE", state), \
             patch.object(supervisor.time, "time", return_value=100), \
             patch.object(supervisor.time, "sleep"), \
             patch.object(supervisor, "service_responsive", side_effect=health), \
             patch.object(supervisor, "control", side_effect=reports) as control, \
             patch.object(supervisor.subprocess, "run", return_value=Mock(stdout="true")) as run, \
             patch.object(supervisor, "finish") as finish:
            supervisor.supervise()
            finish.assert_called_once()
            return control.call_count, [call.args[0] for call in run.call_args_list]

    def test_expired_deadline_never_restarts_worker_or_search_service(self):
        calls, commands = self.exercise("1970-01-01T00:00:01+00:00", [], [])
        self.assertEqual(calls, 0)
        self.assertEqual(commands, [])

    def test_three_unresponsive_probes_restart_search_service(self):
        _, commands = self.exercise(
            "2099-01-01T00:00:00+00:00", [False, False, False],
            [{"status": "running"}, {"status": "running"}, {"status": "finished"}],
        )
        self.assertEqual(sum(command == ["docker", "compose", "restart", "openserp"] for command in commands), 1)

    def test_responsive_service_with_google_blocks_does_not_restart(self):
        _, commands = self.exercise(
            "2099-01-01T00:00:00+00:00", [True],
            [{"status": "finished", "captcha_rate": 1, "search_success_rate": 0}],
        )
        self.assertEqual(commands, [])
