"""Rebuildable, licensed SERP query projections, never a second raw fact store.

No external Whale schema is assumed. A separately approved reader must implement
read_version(tenant_id, EvidenceReference) -> SERPObservation. The HTTP service
does not provision a reader, approve a dataset, start collection, or mutate it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import unquote

from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError, canonical_json, identity, parse_time, plain_json, text, utc_text,
)
from .google_evidence import EvidenceReference, FrozenManifest, page_limit
from .google_metrics import organic_position
from .google_observations import PROJECTION_VERSION, SERPObservation, _url
from .google_parser_quality import ParserQualityGate
from .google_rights import authorize

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_projection_states (
 tenant_id text PRIMARY KEY, revision bigint NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sf_google_projection_tombstones (
 tenant_id text NOT NULL, grant_id text NOT NULL, document_id text NOT NULL, document_version text NOT NULL,
 reason text NOT NULL, cleared_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,grant_id,document_id,document_version)
);
CREATE TABLE IF NOT EXISTS sf_google_serp_runs (
 tenant_id text NOT NULL, run_id text NOT NULL, evidence_id text NOT NULL,
 query_id text NOT NULL, variant_id text NOT NULL,
 grant_id text NOT NULL, grant_version text NOT NULL,
 source_adapter text NOT NULL, source_version text NOT NULL, collection_version text NOT NULL,
 observed_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 document_id text NOT NULL, document_version text NOT NULL,
 projection_hash text NOT NULL, reference jsonb NOT NULL, header jsonb NOT NULL,
 PRIMARY KEY(tenant_id,run_id),
 UNIQUE(tenant_id,document_id,document_version,run_id)
);
CREATE INDEX IF NOT EXISTS sf_google_serp_runs_query
 ON sf_google_serp_runs(tenant_id,query_id,observed_at,run_id);
CREATE INDEX IF NOT EXISTS sf_google_serp_runs_variant
 ON sf_google_serp_runs(tenant_id,variant_id,observed_at,run_id);
CREATE INDEX IF NOT EXISTS sf_google_serp_runs_order
 ON sf_google_serp_runs(tenant_id,observed_at,run_id);
CREATE INDEX IF NOT EXISTS sf_google_serp_runs_expiry ON sf_google_serp_runs(expires_at);
CREATE TABLE IF NOT EXISTS sf_google_serp_items (
 tenant_id text NOT NULL, run_id text NOT NULL, item_id text NOT NULL,
 occurrence_order integer NOT NULL, result_type text NOT NULL, target_url text, item jsonb NOT NULL,
 PRIMARY KEY(tenant_id,run_id,item_id), UNIQUE(tenant_id,run_id,occurrence_order),
 FOREIGN KEY(tenant_id,run_id) REFERENCES sf_google_serp_runs ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS sf_google_serp_items_target
 ON sf_google_serp_items(tenant_id,target_url,run_id);
CREATE TABLE IF NOT EXISTS sf_google_serp_features (
 tenant_id text NOT NULL, run_id text NOT NULL, feature_id text NOT NULL,
 occurrence_order integer NOT NULL, feature_type text NOT NULL, feature jsonb NOT NULL,
 PRIMARY KEY(tenant_id,run_id,feature_id), UNIQUE(tenant_id,run_id,occurrence_order),
 FOREIGN KEY(tenant_id,run_id) REFERENCES sf_google_serp_runs ON DELETE CASCADE
);
"""

MIGRATIONS = """
ALTER TABLE sf_google_serp_runs
 ADD COLUMN IF NOT EXISTS evidence_id text;
CREATE UNIQUE INDEX IF NOT EXISTS sf_google_serp_runs_evidence_id
 ON sf_google_serp_runs(tenant_id,evidence_id)
 WHERE evidence_id IS NOT NULL;
"""


@dataclass(frozen=True)
class ReaderAdmission:
    contract_ref: str
    dataset_id: str
    approval_ref: str
    approved: bool = False
    production_approved: bool = False

    def __post_init__(self):
        for name in ("contract_ref", "dataset_id", "approval_ref"):
            text(getattr(self, name), name)
        if any(type(getattr(self, name)) is not bool for name in ("approved", "production_approved")):
            raise DomainError("invalid_argument", "Reader approval must be explicit")


