"""Minimal Google Places API (New) client for short-lived, on-demand display.

The adapter never logs credentials or response bodies.  Callers may cache the
normalized display payload briefly, but only place IDs and audit metadata are
intended for durable storage.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .product import ProductError

API_ROOT = "https://places.googleapis.com/v1"
LIST_FIELDS = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.primaryType,places.types,places.businessStatus,places.rating,"
    "places.userRatingCount,places.googleMapsUri"
)
DETAIL_FIELDS = (
    "id,displayName,formattedAddress,location,primaryType,types,businessStatus,"
    "websiteUri,nationalPhoneNumber,regularOpeningHours,rating,userRatingCount,"
    "googleMapsUri"
)


def _secret(env=os.environ):
    value = env.get("SEARCHFLOW_GOOGLE_PLACES_API_KEY", "").strip()
    if value:
        return value
    path = Path(env.get("SEARCHFLOW_GOOGLE_PLACES_API_KEY_FILE", "state/private/google-places-api-key"))
    return path.read_text().strip() if path.is_file() else ""


def availability(env=os.environ):
    enabled = env.get("SEARCHFLOW_GOOGLE_PLACES_ENABLED") == "true"
    # Reuse the existing Local source contract and its two independent
    # approvals.  A Places key alone must never make production collection
    # available.
    approved = env.get("SEARCHFLOW_GOOGLE_LOCAL_APPROVED") == "true"
    production = env.get("SEARCHFLOW_GOOGLE_LOCAL_PRODUCTION_APPROVED") == "true"
    contract_configured = all(env.get(name, "").strip() for name in (
        "SEARCHFLOW_GOOGLE_LOCAL_CONTRACT_REF",
        "SEARCHFLOW_GOOGLE_LOCAL_DATASET_ID",
        "SEARCHFLOW_GOOGLE_LOCAL_APPROVAL_REF",
    ))
    configured = bool(_secret(env))
    return {
        "enabled": enabled and approved and production and contract_configured and configured,
        "configured": configured,
        "approved": approved,
        "production_approved": production,
        "contract_configured": contract_configured,
        "cache_ttl_seconds": 1800,
        "reviews": False,
        "photos": False,
        "export": False,
        "whale": False,
    }


def _request(path, payload, field_mask, *, method="POST", opener=None, env=os.environ):
    state = availability(env)
    if not state["enabled"]:
        raise ProductError("google_places_disabled", "Google Maps 实时查询尚未完成来源合同、双批准或密钥配置", 503)
    raw = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(
        API_ROOT + path,
        data=raw,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": _secret(env),
            "X-Goog-FieldMask": field_mask,
            "User-Agent": "SearchFlow-Places/1.0",
        },
    )
    try:
        response = (opener or urllib.request.urlopen)(req, timeout=35)
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ProductError("upstream_invalid", "Google Places 响应过大", 502)
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("response is not object")
        request_id = response.headers.get("X-Goog-Maps-Platform-Request-Id") or response.headers.get("X-Request-Id")
        return data, request_id, hashlib.sha256(body).hexdigest()
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status == 429:
            raise ProductError("google_places_rate_limited", "Google Places 暂时限流", 429) from None
        if status in (401, 403):
            raise ProductError("google_places_not_authorized", "Google Places 凭据或 API 权限无效", 503) from None
        if status >= 500:
            raise ProductError("google_places_unavailable", "Google Places 暂时不可用", 503) from None
        raise ProductError("google_places_invalid_request", "Google Places 拒绝了查询参数", 400) from None
    except ProductError:
        raise
    except (OSError, ValueError, json.JSONDecodeError):
        raise ProductError("google_places_unavailable", "Google Places 暂时不可用", 503) from None


def _name(value):
    return value.get("text") if isinstance(value, dict) and isinstance(value.get("text"), str) else None


def normalize_place(place, *, detail=False):
    if not isinstance(place, dict) or not isinstance(place.get("id"), str):
        return None
    location = place.get("location") if isinstance(place.get("location"), dict) else {}
    result = {
        "place_id": place["id"],
        "name": _name(place.get("displayName")),
        "formatted_address": place.get("formattedAddress") if isinstance(place.get("formattedAddress"), str) else None,
        "location": {"lat": location.get("latitude"), "lng": location.get("longitude")} if location else None,
        "primary_type": place.get("primaryType") if isinstance(place.get("primaryType"), str) else None,
        "types": [x for x in place.get("types", []) if isinstance(x, str)][:20],
        "business_status": place.get("businessStatus") if isinstance(place.get("businessStatus"), str) else None,
        "rating": place.get("rating") if isinstance(place.get("rating"), (int, float)) else None,
        "user_rating_count": place.get("userRatingCount") if type(place.get("userRatingCount")) is int else None,
        "google_maps_uri": place.get("googleMapsUri") if isinstance(place.get("googleMapsUri"), str) else None,
    }
    if detail:
        result.update(
            website_uri=place.get("websiteUri") if isinstance(place.get("websiteUri"), str) else None,
            national_phone_number=place.get("nationalPhoneNumber") if isinstance(place.get("nationalPhoneNumber"), str) else None,
            regular_opening_hours=place.get("regularOpeningHours") if isinstance(place.get("regularOpeningHours"), dict) else None,
        )
    return result


def search(parameters, *, opener=None, env=os.environ):
    mode = parameters["mode"]
    location = parameters.get("location")
    restriction = None
    if location:
        restriction = {"circle": {"center": {"latitude": location["lat"], "longitude": location["lng"]}, "radius": parameters["radius_m"]}}
    if mode == "text":
        text_query = parameters["query"] + ((" " + parameters["city"]) if parameters.get("city") else "")
        payload = {"textQuery": text_query, "pageSize": 20, "languageCode": parameters["language"], "regionCode": parameters["region"]}
        if restriction:
            payload["locationBias"] = restriction
        if parameters.get("included_type"):
            payload["includedType"] = parameters["included_type"]
        path, sku = "/places:searchText", "places_text_search_pro"
    else:
        payload = {"maxResultCount": 20, "languageCode": parameters["language"], "regionCode": parameters["region"], "locationRestriction": restriction}
        if parameters.get("included_type"):
            payload["includedTypes"] = [parameters["included_type"]]
        payload["rankPreference"] = "DISTANCE"
        path, sku = "/places:searchNearby", "places_nearby_search_pro"
    data, request_id, response_hash = _request(path, payload, LIST_FIELDS, opener=opener, env=env)
    places = [x for x in (normalize_place(p) for p in data.get("places", [])) if x]
    return {"places": places, "provider_request_id": request_id, "response_hash": response_hash, "sku": sku, "field_mask": LIST_FIELDS}


def detail(place_id, *, language="en", region="US", opener=None, env=os.environ):
    encoded = urllib.parse.quote(place_id, safe="")
    query = urllib.parse.urlencode({"languageCode": language, "regionCode": region})
    data, request_id, response_hash = _request("/places/" + encoded + "?" + query, None, DETAIL_FIELDS, method="GET", opener=opener, env=env)
    place = normalize_place(data, detail=True)
    if not place:
        raise ProductError("google_place_not_found", "地点不存在或未返回可显示字段", 404)
    return {"place": place, "provider_request_id": request_id, "response_hash": response_hash, "sku": "place_details_enterprise", "field_mask": DETAIL_FIELDS}
