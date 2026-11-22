"""Durable, non-repeating Google Web keyword supply."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from .campaign_store import CampaignStore

LABELS = Path(__file__).with_name('data') / 'google_web_keyword_labels.json'
INTENTS = {
    'museums': ('collections', 'exhibitions', 'history'),
    'libraries': ('archives', 'collections', 'history'),
    'universities': ('research centers', 'library collections', 'campus history'),
    'cities': ('urban planning', 'local history', 'public transportation'),
    'railway_stations': ('railway history', 'architecture', 'transport connections'),
    'bridges': ('construction history', 'engineering design', 'maintenance'),
    'airports': ('airport history', 'terminal design', 'transport connections'),
    'mountains': ('geology', 'hiking trails', 'protected areas'),
    'rivers': ('watershed', 'water quality', 'conservation'),
    'lakes': ('ecology', 'water quality', 'conservation'),
    'software': ('documentation', 'release history', 'user guide'),
    'satellites': ('mission overview', 'orbit data', 'launch history'),
}

SCHEMA = """
CREATE OR REPLACE FUNCTION serp_web_keyword_key(raw text) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
 SELECT sha256(convert_to(lower(btrim(regexp_replace(
     normalize(raw, NFKC), '[[:space:]]+', ' ', 'g'))), 'UTF8'))
