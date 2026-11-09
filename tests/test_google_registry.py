"""Real isolated database tests; all approvals/assets here are fixtures."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.google_domain import DomainError
from realtime.google_registry import RightsRegistry
from realtime.google_rights import (
    DeliveryContext, Grant, IngestPermit, Principal, authorize,
)
from tests.test_google_domain import NOW, registration


def fixture_permit(**kw):
    args = dict(permit_id="permit:one", tenant_id="one", grant_id="grant:one",
                asset_id="asset:one", source_adapter="fixture", source_version="1",
                dataset_id="validation_fixture", source_platform="google_search",
                agent_id="agent:fixture", credential_scope_ref="credential:fixture",
                modes=frozenset({"scheduled", "snapshot", "live", "backfill"}),
                ingest_classes=frozenset({"normal", "realtime", "backfill"}),
                capabilities=frozenset({"identity", "title"}), task_types=frozenset({"keyword_search"}),
                autonomous_allowed=True, admission="testing", approval_ref="fixture:approval",
                expires_at=NOW + timedelta(days=1), version="1", production_approved=False)
    return IngestPermit(**{**args, **kw})


def fixture_delivery(**kw):
    args = dict(dataset_id="validation_fixture", source_platform="google_search",
                agent_id="agent:fixture", credential_scope_ref="credential:fixture",
                task_id=None, task_type=None, provided_capabilities=frozenset({"title"}))
    return DeliveryContext(**{**args, **kw})


class IngestAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.grant = Grant("one", "asset:one", "grant:one", "grant-version",
                           frozenset({"title"}), frozenset({"raw_store"}),
                           NOW + timedelta(days=2), source_adapter="fixture", source_version="1")
        self.principal = Principal("one", frozenset({"grant:one"}), "registry-epoch",
                                   grant_versions={"grant:one": "grant-version"})
        self.source = registration()

    def admit(self, permit=None, delivery=None, **kw):
        return (permit or fixture_permit()).check(
            principal=self.principal, grant=self.grant, source=self.source,
            delivery=delivery or fixture_delivery(), mode=kw.pop("mode", "scheduled"),
            ingest_class=kw.pop("ingest_class", "normal"), fields={"title"},
            now=NOW, production=kw.pop("production", False), **kw)

    def test_modes_do_not_implicitly_grant_an_ingest_class(self):
        self.admit(mode="scheduled", ingest_class="normal")
        self.admit(mode="scheduled", ingest_class="realtime")
        self.admit(mode="backfill", ingest_class="backfill")
        with self.assertRaises(DomainError):
            self.admit(mode="backfill", ingest_class="realtime")
        with self.assertRaises(DomainError):
            self.admit(permit=fixture_permit(ingest_classes=frozenset({"normal"})),
                       mode="scheduled", ingest_class="realtime")
        with self.assertRaises(DomainError):
            self.admit(permit=fixture_permit(modes=frozenset({"scheduled"})), mode="live")

    def test_dataset_platform_agent_key_and_task_ownership_are_independent(self):
        for kw in (dict(dataset_id="wrong"), dict(source_platform="other"),
                   dict(agent_id="other"), dict(credential_scope_ref="other"),
                   dict(task_id="fake", task_type=None),
                   dict(task_id="task:one", task_type="backfill")):
            with self.subTest(kw=kw), self.assertRaises(DomainError):
                self.admit(delivery=fixture_delivery(**kw))
        self.admit(delivery=fixture_delivery(task_id="task:one", task_type="keyword_search"),
                   permit=fixture_permit(allowed_task_ids=frozenset({"task:one"})))
        with self.assertRaises(DomainError):
            self.admit(delivery=fixture_delivery(task_id="task:other", task_type="keyword_search"),
                       permit=fixture_permit(allowed_task_ids=frozenset({"task:one"})))
        with self.assertRaises(DomainError):
            self.admit(permit=fixture_permit(autonomous_allowed=False))

    def test_real_fields_capabilities_expiry_and_production_are_all_checked(self):
        for permit, delivery in (
            (fixture_permit(capabilities=frozenset({"identity"})), fixture_delivery()),
            (fixture_permit(), fixture_delivery(provided_capabilities=frozenset({"body"}))),
            (fixture_permit(expires_at=NOW), fixture_delivery()),
            (fixture_permit(admission="planned"), fixture_delivery()),
        ):
            with self.assertRaises(DomainError):
                self.admit(permit=permit, delivery=delivery)
        with self.assertRaises(DomainError):
            self.admit(production=True)
        self.source = replace(self.source, production_approved=True)
        self.admit(permit=fixture_permit(admission="approved", production_approved=True),
                   production=True)

    def test_grant_version_is_per_asset_not_the_tenant_epoch(self):
        authorize(self.principal, self.grant, self.source, "title", "raw_store", now=NOW)
        with self.assertRaises(DomainError):
            authorize(replace(self.principal, grant_versions={"grant:one": "stale"}),
                      self.grant, self.source, "title", "raw_store", now=NOW)
        with self.assertRaises(DomainError):
            authorize(self.principal, replace(self.grant, source_adapter="other"),
                      self.source, "title", "raw_store", now=NOW)
        with self.assertRaises(TypeError):
            self.principal.grant_versions["grant:one"] = "changed"


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class RegistryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.root = os.environ["TEST_DATABASE_URL"]
        self.schema = "google_registry_" + uuid4().hex
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.dsn = make_conninfo(self.root, options="-c search_path=" + self.schema)
        self.registry = RightsRegistry(self.dsn)
        self.registry.initialize()
        self.registry.initialize()
        self.source = registration()
        self.grant = Grant("one", "asset:one", "grant:one", "g1", frozenset({"title"}),
                           frozenset({"raw_store", "internal_display", "export"}),
                           NOW + timedelta(days=2), source_adapter="fixture", source_version="1")

    def tearDown(self):
        with psycopg.connect(self.root) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def source_save(self, **kw):
        return self.registry.save_source("one", kw.pop("source", self.source),
                                         expected_revision=kw.pop("expected_revision", 0),
                                         actor_ref="fixture:operator", now=NOW, **kw)

    def grant_save(self, grant=None, **kw):
        return self.registry.save_grant(grant or self.grant,
                                        expected_revision=kw.pop("expected_revision", 0),
                                        actor_ref="fixture:operator", now=NOW, **kw)

    def test_bootstrap_is_empty_and_does_not_touch_legacy_schema(self):
        self.assertEqual(self.registry.sources("one", now=NOW), [])
        self.assertEqual(self.registry.principal("one", now=NOW).grant_ids, frozenset())
        with psycopg.connect(self.dsn) as db:
            names = {r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s",
                                             (self.schema,))}
        self.assertEqual(names, {"sf_google_registry", "sf_google_tenant_rights"})
        with self.assertRaises(DomainError):
            self.registry.source("one", "fixture", "1")

    def test_roundtrip_versions_field_uses_deadlines_and_tenant_isolation(self):
        saved = self.source_save()
        self.assertEqual(saved["revision"], 1)
        read = self.registry.source("one", "fixture", "1")
        self.assertEqual(read, self.source)
        self.assertFalse(self.registry.sources("one", now=NOW)[0]["production_ready"])
        with self.assertRaises(DomainError):
            self.registry.source("two", "fixture", "1")
        self.assertEqual(self.registry.sources("two", now=NOW), [])
        self.assertEqual(self.registry.history("two", "source", saved["key"]), [])

    def test_optimistic_cas_keeps_history_and_invalidates_prior_principals(self):
        self.source_save()
        old = self.registry.principal("one", now=NOW)
        def write(_):
            try:
                return self.source_save(expected_revision=1,
                                        source=replace(self.source, admission="revoked"))["revision"]
            except DomainError as exc:
                return exc.code
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, range(2)))
        self.assertEqual(sorted(map(str, results)), ["2", "precondition_failed"])
        current = self.registry.principal("one", now=NOW)
        self.assertNotEqual(old.rights_version, current.rights_version)
        with self.assertRaises(DomainError):
            self.registry.assert_current(old, now=NOW)
        key = self.registry.source_key("fixture", "1")
        self.assertEqual(len(self.registry.history("one", "source", key)), 2)
        self.assertEqual(self.registry.source("one", "fixture", "1").admission, "revoked")

    def test_grants_bind_actual_tenant_source_and_asset_versions(self):
        self.source_save()
        self.grant_save()
        other = replace(self.grant, grant_id="grant:second", asset_id="asset:second", version="g7")
        self.grant_save(other)
        p = self.registry.principal("one", now=NOW)
        self.assertEqual(p.grant_versions, {"grant:one": "g1", "grant:second": "g7"})
        self.registry.assert_current(p, now=NOW)
        authorize(p, self.registry.grant("one", "grant:one"), self.source,
                  "title", "export", now=NOW)
        with self.assertRaises(DomainError):
            self.registry.grant("two", "grant:one")
        with self.assertRaises(DomainError):
            self.grant_save(replace(self.grant, grant_id="bad", source_adapter="missing"))
        with self.assertRaises(DomainError):
            self.grant_save(replace(self.grant, grant_id="bad", source_adapter=None))

    def test_revocation_survives_new_instances_and_cannot_expand_a_stale_cursor(self):
        self.source_save()
        self.grant_save()
        p = self.registry.principal("one", now=NOW)
        self.grant_save(replace(self.grant, revoked=True, version="g2"), expected_revision=1)
        restored = RightsRegistry(self.dsn)
        self.assertEqual(restored.principal("one", now=NOW).grant_ids, frozenset())
        with self.assertRaises(DomainError):
            restored.assert_current(p, now=NOW)
        self.assertTrue(restored.grant("one", "grant:one").revoked)
        with self.assertRaises(DomainError):
            self.grant_save(replace(self.grant, fields=frozenset({"body"})),
                            expected_revision=2)

    def test_persistent_permit_requires_registered_scope_and_preserves_class_limits(self):
        self.source_save()
        self.grant_save()
        permit = fixture_permit()
        saved = self.registry.save_permit(permit, expected_revision=0,
                                          actor_ref="fixture:operator", now=NOW)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.registry.permit("one", "permit:one"), permit)
        with self.assertRaises(DomainError):
            self.registry.permit("two", "permit:one")
        with self.assertRaises(DomainError):
            self.registry.save_permit(replace(permit, permit_id="bad", grant_id="unknown"),
                                      expected_revision=0, actor_ref="fixture:operator", now=NOW)
        with self.assertRaises(DomainError):
            self.registry.save_permit(replace(permit, permit_id="bad", production_approved=True),
                                      expected_revision=0, actor_ref="fixture:operator", now=NOW)

    def test_customer_key_scope_is_not_implicitly_every_asset_or_manager(self):
        self.source_save()
        self.grant_save()
        absent = self.registry.principal("one", subject_ref="key:unassigned", now=NOW)
        self.assertEqual(absent.grant_ids, frozenset())
        self.assertEqual(absent.roles, frozenset())
        self.registry.save_subject("one", "key:reader", grant_ids={"grant:one"}, roles={"reader"},
                                   expected_revision=0, actor_ref="fixture:operator", now=NOW)
        p = self.registry.principal("one", subject_ref="key:reader", now=NOW)
        self.assertEqual(p.grant_ids, frozenset({"grant:one"}))
        self.assertNotIn("manager", p.roles)
        self.registry.assert_current(p, now=NOW)
        self.registry.save_subject("one", "key:reader", grant_ids=set(), roles=set(),
                                   expected_revision=1, actor_ref="fixture:operator", now=NOW)
        with self.assertRaises(DomainError):
            self.registry.assert_current(p, now=NOW)
        with self.assertRaises(DomainError):
            self.registry.save_subject("two", "key:reader", grant_ids={"grant:one"}, roles={"reader"},
                                       expected_revision=0, actor_ref="fixture:operator", now=NOW)


if __name__ == "__main__":
    unittest.main()
