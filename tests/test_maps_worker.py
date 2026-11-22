"""Maps leader ownership and idle availability are service invariants."""
import os
import unittest
from unittest.mock import MagicMock, patch
from realtime import maps_worker


class MapsWorkerTests(unittest.TestCase):
    def exercise(self, acquired):
        store = MagicMock()
        schema, leader, load = MagicMock(), MagicMock(), MagicMock()
        leader.execute.return_value.fetchone.return_value = {'acquired': acquired}
        load.execute.return_value.fetchone.return_value = {'busy': False}
        store.base.connect.side_effect = [schema, leader, load, load]
        for connection in (schema, leader, load):
            connection.__enter__.return_value = connection
        with patch.dict(os.environ, {'DATABASE_URL': 'test-only'}), \
             patch.object(maps_worker, 'ProductStore', return_value=store), \
             patch.object(maps_worker, 'config', return_value={'middle_base_url': 'https://middle.example'}), \
             patch.object(maps_worker.Path, 'read_text', return_value='test-only'), \
             patch.object(maps_worker, 'MapsMiddlePlatformClient'), \
             patch.object(maps_worker, 'MapsCollector') as collector, \
             patch('realtime.middle_direct.DirectTasks') as direct, \
             patch.object(maps_worker, 'process_middle_pending') as process, \
             patch.object(maps_worker.time, 'sleep', side_effect=KeyboardInterrupt):
            direct.return_value.poll.return_value = False
            if acquired:
                with self.assertRaises(KeyboardInterrupt):
                    maps_worker.main()
                collector.return_value.register.assert_called_once()
                collector.return_value.heartbeat.assert_called_once_with(0)
                process.assert_called_once()
            else:
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    maps_worker.main()
                collector.return_value.register.assert_not_called()
                collector.return_value.heartbeat.assert_not_called()
                process.assert_not_called()

    def test_idle_leader_stays_available_to_middle(self):
        self.exercise(True)

    def test_second_worker_cannot_register_or_claim(self):
        self.exercise(False)
