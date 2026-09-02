import tempfile
import unittest
from pathlib import Path

from realtime.scrapy_runner import repair_jobdir


class ScrapyRunnerTests(unittest.TestCase):
    def test_repairs_only_truncated_lifo_queue_files(self):
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            queue_dir = job_dir / "requests.queue" / "example.com"
            chunk_dir = queue_dir / "0s"
            chunk_dir.mkdir(parents=True)
            (queue_dir / "0").write_bytes(b"")
            (queue_dir / "-1").write_bytes(b"abc")
            (queue_dir / "2").write_bytes(b"\x00\x00\x00\x00")
            (chunk_dir / "q00000").write_bytes(b"")

            repaired = repair_jobdir(job_dir)

            self.assertEqual(repaired, 2)
            self.assertFalse((queue_dir / "0").exists())
            self.assertFalse((queue_dir / "-1").exists())
            self.assertTrue((queue_dir / "2").exists())
            self.assertTrue((chunk_dir / "q00000").exists())


if __name__ == "__main__":
    unittest.main()
