"""Synthetic Local contracts prove field/use boundaries, not map licensing."""
import unittest
from dataclasses import replace
from datetime import timedelta

from realtime.google_domain import DomainError
from realtime.google_evidence import CursorCodec
from realtime.google_local import (
    GridBundle,
    GridDefinition,
    GridItem,
    GridObservation,
    GridPoint,
    LocalEvidence,
    LocalService,
    ObservationBundle,
    PlaceReference,
    ReviewInsight,
    ReviewTheme,
)
from realtime.google_rights import FieldRight, Grant, Principal
from tests.test_google_domain import NOW, registration


def point(point_id="p1", lat="37.1", lng="-122.2"):
    return GridPoint(point_id=point_id, lat=lat, lng=lng)


def grid(grid_id="grid:1", **changes):
    value = GridDefinition(
        grid_id=grid_id,
        grid_version="grid-v1",
        query="coffee",
        variant_ref="variant:1",
        center={"lat": "37.1", "lng": "-122.2"},
        points=(point(), point("p2", "37.2", "-122.3")),
        excluded_points=(point("excluded", "0", "0"),),
        spacing_m=1000,
        method="regular-grid-v1",
    )
    return replace(value, **changes)


def evidence(**changes):
    value = LocalEvidence(
        adapter_id="local",
        adapter_version="1",
        source_api_version="local-api-v1",
        contract_ref="fixture:local-contract",
        dataset_id="fixture:local-dataset",
        source_observed_at=NOW.isoformat(),
        evidence_expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    return replace(value, **changes)


def observed(**changes):
    value = GridObservation(
        observation_id="observation:1",
        run_id="run:1",
        point_id="p1",
        status="observed",
        depth=10,
        items=(GridItem("place:1", 1, "Cafe", "https://maps.example/place"),),
        observed_at=NOW.isoformat(),
        cost_units="1",
    )
    return replace(value, **changes)


class LocalAdapter:
    contract_ref = "fixture:local-contract"
    dataset_id = "fixture:local-dataset"

    def __init__(self):
        self.grid_output = GridBundle(evidence(), "asset:local", (grid(),))
        self.observation_output = ObservationBundle(
            evidence(), "asset:local", "grid:1", (observed(),), True)
        self.place_output = PlaceReference(
            evidence(), "asset:local", "place:1", "google",
            "https://maps.example/place", True, False,
            ("no_long_term_attribute_store",),
        )
        self.review_output = ReviewInsight(
            evidence(), "asset:local", "place:1", 100, 80,
            (ReviewTheme("service", 20, 80, ("evidence:1",)),),
            "approved-sample-v1",
            ("sample_not_all_consumers",),
        )
        self.calls = []
        self.revoke = None

    def _called(self, name, tenant_id, request):
        self.calls.append((name, tenant_id, request))
        if self.revoke:
            self.revoke()

    def grids(self, tenant_id, request):
        self._called("grids", tenant_id, request)
        return self.grid_output

    def observations(self, tenant_id, request):
        self._called("observations", tenant_id, request)
        return self.observation_output

    def place_reference(self, tenant_id, request):
        self._called("place", tenant_id, request)
        return self.place_output

    def review_insights(self, tenant_id, request):
        self._called("reviews", tenant_id, request)
        return self.review_output


class LocalRegistry:
    def __init__(self):
        fields = (
            "local_grid_definition", "local_observation",
            "place_reference", "review_insight",
        )
        self.source_value = registration(
            adapter_id="local",
            source_api_version="local-api-v1",
            production_approved=True,
            field_rights=tuple(
                FieldRight(
                    field, frozenset({"internal_display"}),
                    NOW + timedelta(hours=2),
                )
                for field in fields
            ),
        )
        self.grant_value = Grant(
            tenant_id="tenant:local",
            asset_id="asset:local",
            grant_id="grant:local",
            version="1",
            fields=frozenset(fields),
            uses=frozenset({"internal_display"}),
            expires_at=NOW + timedelta(hours=2),
            source_adapter="local",
            source_version="1",
        )
        self.current = True

    def source(self, tenant_id, source_id, version):
        if (tenant_id, source_id, version) != ("tenant:local", "local", "1"):
            raise DomainError("not_authorized", "No source", 403)
        return self.source_value

    def grant(self, tenant_id, grant_id):
        if (tenant_id, grant_id) != ("tenant:local", "grant:local"):
            raise DomainError("not_authorized", "No grant", 403)
        return self.grant_value

    def assert_current(self, principal, *, now):
        if not self.current:
            raise DomainError("not_authorized", "Rights changed", 403)


class LocalContractTests(unittest.TestCase):
    def test_missing_points_and_ranks_are_not_fabricated(self):
        missing = observed(status="missing", depth=10, items=(), reason="not_returned")
        self.assertEqual(missing.to_dict()["items"], [])
        with self.assertRaises(DomainError):
            observed(status="missing", items=(
                GridItem("place:1", 1, "Invented"),))
        with self.assertRaises(DomainError):
            observed(items=(
                GridItem("place:1", 11, "Beyond depth"),))

    def test_grid_excluded_points_must_not_be_sampled(self):
        with self.assertRaises(DomainError):
            grid(excluded_points=(point("p1"),))
        with self.assertRaises(DomainError):
            grid(center={"lat": "91", "lng": "0"})

    def test_review_theme_denominators_and_micro_reference_boundary(self):
        with self.assertRaises(DomainError):
            ReviewTheme("service", 81, 80, ("e1",))
        insight = ReviewInsight(
            evidence(), "asset:local", "place:1", 100, 80,
            (ReviewTheme("service", 20, 80, ("e1",)),),
            "sort-v1", ("sample_not_all_consumers",))
        self.assertEqual(insight.themes[0].to_dict()["proportion"], "0.25")
        self.assertNotIn("raw_reviews", insight.themes[0].to_dict())


class LocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.registry = LocalRegistry()
        self.adapter = LocalAdapter()
        self.principal = Principal(
            "tenant:local", frozenset({"grant:local"}), "1",
            roles=frozenset({"reader"}))
        self.clock = NOW + timedelta(seconds=1)
        self.service = LocalService(
            self.registry, self.adapter,
            cursor_codec=CursorCodec(b"l" * 32), clock=lambda: self.clock)

    def params(self, **changes):
        value = {
            "source": "local", "source_version": "1", "asset": "asset:local",
        }
        value.update(changes)
        return value

    def test_all_routes_require_typed_source_and_scope(self):
        grids = self.service.read(
            self.principal, "/local/grids", self.params(), now=self.clock)
        self.assertEqual(grids["grids"][0]["grid_id"], "grid:1")
        observations = self.service.read(
            self.principal, "/local/grids/grid:1/observations",
            self.params(), now=self.clock)
        self.assertEqual(observations["observations"][0]["status"], "observed")
        place = self.service.read(
            self.principal, "/places/place:1/reference",
            self.params(), now=self.clock)
        self.assertFalse(place["place"]["raw_store_allowed"])
        reviews = self.service.read(
            self.principal, "/local/review-insights",
            self.params(place_id="place:1"), now=self.clock)
        self.assertEqual(reviews["themes"][0]["proportion"], "0.25")

    def test_raw_source_proxy_is_rejected_before_adapter(self):
        for path in (
                "/local/grids",
                "/local/grids/grid:1/observations",
                "/places/place:1/reference"):
            self.adapter.calls.clear()
            with self.subTest(path=path), self.assertRaises(DomainError):
                self.service.read(
                    self.principal, path,
                    self.params(fields="all"), now=self.clock)
            self.assertEqual(self.adapter.calls, [])

    def test_evidence_expiry_and_revocation_are_rechecked(self):
        self.adapter.grid_output = GridBundle(
            evidence(evidence_expires_at=(NOW + timedelta(milliseconds=500)).isoformat()),
            "asset:local", (grid(),))
        with self.assertRaises(DomainError) as error:
            self.service.read(
                self.principal, "/local/grids", self.params(), now=self.clock)
        self.assertEqual(error.exception.code, "expired")
        self.adapter.grid_output = GridBundle(evidence(), "asset:local", (grid(),))
        self.adapter.revoke = lambda: setattr(self.registry, "current", False)
        with self.assertRaises(DomainError) as error:
            self.service.read(
                self.principal, "/local/grids", self.params(), now=self.clock)
        self.assertEqual(error.exception.code, "not_authorized")

    def test_place_cannot_claim_raw_storage_beyond_grant(self):
        self.adapter.place_output = replace(
            self.adapter.place_output, raw_store_allowed=True)
        with self.assertRaises(DomainError) as error:
            self.service.read(
                self.principal, "/places/place:1/reference",
                self.params(), now=self.clock)
        self.assertEqual(error.exception.code, "use_not_permitted")


if __name__ == "__main__":
    unittest.main()
