"""Bounded temporary event spool with retry identity and staged reconciliation.

No background sender or schema migration is started by import/instantiation.
Formal Whale schema and source admission must be approved before wiring a
sender. Queued, terminal receipt and independent readback remain separate.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .google_domain import DomainError, canonical_json, decimal, identity, parse_time, text, utc_text
from .google_ingest_ledger import Counter, HourlyIngestLedger, RunRecord
from .google_rights import (
    DeliveryContext, FieldRight, Grant, IngestPermit, Principal,
    SourceRegistration, authorize,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_ingest_events (
 event_id text PRIMARY KEY, tenant_id text NOT NULL, grant_id text NOT NULL,
 asset_id text NOT NULL, source_adapter text NOT NULL, rights_version text NOT NULL,
 source_version text NOT NULL, source_api_version text NOT NULL,
 license_id text NOT NULL, license_version text NOT NULL,
 grant_version text NOT NULL, permit_id text NOT NULL, permit_version text NOT NULL,
 delivery_json jsonb NOT NULL,
 run_id text NOT NULL, item_id text NOT NULL, payload_hash char(64) NOT NULL,
 observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
 source_observed_at timestamptz, cache_hit boolean NOT NULL,
 mode text NOT NULL CHECK(mode IN ('scheduled','snapshot','backfill','live')),
 ingest_class text NOT NULL CHECK(ingest_class IN ('normal','realtime','backfill')),
 fields_json jsonb, field_names jsonb NOT NULL, payload_bytes integer NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','inflight','ingest_pending','terminal','readable','rejected','expired','canceled')),
 attempts integer NOT NULL DEFAULT 0,
 expires_at timestamptz NOT NULL, next_attempt_at timestamptz NOT NULL,
 lease_token text, lease_expires_at timestamptz,
 receipt_id text, receipt_status text, document_id text, document_version text,
 readable_at timestamptz, error_code text,
 CHECK(received_at>=observed_at),
 CHECK(source_observed_at IS NULL OR source_observed_at<=observed_at)
);
CREATE INDEX IF NOT EXISTS sf_google_ingest_due
 ON sf_google_ingest_events(tenant_id,source_adapter,next_attempt_at)
 WHERE state IN ('pending','inflight');
"""


VALIDATION_RAW_ENVIRONMENT = "validation_raw"


def _safe(value):
    """Transport secrets must not become business facts or error payloads."""
    sensitive = r"token|password|secret|cookie|authorization|api[_-]?key|proxy|headers"
    if isinstance(value, dict):
        if any(re.search(sensitive, key, re.I) for key in value):
            raise DomainError("invalid_argument", "Sensitive transport field is forbidden")
        for child in value.values():
            _safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe(child)
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        try:
            url = urlsplit(value)
            if url.username or url.password or any(re.search(sensitive, k, re.I)
                                                  for k, _ in parse_qsl(url.query)):
                raise ValueError()
        except ValueError:
            raise DomainError("invalid_argument", "Credential-bearing URL is forbidden") from None


def _event(row):
    if row is None:
        raise DomainError("not_authorized", "Event is not accessible", 404)
    result = dict(row)
    result["fields"] = result.pop("fields_json")
    return result


