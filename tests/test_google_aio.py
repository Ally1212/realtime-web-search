import json
import unittest
from datetime import timedelta

from realtime.google_aio import (
    AIOExpansionQueue,
    AIOExpansionRequest,
    AIOExpansionResult,
)
from realtime.google_domain import DomainError
from tests.test_google_domain import NOW


class AIOExpansionQueueTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        self.queue = AIOExpansionQueue(clock=lambda: self.clock)

    def enqueue(self, observation_id="observation:1"):
        return self.queue.enqueue(
            AIOExpansionRequest(
                observation_id=observation_id,
                run_id="run:1",
                timeout_seconds=60,
                created_at=NOW.isoformat(),
            ),
            provider_handle="short-lived-secret-handle",
        )

    def test_handle_is_never_serialized_in_public_task(self):
        task = self.enqueue()
        self.assertNotIn("short-lived-secret-handle", json.dumps(task))
        claimed = self.queue.claim()
        self.assertNotIn("short-lived-secret-handle", json.dumps(
            {key: value for key, value in claimed.items() if key != "handle"}))
        self.assertEqual(
            repr(claimed["handle"]), "<redacted-aio-handle>")
        self.assertEqual(self.queue.result(task["task_id"])["result"], None)

    def test_expired_handle_is_not_interpreted_as_no_aio(self):
        task = self.enqueue()
        self.clock += timedelta(seconds=61)
        result = self.queue.claim()
        self.assertIsNone(result)
        stored = self.queue.result(task["task_id"])
        self.assertEqual(stored["state"], "expired")
        self.assertEqual(stored["reason"], "short_lived_handle_expired")
        self.assertFalse(stored["eligible_for_visibility"])

    def test_completed_expansion_is_the_only_visibility_eligible_result(self):
        task = self.enqueue()
        self.queue.claim()
        result = AIOExpansionResult(
            "observation:1", "expanded",
            answer_text="Permitted answer",
            citations=("https://example.test/citation",),
        )
        data = self.queue.complete(task["task_id"], result)
        self.assertTrue(data["eligible_for_visibility"])
        with self.assertRaises(DomainError) as error:
            self.queue.complete(task["task_id"], result)
        self.assertEqual(error.exception.code, "precondition_failed")

    def test_failure_timeout_and_not_available_never_invent_answer(self):
        for state in ("not_available", "timeout", "failed"):
            with self.subTest(state=state):
                task = self.enqueue("observation:" + state)
                self.queue.claim()
                result = AIOExpansionResult(
                    "observation:" + state, state, reason="source_" + state)
                data = self.queue.complete(task["task_id"], result)
                self.assertFalse(data["eligible_for_visibility"])
                self.assertIsNone(data["answer_text"])
                self.assertEqual(data["citations"], [])
        with self.assertRaises(DomainError):
            AIOExpansionResult(
                "observation:bad", "timeout", answer_text="invented")

    def test_result_scope_and_attempts_are_bound(self):
        task = self.enqueue()
        claimed = self.queue.claim()
        self.assertEqual(claimed["attempts"], 1)
        with self.assertRaises(DomainError) as error:
            self.queue.complete(
                task["task_id"],
                AIOExpansionResult("observation:other", "expanded", answer_text="x"),
            )
        self.assertEqual(error.exception.code, "incompatible_scope")


if __name__ == "__main__":
    unittest.main()
