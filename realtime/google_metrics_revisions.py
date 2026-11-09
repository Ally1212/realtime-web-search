"""Audit-only revision ledger for GA4, Ads and Merchant report rereads.

The ledger stores versions, hashes, windows and observable source accounting.
It never stores report rows, invents preliminary status, or estimates missing
revision fees / query scans. Original facts remain in the approved platform.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError, canonical_json, identity, parse_time, plain_json, text,
    utc_text,
)

FAMILIES = frozenset({"ga4", "ads", "merchant"})
STAGES = {
    "ga4": frozenset({"intraday", "final"}),
    "ads": frozenset({"current", "conversion_adjusted"}),
    "merchant": frozenset({"current", "revised"}),
}
READ_CONDITIONS = frozenset({
    "initial_read", "scheduled_reread", "manual_reread", "lag_reread",
    "revision_reread",
})
COMPLETENESS = frozenset({"complete", "partial", "unknown"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_metric_revision_policies (
 tenant_id text NOT NULL, family text NOT NULL, policy_version text NOT NULL,
 config jsonb NOT NULL, effective_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,family,policy_version)
);
CREATE TABLE IF NOT EXISTS sf_google_metric_report_revisions (
 tenant_id text NOT NULL, family text NOT NULL, partition_id text NOT NULL,
 observation_id text NOT NULL, stage text NOT NULL,
 source_revision text NOT NULL, payload_id text NOT NULL,
 scope jsonb NOT NULL, accounting jsonb NOT NULL,
 source_observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,family,partition_id,observation_id)
);
CREATE INDEX IF NOT EXISTS sf_google_metric_revisions_partition
 ON sf_google_metric_report_revisions(tenant_id,family,partition_id,source_observed_at);
"""


