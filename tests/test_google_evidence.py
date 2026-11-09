import base64
import json
import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.google_domain import DomainError
from realtime.google_evidence import CursorCodec, EvidenceReference, FrozenManifest, page_limit
from realtime.google_rights import Grant, Principal
from tests.test_google_domain import NOW, SCOPE, registration


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal("one", frozenset({"grant:one"}), "1")
        self.grant = Grant("one", "asset:one", "grant:one", "1",
                           frozenset({"title"}), frozenset({"internal_display", "export"}),
                           NOW + timedelta(hours=1))
        self.source = registration()
        self.reference = EvidenceReference(
            document_id="whale:document:1", document_version="1",
            observation_id="run:1", source_adapter="fixture", parser_version="1",
            source_adapter_version="1", source_api_version="1",
            license_id="fixture-only", license_version="1",
            source_hash="sha256:" + "a" * 64, grant_id="grant:one", asset_id="asset:one",
            fields=("title",), source_observed_at=None, observed_at=NOW,
            evidence_expires_at=NOW + timedelta(days=1),
        )
        self.resolver = lambda ref: (self.source, self.grant)

    def manifest(self):
        return FrozenManifest.create(
            principal=self.principal, references=[self.reference], scope=SCOPE,
            query_template="serp-comparison:1", parameters={"query_id": "query:one"},
            method_versions={"rank": "1"}, collection_version="cohort:one",
            now=NOW, resolve=self.resolver,
        )

    def test_manifest_contains_frozen_references_not_a_second_raw_store(self):
        manifest = self.manifest()
        self.assertEqual(manifest.expires_at, self.grant.expires_at)
        self.assertEqual(manifest.manifest_id, self.manifest().manifest_id)
        self.assertEqual(manifest.to_dict()["references"][0]["source_observed_at"], None)
        self.assertNotIn("payload", manifest.to_dict())
        self.assertNotIn("title", manifest.to_dict()["references"][0])
        with self.assertRaises(TypeError):
            manifest.parameters["query_id"] = "another"
        changed = replace(manifest, collection_version="cohort:two")
        self.assertNotEqual(changed.manifest_id, manifest.manifest_id)

    def test_read_export_rechecks_current_source_grant_and_expiry(self):
        m = self.manifest()
        m.check_access(self.principal, use="export", now=NOW, resolve=self.resolver)
        self.grant = replace(self.grant, revoked=True)
        with self.assertRaises(DomainError):
            m.check_access(self.principal, use="export", now=NOW, resolve=self.resolver)
        self.grant = replace(self.grant, revoked=False)
        with self.assertRaises(DomainError):
            m.check_access(self.principal, use="export", now=self.grant.expires_at,
                           resolve=self.resolver)
        self.source = replace(self.source, admission="revoked")
        with self.assertRaises(DomainError):
            m.check_access(self.principal, use="internal_display", now=NOW, resolve=self.resolver)

    def test_same_url_or_doc_cannot_transfer_ownership(self):
        m = self.manifest()
        with self.assertRaises(DomainError):
            m.check_access(replace(self.principal, tenant_id="two"), use="export",
                           now=NOW, resolve=self.resolver)
        self.grant = replace(self.grant, asset_id="asset:other")
        with self.assertRaises(DomainError):
            m.check_access(self.principal, use="export", now=NOW, resolve=self.resolver)

    def test_reference_requires_real_version_hash_and_no_future_source_time(self):
        for kw in ({"document_id": ""}, {"document_version": ""}, {"source_hash": "bad"},
                   {"fields": ()}, {"observed_at": "2026-11-04"},
                   {"source_observed_at": NOW + timedelta(seconds=1)}):
            with self.assertRaises(DomainError):
                replace(self.reference, **kw)

    def test_missing_independent_whale_reference_is_not_filled_with_local_id(self):
        with self.assertRaises(DomainError):
            replace(self.reference, document_id=None)
        self.grant = replace(self.grant, uses=frozenset())
        with self.assertRaises(DomainError):
            self.manifest()

    def test_source_and_license_versions_are_pinned_and_rights_cannot_expand(self):
        m = self.manifest()
        for kw in (dict(adapter_version="2"), dict(source_api_version="2"),
                   dict(license_version="2"), dict(license_id="different")):
            self.source = registration(**kw)
            with self.assertRaises(DomainError):
                m.check_access(self.principal, use="export", now=NOW, resolve=self.resolver)
        self.source = registration()
        self.grant = replace(self.grant, uses=frozenset({"internal_display"}))
        m = self.manifest()
        self.grant = replace(self.grant, uses=frozenset({"internal_display", "export"}))
        with self.assertRaises(DomainError):
            m.check_access(self.principal, use="export", now=NOW, resolve=self.resolver)


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.codec = CursorCodec(b"f" * 32)
        self.scope = dict(tenant_id="one", rights_version="1", filters=SCOPE,
                          sort=["observed_at", "id"], source="fixture",
                          manifest_id="manifest:one")

    def token(self):
        return self.codec.encode(binding=self.scope, position=["2026-11-04T00:00:00Z", "id:1"],
                                 expires_at=NOW + timedelta(minutes=10), now=NOW)

    def test_signed_cursor_binds_every_scope_and_rights_dimension(self):
        token = self.token()
        self.assertEqual(self.codec.decode(token, binding=self.scope, now=NOW),
                         ["2026-11-04T00:00:00Z", "id:1"])
        for key, value in (("tenant_id", "two"), ("rights_version", "2"),
                           ("filters", {}), ("sort", ["id"]), ("source", "other"),
                           ("manifest_id", "manifest:two")):
            with self.assertRaises(DomainError):
                self.codec.decode(token, binding={**self.scope, key: value}, now=NOW)
        with self.assertRaises(DomainError):
            self.codec.decode(token, binding=self.scope, now=NOW + timedelta(minutes=10))

    def test_tampered_malformed_huge_and_wrong_key_are_rejected(self):
        token = self.token()
        for candidate in ("", "not-a-token", token + "x", "a" * 8193):
            with self.assertRaises(DomainError):
                self.codec.decode(candidate, binding=self.scope, now=NOW)
        raw, sig = token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        payload["position"] = ["changed"]
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + "." + sig
        with self.assertRaises(DomainError):
            self.codec.decode(forged, binding=self.scope, now=NOW)
        with self.assertRaises(DomainError):
            CursorCodec(b"x" * 32).decode(token, binding=self.scope, now=NOW)
        with self.assertRaises(DomainError):
            CursorCodec(b"short")

    def test_page_limits_are_bounded_not_silently_clipped(self):
        self.assertEqual(page_limit(None), 20)
        self.assertEqual(page_limit("100"), 100)
        for value in (0, -1, 101, True, 2.5, "", "2.0", "2e1"):
            with self.assertRaises(DomainError):
                page_limit(value)


if __name__ == "__main__":
    unittest.main()
