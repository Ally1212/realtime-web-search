"""Independent, pinned Whale document readback for existing SERP uploads.

This checks a version returned by the Consumer API. It does not turn the old
per-URL upload into an approved run/item observation schema.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from .google_data import digest
from urllib.parse import quote, urlsplit

import requests

from .google_domain import DomainError, text


@dataclass(frozen=True)
class ReadbackTarget:
    document_id: str
    version_id: str
    dataset_id: str
    source_platform: str
    expected_content_hash: str | None = None
    expected_raw_payload_hash: str | None = None
    expected_platform_payload_hash: str | None = None

    def __post_init__(self):
        for name in ("document_id", "version_id", "dataset_id", "source_platform"):
            value = getattr(self, name)
            text(value, name, maximum=256)
            if any(c in value for c in "/?#"):
                raise DomainError("invalid_argument", "Readback target contains path characters")

        for name in ('expected_content_hash', 'expected_raw_payload_hash', 'expected_platform_payload_hash'):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', value)):
                raise DomainError('invalid_argument', 'Expected hashes must be lowercase SHA256')


class WhaleReadbackClient:
    def __init__(self, base_url: str, consumer_key: str, *, session=None, min_interval_seconds=0):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise DomainError("invalid_argument", "Whale readback requires a credential-free HTTPS base URL")
        text(consumer_key, "Consumer key", maximum=2048)
        self.base_url = base_url.rstrip("/")
        self._key = consumer_key
        if isinstance(min_interval_seconds, bool) or not isinstance(min_interval_seconds, (int, float)) or not 0 <= min_interval_seconds <= 15:
            raise DomainError('invalid_argument', 'Invalid readback request interval')
        self._interval = min_interval_seconds
        self._last_request = None
        self._session = session or requests.Session()

    def _request(self, method, path, *, body=None):
        if self._last_request is not None and self._interval:
            wait = self._interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
        self._last_request = time.monotonic()
        try:
            response = self._session.request(
                method, self.base_url + path,
                headers={"Authorization": "Bearer " + self._key,
                         "Accept": "application/json",
                         **({"Content-Type": "application/json"} if body is not None else {})},
                json=body, timeout=15, allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DomainError("source_error", "Whale readback network failed", 503) from None
        if response.status_code in {401, 403}:
            raise DomainError("not_authorized", "Consumer key lacks document/version read access", 403)
        if response.status_code == 404:
            raise DomainError("not_covered", "Requested Whale document/version is unavailable", 404)
        if response.status_code != 200:
            raise DomainError("source_error", "Whale readback failed", 503)
        try:
            payload = response.json()
        except ValueError:
            raise DomainError("source_error", "Whale returned invalid JSON", 503) from None
        if not isinstance(payload, dict):
            raise DomainError("source_error", "Whale returned an invalid readback envelope", 503)
        return payload

    def _read_pair(self, target: ReadbackTarget):
        document_response = self._request(
            "GET", "/v1/documents/" + quote(target.document_id, safe=""))
        document = document_response.get("document")
        self._check_document(target, document)
        versions_response = self._request(
            "POST", "/v1/versions/batch-get",
            body={"items": [{"document_id": target.document_id,
                              "version_id": target.version_id}],
                  "include_body": False},
        )
        versions = versions_response.get("versions")
        if not isinstance(versions, list) or len(versions) != 1:
            raise DomainError("source_error", "Whale version readback is incomplete", 503)
        return document, versions[0]

    def verify(self, target: ReadbackTarget):
        document, version = self._read_pair(target)
        return self._proof(target, document, version)

    def read_full_run(self, target, *, expected_payload=None):
        """Read an immutable run envelope, never reconstruct from current metadata."""
        document, version = self._read_pair(target)
        proof = self._proof(target, document, version)
        payload = version.get('platform_payload')
        if not isinstance(payload, dict) or payload.get('schema_version') != 'google-serp-run-envelope-1':
            raise DomainError('not_covered', 'Pinned full-run platform payload is unavailable', 404)
        run_id, observation = payload.get('run_id'), payload.get('observation')
        if (not isinstance(run_id, str) or not run_id or not isinstance(observation, dict)
                or observation.get('request_id') != run_id or observation.get('source') != 'google_web'
                or document.get('external_content_id') != 'serp-run:' + run_id):
            raise DomainError('source_error', 'Pinned run identity differs', 503)
        occurrences = observation.get('occurrences')
        if not isinstance(occurrences, list):
            raise DomainError('source_error', 'Pinned run occurrence list is invalid', 503)
        identities = set()
        for item in occurrences:
            if (not isinstance(item, dict) or item.get('request_id') != run_id
                    or not isinstance(item.get('occurrence_id'), str) or not item['occurrence_id']
                    or item['occurrence_id'] in identities):
                raise DomainError('source_error', 'Pinned run occurrence identity differs', 503)
            identities.add(item['occurrence_id'])
        # JSON numeric spellings 1 and 1.0 are equal; booleans are not numbers.
        def semantic(value):
            if type(value) is float and value.is_integer():
                return int(value)
            if isinstance(value, list):
                return [semantic(v) for v in value]
            if isinstance(value, dict):
                return {k: semantic(v) for k, v in value.items()}
            return value
        if expected_payload is not None and digest(semantic(payload)) != digest(semantic(expected_payload)):
            raise DomainError('source_error', 'Pinned full run differs from expected source', 503)
        proof.update(run_id=run_id, occurrence_count=len(occurrences), payload_binding='pinned_version',
                     payload_roundtrip_verified=expected_payload is not None)
        return dict(proof=proof, payload=payload)

    def read_full_feed_run(self, target, *, expected_candidate):
        """Validate a pinned News/Trending Now candidate, not formal admission.

        An explicit source-bound candidate is mandatory. Current document
        metadata never substitutes for the immutable version's payload.
        This method has no upload, registration or configuration side effects.
        """
        from .google_feed_delivery import validate_feed_candidate, validate_feed_payload
        summary = validate_feed_candidate(expected_candidate)
        if target.source_platform != summary['source']:
            raise DomainError('invalid_argument', 'Feed target source differs from expected candidate')
        document, version = self._read_pair(target)
        proof = self._proof(target, document, version)
        payload = version.get('platform_payload')
        if not isinstance(payload, dict):
            raise DomainError('not_covered', 'Pinned feed payload is unavailable', 404)
        actual = validate_feed_payload(payload)
        if (actual != summary
                or document.get('external_content_id') != expected_candidate['object_key']
                or digest(payload) != digest(expected_candidate['payload'])
                or str(version['version'].get('raw_payload_hash') or '').removeprefix('sha256:')
                    != expected_candidate['payload_hash'].removeprefix('sha256:')):
            raise DomainError('source_error', 'Pinned feed identity, hash or payload differs', 503)
        proof.update(**summary, payload_binding='pinned_version', payload_roundtrip_verified=True,
                     candidate_contract_only=True)
        return dict(proof=proof, payload=payload)

    @staticmethod
    def _check_document(target, document):
        if not isinstance(document, dict) or (
            document.get("id") != target.document_id
            or document.get("dataset_id") != target.dataset_id
            or document.get("source_platform") != target.source_platform
        ):
            raise DomainError("source_error", "Whale document identity or dataset differs", 503)

    def _proof(self, target, document, item):
        self._check_document(target, document)
        if not isinstance(item, dict):
            raise DomainError("source_error", "Whale version item is invalid", 503)
        if item.get("error"):
            code = item['error'].get('code') if isinstance(item['error'], dict) else None
            raise DomainError("not_authorized" if code == 'forbidden' else "not_covered",
                              "Whale denied or omitted the pinned version", 403 if code == 'forbidden' else 404)
        version = item.get("version")
        if (item.get("document_id") != target.document_id
                or item.get("version_id") != target.version_id
                or not isinstance(version, dict)
                or version.get("document_id") != target.document_id
                or version.get("version_id") != target.version_id):
            raise DomainError("source_error", "Whale returned a different document version", 503)
        expected = {'content_hash': target.expected_content_hash,
                    'raw_payload_hash': target.expected_raw_payload_hash}
        verified = []
        for name, value in expected.items():
            if value is not None:
                if str(version.get(name) or '').removeprefix('sha256:') != value.removeprefix('sha256:'):
                    raise DomainError('source_error', 'Whale pinned content hash differs', 503)
                verified.append(name)
        if target.expected_platform_payload_hash is not None:
            payload = item.get('platform_payload')
            if not isinstance(payload, dict) or digest(payload) != target.expected_platform_payload_hash.removeprefix('sha256:'):
                raise DomainError('source_error', 'Whale pinned platform payload differs', 503)
            verified.append('platform_payload_hash')
        return {
            "document_id": target.document_id,
            "version_id": target.version_id,
            "dataset_id": target.dataset_id,
            "source_platform": target.source_platform,
            "version_readable": True,
            "current_document_version_matches": document.get('current_version_id') == target.version_id,
            "document_status": document.get('status'),
            "metadata_binding": 'current_document_not_pinned_version_payload',
            "discovery_metadata_present": isinstance(document.get('metadata', {}).get('discovery'), dict)
                if isinstance(document.get('metadata'), dict) else False,
            "verified_hashes": verified,
            "content_verification": "matched" if verified else "not_requested",
            "platform_payload_visible": isinstance(item.get("platform_payload"), dict),
            "content_hash": version.get("content_hash"),
            "raw_payload_hash": version.get("raw_payload_hash"),
            "run_item_schema_verified": False,
            "terminal_receipt_verified": False,
        }


    def verify_many(self, targets):
        """Read at most 100 targets; per-item failures never become empty successes."""
        if not isinstance(targets, list) or not 1 <= len(targets) <= 100 or any(not isinstance(t, ReadbackTarget) for t in targets):
            raise DomainError('invalid_argument', 'Readback requires 1-100 typed targets')
        documents = {}
        ids = list(dict.fromkeys(t.document_id for t in targets))
        for offset in range(0, len(ids), 50):
            wanted = ids[offset:offset + 50]
            response = self._request('POST', '/v1/documents/batch-get',
                                     body={'document_ids': wanted, 'include_content': False})
            rows = response.get('documents')
            if not isinstance(rows, list):
                raise DomainError('source_error', 'Whale document batch is invalid', 503)
            for row in rows:
                if not isinstance(row, dict) or row.get('document_id') not in wanted or row['document_id'] in documents:
                    raise DomainError('source_error', 'Whale document batch identity differs', 503)
                documents[row['document_id']] = row
        pairs = list(dict.fromkeys((t.document_id, t.version_id) for t in targets))
        response = self._request('POST', '/v1/versions/batch-get', body={
            'items': [{'document_id': d, 'version_id': v} for d, v in pairs], 'include_body': False})
        versions = {}
        rows = response.get('versions')
        if not isinstance(rows, list):
            raise DomainError('source_error', 'Whale version batch is invalid', 503)
        for row in rows:
            if not isinstance(row, dict):
                raise DomainError('source_error', 'Whale version batch item invalid', 503)
            pair = (row.get('document_id'), row.get('version_id'))
            if pair not in pairs or pair in versions:
                raise DomainError('source_error', 'Whale version batch identity differs', 503)
            versions[pair] = row
        outcomes = []
        for target in targets:
            item = dict(document_id=target.document_id, version_id=target.version_id)
            try:
                document = documents.get(target.document_id)
                version = versions.get((target.document_id, target.version_id))
                if document is None or version is None:
                    raise DomainError('not_covered', 'Whale omitted a requested item', 404)
                if document.get('error'):
                    code = document['error'].get('code') if isinstance(document['error'], dict) else None
                    raise DomainError('not_authorized' if code == 'forbidden' else 'not_covered', 'Whale document unavailable', 403 if code == 'forbidden' else 404)
                item['proof'] = self._proof(target, document.get('document'), version)
            except DomainError as error:
                item['error'] = error.code
            outcomes.append(item)
        return outcomes