class ObservationProjection:
    def __init__(self, dsn, *, registry, reader=None, admission=None, cursor_codec=None,
                 production=True, clock=None, parser_quality_gate=None):
        self.dsn, self.registry, self.reader = dsn, registry, reader
        self.admission, self.cursor_codec, self.production = admission, cursor_codec, production
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if parser_quality_gate is not None and not isinstance(
                parser_quality_gate, ParserQualityGate):
            raise DomainError(
                "invalid_argument", "parser_quality_gate must be a typed gate")
        self.parser_quality_gate = parser_quality_gate

    def initialize(self):
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,5)")
            db.execute(SCHEMA)
            db.execute(MIGRATIONS)
            rows = db.execute("""SELECT tenant_id,run_id,reference
                                 FROM sf_google_serp_runs
                                 WHERE evidence_id IS NULL""").fetchall()
            for row in rows:
                ref = EvidenceReference(**row["reference"])
                db.execute("""UPDATE sf_google_serp_runs SET evidence_id=%s
                              WHERE tenant_id=%s AND run_id=%s""",
                           (ref.evidence_id, row["tenant_id"], row["run_id"]))

    def _ready(self, principal, now):
        if (self.reader is None or not isinstance(self.admission, ReaderAdmission)
                or not self.admission.approved
                or self.production and not self.admission.production_approved
                or getattr(self.reader, "contract_ref", None) != self.admission.contract_ref
                or getattr(self.reader, "dataset_id", None) != self.admission.dataset_id):
            raise DomainError("not_covered", "No independently approved Whale version reader is connected", 501)
        self.registry.assert_current(principal, now=now)
        if "reader" not in principal.roles or not principal.grant_ids:
            raise DomainError("not_authorized", "A reader role and explicit asset grants are required", 403)
        for gid in principal.grant_ids:
            grant = self.registry.grant(principal.tenant_id, gid)
            source = self.registry.source(principal.tenant_id, grant.source_adapter, grant.source_version)
            for field in grant.fields:
                try:
                    authorize(principal, grant, source, field, "internal_display", now=now, production=self.production)
                    return
                except DomainError as exc:
                    if exc.code not in {"expired", "not_covered", "not_authorized", "use_not_permitted"}:
                        raise
        raise DomainError("use_not_permitted", "No current source fields are licensed for this read", 403)

    def _rights(self, principal, ref, *, use, now, expected_grant_version=None, db=None):
        source = self.registry.source(principal.tenant_id, ref.source_adapter, ref.source_adapter_version, db=db)
        grant = self.registry.grant(principal.tenant_id, ref.grant_id, db=db)
        if (not FrozenManifest._source_matches(ref, source) or grant.asset_id != ref.asset_id
                or expected_grant_version is not None and grant.version != expected_grant_version):
            raise DomainError("not_authorized", "Projection source/license/asset/grant version changed", 403)
        deadlines = [grant.expires_at]
        if ref.evidence_expires_at is not None:
            deadlines.append(ref.evidence_expires_at)
        for name in ref.fields:
            right = authorize(principal, grant, source, name, use, now=now, production=self.production)
            if right.retention_deadline is not None:
                deadlines.append(right.retention_deadline)
        expiry = min(deadlines)
        if expiry <= parse_time(now):
            raise DomainError("expired", "Projected evidence has expired", 410)
        return expiry, grant.version

    def _header(self, row):
        quality = self._quality(row)
        header = row.header()
        if quality is not None:
            header = {**header, "parser_quality": quality}
        return header

    def _hash(self, row):
        value = row.to_dict()
        value["header"] = self._header(row)
        return identity("projection", value, version=PROJECTION_VERSION)

    def _quality(self, row):
        if self.parser_quality_gate is None:
            if row.parser_message is not None:
                raise DomainError(
                    "not_covered",
                    "Parser quality metadata requires an explicitly configured gate",
                    503,
                )
            return None
        if row.parser_message is None:
            raise DomainError(
                "source_error",
                "Configured parser quality gate received no typed parser message",
                503,
            )
        quality = self.parser_quality_gate.evaluate(row.parser_message)
        if (
                not quality["formal_ranking_eligible"]
                and any(item.organic_position is not None for item in row.items)
        ):
            raise DomainError(
                "source_error",
                "Parser quality excludes this run from formal organic ranking",
                503,
            )
        return quality

    def _read_version(self, principal, ref):
        row = self.reader.read_version(principal.tenant_id, ref)
        if not isinstance(row, SERPObservation) or row.reference != ref:
            raise DomainError("source_error", "Independent reader returned a different or unmapped version", 503)
        return row

    @staticmethod
    def _lock(db, tenant):
        # Serialize rebuild/clear so a deleted version cannot reappear in a race.
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,20261105))", (tenant,))
        db.execute("INSERT INTO sf_google_projection_states(tenant_id) VALUES(%s) ON CONFLICT DO NOTHING",
                   (tenant,))

    @staticmethod
    def _advance(db, tenant):
        db.execute("UPDATE sf_google_projection_states SET revision=revision+1 WHERE tenant_id=%s", (tenant,))

    def rebuild(self, principal, reference, *, now):
        self._ready(principal, now)
        if not isinstance(reference, EvidenceReference):
            raise DomainError("invalid_argument", "An explicit immutable Whale version reference is required")
        if reference.observed_at > parse_time(now):
            raise DomainError("invalid_argument", "Cannot project a future observation")
        expiry, version = self._rights(principal, reference, use="derived_store", now=now)
        row = self._read_version(principal, reference)
        quality = self._quality(row)
        digest, tenant = self._hash(row), principal.tenant_id
        with self.registry.connect() as db:
            self._lock(db, tenant)
            db.execute("SELECT epoch FROM sf_google_tenant_rights WHERE tenant_id=%s FOR SHARE", (tenant,))
            finish = self.clock()
            self.registry.assert_current(principal, now=finish, db=db)
            expiry, version = self._rights(principal, reference, use="derived_store", now=finish, db=db)
            if db.execute("""SELECT 1 FROM sf_google_projection_tombstones
                             WHERE tenant_id=%s AND grant_id=%s AND document_id=%s AND document_version=%s""",
                          (tenant, reference.grant_id, reference.document_id, reference.document_version)).fetchone():
                raise DomainError("expired", "Version was cleared; replay/recovery cannot resurrect it", 410)
            old = db.execute("""SELECT projection_hash,grant_version FROM sf_google_serp_runs
                                WHERE tenant_id=%s AND run_id=%s""", (tenant, row.run_id)).fetchone()
            if old:
                if old["projection_hash"] != digest or old["grant_version"] != version:
                    raise DomainError("idempotency_conflict", "Stable observation has conflicting projected facts", 409)
                return {"run_id": row.run_id, "created": False}
            db.execute("""
                INSERT INTO sf_google_serp_runs(tenant_id,run_id,evidence_id,query_id,variant_id,
                 grant_id,grant_version,
                 source_adapter,source_version,collection_version,observed_at,expires_at,
                 document_id,document_version,projection_hash,reference,header)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (tenant, row.run_id, reference.evidence_id, row.query.query_id,
                  row.variant.variant_id, reference.grant_id, version,
                  reference.source_adapter, reference.source_adapter_version, row.collection_version,
                  reference.observed_at, expiry, reference.document_id, reference.document_version, digest,
                          Jsonb(reference.to_dict()), Jsonb(self._header(row))))
            for index, item in enumerate(row.items):
                db.execute("INSERT INTO sf_google_serp_items VALUES(%s,%s,%s,%s,%s,%s,%s)",
                           (tenant, row.run_id, item.item_id, index, item.result_type,
                            item.target_url, Jsonb(item.to_dict())))
            for index, feature in enumerate(row.features):
                db.execute("INSERT INTO sf_google_serp_features VALUES(%s,%s,%s,%s,%s,%s)",
                           (tenant, row.run_id, feature.feature_id, index, feature.feature_type,
                            Jsonb(feature.to_dict())))
            self._advance(db, tenant)
        return {"run_id": row.run_id, "created": True}

    def purge_expired(self, *, now):
        with self.registry.connect() as db:
            rows = db.execute("DELETE FROM sf_google_serp_runs WHERE expires_at<=%s RETURNING tenant_id",
                              (parse_time(now),)).fetchall()
            for tenant in sorted({r["tenant_id"] for r in rows}):
                self._advance(db, tenant)
        return len(rows)

    def reconcile_rights(self, tenant_id, *, now):
        """Internal janitor: remove unlicensed facts even after all grants revoke."""
        text(tenant_id, "tenant_id")
        with self.registry.connect() as db:
            self._lock(db, tenant_id)
            db.execute("SELECT epoch FROM sf_google_tenant_rights WHERE tenant_id=%s FOR SHARE", (tenant_id,))
            principal = self.registry.principal(tenant_id, now=now, db=db)
            rows = db.execute("SELECT run_id,reference,grant_version FROM sf_google_serp_runs WHERE tenant_id=%s",
                              (tenant_id,)).fetchall()
            removed = 0
            for row in rows:
                ref = EvidenceReference(**row["reference"])
                try:
                    self._rights(principal, ref, use="derived_store", now=now, db=db)
                except DomainError as exc:
                    if exc.code not in {"not_authorized", "not_covered", "use_not_permitted", "expired"}:
                        raise
                    db.execute("""INSERT INTO sf_google_projection_tombstones VALUES(%s,%s,%s,%s,%s,%s)
                                  ON CONFLICT DO NOTHING""",
                               (tenant_id, ref.grant_id, ref.document_id, ref.document_version, "revoked", parse_time(now)))
                    removed += db.execute("DELETE FROM sf_google_serp_runs WHERE tenant_id=%s AND run_id=%s",
                                          (tenant_id, row["run_id"])).rowcount
            if removed:
                self._advance(db, tenant_id)
        return removed

    def clear_document(self, principal, reference, *, reason, now):
        self._ready(principal, now)
        if "manager" not in principal.roles:
            raise DomainError("not_authorized", "Clearing a projection requires the manager role", 403)
        if reason not in {"source_deleted", "revoked", "operator_erasure"}:
            raise DomainError("invalid_argument", "An explicit supported clear reason is required")
        self._rights(principal, reference, use="derived_store", now=now)
        with self.registry.connect() as db:
            self._lock(db, principal.tenant_id)
            db.execute("SELECT epoch FROM sf_google_tenant_rights WHERE tenant_id=%s FOR SHARE",
                       (principal.tenant_id,))
            self.registry.assert_current(principal, now=self.clock(), db=db)
            db.execute("""INSERT INTO sf_google_projection_tombstones
                          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                       (principal.tenant_id, reference.grant_id, reference.document_id, reference.document_version,
                        reason, parse_time(now)))
            db.execute("""DELETE FROM sf_google_serp_runs
                          WHERE tenant_id=%s AND grant_id=%s AND document_id=%s AND document_version=%s""",
                       (principal.tenant_id, reference.grant_id, reference.document_id, reference.document_version))
            self._advance(db, principal.tenant_id)

    @staticmethod
    def route(path):
        if path in {"/keywords", "/query-variants", "/observations", "/serp-history", "/serp-features"}:
            return path[1:], None
        match = re.fullmatch(r"/(keywords|observations)/([^/]+)(/items)?", path)
        if match and (match[3] is None or match[1] == "observations"):
            return ("items" if match[3] else match[1] + "-detail"), unquote(match[2], errors="strict")
        raise DomainError("not_found", "No observation projection route matches", 404)

    def snapshot(self, principal, *, now, filters=None, use="aggregate", max_runs=5000):
        """Return a bounded, fully reverified selected-fact snapshot for aggregation."""
        self._ready(principal, now)
        filters = dict(filters or {})
        allowed = {
            "query_id", "variant_id", "source", "source_version", "collection_version",
            "observed_from", "observed_to", "status", "country", "device", "surface",
            "query",
        }
        if set(filters) - allowed:
            raise DomainError("invalid_argument", "Unknown analytics cohort filter")
        for name, value in filters.items():
            text(value, name, maximum=4096)
        if "status" in filters and filters["status"] not in {
                "succeeded", "partial", "failed"}:
            raise DomainError("invalid_argument", "Unknown observation status")
        if type(max_runs) is not int or not 1 <= max_runs <= 10000:
            raise DomainError("invalid_argument", "Invalid analytics run cap")
        clauses, values = [
            "r.tenant_id=%s", "r.grant_id=ANY(%s)", "r.expires_at>%s",
        ], [principal.tenant_id, sorted(principal.grant_ids), parse_time(now)]
        for name in ("query_id", "variant_id", "source_version", "collection_version"):
            if name in filters:
                clauses.append("r." + name + "=%s")
                values.append(filters[name])
        if "source" in filters:
            clauses.append("r.source_adapter=%s")
            values.append(filters["source"])
        for name in ("country", "device", "surface"):
            if name in filters:
                clauses.append("r.header->'variant'->>%s=%s")
                values += [name, filters[name]]
        if "status" in filters:
            clauses.append("r.header->>'status'=%s")
            values.append(filters["status"])
        if "query" in filters:
            from .google_domain import Query
            clauses.append("r.header->'query'->>'normalized_text'=%s")
            values.append(Query(filters["query"]).normalized_text)
        bounds = {}
        for name, operator in (("observed_from", ">="), ("observed_to", "<")):
            if name in filters:
                bounds[name] = parse_time(filters[name])
                clauses.append("r.observed_at" + operator + "%s")
                values.append(bounds[name])
        if len(bounds) == 2 and bounds["observed_from"] >= bounds["observed_to"]:
            raise DomainError(
                "invalid_argument", "Observation window must be nonempty [from,to)")
        with self.registry.connect() as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            state = db.execute(
                "SELECT revision FROM sf_google_projection_states WHERE tenant_id=%s",
                (principal.tenant_id,),
            ).fetchone()
            revision = state["revision"] if state else 0
            rows = db.execute(
                "SELECT r.* FROM sf_google_serp_runs r WHERE " + " AND ".join(clauses) +
                " ORDER BY r.observed_at,r.run_id LIMIT %s",
                (*values, max_runs + 1),
            ).fetchall()
        if len(rows) > max_runs:
            raise DomainError(
                "partial_result",
                "Analytics cohort exceeds the local verified-run cap; narrow the window",
                413,
            )
        observations, deadlines = [], []
        for row in rows:
            ref = EvidenceReference(**row["reference"])
            deadline, _ = self._rights(principal, ref, use=use, now=now)
            deadlines.append(deadline)
            current = self._read_version(principal, ref)
            if self._hash(current) != row["projection_hash"]:
                raise DomainError(
                    "source_error",
                    "Pinned version no longer reproduces the projection",
                    503,
                )
            if canonical_json(self._header(current)) != canonical_json(row["header"]):
                raise DomainError(
                    "source_error",
                    "Selected run projection differs from its pinned version",
                    503,
                )
            observations.append(current)
        self.registry.assert_current(principal, now=now)
        return {
            "observations": tuple(observations),
            "projection_revision": revision,
            "evidence_expires_at": min(deadlines) if deadlines else None,
            "data_as_of": min((row.reference.observed_at for row in observations),
                              default=None),
            "source_observed_at": (
                min(row.reference.source_observed_at for row in observations)
                if observations and all(row.reference.source_observed_at is not None
                                        for row in observations) else None
            ),
        }

    def read(self, principal, path, parameters, *, now):
        self._ready(principal, now)
        kind, detail_id = self.route(path)
        allowed = {"limit", "cursor", "query_id", "variant_id", "source", "source_version",
                   "collection_version", "observed_from", "observed_to", "status",
                   "country", "device", "surface"}
        if kind == "keywords":
            allowed.add("text")
        if kind == "items":
            allowed |= {"result_type", "depth"}
        if kind == "serp-features":
            allowed.add("feature_type")
        if kind == "serp-history":
            allowed.add("target_url")
            if not parameters.get("variant_id") or not parameters.get("target_url"):
                raise DomainError("invalid_argument", "History requires a fixed variant_id and exact target_url")
            _url(parameters["target_url"], "target_url")
        if set(parameters) - allowed:
            raise DomainError("invalid_argument", "Unknown query parameter for observation projection")
        for name, value in parameters.items():
            if name not in {"cursor", "limit", "depth"}:
                text(value, name, maximum=16384 if name == "target_url" else 4096)
        if kind.endswith("-detail") and ("cursor" in parameters or "limit" in parameters):
            raise DomainError("invalid_argument", "Detail endpoints are not paginated")
        if "status" in parameters and parameters["status"] not in {"succeeded", "partial", "failed"}:
            raise DomainError("invalid_argument", "Unknown observation status")
        limit = page_limit(parameters.get("limit"))
        if self.cursor_codec is None:
            raise DomainError("not_covered", "Projection pagination signing is not configured", 503)
        clauses, values = ["r.tenant_id=%s", "r.grant_id=ANY(%s)", "r.expires_at>%s"], [
            principal.tenant_id, sorted(principal.grant_ids), parse_time(now)]
        for name in ("query_id", "variant_id", "source_version", "collection_version"):
            if name in parameters:
                clauses.append("r." + name + "=%s")
                values.append(parameters[name])
        if "source" in parameters:
            clauses.append("r.source_adapter=%s")
            values.append(parameters["source"])
        for name in ("country", "device", "surface"):
            if name in parameters:
                clauses.append("r.header->'variant'->>%s=%s")
                values += [name, parameters[name]]
        if "status" in parameters:
            clauses.append("r.header->>'status'=%s")
            values.append(parameters["status"])
        if "text" in parameters:
            clauses.append("r.header->'query'->>'normalized_text'=%s")
            from .google_domain import Query
            values.append(Query(parameters["text"]).normalized_text)
        bounds = {}
        for name, op in (("observed_from", ">="), ("observed_to", "<")):
            if name in parameters:
                bounds[name] = parse_time(parameters[name])
                clauses.append("r.observed_at" + op + "%s")
                values.append(bounds[name])
        if len(bounds) == 2 and bounds["observed_from"] >= bounds["observed_to"]:
            raise DomainError("invalid_argument", "Observation window must be nonempty [from,to)")
        if detail_id is not None:
            clauses.append("r.query_id=%s" if kind == "keywords-detail" else "r.run_id=%s")
            values.append(detail_id)
        parent_clauses, parent_values = clauses.copy(), values.copy()
        if kind in {"keywords", "keywords-detail"}:
            select, order, position_keys = "DISTINCT ON (r.query_id) r.*", "r.query_id,r.observed_at,r.run_id", ["query_id"]
        elif kind == "query-variants":
            select, order, position_keys = "DISTINCT ON (r.variant_id) r.*", "r.variant_id,r.observed_at,r.run_id", ["variant_id"]
        else:
            select, order, position_keys = "r.*", "r.observed_at,r.run_id", ["observed_at", "run_id"]
        joins = ""
        if kind in {"items", "serp-features"}:
            table, alias, field = (("sf_google_serp_items", "i", "item") if kind == "items"
                                    else ("sf_google_serp_features", "f", "feature"))
            joins = f" JOIN {table} {alias} ON {alias}.tenant_id=r.tenant_id AND {alias}.run_id=r.run_id"
            select += f",{alias}.{field},{alias}.occurrence_order"
            order += f",{alias}.occurrence_order"
            position_keys.append("occurrence_order")
            filter_name = "result_type" if kind == "items" else "feature_type"
            if filter_name in parameters:
                clauses.append(f"{alias}.{filter_name}=%s")
                values.append(parameters[filter_name])
            if "depth" in parameters:
                raw = parameters["depth"]
                if not isinstance(raw, str) or not re.fullmatch("[0-9]{1,6}", raw) or int(raw) < 1:
                    raise DomainError("invalid_argument", "depth must be a positive integer")
                # Source/group/absolute ranks are not interchangeable with natural depth.
                clauses.append("(i.item->>'organic_position')::integer<=%s")
                values.append(int(raw))
        with self.registry.connect() as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            state = db.execute("SELECT revision FROM sf_google_projection_states WHERE tenant_id=%s",
                               (principal.tenant_id,)).fetchone()
            revision = state["revision"] if state else 0
            binding = dict(tenant_id=principal.tenant_id, rights_version=principal.rights_version,
                           filters={**{k: v for k, v in parameters.items() if k != "cursor"},
                                    "limit": limit, "path": path,
                                    "subject_scope": identity("projection-scope", {
                                        "subject": principal.subject_ref,
                                        "grants": sorted(principal.grant_ids),
                                        "versions": principal.grant_versions})},
                           sort=position_keys, source=f"{PROJECTION_VERSION}:{revision}", manifest_id=None)
            cursor_position = None
            if "cursor" in parameters:
                pos = self.cursor_codec.decode(parameters["cursor"], binding=binding, now=now)
                if len(pos) != len(position_keys) or any(
                        type(v) is not (int if key == "occurrence_order" else str)
                        for key, v in zip(position_keys, pos)):
                    raise DomainError("invalid_argument", "Invalid projection cursor position")
                cursor_position = pos
                expr = []
                for key in position_keys:
                    expr.append(f"{'i' if kind == 'items' else 'f'}.occurrence_order" if key == "occurrence_order"
                                else "r." + key)
                placeholders = ["%s::timestamptz" if k == "observed_at" else "%s" for k in position_keys]
                clauses.append("(" + ",".join(expr) + ")>(" + ",".join(placeholders) + ")")
                values += pos
            rows = db.execute("SELECT " + select + " FROM sf_google_serp_runs r" + joins +
                              " WHERE " + " AND ".join(clauses) + " ORDER BY " + order + " LIMIT %s",
                              (*values, 1 if kind.endswith("-detail") else limit + 1)).fetchall()
            parent = None
            if kind == "items" and not rows:
                parent = db.execute("SELECT r.* FROM sf_google_serp_runs r WHERE " +
                                    " AND ".join(parent_clauses) + " LIMIT 1", parent_values).fetchone()
            if detail_id and not rows and parent is None:
                raise DomainError("not_found", "No visible observation/query matches", 404)
            page, more = rows[:limit], len(rows) > limit
            if kind == "serp-history":
                items = db.execute("""SELECT run_id,item FROM sf_google_serp_items
                                      WHERE tenant_id=%s AND run_id=ANY(%s) AND target_url=%s""",
                                   (principal.tenant_id, [r["run_id"] for r in page], parameters["target_url"])).fetchall()
        expiry, checked, result, freshness, source_times = None, {}, [], [], []
        for row in [*page, *([parent] if parent else [])]:
            ref = EvidenceReference(**row["reference"])
            deadline, current_grant_version = self._rights(principal, ref, use="internal_display", now=now)
            if kind == "serp-history":
                self._rights(principal, ref, use="aggregate", now=now)
            expiry = min(expiry, deadline) if expiry else deadline
            rid = (ref.document_id, ref.document_version, ref.observation_id)
            if rid not in checked:
                current = self._read_version(principal, ref)
                if self._hash(current) != row["projection_hash"]:
                    raise DomainError("source_error", "Pinned version no longer reproduces the projection", 503)
                checked[rid] = current
            current = checked[rid]
            header = row["header"]
            if canonical_json(self._header(current)) != canonical_json(header):
                raise DomainError("source_error", "Selected run projection differs from its pinned version", 503)
            if kind in {"items", "serp-features"} and row is not parent:
                field, identifier = ("item", "item_id") if kind == "items" else ("feature", "feature_id")
                candidates = current.items if kind == "items" else current.features
                selected = next((v.to_dict() for v in candidates
                                 if getattr(v, identifier) == row[field][identifier]), None)
                if selected != row[field]:
                    raise DomainError("source_error", "Selected occurrence differs from its pinned version", 503)
            if kind == "items":
                eligible = [index for index, item in enumerate(current.items)
                            if ("result_type" not in parameters or item.result_type == parameters["result_type"])
                            and ("depth" not in parameters or item.organic_position is not None
                                 and item.organic_position <= int(parameters["depth"]))
                            and (cursor_position is None or index > cursor_position[-1])]
                if eligible[:limit + 1] != [r["occurrence_order"] for r in rows]:
                    raise DomainError("source_error", "Item projection is incomplete for the pinned version", 503)
            known_time = (ref.source_observed_at or header["cache_original_observed_at"]
                          or (ref.observed_at if header["cache_hit"] is False else None))
            freshness.append(parse_time(known_time) if known_time is not None else None)
            source_times.append(ref.source_observed_at)
            if row is parent:
                continue
            evidence = dict(evidence=ref.to_dict(), evidence_id=ref.evidence_id,
                            grant_version=current_grant_version,
                            projection_hash=row["projection_hash"])
            if kind.startswith("keywords"):
                entry = dict(header["query"], observed_form_only=True, **evidence)
            elif kind == "query-variants":
                entry = dict(header["variant"], **evidence)
            elif kind in {"items", "serp-features"}:
                entry = dict(row["item" if kind == "items" else "feature"],
                             run_id=row["run_id"], occurrence_order=row["occurrence_order"], **evidence)
            elif kind == "serp-history":
                selected_items = [i["item"] for i in items if i["run_id"] == row["run_id"]]
                expected_items = [i.to_dict() for i in current.items if i.target_url == parameters["target_url"]]
                if sorted(selected_items, key=lambda v: v["item_id"]) != sorted(expected_items, key=lambda v: v["item_id"]):
                    raise DomainError("source_error", "Rank projection differs from independently readable items", 503)
                scope = {**header["variant"], "source": ref.source_adapter,
                         "target_url": parameters["target_url"],
                         "source_version": ref.source_adapter_version,
                         "source_api_version": ref.source_api_version,
                         "parser_version": ref.parser_version, "license_id": ref.license_id,
                         "license_version": ref.license_version,
                         "collection_version": row["collection_version"]}
                metric = organic_position(
                    selected_items,
                    parameters["target_url"], scope=scope, depth=header["returned_depth"] or 0,
                    complete=header["complete"] is True)
                rank = metric.to_dict()
                if header["status"] == "failed":
                    rank.update(value=None, availability="source_error")
                rank["evidence_expires_at"] = utc_text(deadline)
                entry = dict(run_id=row["run_id"], variant_id=row["variant_id"],
                             observed_at=utc_text(row["observed_at"]), status=header["status"],
                             cache_hit=header["cache_hit"], rank=rank,
                             series_scope_id=identity("history-scope", scope), **evidence)
            else:
                entry = dict(header, **evidence)
            result.append(entry)
        finish = self.clock()
        self.registry.assert_current(principal, now=finish)
        for row in [*page, *([parent] if parent else [])]:
            deadline, _ = self._rights(principal, EvidenceReference(**row["reference"]),
                                      use="internal_display", now=finish)
            if kind == "serp-history":
                self._rights(principal, EvidenceReference(**row["reference"]), use="aggregate", now=finish)
            expiry = min(expiry, deadline)
        with self.registry.connect() as db:
            latest = db.execute("SELECT revision FROM sf_google_projection_states WHERE tenant_id=%s",
                                (principal.tenant_id,)).fetchone()
        if (latest["revision"] if latest else 0) != revision:
            raise DomainError("precondition_failed", "Projection changed during the read; retry the query", 412)
        cursor = None
        if more:
            position = [utc_text(page[-1][k]) if k == "observed_at" else page[-1][k] for k in position_keys]
            cursor = self.cursor_codec.encode(binding=binding, position=position, now=finish,
                                              expires_at=min(parse_time(finish) + timedelta(minutes=10), expiry))
        key = {"query-variants": "query_variants", "serp-history": "history",
               "serp-features": "features"}.get(kind, kind)
        return {key: result[0] if kind.endswith("-detail") else result, "next_cursor": cursor,
                "projection_version": PROJECTION_VERSION, "projection_revision": revision,
                "evidence_expires_at": utc_text(expiry) if expiry else None,
                "data_as_of": utc_text(min(freshness)) if freshness and all(freshness) else None,
                "source_observed_at": utc_text(min(source_times)) if source_times and all(source_times) else None,
                "reproducibility": "pinned_versions_rechecked",
                "approval_basis": "internal_reader_port_not_external_schema_acceptance"}

    def evidence(self, principal, evidence_id, *, now):
        """Resolve one stable evidence ID to its currently licensed pinned version."""
        self._ready(principal, now)
        text(evidence_id, "evidence_id", maximum=200)
        with self.registry.connect() as db:
            row = db.execute(
                """SELECT * FROM sf_google_serp_runs
                   WHERE tenant_id=%s AND evidence_id=%s""",
                (principal.tenant_id, evidence_id),
            ).fetchone()
        if not row:
            raise DomainError("not_found", "No evidence with this ID", 404)
        ref = EvidenceReference(**row["reference"])
        deadline, _ = self._rights(
            principal, ref, use="internal_display", now=now)
        current = self._read_version(principal, ref)
        if self._hash(current) != row["projection_hash"]:
            raise DomainError(
                "source_error",
                "Evidence no longer matches its pinned version",
                503,
            )
        grant = self.registry.grant(principal.tenant_id, ref.grant_id)
        metric_ids = {"GO-MET-001"}
        if current.status == "succeeded" and current.complete is True:
            metric_ids.add("GO-MET-002")
        if current.features:
            metric_ids.add("GO-MET-004")
        from .google_catalog import METRIC_CATALOG
        return {
            "evidence_id": evidence_id,
            "evidence": plain_json(current.to_dict()),
            "projection_hash": row["projection_hash"],
            "source_hash": ref.source_hash,
            "versions": {
                "source_adapter": ref.source_adapter,
                "source_adapter_version": ref.source_adapter_version,
                "source_api_version": ref.source_api_version,
                "license_id": ref.license_id,
                "license_version": ref.license_version,
                "parser_version": ref.parser_version,
                "projection": PROJECTION_VERSION,
            },
            "grant": {
                "grant_id": grant.grant_id,
                "grant_version": grant.version,
                "asset_id": grant.asset_id,
                "fields": sorted(ref.fields),
                "allowed_uses": sorted(grant.uses),
            },
            "metric_definitions": [
                dict(metric) for metric in METRIC_CATALOG
                if metric["id"] in metric_ids
            ],
            "additivity": {
                "GO-MET-001": "position_not_additive",
                "GO-MET-002": "ratio_not_additive",
                "GO-MET-004": "rate_not_additive",
            },
            "evidence_expires_at": utc_text(deadline),
            "scope": "one_authorized_pinned_observation",
        }