$$;
CREATE TABLE IF NOT EXISTS serp_web_keyword_claims (
 keyword_key bytea PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS serp_web_keyword_pool_versions (
 version text PRIMARY KEY,
 enabled boolean NOT NULL DEFAULT false,
 cursor bigint NOT NULL DEFAULT -1,
 exhausted boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS serp_web_keyword_pool_one_active
 ON serp_web_keyword_pool_versions (enabled) WHERE enabled;
CREATE TABLE IF NOT EXISTS serp_web_keyword_candidates (
 version text NOT NULL REFERENCES serp_web_keyword_pool_versions(version),
 ordinal bigint NOT NULL,
 keyword_key bytea NOT NULL,
 query text NOT NULL,
 PRIMARY KEY (version, ordinal),
 UNIQUE (version, keyword_key)
);
CREATE OR REPLACE FUNCTION serp_web_keyword_remember_request() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO serp_web_keyword_claims VALUES (serp_web_keyword_key(NEW.query))
 ON CONFLICT DO NOTHING;
 RETURN NEW;
END
$$;
CREATE OR REPLACE FUNCTION serp_web_keyword_remember_campaign() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO serp_web_keyword_claims
 SELECT DISTINCT serp_web_keyword_key(s.query)
 FROM jsonb_array_elements_text(jsonb_build_array(NEW.query) || NEW.aliases) AS s(query)
 ORDER BY 1
 ON CONFLICT DO NOTHING;
 RETURN NEW;
END
$$;
"""

BACKFILL = """
CREATE TRIGGER serp_web_keyword_capture_campaign
 AFTER INSERT OR UPDATE OF query, aliases, sources ON campaigns
 FOR EACH ROW WHEN (NEW.sources ? 'google_web')
 EXECUTE FUNCTION serp_web_keyword_remember_campaign();
CREATE TRIGGER serp_web_keyword_capture_request
 AFTER INSERT ON serp_requests
 FOR EACH ROW WHEN (NEW.source = 'google_web')
 EXECUTE FUNCTION serp_web_keyword_remember_request();
INSERT INTO serp_web_keyword_claims
 SELECT DISTINCT serp_web_keyword_key(query) FROM serp_requests WHERE source='google_web'
 ON CONFLICT DO NOTHING;
INSERT INTO serp_web_keyword_claims
 SELECT DISTINCT serp_web_keyword_key(s.query)
 FROM campaigns c
 CROSS JOIN LATERAL jsonb_array_elements_text(jsonb_build_array(c.query) || c.aliases) AS s(query)
 WHERE c.sources ? 'google_web'
 ON CONFLICT DO NOTHING;
"""


def shuffled_queries(snapshot: bytes) -> tuple[str, list[str]]:
    document = json.loads(snapshot)
    version = document['version'] + '-' + hashlib.sha256(snapshot).hexdigest()[:12]
    rng = random.Random(version)
    groups = []
    for name, category in sorted(document['categories'].items()):
        queries = []
        for entry in sorted(category['labels'], key=lambda row: (row['id'], row['label'])):
            label = ' '.join(entry['label'].split())
            if 2 <= len(label) <= 160 and label.isprintable():
                queries.extend(f'{label} {intent}' for intent in INTENTS[name])
        rng.shuffle(queries)
        groups.append(queries)
    rng.shuffle(groups)
    return version, [
        group[index] for index in range(max(map(len, groups), default=0))
        for group in groups if index < len(group)
    ]


def install(store: CampaignStore, snapshot: bytes, *, activate: bool = False) -> dict:
    version, queries = shuffled_queries(snapshot)
    if not queries:
        raise ValueError('keyword snapshot contains no usable labels')
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(20261010, 2)')
        bootstrap = db.execute(
            "SELECT to_regclass('serp_web_keyword_claims') AS name"
        ).fetchone()['name'] is None
        if bootstrap:
            db.execute(SCHEMA)
            db.execute(BACKFILL)
        db.execute('INSERT INTO serp_web_keyword_pool_versions(version) VALUES(%s) ON CONFLICT DO NOTHING', (version,))
        db.execute(
            '''INSERT INTO serp_web_keyword_candidates(version, ordinal, keyword_key, query)
               SELECT %s, ordinal, serp_web_keyword_key(query), query
               FROM unnest(%s::text[]) WITH ORDINALITY AS batch(query, ordinal)
               ORDER BY ordinal ON CONFLICT DO NOTHING''',
            (version, queries),
        )
        if activate:
            db.execute('UPDATE serp_web_keyword_pool_versions SET enabled=false WHERE enabled AND version<>%s', (version,))
            db.execute('UPDATE serp_web_keyword_pool_versions SET enabled=true WHERE version=%s', (version,))
        row = db.execute(
            '''SELECT p.enabled, p.exhausted, count(c.ordinal) AS candidates
               FROM serp_web_keyword_pool_versions p JOIN serp_web_keyword_candidates c USING(version)
               WHERE p.version=%s GROUP BY p.version''', (version,),
        ).fetchone()
    return dict(version=version, **row)


def active_pool(db):
    if db.execute("SELECT to_regclass('serp_web_keyword_pool_versions') AS name").fetchone()['name'] is None:
        return None
    return db.execute(
        'SELECT version, cursor, exhausted FROM serp_web_keyword_pool_versions WHERE enabled FOR UPDATE'
    ).fetchone()


def refill_capacity(db, run_id: str, slots: int) -> int:
    """Bound the ready queue independently of queries waiting on backoff."""
    state = db.execute(
        """SELECT EXISTS (
               SELECT 1 FROM discovery_source_runtime
               WHERE source IN ('google_web','google_openserp') AND circuit_until > now()
             ) AS circuit_open,
             count(*) AS outstanding,
             count(*) FILTER (WHERE NOT EXISTS (
               SELECT 1 FROM google_page_frontier f
               WHERE f.campaign_id=c.id AND f.enabled AND f.state IN ('cooling','failed','pending')
               AND f.next_run_at > now() + interval '30 seconds'
             )) AS ready
             FROM campaigns c JOIN serp_supply_campaigns s ON s.campaign_id=c.id
             WHERE s.run_id=%s AND c.status='active'""",
        (run_id,),
    ).fetchone()
    if state['circuit_open']:
        return 0
    ready_target = min(128, max(8, 2 * slots))
    outstanding_limit = min(256, 2 * ready_target)
    return max(0, min(ready_target - state['ready'], outstanding_limit - state['outstanding']))


def claim_queries(db, pool, limit: int) -> list[str]:
    """Claim queries in the campaign-creation transaction; rollback releases them."""
    if limit <= 0 or pool['exhausted']:
        return []
    rows = db.execute(
        '''WITH candidates AS MATERIALIZED (
             SELECT ordinal, keyword_key, query FROM serp_web_keyword_candidates p
             WHERE p.version=%s AND p.ordinal>%s
             AND NOT EXISTS (SELECT 1 FROM serp_web_keyword_claims c WHERE c.keyword_key=p.keyword_key)
             ORDER BY ordinal LIMIT %s
           ), claimed AS (
             INSERT INTO serp_web_keyword_claims
             SELECT keyword_key FROM candidates ORDER BY keyword_key
             ON CONFLICT DO NOTHING RETURNING keyword_key
           )
           SELECT c.ordinal, c.query, claimed.keyword_key IS NOT NULL AS picked
           FROM candidates c LEFT JOIN claimed USING(keyword_key) ORDER BY c.ordinal''',
        (pool['version'], pool['cursor'], limit),
    ).fetchall()
    db.execute(
        'UPDATE serp_web_keyword_pool_versions SET cursor=%s, exhausted=%s WHERE version=%s',
        (rows[-1]['ordinal'] if rows else pool['cursor'], len(rows) < limit, pool['version']),
    )
    return [row['query'] for row in rows if row['picked']]


def main() -> None:
    import argparse
    from .config import Config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, default=LABELS)
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    store = CampaignStore(Config().database_url, initialize=False)
    print(json.dumps(install(store, args.labels.read_bytes(), activate=args.activate)))


if __name__ == '__main__':
    main()
