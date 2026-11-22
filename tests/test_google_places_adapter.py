import io
import os
import json
import unittest
from unittest.mock import patch

from realtime.google_places_adapter import DETAIL_FIELDS, LIST_FIELDS, availability, detail, search
from realtime.google_local_jobs import parameters
from realtime.product import ProductError


ENV = {
    "SEARCHFLOW_GOOGLE_PLACES_ENABLED": "true",
    "SEARCHFLOW_GOOGLE_LOCAL_APPROVED": "true",
    "SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED": "true",
    "SEARCHFLOW_GOOGLE_LOCAL_CONTRACT_REF": "fixture-contract",
    "SEARCHFLOW_GOOGLE_LOCAL_DATASET_ID": "fixture-dataset",
    "SEARCHFLOW_GOOGLE_LOCAL_APPROVAL_REF": "fixture-approval",
    "SEARCHFLOW_GOOGLE_PLACES_API_KEY": "secret-test-key",
}


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
        self.headers = {"X-Goog-Maps-Platform-Request-Id": "request-1"}

    def read(self, limit):
        return self.payload


class PlacesAdapterTests(unittest.TestCase):
    def test_key_and_feature_flag_do_not_bypass_local_contract_approvals(self):
        state = availability({"SEARCHFLOW_GOOGLE_PLACES_ENABLED": "true",
                              "SEARCHFLOW_GOOGLE_PLACES_API_KEY": "secret"})
        self.assertFalse(state["enabled"])
        self.assertTrue(state["configured"])
        self.assertFalse(state["contract_configured"])

    def test_text_search_uses_list_mask_and_normalizes_only_display_fields(self):
        seen = {}

        def opener(request, timeout):
            seen.update(url=request.full_url, headers=dict(request.headers), body=json.loads(request.data))
            return Response({"places": [{"id": "place-1", "displayName": {"text": "Cafe"},
                                           "location": {"latitude": 1.2, "longitude": 3.4},
                                           "rating": 4.7, "userRatingCount": 21,
                                           "reviews": [{"text": "must not escape"}]}]})

        result = search(parameters({"mode": "text", "query": "coffee", "city": "Singapore",
                                    "region": "SG", "language": "en"}), opener=opener, env=ENV)
        self.assertEqual(seen["body"]["textQuery"], "coffee Singapore")
        self.assertEqual(seen["headers"]["X-goog-fieldmask"], LIST_FIELDS)
        self.assertNotIn("secret-test-key", json.dumps(result))
        self.assertNotIn("reviews", result["places"][0])
        self.assertEqual(result["places"][0]["place_id"], "place-1")

    def test_nearby_requires_center_and_type(self):
        with self.assertRaises(ProductError):
            parameters({"mode": "nearby", "included_type": "cafe"})
        value = parameters({"mode": "nearby", "included_type": "cafe",
                            "location": {"lat": 1.2, "lng": 103.8}, "radius_m": 800})
        self.assertEqual(value["radius_m"], 800)

    def test_detail_uses_detail_mask_without_reviews_or_photos(self):
        seen = {}

        def opener(request, timeout):
            seen.update(url=request.full_url, headers=dict(request.headers))
            return Response({"id": "place-1", "displayName": {"text": "Cafe"},
                             "nationalPhoneNumber": "+65 1234"})

        result = detail("place-1", language="en", region="SG", opener=opener, env=ENV)
        self.assertEqual(seen["headers"]["X-goog-fieldmask"], DETAIL_FIELDS)
        self.assertNotIn("reviews", DETAIL_FIELDS)
        self.assertNotIn("photos", DETAIL_FIELDS)
        self.assertEqual(result["place"]["national_phone_number"], "+65 1234")

    def test_google_gateway_routes_places_poc_before_legacy_google_domain(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}):
            from realtime.google_gateway import Handler
        class Fake:
            path = "/v1/google/local/capabilities"
        calls = []
        with patch("realtime.google_local_http.handle", side_effect=lambda handler, method: calls.append((handler.path, method)) or True):
            Handler._google(Fake(), "GET")
        self.assertEqual(calls, [("/v1/google/local/capabilities", "GET")])

    def test_generated_page_maps_uses_self_hosted_search_without_customer_key(self):
        from realtime.serp_page import PAGE
        start, end = PAGE.index('<section id="mapsView"'), PAGE.index('<section id="apiView"')
        section = PAGE[start:end]
        self.assertIn('Google Maps 商家搜索', section)
        self.assertIn('id="mapsView"', PAGE)
        import re, json
        source_data = re.search(r'<script[^>]*id="sfSourceData"[^>]*>(.*?)</script>', PAGE, re.S)
        self.assertIsNotNone(source_data)
        self.assertIn('google_maps', {item['id'] for item in json.loads(source_data.group(1))})
        self.assertIn('下载 CSV', section)
        self.assertIn('无需填写 API 密钥', section)
        self.assertNotIn('Whale 同步</button>', section)


if __name__ == "__main__":
    unittest.main()
