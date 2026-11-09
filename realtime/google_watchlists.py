"""Key-scoped watchlists whose item removal never deletes source facts."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .google_domain import (
    DomainError, canonical_json, identity, parse_time, plain_json, text,
    utc_text,
)
from .google_evidence import page_limit

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_google_watchlists (
 tenant_id text NOT NULL, watchlist_id text NOT NULL, idempotency_key text NOT NULL,
 request_hash text NOT NULL, subject_ref text NOT NULL, name text NOT NULL,
 version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,watchlist_id), UNIQUE(tenant_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS sf_google_watchlist_items (
 tenant_id text NOT NULL, watchlist_id text NOT NULL, entity_id text NOT NULL,
 entity_type text NOT NULL, label text, metadata jsonb NOT NULL,
 added_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,watchlist_id,entity_id),
 FOREIGN KEY(tenant_id,watchlist_id)
 REFERENCES sf_google_watchlists ON DELETE CASCADE
);
"""

ENTITY_TYPES = frozenset({
    "query", "run", "item", "feature", "url", "domain", "brand", "topic",
    "term", "report",
})


def _now():
    return datetime.now(timezone.utc)


class WatchlistService:
    def __init__(self, dsn, *, registry, cursor_codec=None, clock=None):
        self.dsn, self.registry = dsn, registry
        self.cursor_codec = cursor_codec
        self.clock = clock or _now

    def initialize(self):
        with self.registry.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(20261104,11)")
            db.execute(SCHEMA)

    @staticmethod
    def route(path):
        if path in {"/watchlists", "/watchlists/"}:
            return "list", None
        match = re.fullmatch(r"/watchlists/([^/]+)", path)
        if match:
            return "detail", unquote(match[1], errors="strict")
        match = re.fullmatch(r"/watchlists/([^/]+)/items/([^/]+)", path)
        if match:
            return "remove", (
                unquote(match[1], errors="strict"),
                unquote(match[2], errors="strict"),
            )
        raise DomainError("not_found", "No watchlist route matches", 404)

    @staticmethod
    def _clean_items(items):
        if not isinstance(items, list) or len(items) > 1000:
            raise DomainError(
                "invalid_argument", "watchlist items must be an array of at most 1000")
        result, seen = [], set()
        for raw in items:
            if not isinstance(raw, dict) or not {"entity_id", "entity_type"} <= set(raw):
                raise DomainError(
                    "invalid_argument",
                    "Each watchlist item requires entity_id and entity_type",
                )
            if set(raw) - {"entity_id", "entity_type", "label", "metadata"}:
                raise DomainError("invalid_argument", "Watchlist item has unknown fields")
            entity_id = text(raw["entity_id"], "entity_id")
            if entity_id in seen:
                raise DomainError("invalid_argument", "Duplicate watchlist entity_id")
            seen.add(entity_id)
            entity_type = raw["entity_type"]
            if entity_type not in ENTITY_TYPES:
                raise DomainError("unsupported", "Unknown watchlist entity_type")
            label = raw.get("label")
            if label is not None:
                label = text(label, "label", maximum=500)
            metadata = raw.get("metadata", {})
            if not isinstance(metadata, dict):
                raise DomainError("invalid_argument", "Watchlist metadata must be an object")
            canonical_json(metadata)
            result.append({
                "entity_id": entity_id,
                "entity_type": entity_type,
                "label": label,
                "metadata": metadata,
            })
        return result

    @staticmethod
    def _public(row, items):
        return {
            "watchlist_id": row["watchlist_id"],
            "name": row["name"],
            "version": row["version"],
            "etag": f'"{row["version"]}"',
            "created_at": utc_text(row["created_at"]),
            "updated_at": utc_text(row["updated_at"]),
            "item_count": len(items),
            "items": [{
                "entity_id": item["entity_id"],
                "entity_type": item["entity_type"],
                "label": item["label"],
                "metadata": plain_json(item["metadata"]),
                "added_at": utc_text(item["added_at"]),
            } for item in items],
            "source_fact_deletion": False,
        }

    def _items(self, db, tenant_id, watchlist_id):
        return db.execute(
            """SELECT * FROM sf_google_watchlist_items
               WHERE tenant_id=%s AND watchlist_id=%s
               ORDER BY added_at,entity_id""",
            (tenant_id, watchlist_id),
        ).fetchall()

    def create(self, principal, body, *, idempotency_key, now):
        text(idempotency_key, "Idempotency-Key", maximum=120)
        if not isinstance(body, dict) or set(body) - {"name", "items"}:
            raise DomainError("invalid_argument", "Watchlist body has unknown fields")
        if "name" not in body:
            raise DomainError("invalid_argument", "Watchlist name is required")
        name = text(body["name"], "name", maximum=500)
        items = self._clean_items(body.get("items", []))
        request_hash = identity("watchlist-request", {"name": name, "items": items})
        with self.registry.connect() as db:
            existing = db.execute(
                """SELECT * FROM sf_google_watchlists
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise DomainError(
                        "idempotency_conflict",
                        "Idempotency key was used for another watchlist",
                        409,
                    )
                return self._public(
                    existing,
                    self._items(
                        db, principal.tenant_id, existing["watchlist_id"]),
                )
        watchlist_id = identity("watchlist", {
            "tenant_id": principal.tenant_id,
            "idempotency_key": idempotency_key,
        })
        try:
            with self.registry.connect() as db:
                db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261111))",
                    (principal.tenant_id,),
                )
                self.registry.assert_current(principal, now=now, db=db)
                conflict = db.execute(
                    """SELECT * FROM sf_google_watchlists
                       WHERE tenant_id=%s AND idempotency_key=%s""",
                    (principal.tenant_id, idempotency_key),
                ).fetchone()
                if conflict:
                    if conflict["request_hash"] != request_hash:
                        raise DomainError(
                            "idempotency_conflict",
                            "Idempotency key was used for another watchlist",
                            409,
                        )
                    return self._public(
                        conflict,
                        self._items(
                            db, principal.tenant_id, conflict["watchlist_id"]),
                    )
                db.execute(
                    """INSERT INTO sf_google_watchlists
                       (tenant_id,watchlist_id,idempotency_key,request_hash,
                        subject_ref,name,version,created_at,updated_at)
                       VALUES(%s,%s,%s,%s,%s,%s,1,%s,%s)""",
                    (
                        principal.tenant_id,
                        watchlist_id,
                        idempotency_key,
                        request_hash,
                        principal.subject_ref,
                        name,
                        now,
                        now,
                    ),
                )
                for item in items:
                    db.execute(
                        """INSERT INTO sf_google_watchlist_items
                           (tenant_id,watchlist_id,entity_id,entity_type,label,
                            metadata,added_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            principal.tenant_id,
                            watchlist_id,
                            item["entity_id"],
                            item["entity_type"],
                            item["label"],
                            Jsonb(item["metadata"]),
                            now,
                        ),
                    )
                row = db.execute(
                    """SELECT * FROM sf_google_watchlists
                       WHERE tenant_id=%s AND watchlist_id=%s""",
                    (principal.tenant_id, watchlist_id),
                ).fetchone()
                return self._public(
                    row, self._items(db, principal.tenant_id, watchlist_id))
        except UniqueViolation:
            with self.registry.connect() as db:
                row = db.execute(
                    """SELECT * FROM sf_google_watchlists
                       WHERE tenant_id=%s AND idempotency_key=%s""",
                    (principal.tenant_id, idempotency_key),
                ).fetchone()
                if not row or row["request_hash"] != request_hash:
                    raise DomainError(
                        "idempotency_conflict",
                        "Idempotency race with another watchlist",
                        409,
                    ) from None
                return self._public(
                    row, self._items(db, principal.tenant_id, row["watchlist_id"]))

    def list(self, principal, parameters, *, now):
        if set(parameters) - {"limit", "cursor"}:
            raise DomainError("invalid_argument", "Unknown watchlist list parameter")
        limit = page_limit(parameters.get("limit"))
        if self.cursor_codec is None:
            raise DomainError(
                "not_covered",
                "Watchlist cursor pagination is not configured",
                503,
            )
        binding = {
            "tenant_id": principal.tenant_id,
            "rights_version": principal.rights_version,
            "filters": {
                "limit": limit,
                "subject": principal.subject_ref,
            },
            "sort": ["created_at", "watchlist_id"],
            "source": "watchlists:1",
            "manifest_id": None,
        }
        after = None
        if "cursor" in parameters:
            after = self.cursor_codec.decode(
                parameters["cursor"], binding=binding, now=now)
        with self.registry.connect() as db:
            self.registry.assert_current(principal, now=now, db=db)
            rows = db.execute(
                """SELECT * FROM sf_google_watchlists
                   WHERE tenant_id=%s AND subject_ref=%s
                   ORDER BY created_at DESC,watchlist_id DESC""",
                (principal.tenant_id, principal.subject_ref),
            ).fetchall()
            if after is not None:
                rows = [
                    row for row in rows
                    if (
                        utc_text(row["created_at"]),
                        row["watchlist_id"],
                    ) < tuple(after)
                ]
            page, more = rows[:limit], len(rows) > limit
            cursor = None
            if more:
                last = page[-1]
                cursor = self.cursor_codec.encode(
                    binding=binding,
                    position=[
                        utc_text(last["created_at"]),
                        last["watchlist_id"],
                    ],
                    expires_at=parse_time(now) + timedelta(minutes=10),
                    now=now,
                )
            watchlists = [
                self._public(
                    row, self._items(db, principal.tenant_id, row["watchlist_id"]))
                for row in page
            ]
        return {
            "watchlists": watchlists,
            "next_cursor": cursor,
            "pagination_state": "signed_cursor",
            "source_fact_deletion": False,
        }

    def detail(self, principal, watchlist_id, *, now):
        text(watchlist_id, "watchlist_id")
        with self.registry.connect() as db:
            self.registry.assert_current(principal, now=now, db=db)
            row = db.execute(
                """SELECT * FROM sf_google_watchlists
                   WHERE tenant_id=%s AND watchlist_id=%s""",
                (principal.tenant_id, watchlist_id),
            ).fetchone()
            if not row:
                raise DomainError("not_found", "No watchlist with this ID", 404)
            if row["subject_ref"] != principal.subject_ref:
                raise DomainError(
                    "not_authorized", "Watchlist belongs to another key", 403)
            items = self._items(db, principal.tenant_id, watchlist_id)
        return self._public(row, items)

    def remove_item(self, principal, watchlist_id, entity_id, *, now):
        text(watchlist_id, "watchlist_id")
        text(entity_id, "entity_id")
        with self.registry.connect() as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,20261111))",
                (principal.tenant_id,),
            )
            self.registry.assert_current(principal, now=now, db=db)
            row = db.execute(
                """SELECT * FROM sf_google_watchlists
                   WHERE tenant_id=%s AND watchlist_id=%s FOR UPDATE""",
                (principal.tenant_id, watchlist_id),
            ).fetchone()
            if not row:
                raise DomainError("not_found", "No watchlist with this ID", 404)
            if row["subject_ref"] != principal.subject_ref:
                raise DomainError(
                    "not_authorized", "Watchlist belongs to another key", 403)
            removed = db.execute(
                """DELETE FROM sf_google_watchlist_items
                   WHERE tenant_id=%s AND watchlist_id=%s AND entity_id=%s""",
                (principal.tenant_id, watchlist_id, entity_id),
            ).rowcount > 0
            if removed:
                db.execute(
                    """UPDATE sf_google_watchlists
                       SET version=version+1,updated_at=%s
                       WHERE tenant_id=%s AND watchlist_id=%s""",
                    (now, principal.tenant_id, watchlist_id),
                )
            current = db.execute(
                """SELECT * FROM sf_google_watchlists
                   WHERE tenant_id=%s AND watchlist_id=%s""",
                (principal.tenant_id, watchlist_id),
            ).fetchone()
            items = self._items(db, principal.tenant_id, watchlist_id)
        return {
            "watchlist": self._public(current, items),
            "removed_entity_id": entity_id,
            "removed": removed,
            "source_fact_deletion": False,
        }

    def purge(self, principal, watchlist_id, *, now):
        """Internal helper kept separate from membership removal."""
        raise DomainError(
            "not_covered",
            "Watchlist deletion is not exposed; only item membership can be removed",
            501,
        )