class IngestSpool:
    def __init__(self, dsn, *, max_bytes=2_000_000_000, max_age_seconds=86400,
                 production=True, hourly_ledger=None):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 2_000_000_000:
            raise DomainError("invalid_argument", "Spool capacity must be between 1 byte and 2 GB")
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400:
            raise DomainError("invalid_argument", "Spool retention must not exceed 24 hours")
        if type(production) is not bool:
            raise DomainError("invalid_argument", "production must be explicit boolean")
        self.dsn, self.max_bytes, self.max_age_seconds = dsn, max_bytes, max_age_seconds
        self.production = production
        self.hourly_ledger = hourly_ledger
        self._connections = threading.BoundedSemaphore(2)

    def admission_check(self, *, permit, delivery, mode, ingest_class, production=False):
        """Fail closed before an environment-specific key or task is created."""
        if not isinstance(permit, IngestPermit) or not isinstance(delivery, DeliveryContext):
            raise DomainError("not_authorized", "Typed platform permit and delivery are required", 403)
        if production and not (permit.production_approved and permit.admission == "approved"):
            raise DomainError(
                "use_not_permitted", "Production registration requires separate approval", 403)
        # validation_raw must remain an explicit non-production environment.  A
        # key intended for another environment cannot be silently re-labelled.
        if delivery.task_id is not None and delivery.task_type == "dry_run":
            raise DomainError("use_not_permitted", "Dry-run delivery is not admitted", 403)
        if permit.capabilities and not delivery.provided_capabilities:
            raise DomainError("invalid_argument", "Empty capability placeholder is rejected", 400)
        if (not delivery.task_id and not permit.autonomous_allowed
                or delivery.task_id is not None and delivery.task_type not in permit.task_types):
            raise DomainError("not_authorized", "Task identity/type is not admitted", 403)
        # The typed check below rejects wrong Dataset/platform/agent/key scope,
        # unapproved capabilities and mode/class combinations.
        permit.check(
            principal=Principal(
                permit.tenant_id, frozenset({permit.grant_id}), "admission-check"),
            grant=_admission_grant(permit),
            source=_admission_source(permit),
            delivery=delivery, mode=mode, ingest_class=ingest_class,
            fields={{"body": "body_text"}.get(capability, capability)
                    for capability in delivery.provided_capabilities - {"identity"}},
            now=permit.expires_at - timedelta(microseconds=1), production=production,
        )
        return {
            "environment": VALIDATION_RAW_ENVIRONMENT,
            "permit_id": permit.permit_id,
            "permit_version": permit.version,
            "dataset_id": delivery.dataset_id,
            "source_platform": delivery.source_platform,
            "agent_id": delivery.agent_id,
            "task_id": delivery.task_id,
            "task_type": delivery.task_type,
            "provided_capabilities": sorted(delivery.provided_capabilities),
            "production_key_created": False,
        }

    @contextmanager
    def connect(self):
        with self._connections:
            with psycopg.connect(self.dsn, row_factory=dict_row) as db:
                yield db

    def initialize(self):
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,1)")
            db.execute(SCHEMA)

    def enqueue(self, *, principal, grant, source, run_id, item_id, observed_at,
                received_at, source_observed_at, cache_hit, fields, mode, ingest_class, now,
                permit=None, delivery=None):
        for name, value in (("run_id", run_id), ("item_id", item_id)):
            text(value, name)
        if type(cache_hit) is not bool:
            raise DomainError("invalid_argument", "Cache state must be explicit")
        if not isinstance(fields, dict) or not fields:
            raise DomainError("invalid_argument", "Event requires licensed source fields")
        if not isinstance(permit, IngestPermit):
            raise DomainError("not_authorized", "Independent platform ingest permit is required", 403)
        permit.check(principal=principal, grant=grant, source=source, delivery=delivery,
                     mode=mode, ingest_class=ingest_class, fields=set(fields), now=now,
                     production=self.production)
        canonical_json(fields)
        _safe(fields)
        observed, received, current = parse_time(observed_at), parse_time(received_at), parse_time(now)
        source_time = parse_time(source_observed_at) if source_observed_at is not None else None
        if received < observed or received > current or source_time is not None and source_time > observed:
            raise DomainError("invalid_argument", "Event times are inconsistent")
        if cache_hit and source_time is None:
            raise DomainError("invalid_argument", "Cached response requires original source observation time")
        expiries = [received + timedelta(seconds=self.max_age_seconds), parse_time(grant.expires_at),
                    permit.expires_at]
        for name in fields:
            right = authorize(principal, grant, source, name, "raw_store", now=current,
                              production=self.production)
            if right.retention_deadline is not None:
                expiries.append(right.retention_deadline)
        expiry = min(expiries)
        if expiry <= current:
            raise DomainError("expired", "Event is past its spool/source deadline", 410)
        event_id = identity("ingest-event", dict(tenant_id=principal.tenant_id,
                            source_adapter=source.adapter_id, run_id=run_id, item_id=item_id))
        frozen = dict(
            event_id=event_id, grant_id=grant.grant_id, asset_id=grant.asset_id,
            rights_version=principal.rights_version, source_version=source.adapter_version,
            source_api_version=source.source_api_version,
            license_id=source.license_id, license_version=source.license_version,
            grant_version=grant.version, permit_id=permit.permit_id, permit_version=permit.version,
            delivery=delivery.to_dict(),
            observed_at=utc_text(observed), received_at=utc_text(received),
            source_observed_at=utc_text(source_time) if source_time else None,
            fields=fields, cache_hit=cache_hit, mode=mode, ingest_class=ingest_class,
        )
        raw = canonical_json(frozen)
        digest, size = hashlib.sha256(raw.encode()).hexdigest(), len(raw.encode())
        with self.connect() as db:
            # The lock covers deduplication and byte reservation across processes.
            db.execute("SELECT pg_advisory_xact_lock(20261104,2)")
            prior = db.execute("SELECT * FROM sf_google_ingest_events WHERE event_id=%s",
                               (event_id,)).fetchone()
            if prior:
                if prior["payload_hash"] != digest:
                    raise DomainError("idempotency_conflict", "Stable event changed payload/time/rights", 409)
                return _event(prior)
            used = db.execute(
                "SELECT coalesce(sum(payload_bytes),0) n FROM sf_google_ingest_events WHERE fields_json IS NOT NULL"
            ).fetchone()["n"]
            if used + size > self.max_bytes:
                raise DomainError("quota_exceeded", "Temporary spool is full; acquisition must pause", 429)
            row = db.execute("""
                INSERT INTO sf_google_ingest_events
                (event_id,tenant_id,grant_id,asset_id,source_adapter,rights_version,run_id,item_id,
                 payload_hash,observed_at,received_at,source_observed_at,cache_hit,mode,ingest_class,
                 fields_json,field_names,payload_bytes,expires_at,next_attempt_at,
                 source_version,source_api_version,license_id,license_version,
                 grant_version,permit_id,permit_version,delivery_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (event_id, principal.tenant_id, grant.grant_id, grant.asset_id, source.adapter_id,
                  principal.rights_version, run_id, item_id, digest, observed, received, source_time,
                  cache_hit, mode, ingest_class, Jsonb(json.loads(canonical_json(fields))),
                  Jsonb(sorted(fields)), size, expiry, current, source.adapter_version,
                  source.source_api_version, source.license_id, source.license_version,
                  grant.version, permit.permit_id, permit.version, Jsonb(delivery.to_dict()))).fetchone()
        return _event(row)

    def _hourly_record(self, row, *, db, transition_at, source_attempts=0, billable_units=0,
                       parsed=None, sent=0, accepted=0, terminal=0, readable=0,
                       rights_expired=0, state="succeeded", retry_of=None):
        if not isinstance(self.hourly_ledger, HourlyIngestLedger):
            return
        if self.hourly_ledger.dsn != self.dsn:
            raise DomainError('incompatible_scope', 'Spool accounting requires the same persistent database scope', 409)
        counters = []
        stages = []
        for stage, unit, quantity in (
                ("source_attempts", "source_request", source_attempts),
                ("billable_units", "source_billing_unit", billable_units),
                ("parsed", "stable_event", parsed),
                ("sent", "stable_event", sent),
                ("accepted", "platform_receipt", accepted),
                ("terminal", "platform_receipt", terminal),
                ("readable", "platform_document", readable),
                ("rights_expired", "stable_event", rights_expired),
        ):
            if quantity is not None and (
                    quantity > 0
                    or stage in {"sent", "rights_expired"}):
                counters.append(Counter(stage, unit, quantity))
                stages.append(stage)
        # Transition identities include every explicit stage so a retry with
        # no new stage never collides with the earlier sent transition.
        stages.append(state)
        record_id = "delivery-event-ledger-2:" + row["event_id"] + ":" + str(row["attempts"]) + ":" + "-".join(stages)
        try:
            run=RunRecord(
                run_id=record_id,
                tenant_id=row["tenant_id"],
                source=row["source_adapter"],
                scheduled_at=row["observed_at"],
                requested_at=row["received_at"],
                source_observed_at=row["source_observed_at"],
                received_at=row["received_at"],
                state=state,
                cache_hit=row["cache_hit"],
                retry_of=retry_of,
                counters=tuple(counters),
                record_kind="delivery_transition",
            )
            self.hourly_ledger.record(run, connection=db)
        except DomainError as exc:
            if exc.code=='precondition_failed':
                self.hourly_ledger.record_late_transition(run,event_id=row['event_id'],
                    transition_at=transition_at,connection=db)
                return
            if exc.code == "idempotency_conflict":
                raise
            # Older rows without explicit source-run timing stay unrecorded
            # rather than being reconstructed as if they had that evidence.
            if exc.code == "invalid_argument":
                return
            raise

    def _hourly_stage(self, row):
        hour = row["observed_at"].replace(minute=0, second=0, microsecond=0)
        return self.hourly_ledger.tenant_hour_summary(
            row["tenant_id"], row["source_adapter"], hour,
        )["counters"]

    def get(self, principal, event_id, *, source=None, grant=None, now=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sf_google_ingest_events WHERE event_id=%s AND tenant_id=%s",
                (event_id, principal.tenant_id)).fetchone()
        result = _event(row)
        if source is None and grant is None and now is None:
            result["fields"] = None
            return result
        if source is None or grant is None or now is None:
            raise DomainError("not_authorized", "Field inspection requires current source/grant/time", 403)
        if row["expires_at"] <= parse_time(now):
            raise DomainError("expired", "Temporary event fields have expired", 410)
        self._authorize_event(row, principal, source, grant, now=now, use="raw_store")
        return result

    def purge_expired(self, *, now):
        current=parse_time(now)
        with self.connect() as db:
            rows = db.execute("""
                UPDATE sf_google_ingest_events SET state='expired',fields_json=NULL,payload_bytes=0,
                 lease_token=NULL,lease_expires_at=NULL,error_code='expired'
                WHERE expires_at<=%s AND state NOT IN ('expired','canceled','rejected')
                RETURNING *
            """, (current,)).fetchall()
            for row in rows:
                self._hourly_record(
                    row, db=db, transition_at=current,
                    rights_expired=1,
                    state="failed",
                )
            return len(rows)

    def _authorize_event(self, row, principal, source, grant, *, now, use):
        if (row["source_adapter"] != source.adapter_id or row["grant_id"] != grant.grant_id
                or row["asset_id"] != grant.asset_id or row["grant_version"] != grant.version
                or row["source_version"] != source.adapter_version
                or row["source_api_version"] != source.source_api_version
                or row["license_id"] != source.license_id or row["license_version"] != source.license_version):
            raise DomainError("not_authorized", "Current event rights/source/asset do not match", 403)
        for name in row["field_names"]:
            authorize(principal, grant, source, name, use, now=now, production=self.production)

    def reserve(self, *, principal, source, resolve_grant, now, limit=20, resolve_permit=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError("invalid_argument", "Batch limit must be between 1 and 100")
        if not callable(resolve_permit):
            raise DomainError("not_authorized", "Dispatch requires a current platform permit resolver", 403)
        current, result = parse_time(now), []
        self.purge_expired(now=current)
        with self.connect() as db:
            rows = db.execute("""
                SELECT * FROM sf_google_ingest_events
                WHERE tenant_id=%s AND source_adapter=%s AND state IN ('pending','inflight')
                  AND next_attempt_at<=%s AND expires_at>%s
                  AND (lease_expires_at IS NULL OR lease_expires_at<=%s)
                ORDER BY received_at,event_id FOR UPDATE SKIP LOCKED LIMIT %s
            """, (principal.tenant_id, source.adapter_id, current, current, current, limit)).fetchall()
            for row in rows:
                try:
                    grant = resolve_grant(row["grant_id"])
                    self._authorize_event(row, principal, source, grant, now=current, use="raw_store")
                    permit = resolve_permit(row["permit_id"])
                    if permit.permit_id != row["permit_id"] or permit.version != row["permit_version"]:
                        raise DomainError("not_authorized", "Platform permit version changed", 403)
                    permit.check(principal=principal, grant=grant, source=source,
                                 delivery=DeliveryContext(**row["delivery_json"]), mode=row["mode"],
                                 ingest_class=row["ingest_class"], fields=set(row["field_names"]),
                                 now=current, production=self.production)
                except DomainError as exc:
                    next_state = "expired" if exc.code == "expired" else "canceled"
                    db.execute("""
                        UPDATE sf_google_ingest_events SET state=%s,fields_json=NULL,payload_bytes=0,
                         lease_token=NULL,lease_expires_at=NULL,error_code=%s WHERE event_id=%s
                    """, (next_state, exc.code, row["event_id"]))
                    self._hourly_record(
                        row, db=db, transition_at=current,
                        rights_expired=1 if next_state == "expired" else 0,
                        state="failed" if next_state == "expired" else "canceled",
                    )
                    continue
                token = secrets.token_urlsafe(24)
                leased = db.execute("""
                    UPDATE sf_google_ingest_events SET state='inflight',attempts=attempts+1,
                     lease_token=%s,lease_expires_at=%s WHERE event_id=%s RETURNING *
                """, (token, min(current + timedelta(seconds=120), row["expires_at"]), row["event_id"])).fetchone()
                result.append(_event(leased))
                self._hourly_record(
                    leased, db=db, transition_at=current,
                    sent=1,
                    state="succeeded",
                )
        return result

    def receipt(self, event_id, lease_token, *, status, receipt_id, now):
        if status not in {"queued", "accepted", "duplicate", "rejected"}:
            raise DomainError("invalid_argument", "Unknown per-event receipt status")
        text(receipt_id, "receipt_id")
        state = {"queued": "ingest_pending", "accepted": "terminal",
                 "duplicate": "terminal", "rejected": "rejected"}[status]
        with self.connect() as db:
            updated = db.execute("""
                UPDATE sf_google_ingest_events SET state=%s,receipt_id=%s,receipt_status=%s,
                 lease_token=NULL,lease_expires_at=NULL,
                 fields_json=CASE WHEN %s='rejected' THEN NULL ELSE fields_json END,
                 payload_bytes=CASE WHEN %s='rejected' THEN 0 ELSE payload_bytes END
                WHERE event_id=%s AND lease_token=%s AND state='inflight'
                  AND lease_expires_at>%s AND expires_at>%s
                RETURNING *
            """, (state, receipt_id, status, state, state, event_id, lease_token,
                  parse_time(now), parse_time(now))).fetchone()
            if updated:
                self._hourly_record(
                    updated, db=db, transition_at=parse_time(now),
                    accepted=0 if state == "rejected" else 1,
                    terminal=0 if state == "ingest_pending" else 1,
                    state="failed" if state == "rejected" else "succeeded",
                )
            return bool(updated)

    def settle_bulk(self, rows, response, *, error_code=None, retry_after=0,
                    jitter_seconds=0, now=None):
        """Settle one result per row; a batch 400 can be partial, not rollback."""
        if not isinstance(response, list) or len(response) != len(rows):
            raise DomainError("source_error", "Bulk response must match submitted rows", 502)
        result = []
        for row, item in zip(rows, response):
            if not isinstance(row, dict) or not isinstance(item, dict):
                raise DomainError("source_error", "Bulk rows/results must be typed objects", 502)
            token = row.get("lease_token")
            event_id = row.get("event_id")
            status = item.get("receipt_status") or item.get("status")
            receipt_id = item.get("receipt_id") or item.get("id")
            failed = item.get("failed_item") is True or status == "failed"
            if status in {"queued", "accepted", "duplicate", "rejected"}:
                if not self.receipt(event_id, token, status=status,
                                    receipt_id=receipt_id, now=now):
                    raise DomainError("source_error", "Bulk lease settlement conflict", 409)
                result.append({"event_id": event_id, "state": status,
                               "received_at": row.get("received_at"),
                               "payload_hash": row.get("payload_hash")})
                continue
            # Unknown, failed or missing item statuses retain the frozen record
            # and retry only that row.  The rest of the batch remains settled.
            if not self.retry(event_id, token, error_code=error_code or "failed_item",
                              retry_after=retry_after, jitter_seconds=jitter_seconds, now=now):
                raise DomainError("source_error", "Bulk retry lease settlement conflict", 409)
            result.append({"event_id": event_id, "state": "retry_scheduled",
                           "failed_item": failed,
                           "received_at": row.get("received_at"),
                           "payload_hash": row.get("payload_hash")})
        return result

    def retry(self, event_id, lease_token, *, error_code, retry_after=0, jitter_seconds=0, now):
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
            raise DomainError("invalid_argument", "Only sanitized machine error codes are retained")
        retry, jitter = decimal(retry_after), decimal(jitter_seconds)
        if not 0 <= retry <= 86400 or not 0 <= jitter <= 60:
            raise DomainError("invalid_argument", "Invalid retry delay/jitter")
        current = parse_time(now)
        with self.connect() as db:
            row = db.execute("""
                SELECT * FROM sf_google_ingest_events WHERE event_id=%s AND state='inflight'
                 AND lease_token=%s AND lease_expires_at>%s AND expires_at>%s FOR UPDATE
            """, (event_id, lease_token, current, current)).fetchone()
            if not row:
                return False
            seconds = float(max(retry, min(3600, 2 ** min(row["attempts"], 10))) + jitter)
            db.execute("""
                UPDATE sf_google_ingest_events SET state='pending',next_attempt_at=%s,error_code=%s,
                 lease_token=NULL,lease_expires_at=NULL WHERE event_id=%s
            """, (current + timedelta(seconds=seconds), error_code, event_id))
            self._hourly_record(
                row, db=db, transition_at=current,
                state="failed",
            )
            return True

    def poll_receipt(self, principal, event_id, *, receipt_id, status, source, grant, now):
        if status not in {"queued", "accepted", "duplicate", "rejected"}:
            raise DomainError("invalid_argument", "Unknown receipt polling status")
        current = parse_time(now)
        with self.connect() as db:
            row = db.execute("""
                SELECT * FROM sf_google_ingest_events WHERE event_id=%s AND tenant_id=%s FOR UPDATE
            """, (event_id, principal.tenant_id)).fetchone()
            if not row or not row["receipt_id"] or row["receipt_id"] != receipt_id:
                raise DomainError("not_authorized", "Receipt identity is not accessible", 404)
            if row["expires_at"] <= current:
                raise DomainError("expired", "Receipt observation has expired", 410)
            self._authorize_event(row, principal, source, grant, now=current, use="raw_store")
            if row["state"] in {"terminal", "readable", "rejected"}:
                if row["receipt_status"] != status:
                    raise DomainError("source_error", "Terminal receipt status changed", 409)
                return
            if row["state"] != "ingest_pending":
                raise DomainError("ingest_pending", "Event has not been queued by Whale", 409)
            if status == "queued":
                return
            state = {"queued": "ingest_pending", "accepted": "terminal",
                     "duplicate": "terminal", "rejected": "rejected"}[status]
            db.execute("""
                UPDATE sf_google_ingest_events SET state=%s,receipt_status=%s,
                 fields_json=CASE WHEN %s='rejected' THEN NULL ELSE fields_json END,
                 payload_bytes=CASE WHEN %s='rejected' THEN 0 ELSE payload_bytes END
                WHERE event_id=%s
            """, (state, status, state, state, event_id))
            self._hourly_record(
                row, db=db, transition_at=current,
                # Admission was already counted on the original queued ack.
                terminal=1,
                state="failed" if state == "rejected" else "succeeded",
            )

    def readback(self, principal, event_id, *, proof, source, grant, now):
        current = parse_time(now)
        with self.connect() as db:
            row = db.execute("""
                SELECT * FROM sf_google_ingest_events WHERE event_id=%s AND tenant_id=%s FOR UPDATE
            """, (event_id, principal.tenant_id)).fetchone()
            if not row or row["state"] not in {"terminal", "readable"}:
                raise DomainError("ingest_pending", "Terminal receipt required before independent readback", 409)
            if row["expires_at"] <= current:
                raise DomainError("expired", "Spool evidence has expired", 410)
            self._authorize_event(row, principal, source, grant, now=current, use="internal_display")
            if (not isinstance(proof, dict) or proof.get("event_id") != event_id
                    or proof.get("payload_hash") != row["payload_hash"]
                    or parse_time(proof.get("observed_at")) != row["observed_at"]):
                raise DomainError("source_error", "Independent readback differs from the frozen event", 409)
            document, version = proof.get("document_id"), proof.get("document_version")
            text(document, "document_id")
            text(version, "document_version")
            if row["state"] == "readable" and (row["document_id"] != document or row["document_version"] != version):
                raise DomainError("source_error", "Readback identity changed", 409)
            db.execute("""
                UPDATE sf_google_ingest_events SET state='readable',document_id=%s,document_version=%s,
                 readable_at=coalesce(readable_at,%s),fields_json=NULL,payload_bytes=0
                WHERE event_id=%s
            """, (document, version, current, event_id))
            self._hourly_record(
                row, db=db, transition_at=current,
                readable=1,
                state="succeeded",
            )

    def counts(self, principal, *, now):
        with self.connect() as db:
            rows = db.execute("""
                SELECT state,count(*) n,coalesce(sum(payload_bytes),0) bytes
                FROM sf_google_ingest_events WHERE tenant_id=%s GROUP BY state
            """, (principal.tenant_id,)).fetchall()
        return {
            "events": sum(r["n"] for r in rows), "unit": "stable_events_not_source_requests_or_rows",
            "states": {r["state"]: r["n"] for r in rows},
            "readable": sum(r["n"] for r in rows if r["state"] == "readable"),
            "spool_bytes": sum(r["bytes"] for r in rows),
            "as_of": utc_text(now),
        }


def _admission_grant(permit):
    fields = {{"body": "body_text"}.get(name, name) for name in permit.capabilities}
    return Grant(
        permit.tenant_id, permit.asset_id, permit.grant_id, "admission-check",
        frozenset(fields), frozenset({"raw_store"}), permit.expires_at,
        source_adapter=permit.source_adapter, source_version=permit.source_version)


def _admission_source(permit):
    fields = {{"body": "body_text"}.get(name, name) for name in permit.capabilities}
    return SourceRegistration(
        adapter_id=permit.source_adapter, adapter_version=permit.source_version,
        source_api_version="admission-check", endpoint="https://invalid.local",
        coverage={"environment": [VALIDATION_RAW_ENVIRONMENT]},
        admission="approved", approval_ref=permit.approval_ref,
        license_id="admission-check", license_version="1", refresh_seconds=0,
        cache_seconds=0, billing_unit="request",
        field_rights=tuple(FieldRight(name, frozenset({"raw_store"}), permit.expires_at)
                           for name in fields),
        retention_policy="admission-check", production_approved=permit.production_approved)
