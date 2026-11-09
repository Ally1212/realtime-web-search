"""Deterministic reports over frozen manifests; no LLM or source call is used."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError, canonical_json, identity, parse_time, plain_json, text, utc_text,
)
from .google_evidence import EvidenceReference, page_limit

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_reports (
 tenant_id text NOT NULL, report_id text NOT NULL, idempotency_key text NOT NULL,
 request_hash text NOT NULL, manifest_id text NOT NULL, report_type text NOT NULL,
 subject_scope text NOT NULL, subject_ref text NOT NULL,
 generated_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 input_manifest_sha256 text NOT NULL, report_sha256 text NOT NULL,
 result jsonb NOT NULL, evidence jsonb NOT NULL, versions jsonb NOT NULL,
 PRIMARY KEY(tenant_id,report_id), UNIQUE(tenant_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS sf_google_reports_expiry
 ON sf_google_reports(tenant_id,expires_at);
"""

REPORT_VERSION = "deterministic-report-2"
ANALYSIS_VERSION = "deterministic-report-analysis-2"
REPORT_TYPE = "deterministic"


def _now():
    return datetime.now(timezone.utc)


def _sha(value):
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


class ReportService:
    """Generate a reproducible report only from a frozen item manifest."""

    def __init__(self, dsn, *, registry, manifests, clock=None):
        self.dsn, self.registry, self.manifests = dsn, registry, manifests
        self.projection = manifests.projection
        self.clock = clock or _now

    def initialize(self):
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,10)")
            db.execute(SCHEMA)

    @staticmethod
    def route(path):
        if path == "/reports":
            return "list", None
        match = re.fullmatch(r"/reports/([^/]+)", path)
        if match:
            return "detail", match[1]
        raise DomainError("not_found", "No report route matches", 404)

    @staticmethod
    def _subject(principal):
        return identity("report-subject", {
            "subject": principal.subject_ref,
            "grants": sorted(principal.grant_ids),
        })

    @staticmethod
    def _manifest_digest(manifest):
        return _sha({
            "manifest_id": manifest["manifest_id"],
            "projection_revision": manifest["projection_revision"],
            "source_path": manifest["source_path"],
            "filters": plain_json(manifest["filters"]),
            "method_versions": plain_json(manifest["method_versions"]),
            "rights_at_query": plain_json(manifest["rights_at_query"]),
            "evidence": plain_json(manifest["evidence"]),
            "expires_at": utc_text(manifest["expires_at"]),
        })

    @staticmethod
    def _report_digest(result):
        payload = {key: value for key, value in result.items() if key != "report_sha256"}
        return _sha(payload)

    def _validate(self, principal, manifest_id, *, now):
        manifest = self.manifests.manifest_snapshot(
            principal, manifest_id, now=now)
        evidence = manifest["evidence"] or {}
        rows = evidence.get("items")
        if not isinstance(rows, list) or not rows:
            raise DomainError(
                "invalid_argument",
                "Deterministic reports require a nonempty frozen item manifest",
            )
        checked, frozen, rights, expiry = {}, [], {}, parse_time(manifest["expires_at"])
        observation_limitations = {}
        for row in rows:
            if (not isinstance(row, dict) or not row.get("item_id")
                    or not isinstance(row.get("result_type"), str)
                    or not row.get("evidence") or not row.get("projection_hash")):
                raise DomainError(
                    "source_error",
                    "Frozen report item lacks item/result/evidence/version",
                    503,
                )
            try:
                ref = EvidenceReference(**row["evidence"])
            except (TypeError, ValueError):
                raise DomainError(
                    "source_error", "Frozen report evidence reference is invalid", 503
                ) from None
            # A report is displayed and aggregates item facts, so both uses are
            # required. Neither use is inferred from ownership.
            aggregate_deadline, _ = self.projection._rights(
                principal, ref, use="aggregate", now=now)
            display_deadline, _ = self.projection._rights(
                principal, ref, use="internal_display", now=now)
            expiry = min(expiry, aggregate_deadline, display_deadline)
            ref_key = (
                ref.document_id, ref.document_version, ref.observation_id,
                ref.grant_id, row["projection_hash"],
            )
            if ref_key not in checked:
                current = self.projection._read_version(principal, ref)
                if self.projection._hash(current) != row["projection_hash"]:
                    raise DomainError(
                        "source_error",
                        "Frozen report item no longer matches its pinned version",
                        503,
                    )
                observation_limitations[ref.observation_id] = {
                    "status": current.status,
                    "cache_hit": current.cache_hit,
                    "cache_age_seconds": current.cache_age_seconds,
                    "truncated": current.truncated,
                    "limitations": current.limitations.to_dict(),
                }
                item = next(
                    (candidate for candidate in current.items
                     if candidate.item_id == row["item_id"]), None)
                if item is None:
                    raise DomainError(
                        "source_error",
                        "Frozen report item is absent from its pinned version",
                        503,
                    )
                selected = item.to_dict()
                for name in ("result_type", "organic_position", "target_url"):
                    if name in row and selected.get(name) != row[name]:
                        raise DomainError(
                            "source_error",
                            "Frozen report item differs from its pinned occurrence",
                            503,
                        )
                checked[ref_key] = selected
            if ref.grant_id not in rights:
                grant = self.registry.grant(principal.tenant_id, ref.grant_id)
                rights[ref.grant_id] = {
                    "grant_id": grant.grant_id,
                    "grant_version": grant.version,
                    "asset_id": grant.asset_id,
                    "source_adapter": grant.source_adapter,
                    "source_version": grant.source_version,
                    "allowed_uses": sorted(grant.uses),
                    "fields": sorted(ref.fields),
                    "source_hash": ref.source_hash,
                    "license_id": ref.license_id,
                    "license_version": ref.license_version,
                    "parser_version": ref.parser_version,
                    "source_api_version": ref.source_api_version,
                    "evidence_expires_at": (
                        utc_text(ref.evidence_expires_at)
                        if ref.evidence_expires_at else None
                    ),
                    "grant_expires_at": utc_text(grant.expires_at),
                }
            frozen.append({
                "item_id": row["item_id"],
                "evidence": ref.to_dict(),
                "projection_hash": row["projection_hash"],
            })
        if expiry <= parse_time(now):
            raise DomainError("expired", "Report evidence has expired", 410)
        return manifest, rows, frozen, rights, expiry, observation_limitations

    @staticmethod
    def _analysis(rows):
        by_type = {}
        for row in rows:
            by_type[row["result_type"]] = by_type.get(row["result_type"], 0) + 1
        organic = [row for row in rows if row["result_type"] == "organic"]
        positioned = [
            row for row in organic if isinstance(row.get("organic_position"), int)
        ]
        unknown = [row for row in organic if row not in positioned]
        top_k = {
            str(k): sum(1 for row in positioned
                        if row["organic_position"] <= k)
            for k in (1, 3, 10)
        }
        return {
            "observed_items": len(rows),
            "by_result_type": dict(sorted(by_type.items())),
            "organic_items": len(organic),
            "organic_position_provided": len(positioned),
            "organic_position_unknown": len(unknown),
            "unique_target_urls": len({
                row.get("target_url") for row in rows if row.get("target_url")
            }),
            "top_k_observed": top_k,
            "top_k_denominator": "organic_items_with_known_natural_position",
        }, organic, positioned, unknown

    @classmethod
    def _result(cls, *, report_id, manifest, research_question, frozen, rights,
                expiry, now, observation_limitations):
        analysis, organic, positioned, unknown = cls._analysis(
            manifest["evidence"]["items"])
        conclusions = [{
            "conclusion_id": "frozen-item-coverage",
            "statement": (
                f"The frozen manifest contains {analysis['observed_items']} "
                "typed SERP result occurrences."
            ),
            "value": analysis["observed_items"],
            "unit": "result_occurrences",
            "denominator": None,
            "scope": "frozen_manifest",
            "provenance_kind": "research_derived",
            "measurement_nature": "exact_in_scope",
            "evidence_item_ids": [row["item_id"] for row in manifest["evidence"]["items"]],
        }, {
            "conclusion_id": "natural-position-coverage",
            "statement": (
                f"{len(positioned)} of {len(organic)} organic occurrences "
                "have a known natural position."
            ),
            "value": len(positioned),
            "unit": "organic_result_occurrences_with_position",
            "denominator": len(organic),
            "scope": "frozen_manifest_organic_results",
            "provenance_kind": "research_derived",
            "measurement_nature": "exact_in_scope",
            "evidence_item_ids": [row["item_id"] for row in positioned],
        }]
        counterexamples = [{
            "item_id": row["item_id"],
            "reason": "position_unknown",
            "statement": "Unknown natural position is not treated as outside observed depth.",
        } for row in unknown[:25]]
        counterexamples.extend({
            "item_id": row["item_id"],
            "reason": "non_organic_excluded_from_rank_summary",
            "statement": "Non-organic result types are excluded from the rank denominator.",
        } for row in manifest["evidence"]["items"]
            if row["result_type"] != "organic" and len(counterexamples) < 50)
        limitations = [
            "results_are_limited_to_the_frozen_manifest",
            "unknown_positions_are_not_beyond_depth",
            "no_global_google_population_is_claimed",
            "no_causal_or_business_outcome_is_inferred",
            "deterministic_report_does_not_use_an_llm",
        ]
        actions = []
        if unknown:
            actions.append({
                "action_id": "verify-unknown-positions",
                "description": (
                    "Inspect the pinned source occurrences for organic items "
                    "without a natural position before comparing ranks."
                ),
                "reason": "unknown_positions_are_not_beyond_depth",
            })
        actions.append({
            "action_id": "compare-fixed-cohort",
            "description": (
                "Compare this fixed manifest with another fixed cohort "
                "using the same query, market, device, surface and method versions."
            ),
            "reason": "a_single_frozen_cohort_cannot_establish_change_or_causality",
        })
        result = {
            "report_id": report_id,
            "manifest_id": manifest["manifest_id"],
            "report_type": REPORT_TYPE,
            "status": "succeeded",
            "deterministic": True,
            "ai_generated": False,
            "research_question": research_question,
            "generated_at": utc_text(now),
            "expires_at": utc_text(expiry),
            "input_manifest_sha256": cls._manifest_digest(manifest),
            "versions": {
                "report": REPORT_VERSION,
                "analysis": ANALYSIS_VERSION,
                "manifest": plain_json(manifest["method_versions"]),
                "projection_revision": manifest["projection_revision"],
            },
            "scope": {
                "source_path": manifest["source_path"],
                "filters": plain_json(manifest["filters"]),
                "cohort": "frozen_manifest",
            },
            "rights": list(rights.values()),
            "evidence": plain_json(frozen),
            "analysis": analysis,
            "conclusions": conclusions,
            "counterexamples": counterexamples,
            "limitations": limitations,
            "observation_limitations": observation_limitations,
            "pending_verification_actions": actions,
        }
        result["report_sha256"] = cls._report_digest(result)
        return result

    def create(self, principal, body, *, idempotency_key, now):
        text(idempotency_key, "Idempotency-Key", maximum=120)
        allowed = {"manifest_id", "report_type", "research_question"}
        if not isinstance(body, dict) or not {"manifest_id", "report_type"} <= set(body):
            raise DomainError(
                "invalid_argument",
                "Report body requires manifest_id and report_type",
            )
        if set(body) - allowed:
            raise DomainError("invalid_argument", "Report body has unknown fields")
        manifest_id = text(body["manifest_id"], "manifest_id")
        if body["report_type"] != REPORT_TYPE:
            raise DomainError(
                "unsupported",
                "Only deterministic reports are implemented; AI reports remain P2",
                400,
            )
        question = body.get("research_question")
        if question is not None:
            question = text(question, "research_question", maximum=2000)
        request_hash = identity("report-request", {
            "manifest_id": manifest_id,
            "report_type": REPORT_TYPE,
            "research_question": question,
        })
        with self.registry.connect() as db:
            existing = db.execute(
                """SELECT * FROM sf_google_reports
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise DomainError(
                    "idempotency_conflict",
                    "Idempotency key was used for another report",
                    409,
                )
            return self.detail(principal, existing["report_id"], now=now)

        manifest, _, frozen, rights, expiry, observation_limitations = self._validate(
            principal, manifest_id, now=now)
        report_id = identity("report", {
            "tenant_id": principal.tenant_id,
            "idempotency_key": idempotency_key,
        })
        result = self._result(
            report_id=report_id,
            manifest=manifest,
            research_question=question,
            frozen=frozen,
            rights=rights,
            expiry=expiry,
            now=now,
            observation_limitations=observation_limitations,
        )
        versions = result["versions"]
        try:
            with self.registry.connect() as db:
                db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261110))",
                    (principal.tenant_id,),
                )
                self.registry.assert_current(principal, now=now, db=db)
                conflict = db.execute(
                    """SELECT * FROM sf_google_reports
                       WHERE tenant_id=%s AND idempotency_key=%s""",
                    (principal.tenant_id, idempotency_key),
                ).fetchone()
                if conflict:
                    if conflict["request_hash"] != request_hash:
                        raise DomainError(
                            "idempotency_conflict",
                            "Idempotency key was used for another report",
                            409,
                        )
                    return plain_json(conflict["result"])
                db.execute(
                    """INSERT INTO sf_google_reports
                       (tenant_id,report_id,idempotency_key,request_hash,manifest_id,
                        report_type,subject_scope,subject_ref,generated_at,expires_at,
                        input_manifest_sha256,report_sha256,result,evidence,versions)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        principal.tenant_id,
                        report_id,
                        idempotency_key,
                        request_hash,
                        manifest_id,
                        REPORT_TYPE,
                        self._subject(principal),
                        principal.subject_ref,
                        parse_time(now),
                        expiry,
                        result["input_manifest_sha256"],
                        result["report_sha256"],
                        Jsonb(result),
                        Jsonb(frozen),
                        Jsonb(versions),
                    ),
                )
            return result
        except UniqueViolation:
            with self.registry.connect() as db:
                row = db.execute(
                    """SELECT * FROM sf_google_reports
                       WHERE tenant_id=%s AND idempotency_key=%s""",
                    (principal.tenant_id, idempotency_key),
                ).fetchone()
            if not row or row["request_hash"] != request_hash:
                raise DomainError(
                    "idempotency_conflict",
                    "Idempotency race with another report",
                    409,
                ) from None
            return self.detail(principal, row["report_id"], now=now)

    def detail(self, principal, report_id, *, now):
        text(report_id, "report_id")
        with self.registry.connect() as db:
            row = db.execute(
                """SELECT * FROM sf_google_reports
                   WHERE tenant_id=%s AND report_id=%s""",
                (principal.tenant_id, report_id),
            ).fetchone()
        if not row:
            raise DomainError("not_found", "No report with this ID", 404)
        if parse_time(now) >= parse_time(row["expires_at"]):
            raise DomainError(
                "expired",
                "Report evidence has expired; restricted content is unavailable",
                410,
            )
        if (row["subject_scope"] != self._subject(principal)
                or row["subject_ref"] != principal.subject_ref):
            raise DomainError("not_authorized", "Report belongs to another key scope", 403)
        manifest, _, _, _, expiry, observation_limitations = self._validate(
            principal, row["manifest_id"], now=now)
        if expiry != parse_time(row["expires_at"]):
            raise DomainError(
                "source_error", "Report expiry no longer matches current rights", 503)
        if self._manifest_digest(manifest) != row["input_manifest_sha256"]:
            raise DomainError(
                "source_error", "Frozen report input manifest no longer matches", 503)
        result = plain_json(row["result"])
        if result.get("observation_limitations") != observation_limitations:
            raise DomainError(
                "source_error", "Report limitation states differ from pinned versions", 503)
        if self._report_digest(result) != row["report_sha256"]:
            raise DomainError(
                "source_error", "Stored report does not match its hash", 503)
        if result.get("report_sha256") != row["report_sha256"]:
            raise DomainError(
                "source_error", "Stored report hash is inconsistent", 503)
        return result

    def list(self, principal, parameters, *, now):
        """List report metadata without exposing stored conclusions or evidence."""
        if set(parameters) - {"limit", "cursor"}:
            raise DomainError("invalid_argument", "Unknown report list parameter")
        limit = page_limit(parameters.get("limit"))
        with self.registry.connect() as db:
            self.registry.assert_current(principal, now=now, db=db)
            rows = db.execute(
                """SELECT report_id,manifest_id,report_type,
                          result->>'research_question' AS research_question,
                          generated_at,expires_at,report_sha256,versions
                   FROM sf_google_reports
                   WHERE tenant_id=%s AND subject_scope=%s AND subject_ref=%s
                     AND expires_at>%s
                   ORDER BY generated_at DESC,report_id DESC""",
                (
                    principal.tenant_id,
                    self._subject(principal),
                    principal.subject_ref,
                    parse_time(now),
                ),
            ).fetchall()
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {
                "limit": limit,
                "subject_scope": self._subject(principal),
                "subject_ref": principal.subject_ref,
            },
            "sort": ["generated_at", "report_id"],
            "source": "reports:1",
            "manifest_id": None,
        }
        codec = getattr(self.projection, "cursor_codec", None)
        if codec is None:
            raise DomainError(
                "not_covered", "Report pagination signing is not configured", 503)
        after = None
        if "cursor" in parameters:
            after = codec.decode(
                parameters["cursor"], binding=binding, now=now)
        selected = [
            row for row in rows
            if after is None
            or (utc_text(row["generated_at"]), row["report_id"]) < tuple(after)
        ]
        page, more = selected[:limit], len(selected) > limit
        cursor = None
        if more:
            last = page[-1]
            cursor = codec.encode(
                binding=binding,
                position=[utc_text(last["generated_at"]), last["report_id"]],
                expires_at=min(
                    parse_time(now) + timedelta(minutes=10),
                    min(parse_time(row["expires_at"]) for row in page),
                ),
                now=now,
            )
        reports = [{
            "report_id": row["report_id"],
            "manifest_id": row["manifest_id"],
            "report_type": row["report_type"],
            "research_question": row["research_question"],
            "generated_at": utc_text(row["generated_at"]),
            "expires_at": utc_text(row["expires_at"]),
            "report_sha256": row["report_sha256"],
            "versions": plain_json(row["versions"]),
            "detail_path": "/v1/google/reports/" + row["report_id"],
        } for row in page]
        return {
            "reports": reports,
            "next_cursor": cursor,
            "result_payload_included": False,
            "validation": "detail_rechecks_current_rights_and_pinned_manifest",
        }

    def purge_expired(self, *, now):
        with self.registry.connect() as db:
            return db.execute(
                "DELETE FROM sf_google_reports WHERE expires_at<=%s",
                (parse_time(now),),
            ).rowcount