def _day(value, name):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DomainError("invalid_argument", f"{name} must use YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise DomainError("invalid_argument", f"{name} must use YYYY-MM-DD")
    return parsed


def _optional_amount(value, name):
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise DomainError("invalid_argument", f"{name} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class RereadPolicy:
    """Source-approved timing; None means the source did not disclose it."""

    family: str
    policy_version: str
    preliminary_window_seconds: int | None
    final_after_seconds: int | None
    minimum_reread_seconds: int | None
    max_rereads: int | None
    lag_reread_allowed: bool
    lag_only_metrics: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self):
        text(self.policy_version, "policy_version")
        if self.family not in FAMILIES:
            raise DomainError("invalid_argument", "Unknown report family")
        for name in (
                "preliminary_window_seconds", "final_after_seconds",
                "minimum_reread_seconds", "max_rereads"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise DomainError("invalid_argument", f"{name} must be nonnegative or unknown")
        metrics = tuple(self.lag_only_metrics)
        if (
                len(set(metrics)) != len(metrics)
                or any(not isinstance(item, str) or not item for item in metrics)):
            raise DomainError("invalid_argument", "Invalid lag-only metric list")
        if type(self.lag_reread_allowed) is not bool:
            raise DomainError("invalid_argument", "lag_reread_allowed must be boolean")
        if self.lag_reread_allowed and not metrics:
            raise DomainError(
                "invalid_argument", "Lag rereads require explicitly metric-limited scope")
        if metrics and not self.lag_reread_allowed:
            raise DomainError("invalid_argument", "Metric-limited scope needs lag reread approval")
        if self.note is not None:
            text(self.note, "policy note", maximum=2048)
        object.__setattr__(self, "lag_only_metrics", metrics)

    def to_dict(self):
        return {
            name: plain_json(getattr(self, name))
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class MetricPartition:
    tenant_id: str
    family: str
    source_adapter: str
    source_version: str
    source_api_version: str
    contract_ref: str
    dataset_id: str
    asset_id: str
    template_version: str
    report_version: str
    period_start: str
    period_end: str
    source_timezone: str
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    scope: dict

    def __post_init__(self):
        for name in (
                "tenant_id", "source_adapter", "source_version",
                "source_api_version", "contract_ref", "dataset_id",
                "asset_id", "template_version", "report_version",
                "source_timezone"):
            text(getattr(self, name), name)
        if self.family not in FAMILIES:
            raise DomainError("invalid_argument", "Unknown report family")
        if _day(self.period_start, "period_start") >= _day(self.period_end, "period_end"):
            raise DomainError(
                "invalid_argument", "Report partition period must be left-closed/right-open")
        for name in ("dimensions", "metrics"):
            values = tuple(getattr(self, name))
            if (
                    not values or len(set(values)) != len(values)
                    or any(not isinstance(item, str) or not item for item in values)):
                raise DomainError("invalid_argument", f"Invalid partition {name}")
            object.__setattr__(self, name, values)
        if not isinstance(self.scope, dict) or not self.scope:
            raise DomainError("invalid_argument", "Explicit report scope is required")
        canonical_json(self.scope)

    def to_dict(self):
        return {
            name: plain_json(getattr(self, name))
            for name in self.__dataclass_fields__
        }

    @property
    def partition_id(self):
        body = self.to_dict()
        body["dimensions"] = sorted(self.dimensions)
        body["metrics"] = sorted(self.metrics)
        return identity("metric-report-partition", body)


@dataclass(frozen=True)
class SourceAccounting:
    """Only values actually disclosed by the source/contract are admissible."""

    reread_condition: str
    revision_fee_micros: int | None = None
    query_scans: int | None = None
    tokens_consumed: int | None = None
    rows_scanned: int | None = None
    bytes_scanned: int | None = None
    accounting_basis: str | None = None
    fee_currency: str | None = None

    def __post_init__(self):
        if self.reread_condition not in READ_CONDITIONS:
            raise DomainError("invalid_argument", "Unknown reread condition")
        for name in (
                "revision_fee_micros", "query_scans", "tokens_consumed",
                "rows_scanned", "bytes_scanned"):
            _optional_amount(getattr(self, name), name)
        if self.accounting_basis is not None:
            text(self.accounting_basis, "accounting_basis", maximum=2048)
        if self.fee_currency is not None:
            if (
                    not isinstance(self.fee_currency, str)
                    or len(self.fee_currency) != 3
                    or not self.fee_currency.isalpha()
                    or self.fee_currency != self.fee_currency.upper()):
                raise DomainError(
                    "invalid_argument", "fee_currency must be an uppercase ISO code")

    def to_dict(self):
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class ReportRevision:
    partition: MetricPartition
    observation_id: str
    stage: str
    source_revision: str
    source_observed_at: str
    received_at: str
    expires_at: str
    payload: dict
    accounting: SourceAccounting
    response_complete: bool = True
    preliminary: bool | None = None
    reread_of: str | None = None

    def __post_init__(self):
        if (
                not isinstance(self.partition, MetricPartition)
                or not isinstance(self.accounting, SourceAccounting)):
            raise DomainError("invalid_argument", "Expected typed partition/accounting")
        for name in ("observation_id", "source_revision"):
            text(getattr(self, name), name)
        partition = self.partition
        if self.stage not in STAGES[partition.family]:
            raise DomainError("invalid_argument", "Stage is not valid for this report family")
        observed, received = parse_time(self.source_observed_at), parse_time(self.received_at)
        expires = parse_time(self.expires_at)
        if observed > received or received >= expires:
            raise DomainError(
                "expired", "Source observation/receipt/expiry order is invalid", 410)
        if type(self.response_complete) is not bool:
            raise DomainError("invalid_argument", "response_complete must be boolean")
        if self.preliminary is not None and type(self.preliminary) is not bool:
            raise DomainError("invalid_argument", "preliminary must be boolean or unknown")
        if self.reread_of is not None:
            text(self.reread_of, "reread_of")
            if self.reread_of == self.observation_id:
                raise DomainError("invalid_argument", "A revision cannot reread itself")
        if not isinstance(self.payload, dict) or not self.payload:
            raise DomainError("invalid_argument", "A report hash payload is required")
        if not isinstance(self.payload.get("source_revision"), str):
            raise DomainError("invalid_argument", "Payload must bind the source revision")
        if partition.family == "ga4" and self.stage == "final" and self.preliminary is not False:
            raise DomainError(
                "invalid_argument", "GA4 final observations require preliminary=false")
        if partition.family == "ga4" and self.stage == "intraday" and self.preliminary is not True:
            raise DomainError(
                "invalid_argument", "GA4 intraday observations require preliminary=true")
        canonical_json(self.payload)
        object.__setattr__(self, "source_observed_at", utc_text(observed))
        object.__setattr__(self, "received_at", utc_text(received))
        object.__setattr__(self, "expires_at", utc_text(expires))

    def to_dict(self):
        return {
            "partition": self.partition.to_dict(),
            "observation_id": self.observation_id, "stage": self.stage,
            "source_revision": self.source_revision,
            "source_observed_at": self.source_observed_at,
            "received_at": self.received_at, "expires_at": self.expires_at,
            "payload": plain_json(self.payload),
            "accounting": self.accounting.to_dict(),
            "response_complete": self.response_complete,
            "preliminary": self.preliminary, "reread_of": self.reread_of,
        }

    @property
    def payload_id(self):
        body = plain_json(self.to_dict())
        body.pop("received_at")
        body["payload"] = plain_json(self.payload)
        return identity("metric-report-revision", body)

    def eligibility(self, *, now):
        reasons = []
        if not self.response_complete:
            reasons.append("incomplete_response")
        if parse_time(self.expires_at) <= parse_time(now):
            reasons.append("expired")
        stale = (
            self.source_revision != "unknown"
            and self.source_revision != self.payload.get("source_revision")
        )
        if stale:
            reasons.append("payload_source_revision_mismatch")
        expected_stage = {
            "ga4": "final" if self.preliminary is False else "intraday",
            "ads": "conversion_adjusted" if self.stage == "conversion_adjusted" else "current",
            "merchant": "revised" if self.stage == "revised" else "current",
        }[self.partition.family]
        if self.stage != expected_stage:
            reasons.append("preliminary_final_stage_mismatch")
        if self.partition.family == "ga4" and self.preliminary is None:
            reasons.append("unknown_preliminary_state")
        return reasons


class MetricRevisionLedger:
    def __init__(self, dsn, *, clock=None):
        self.dsn = dsn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def initialize(self):
        with psycopg.connect(self.dsn) as db:
            db.execute("SELECT pg_advisory_xact_lock(20261112,1)")
            db.execute(SCHEMA)

    @staticmethod
    def _lock(db, partition):
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (partition.partition_id,),
        )

    def policy(self, partition, *, policy):
        if not isinstance(partition, MetricPartition) or not isinstance(policy, RereadPolicy):
            raise DomainError("invalid_argument", "Expected typed partition and policy")
        if policy.family != partition.family:
            raise DomainError("incompatible_scope", "Policy and partition family differ", 409)
        if not policy.lag_reread_allowed and partition.family == "ads":
            raise DomainError(
                "use_not_permitted", "Ads conversion lag reread requires approved metric scope", 403)
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            self._lock(db, partition)
            prior = db.execute(
                """SELECT config FROM sf_google_metric_revision_policies
                   WHERE tenant_id=%s AND family=%s AND policy_version=%s""",
                (partition.tenant_id, partition.family, policy.policy_version),
            ).fetchone()
            config = policy.to_dict()
            if prior and prior["config"] != config:
                raise DomainError("idempotency_conflict", "Policy version changed", 409)
            if not prior:
                db.execute(
                    """INSERT INTO sf_google_metric_revision_policies
                       (tenant_id,family,policy_version,config,effective_at)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (partition.tenant_id, partition.family, policy.policy_version,
                     Jsonb(config), parse_time(self.clock())),
                )
        return policy.policy_version

    def _admission(self, db, revision):
        partition = revision.partition
        policies = db.execute(
            """SELECT policy_version,config FROM sf_google_metric_revision_policies
               WHERE tenant_id=%s AND family=%s ORDER BY effective_at DESC,policy_version DESC""",
            (partition.tenant_id, partition.family),
        ).fetchall()
        policy = RereadPolicy(**policies[0]["config"]) if policies else None
        prior = db.execute(
            """SELECT observation_id,stage,source_revision,payload_id,accounting,
                      source_observed_at
               FROM sf_google_metric_report_revisions
               WHERE tenant_id=%s AND family=%s AND partition_id=%s
               ORDER BY source_observed_at""",
            (partition.tenant_id, partition.family, partition.partition_id),
        ).fetchall()
        reasons = revision.eligibility(now=self.clock())
        if revision.reread_of is not None and not any(
                row["observation_id"] == revision.reread_of for row in prior):
            raise DomainError("incompatible_scope", "Reread references an unknown observation", 409)
        if policy is not None and revision.reread_of is not None:
            original = next(
                row for row in prior if row["observation_id"] == revision.reread_of)
            elapsed = parse_time(revision.source_observed_at) - original["source_observed_at"]
            if (
                    policy.minimum_reread_seconds is not None
                    and elapsed.total_seconds() < policy.minimum_reread_seconds):
                reasons.append("reread_too_early")
            if (
                    policy.final_after_seconds is not None
                    and revision.stage == ("final" if partition.family == "ga4"
                                           else "conversion_adjusted" if partition.family == "ads"
                                           else "revised")
                    and elapsed.total_seconds() < policy.final_after_seconds):
                reasons.append("final_before_source_window")
        if (
                policy is not None and revision.accounting.reread_condition == "lag_reread"
                and not set(partition.metrics) <= set(policy.lag_only_metrics)):
            reasons.append("lag_reread_scope_not_approved")
        return policy, prior, reasons

    def record(self, revision):
        if not isinstance(revision, ReportRevision):
            raise DomainError("invalid_argument", "Expected typed report revision")
        now = parse_time(self.clock())
        if parse_time(revision.received_at) > now:
            raise DomainError("invalid_argument", "Receipt is in the future")
        if parse_time(revision.expires_at) <= now:
            raise DomainError("expired", "Report evidence has expired", 410)
        partition = revision.partition
        payload_id = revision.payload_id
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            self._lock(db, partition)
            policy, prior, reasons = self._admission(db, revision)
            same_id = next((row for row in prior
                            if row["observation_id"] == revision.observation_id), None)
            if same_id:
                if same_id["payload_id"] != payload_id:
                    raise DomainError("idempotency_conflict", "Observation payload changed", 409)
            else:
                if reasons:
                    raise DomainError("incompatible_scope", "Revision is not eligible: " + ",".join(reasons), 409)
                if prior and revision.source_revision != "unknown":
                    previous_max = max(
                        (row["source_revision"] for row in prior
                         if row["source_revision"] != "unknown"),
                        default=None)
                    # Source-provided revision strings are opaque; no lexical
                    # ordering or numeric invention is applied.
                    if previous_max is not None and revision.source_revision == previous_max and (
                            revision.stage == prior[-1]["stage"]):
                        raise DomainError(
                            "idempotency_conflict",
                            "Same source revision cannot be resent as a new observation", 409)
                if policy is not None and policy.max_rereads is not None and (
                        len([row for row in prior
                             if row["accounting"].get("reread_of") is not None])
                        >= policy.max_rereads):
                    raise DomainError(
                        "quota_exceeded", "Source-approved reread budget exhausted", 429)
                db.execute(
                    """INSERT INTO sf_google_metric_report_revisions
                       (tenant_id,family,partition_id,observation_id,stage,source_revision,
                        payload_id,scope,accounting,source_observed_at,received_at,expires_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (partition.tenant_id, partition.family, partition.partition_id,
                     revision.observation_id, revision.stage, revision.source_revision,
                     payload_id, Jsonb(partition.to_dict()),
                     Jsonb({
                         **revision.accounting.to_dict(),
                         "reread_of": revision.reread_of,
                         "response_complete": revision.response_complete,
                         "preliminary": revision.preliminary,
                         "eligible": not reasons,
                     }),
                     parse_time(revision.source_observed_at),
                     parse_time(revision.received_at), parse_time(revision.expires_at)),
                )
        return {
            "tenant_id": partition.tenant_id, "family": partition.family,
            "partition_id": partition.partition_id,
            "observation_id": revision.observation_id,
            "source_revision": revision.source_revision,
            "stage": revision.stage, "payload_id": payload_id,
            "eligible": not reasons, "exclusions": reasons,
            "policy_version": policy.policy_version if policy else None,
        }

    def history(self, partition):
        if not isinstance(partition, MetricPartition):
            raise DomainError("invalid_argument", "Expected typed partition")
        now = parse_time(self.clock())
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            rows = db.execute(
                """SELECT * FROM sf_google_metric_report_revisions
                   WHERE tenant_id=%s AND family=%s AND partition_id=%s
                   ORDER BY source_observed_at,observation_id""",
                (partition.tenant_id, partition.family, partition.partition_id),
            ).fetchall()
        return [{
            "observation_id": row["observation_id"],
            "source_revision": row["source_revision"],
            "stage": row["stage"], "payload_id": row["payload_id"],
            "source_observed_at": utc_text(row["source_observed_at"]),
            "received_at": utc_text(row["received_at"]),
            "expires_at": utc_text(row["expires_at"]),
            "expired": row["expires_at"] <= now,
            "accounting": row["accounting"],
            "payload": None, "payload_hash": row["payload_id"],
            "payload_retained": False,
            "platform_readback_verified": False,
        } for row in rows]

    def account_partition(self, partition):
        """Expose missing source accounting as unknown, never as zero."""
        history = self.history(partition)
        fields = (
            "revision_fee_micros", "query_scans", "tokens_consumed",
            "rows_scanned", "bytes_scanned",
        )
        totals = {}
        for name in fields:
            groups = {}
            disclosed = 0
            for row in history:
                value = row["accounting"].get(name)
                if value is None:
                    continue
                disclosed += 1
                key = (
                    row["accounting"].get("accounting_basis"),
                    row["accounting"].get("fee_currency"),
                )
                groups.setdefault(key, []).append(value)
            if not groups:
                value = None
                availability = "unknown"
            elif len(groups) == 1:
                value = sum(next(iter(groups.values())))
                availability = "source_reported"
            else:
                value = None
                availability = "mixed_basis"
            totals[name] = {
                "value": value,
                "availability": availability,
                "reported_observations": disclosed,
                "missing_observations": len(history) - disclosed,
                "basis_groups": [
                    {
                        "accounting_basis": basis,
                        "fee_currency": currency,
                        "value": sum(values),
                        "observations": len(values),
                    }
                    for (basis, currency), values in sorted(
                        groups.items(), key=lambda item: repr(item[0]))
                ] if len(groups) > 1 else [],
            }
        return {
            "partition_id": partition.partition_id,
            "observations": len(history),
            "preliminary_observations": sum(
                row["accounting"].get("preliminary") is True for row in history),
            "final_observations": sum(
                row["accounting"].get("preliminary") is False for row in history),
            "rereads": sum(
                row["accounting"].get("reread_of") is not None for row in history),
            "source_accounting": totals,
            "unavailable_accounting": {
                name: value["missing_observations"]
                for name, value in totals.items()
                if value["availability"] == "unknown"
            },
            "unknown_is_zero": False,
        }
