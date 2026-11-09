"""Isolated PostgreSQL boundary tests, not real Whale acceptance."""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.google_domain import DomainError
from realtime.google_ingest import IngestSpool
from realtime.google_ingest_ledger import HourlyIngestLedger
from realtime.google_rights import Grant, Principal
from tests.test_google_domain import NOW, registration
from tests.test_google_registry import fixture_delivery, fixture_permit


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class IngestSpoolTests(unittest.TestCase):
    def setUp(self):
        self.root = os.environ["TEST_DATABASE_URL"]
        self.schema = "google_ingest_" + uuid4().hex
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root, options="-c search_path=" + self.schema)
        self.source = registration()
        self.grant = Grant("one", "asset:one", "grant:one", "1",
                           frozenset({"title"}), frozenset({"raw_store", "internal_display"}),
                           NOW + timedelta(days=3))
        self.principal = Principal("one", frozenset({"grant:one"}), "1")
        self.permit, self.delivery = fixture_permit(), fixture_delivery()
        self.hourly = HourlyIngestLedger.persistent(
            self.dsn, lateness_grace_seconds=900, clock=lambda: NOW + timedelta(hours=2))
        self.spool = IngestSpool(
            self.dsn, max_bytes=100_000, max_age_seconds=86400,
            production=False, hourly_ledger=self.hourly)
        self.spool.initialize()
        self.spool.initialize()

    def tearDown(self):
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def enqueue(self, run="run:one", item="item:one", title="Observed",
                received=NOW, mode="scheduled", ingest_class="normal"):
        return self.spool.enqueue(
            principal=self.principal, grant=self.grant, source=self.source,
            run_id=run, item_id=item, observed_at=NOW, received_at=received,
            source_observed_at=None, cache_hit=False, fields={"title": title},
            mode=mode, ingest_class=ingest_class, now=NOW, permit=self.permit, delivery=self.delivery,
        )

    def claim(self, **kw):
        return self.spool.reserve(
            principal=self.principal, source=self.source,
            resolve_grant=lambda gid: self.grant,
            resolve_permit=lambda pid: self.permit, now=NOW, **kw)

    def view(self, event_id, **kw):
        return self.spool.get(self.principal, event_id, source=self.source,
                              grant=self.grant, now=NOW, **kw)

    def test_initialization_only_creates_new_isolated_tables(self):
        with psycopg.connect(self.dsn) as db:
            names = {r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s",
                                             (self.schema,))}
        self.assertEqual(names, {
            "sf_google_ingest_events",
            "sf_google_ingest_plan_points",
            "sf_google_ingest_runs",
            "sf_google_ingest_closed_hours",
            "sf_google_ingest_late_transitions",
        })

    def test_100_concurrent_retries_create_one_event_but_new_run_is_new_observation(self):
        barrier = threading.Barrier(100)
        def submit(_):
            barrier.wait(timeout=10)
            return self.enqueue()["event_id"]
        with ThreadPoolExecutor(100) as pool:
            ids = list(pool.map(submit, range(100)))
        self.assertEqual(len(set(ids)), 1)
        self.assertNotEqual(ids[0], self.enqueue(run="run:two")["event_id"])
        self.assertNotEqual(ids[0], self.enqueue(item="item:two")["event_id"])
        with self.assertRaises(DomainError) as ctx:
            self.enqueue(title="Changed")
        self.assertEqual(ctx.exception.code, "idempotency_conflict")
        with self.assertRaises(DomainError):
            self.enqueue(received=NOW + timedelta(seconds=1))
        self.assertEqual(self.spool.counts(self.principal, now=NOW)["events"], 3)

    def test_mode_and_header_are_independent_and_validated_before_storing(self):
        for mode, ingest_class in (("scheduled", "normal"), ("snapshot", "normal"),
                                   ("live", "realtime"), ("backfill", "backfill")):
            event = self.enqueue(run=mode, mode=mode, ingest_class=ingest_class)
            self.assertEqual(event["ingest_class"], ingest_class)
        for mode, ingest_class in (("backfill", "realtime"), ("live", "normal"),
                                   ("scheduled", "unknown"), ("unapproved", "normal")):
            with self.assertRaises(DomainError):
                self.enqueue(run=mode + ingest_class, mode=mode, ingest_class=ingest_class)

    def test_validation_raw_admission_rejects_wrong_scope_and_empty_placeholders(self):
        admitted = self.spool.admission_check(
            permit=fixture_permit(), delivery=fixture_delivery(), mode="scheduled",
            ingest_class="normal")
        self.assertEqual(admitted, {
            "environment": "validation_raw", "permit_id": "permit:one",
            "permit_version": "1", "dataset_id": "validation_fixture",
            "source_platform": "google_search", "agent_id": "agent:fixture",
            "task_id": None, "task_type": None, "provided_capabilities": ["title"],
            "production_key_created": False,
        })
        cases = (
            fixture_delivery(dataset_id="wrong"),
            fixture_delivery(source_platform="other"),
            fixture_delivery(agent_id="other"),
            fixture_delivery(credential_scope_ref="other"),
            fixture_delivery(provided_capabilities=frozenset()),
            fixture_delivery(task_id="dry", task_type="dry_run"),
            fixture_delivery(task_id="task:one", task_type="unapproved"),
        )
        for delivery in cases:
            with self.subTest(delivery=delivery), self.assertRaises(DomainError):
                self.spool.admission_check(
                    permit=fixture_permit(), delivery=delivery,
                    mode="scheduled", ingest_class="normal")

    def test_production_admission_is_separate_and_creates_no_key(self):
        with self.assertRaises(DomainError) as error:
            self.spool.admission_check(
                permit=fixture_permit(), delivery=fixture_delivery(),
                mode="scheduled", ingest_class="normal", production=True)
        self.assertEqual(error.exception.code, "use_not_permitted")
        result = self.spool.admission_check(
            permit=fixture_permit(
                admission="approved", production_approved=True,
                allowed_task_ids=frozenset({"task:one"})),
            delivery=fixture_delivery(task_id="task:one", task_type="keyword_search"),
            mode="scheduled", ingest_class="normal", production=True)
        self.assertTrue(result["production_key_created"] is False)

    def test_partial_bulk_receipts_do_not_make_queued_or_accepted_readable(self):
        self.enqueue(run="1")
        self.enqueue(run="2")
        self.enqueue(run="3")
        rows = self.claim(limit=3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(self.spool.receipt(rows[0]["event_id"], rows[0]["lease_token"],
                                          status="queued", receipt_id="receipt:1", now=NOW))
        self.assertTrue(self.spool.receipt(rows[1]["event_id"], rows[1]["lease_token"],
                                          status="accepted", receipt_id="receipt:2", now=NOW))
        self.assertTrue(self.spool.retry(rows[2]["event_id"], rows[2]["lease_token"],
                                        error_code="partial_bulk_response", retry_after=60,
                                        jitter_seconds=3, now=NOW))
        counts = self.spool.counts(self.principal, now=NOW)
        self.assertEqual(counts["states"], {"ingest_pending": 1, "pending": 1, "terminal": 1})
        self.assertEqual(counts["readable"], 0)
        self.assertEqual(self.view(rows[2]["event_id"])["fields"],
                         rows[2]["fields"])
        self.assertEqual(self.spool.get(self.principal, rows[2]["event_id"])["next_attempt_at"],
                         NOW + timedelta(seconds=63))

    def test_mixed_bulk_400_is_not_atomic_rollback_and_failed_item_is_per_row(self):
        for index in range(1, 4):
            self.enqueue(run=str(index))
        rows = self.claim(limit=3)
        results = self.spool.settle_bulk(
            rows,
            [
                {"receipt_status": "queued", "receipt_id": "receipt:good"},
                {"failed_item": True, "status": "invalid_field"},
                {"receipt_status": "duplicate", "receipt_id": "receipt:dup"},
            ],
            error_code="bulk_http_400", retry_after=30, jitter_seconds=2, now=NOW,
        )
        self.assertEqual([item["state"] for item in results],
                         ["queued", "retry_scheduled", "duplicate"])
        self.assertTrue(results[1]["failed_item"])
        states = self.spool.counts(self.principal, now=NOW)["states"]
        self.assertEqual(states, {"ingest_pending": 1, "pending": 1, "terminal": 1})
        retried = self.view(rows[1]["event_id"])
        self.assertEqual(retried["fields"], rows[1]["fields"])
        self.assertEqual(retried["received_at"], rows[1]["received_at"])
        self.assertEqual(retried["payload_hash"], rows[1]["payload_hash"])
        self.assertEqual(retried["next_attempt_at"], NOW + timedelta(seconds=32))
        with self.assertRaises(DomainError) as error:
            self.spool.settle_bulk(rows[:2], [{"receipt_status": "queued"}], now=NOW)
        self.assertEqual(error.exception.code, "source_error")

    def test_hourly_ledger_tracks_lifecycle_without_inventing_source_attempts(self):
        self.enqueue()
        row = self.claim()[0]
        stage = {(r["stage"], r["unit"]): r
                 for r in self.spool._hourly_stage(row)}
        self.assertEqual(stage[("sent", "stable_event")]["quantity"], 1)
        self.assertFalse(any(stage == ("source_attempts", "source_request")
                             for stage in stage))
        self.spool.receipt(row["event_id"], row["lease_token"],
                           status="accepted", receipt_id="receipt:1", now=NOW)
        self.spool.readback(
            self.principal, row["event_id"],
            proof=dict(event_id=row["event_id"], payload_hash=row["payload_hash"],
                       document_id="whale:1", document_version="1", observed_at=NOW),
            source=self.source, grant=self.grant, now=NOW)
        stage = {(r["stage"], r["unit"]): r
                 for r in self.spool._hourly_stage(row)}
        self.assertEqual(stage[("sent", "stable_event")]["quantity"], 1)
        self.assertEqual(stage[("accepted", "platform_receipt")]["quantity"], 1)
        self.assertEqual(stage[("terminal", "platform_receipt")]["quantity"], 1)
        self.assertEqual(stage[("readable", "platform_document")]["quantity"], 1)
        self.assertNotIn(("source_attempts", "source_request"), stage)

    def test_readback_requires_matching_event_hash_version_and_current_rights(self):
        self.enqueue()
        row = self.claim()[0]
        self.spool.receipt(row["event_id"], row["lease_token"],
                           status="accepted", receipt_id="receipt:1", now=NOW)
        proof = dict(event_id=row["event_id"], payload_hash=row["payload_hash"],
                     document_id="whale:1", document_version="1", observed_at=NOW)
        for kw in ({"payload_hash": "wrong"}, {"observed_at": NOW + timedelta(seconds=1)},
                   {"document_version": ""}):
            with self.assertRaises(DomainError):
                self.spool.readback(self.principal, row["event_id"], proof={**proof, **kw},
                                    source=self.source, grant=self.grant, now=NOW)
        self.spool.readback(self.principal, row["event_id"], proof=proof,
                            source=self.source, grant=self.grant, now=NOW)
        out = self.spool.get(self.principal, row["event_id"])
        self.assertEqual(out["state"], "readable")
        self.assertIsNone(out["fields"])
        self.assertEqual(out["document_id"], "whale:1")
        self.assertEqual(self.spool.counts(self.principal, now=NOW)["readable"], 1)

    def test_leases_restart_and_stale_settlement_cas(self):
        self.enqueue()
        first = self.claim()[0]
        self.assertEqual(self.claim(), [])
        restored = IngestSpool(self.dsn, max_bytes=100_000, max_age_seconds=86400, production=False)
        second = restored.reserve(principal=self.principal, source=self.source,
                                  resolve_grant=lambda gid: self.grant,
                                  resolve_permit=lambda pid: self.permit,
                                  now=NOW + timedelta(seconds=121))[0]
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        self.assertFalse(restored.receipt(first["event_id"], first["lease_token"],
                                         status="accepted", receipt_id="stale", now=NOW))
        self.assertTrue(restored.receipt(second["event_id"], second["lease_token"],
                                        status="queued", receipt_id="current",
                                        now=NOW + timedelta(seconds=121)))

    def test_tenant_isolation_revocation_expiry_and_spool_backpressure(self):
        event = self.enqueue()
        with self.assertRaises(DomainError):
            self.spool.get(replace(self.principal, tenant_id="two"), event["event_id"])
        self.assertEqual(self.spool.counts(replace(self.principal, tenant_id="two"), now=NOW)["events"], 0)
        self.grant = replace(self.grant, revoked=True)
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.spool.get(self.principal, event["event_id"])["state"], "canceled")
        self.assertIsNone(self.spool.get(self.principal, event["event_id"])["fields"])
        self.grant = replace(self.grant, revoked=False)
        self.enqueue(run="expiring")
        self.assertEqual(self.spool.purge_expired(now=NOW + timedelta(days=1)), 1)
        self.assertEqual(self.spool.counts(self.principal, now=NOW + timedelta(days=1))["states"],
                         {"canceled": 1, "expired": 1})
        small = IngestSpool(self.dsn, max_bytes=1, max_age_seconds=86400, production=False)
        with self.assertRaises(DomainError) as ctx:
            small.enqueue(principal=self.principal, grant=self.grant, source=self.source,
                          run_id="large", item_id="item", observed_at=NOW, received_at=NOW,
                          source_observed_at=None, cache_hit=False, fields={"title": "Observed"},
                          mode="scheduled", ingest_class="normal", now=NOW,
                          permit=self.permit, delivery=self.delivery)
        self.assertEqual(ctx.exception.code, "quota_exceeded")

    def test_fields_require_rights_and_sensitive_transport_never_enters_spool(self):
        args = dict(principal=self.principal, grant=self.grant, source=self.source,
                    run_id="bad", item_id="bad", observed_at=NOW, received_at=NOW,
                    source_observed_at=None, cache_hit=False, mode="scheduled",
                    ingest_class="normal", now=NOW, permit=self.permit, delivery=self.delivery)
        for fields in ({"body": "unlicensed"}, {"title": {"access_token": "secret"}},
                       {"title": "https://example.com?api_key=secret"}):
            with self.assertRaises(DomainError):
                self.spool.enqueue(**args, fields=fields)

    def test_spool_inspection_cannot_bypass_current_grant_or_expiry(self):
        event = self.enqueue()
        self.assertIsNone(self.spool.get(self.principal, event["event_id"])["fields"])
        self.assertEqual(self.view(event["event_id"])["fields"], {"title": "Observed"})
        with self.assertRaises(DomainError):
            self.spool.get(self.principal, event["event_id"], source=self.source,
                           grant=replace(self.grant, revoked=True), now=NOW)
        with self.assertRaises(DomainError):
            self.spool.get(self.principal, event["event_id"], source=self.source,
                           grant=self.grant, now=NOW + timedelta(days=1))

    def test_queued_receipt_can_reach_terminal_only_with_matching_identity(self):
        self.enqueue()
        row = self.claim()[0]
        self.spool.receipt(row["event_id"], row["lease_token"], status="queued",
                           receipt_id="receipt:queued", now=NOW)
        self.assertEqual(self.spool.get(self.principal, row["event_id"])["state"], "ingest_pending")
        with self.assertRaises(DomainError):
            self.spool.poll_receipt(self.principal, row["event_id"], receipt_id="wrong",
                                    status="accepted", source=self.source, grant=self.grant, now=NOW)
        self.spool.poll_receipt(self.principal, row["event_id"], receipt_id="receipt:queued",
                                status="accepted", source=self.source, grant=self.grant, now=NOW)
        self.assertEqual(self.spool.get(self.principal, row["event_id"])["state"], "terminal")
        self.assertEqual(self.spool.counts(self.principal, now=NOW)["readable"], 0)
        self.spool.poll_receipt(self.principal, row["event_id"], receipt_id="receipt:queued",
                                status="accepted", source=self.source, grant=self.grant, now=NOW)

    def test_receipt_requires_successful_cas_even_when_state_already_matches(self):
        self.enqueue()
        row = self.claim()[0]
        for ledger in (None, self.hourly):
            self.spool.hourly_ledger = ledger
            with self.subTest(ledger=ledger is not None):
                self.assertFalse(self.spool.receipt(row['event_id'], 'stale-token',
                    status='accepted', receipt_id='stale', now=NOW))
        self.spool.hourly_ledger = self.hourly
        self.assertTrue(self.spool.receipt(row['event_id'], row['lease_token'],
            status='accepted', receipt_id='original', now=NOW))
        before = self.spool._hourly_stage(row)
        for ledger in (None, self.hourly):
            self.spool.hourly_ledger = ledger
            with self.subTest(already_terminal=True, ledger=ledger is not None):
                self.assertFalse(self.spool.receipt(row['event_id'], row['lease_token'],
                    status='accepted', receipt_id='replayed', now=NOW))
        self.spool.hourly_ledger = self.hourly
        self.assertEqual(self.spool.get(self.principal, row['event_id'])['receipt_id'], 'original')
        self.assertEqual(self.spool._hourly_stage(row), before)

    def test_rejected_receipt_is_terminal_failure_not_accepted_quantity(self):
        self.enqueue()
        row = self.claim()[0]
        self.assertTrue(self.spool.receipt(row['event_id'], row['lease_token'],
            status='rejected', receipt_id='rejection', now=NOW))
        stages = {(r['stage'], r['unit']): r['quantity'] for r in self.spool._hourly_stage(row)}
        self.assertNotIn(('accepted', 'platform_receipt'), stages)
        self.assertEqual(stages[('terminal', 'platform_receipt')], 1)

    def test_polling_does_not_recount_queue_admission(self):
        self.enqueue()
        row=self.claim()[0]
        self.spool.receipt(row['event_id'],row['lease_token'],status='queued',receipt_id='q',now=NOW)
        for status in ('queued','queued','accepted','accepted'):
            self.spool.poll_receipt(self.principal,row['event_id'],receipt_id='q',status=status,
                source=self.source,grant=self.grant,now=NOW)
        stages={(r['stage'],r['unit']):r['quantity'] for r in self.spool._hourly_stage(row)}
        self.assertEqual(stages[('accepted','platform_receipt')],1)
        self.assertEqual(stages[('terminal','platform_receipt')],1)
        self.assertNotIn(('readable','platform_document'),stages)

    def test_state_and_ledger_rollback_together_after_post_write_failure(self):
        self.enqueue()
        row=self.claim()[0]
        before=self.spool._hourly_stage(row)
        record=self.hourly.record
        def fail_after_ledger_write(*args,**kwargs):
            record(*args,**kwargs)
            raise RuntimeError('injected failure after ledger insert')
        with patch.object(self.hourly,'record',side_effect=fail_after_ledger_write):
            with self.assertRaises(RuntimeError):
                self.spool.receipt(row['event_id'],row['lease_token'],status='accepted',receipt_id='ack',now=NOW)
        self.assertEqual(self.spool.get(self.principal,row['event_id'])['state'],'inflight')
        self.assertEqual(self.spool._hourly_stage(row),before)
        restored=IngestSpool(self.dsn,production=False,hourly_ledger=self.hourly)
        self.assertTrue(restored.receipt(row['event_id'],row['lease_token'],status='accepted',receipt_id='ack',now=NOW))

    def test_two_items_in_one_run_each_have_independent_delivery_counters(self):
        for item in ('first','second'):self.enqueue(item=item)
        rows=self.claim(limit=2)
        for row in rows:
            self.spool.receipt(row['event_id'],row['lease_token'],status='accepted',receipt_id='ack:'+row['item_id'],now=NOW)
        stages={(r['stage'],r['unit']):r['quantity'] for r in self.spool._hourly_stage(rows[0])}
        self.assertEqual(stages[('sent','stable_event')],2)
        self.assertEqual(stages[('accepted','platform_receipt')],2)
        self.assertEqual(stages[('terminal','platform_receipt')],2)

    def test_database_disconnect_before_commit_rolls_back_event_and_ledger_then_recovers(self):
        self.enqueue();row=self.claim()[0];before=self.spool._hourly_stage(row)
        record=self.hourly.record
        def terminate_before_commit(*args,**kwargs):
            result=record(*args,**kwargs)
            db=kwargs['connection']
            with psycopg.connect(self.root,autocommit=True) as control:
                self.assertTrue(control.execute('SELECT pg_terminate_backend(%s)',(db.info.backend_pid,)).fetchone()[0])
            return result
        with patch.object(self.hourly,'record',side_effect=terminate_before_commit):
            with self.assertRaises(psycopg.OperationalError):
                self.spool.receipt(row['event_id'],row['lease_token'],status='accepted',receipt_id='ack',now=NOW)
        self.assertEqual(self.spool.get(self.principal,row['event_id'])['state'],'inflight')
        self.assertEqual(self.spool._hourly_stage(row),before)
        restored=IngestSpool(self.dsn,production=False,hourly_ledger=self.hourly)
        self.assertTrue(restored.receipt(row['event_id'],row['lease_token'],status='accepted',receipt_id='ack',now=NOW))

    def test_batch_reservation_and_ledger_are_atomic_on_second_item_failure(self):
        for item in ('first','second'):self.enqueue(item=item)
        record=self.hourly.record;calls=0
        def fail_second(*args,**kwargs):
            nonlocal calls
            result=record(*args,**kwargs);calls+=1
            if calls==2:raise RuntimeError('injected second item failure')
            return result
        with patch.object(self.hourly,'record',side_effect=fail_second):
            with self.assertRaises(RuntimeError):self.claim(limit=2)
        self.assertEqual(self.spool.counts(self.principal,now=NOW)['states'],{'pending':2})
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sf_google_ingest_runs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT sum(attempts) FROM sf_google_ingest_events').fetchone()[0],0)
        self.assertEqual(len(self.claim(limit=2)),2)

    def test_memory_ledger_cannot_commit_separately_from_persistent_spool(self):
        self.enqueue();self.spool.hourly_ledger=HourlyIngestLedger()
        with self.assertRaises(DomainError):self.claim()
        self.assertEqual(self.spool.counts(self.principal,now=NOW)['states'],{'pending':1})

    def test_delivery_kind_survives_database_reload_and_is_never_a_fresh_source_run(self):
        self.enqueue();row=self.claim()[0]
        summary=self.hourly.tenant_hour_summary('one',self.source.adapter_id,NOW)
        self.assertEqual(summary['runs']['total'],0)
        self.assertEqual(summary['runs']['fresh_samples'],0)
        self.assertEqual(summary['delivery_transitions']['total'],1)

    def test_sealed_hour_does_not_block_expiry_or_change_the_original_summary(self):
        self.enqueue();row=self.claim()[0]
        sealed=self.hourly.close_tenant_hour('one',self.source.adapter_id,NOW)
        self.assertEqual(self.spool.purge_expired(now=NOW+timedelta(days=1)),1)
        after=self.spool.get(self.principal,row['event_id'])
        self.assertEqual(after['state'],'expired')
        self.assertEqual(after['payload_bytes'],0)
        with psycopg.connect(self.dsn) as db:
            self.assertIsNone(db.execute('SELECT fields_json FROM sf_google_ingest_events').fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM sf_google_ingest_late_transitions').fetchone()[0],1)
        self.assertEqual(self.hourly.tenant_hour_summary('one',self.source.adapter_id,NOW),sealed)
        self.assertEqual(self.spool.purge_expired(now=NOW+timedelta(days=1)),0)

    def test_sealed_hour_allows_late_terminal_and_readback_without_rewriting_it(self):
        self.enqueue();row=self.claim()[0]
        self.spool.receipt(row['event_id'],row['lease_token'],status='queued',receipt_id='q',now=NOW)
        sealed=self.hourly.close_tenant_hour('one',self.source.adapter_id,NOW)
        later=NOW+timedelta(hours=2)
        self.spool.poll_receipt(self.principal,row['event_id'],receipt_id='q',status='accepted',
            source=self.source,grant=self.grant,now=later)
        proof=dict(event_id=row['event_id'],payload_hash=row['payload_hash'],document_id='doc',document_version='v',observed_at=NOW)
        for _ in range(2):
            self.spool.readback(self.principal,row['event_id'],proof=proof,source=self.source,grant=self.grant,now=later)
        self.assertEqual(self.spool.get(self.principal,row['event_id'])['state'],'readable')
        self.assertEqual(self.hourly.tenant_hour_summary('one',self.source.adapter_id,NOW),sealed)
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sf_google_ingest_late_transitions').fetchone()[0],2)

    def test_sealed_hour_does_not_block_revocation_and_clear_fields(self):
        event=self.enqueue();sealed=self.hourly.close_tenant_hour('one',self.source.adapter_id,NOW)
        self.grant=replace(self.grant,revoked=True)
        self.assertEqual(self.spool.reserve(principal=self.principal,source=self.source,
            resolve_grant=lambda gid:self.grant,resolve_permit=lambda pid:self.permit,now=NOW+timedelta(hours=2)),[])
        after=self.spool.get(self.principal,event['event_id'])
        self.assertEqual(after['state'],'canceled');self.assertEqual(after['payload_bytes'],0)
        self.assertEqual(self.hourly.tenant_hour_summary('one',self.source.adapter_id,NOW),sealed)

    def test_late_audit_and_expiry_rollback_together_and_audit_has_no_source_content(self):
        self.enqueue();row=self.claim()[0]
        self.hourly.close_tenant_hour('one',self.source.adapter_id,NOW)
        record=self.hourly.record_late_transition
        def fail_after_insert(*args,**kwargs):
            record(*args,**kwargs);raise RuntimeError('injected late audit failure')
        with patch.object(self.hourly,'record_late_transition',side_effect=fail_after_insert):
            with self.assertRaises(RuntimeError):self.spool.purge_expired(now=NOW+timedelta(days=1))
        self.assertEqual(self.spool.get(self.principal,row['event_id'])['state'],'inflight')
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sf_google_ingest_late_transitions').fetchone()[0],0)
        self.assertEqual(self.spool.purge_expired(now=NOW+timedelta(days=1)),1)
        with psycopg.connect(self.dsn) as db:
            audit=str(db.execute('SELECT * FROM sf_google_ingest_late_transitions').fetchall())
        self.assertNotIn('Observed',audit);self.assertNotIn('run:one',audit)

    def test_replayed_readback_already_in_sealed_hour_does_not_create_a_late_adjustment(self):
        self.enqueue();row=self.claim()[0]
        self.spool.receipt(row['event_id'],row['lease_token'],status='accepted',receipt_id='a',now=NOW)
        proof=dict(event_id=row['event_id'],payload_hash=row['payload_hash'],document_id='doc',document_version='v',observed_at=NOW)
        self.spool.readback(self.principal,row['event_id'],proof=proof,source=self.source,grant=self.grant,now=NOW)
        sealed=self.hourly.close_tenant_hour('one',self.source.adapter_id,NOW)
        self.spool.readback(self.principal,row['event_id'],proof=proof,source=self.source,grant=self.grant,now=NOW+timedelta(hours=2))
        self.assertEqual(self.hourly.tenant_hour_summary('one',self.source.adapter_id,NOW),sealed)
        with psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sf_google_ingest_late_transitions').fetchone()[0],0)

    def test_old_missing_record_kind_becomes_unknown_not_inferred_from_id(self):
        self.enqueue();self.claim()
        with psycopg.connect(self.dsn) as db:
            db.execute('ALTER TABLE sf_google_ingest_runs DROP COLUMN record_kind')
        self.hourly.initialize()
        summary=self.hourly.tenant_hour_summary('one',self.source.adapter_id,NOW)
        self.assertEqual(summary['runs']['fresh_samples'],0)
        self.assertEqual(summary['runs']['total'],0)
        self.assertEqual(summary['delivery_transitions']['total'],0)
        self.assertEqual(summary['unclassified_records']['total'],1)

    def test_changed_source_or_license_cannot_replay_an_existing_spool_event(self):
        for i, kw in enumerate((dict(adapter_version="2"), dict(source_api_version="2"),
                               dict(license_id="different"), dict(license_version="2"))):
            with self.subTest(kw=kw):
                self.enqueue(run=str(i))
                self.assertEqual(self.spool.reserve(
                    principal=self.principal, source=registration(**kw),
                    resolve_grant=lambda gid: self.grant,
                    resolve_permit=lambda pid: self.permit, now=NOW), [])
        self.assertEqual(self.spool.counts(self.principal, now=NOW)["states"], {"canceled": 4})

    def test_missing_permit_or_changed_delivery_is_rejected_before_spooling(self):
        self.permit = None
        with self.assertRaises(DomainError):
            self.enqueue()
        self.permit = fixture_permit()
        self.delivery = fixture_delivery(dataset_id="wrong")
        with self.assertRaises(DomainError):
            self.enqueue()
        self.assertEqual(self.spool.counts(self.principal, now=NOW)["events"], 0)

    def test_each_dispatch_rechecks_current_permit_and_source_production_admission(self):
        self.enqueue()
        self.permit = replace(self.permit, admission="revoked")
        self.assertEqual(self.claim(), [])
        self.assertEqual(self.spool.counts(self.principal, now=NOW)["states"], {"canceled": 1})
        production = IngestSpool(self.dsn)
        with self.assertRaises(DomainError):
            production.enqueue(principal=self.principal, grant=self.grant, source=self.source,
                               run_id="prod", item_id="item", observed_at=NOW, received_at=NOW,
                               source_observed_at=None, cache_hit=False, fields={"title": "Observed"},
                               mode="scheduled", ingest_class="normal", now=NOW,
                               permit=fixture_permit(), delivery=fixture_delivery())

    def test_unrelated_tenant_epoch_change_does_not_discard_valid_frozen_events(self):
        self.enqueue()
        current = Principal("one", frozenset({"grant:one"}), "new-global-epoch",
                            grant_versions={"grant:one": self.grant.version})
        rows = self.spool.reserve(principal=current, source=self.source,
                                  resolve_grant=lambda gid: self.grant,
                                  resolve_permit=lambda pid: self.permit, now=NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rights_version"], "1")


if __name__ == "__main__":
    unittest.main()
