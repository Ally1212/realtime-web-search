import concurrent.futures
import json
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from realtime.campaign_store import CampaignStore, _POOLS, _INITIALIZED_DSNS
from realtime.serp_keyword_pool import LABELS, active_pool, claim_queries, install, shuffled_queries
from realtime.serp_store import SerpStore
from realtime.serp_console import ConsoleStore
from realtime.serp_worker import SerpSupply
from realtime.config import Config


def snapshot(version='candidate'):
    return json.dumps(dict(version=version, categories={'museums': {'labels': [
        dict(id='Q1', label='Example Museum'), dict(id='Q2', label='Other Museum')
    ]}})).encode()


@pytest.fixture
def store():
    root = os.environ.get('TEST_DATABASE_URL')
    if not root:
        pytest.skip('requires isolated PostgreSQL')
    schema = 'keyword_' + uuid4().hex
    with psycopg.connect(root) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    dsn = make_conninfo(root, options=f'-c search_path={schema}')
    try:
        store = CampaignStore(dsn)
        SerpStore(dsn).initialize()
        yield store, dsn
    finally:
        _INITIALIZED_DSNS.discard(dsn)
        for key in list(_POOLS):
            if key[0] == dsn:
                _POOLS.pop(key).close()
        with psycopg.connect(root) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_public_snapshot_is_reproducible():
    version, candidates = shuffled_queries(LABELS.read_bytes())
    assert version.startswith('wikidata-en-')
    assert len(candidates) > 6000
    assert (version, candidates) == shuffled_queries(LABELS.read_bytes())


def test_claim_is_global_atomic_persistent_and_does_not_cycle(store):
    base, dsn = store
    base.create_campaign(' Ｅxample  Museum HISTORY ', [], 1, 'direct', ['google_web'])
    install(base, snapshot(), activate=True)
    base.create_campaign('Other Museum Exhibitions', ['Example Museum Collections'], 1, 'direct', ['google_web'])

    def claim():
        with CampaignStore(dsn, initialize=False).connect() as db:
            return claim_queries(db, active_pool(db), 2)

    with pytest.raises(RuntimeError):
        with base.connect() as db:
            first = claim_queries(db, active_pool(db), 1)
            raise RuntimeError('simulated failed campaign creation')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(executor.map(lambda _: claim(), range(2)))
    claimed = sum(batches, [])
    assert len(claimed) == len(set(claimed)) == 3
    assert first[0] in claimed
    assert not {'Example Museum history','Other Museum exhibitions','Example Museum collections'} & set(claimed)
    assert claim() == []
    install(base, snapshot('new-version'), activate=True)
    assert claim() == []


def test_worker_claims_only_once_and_refills_without_repeating(store):
    base, dsn = store
    cfg = Config(database_url=dsn, default_proxy_profile='direct', crawler_slots=2,
                 serp_run_id='test-pool')
    console = ConsoleStore(dsn)
    console.initialize_console(cfg)
    console.save_settings(dict(continuous_enabled=True, continuous_sources=['google_web'], web_max_pages=11), cfg)
    install(base, snapshot(), activate=True)

    class Queue:
        def __init__(self):
            self.ids = []

        def enqueue(self, cid):
            self.ids.append(cid)

    queue = Queue()
    SerpSupply(cfg, base, queue).tick()
    assert len(queue.ids) == 6
    SerpSupply(cfg, CampaignStore(dsn, initialize=False), queue).tick()
    assert len(queue.ids) == 6
    with base.connect() as db:
        assert db.execute('SELECT count(DISTINCT query) AS n FROM campaigns').fetchone()['n'] == 6
        db.execute("UPDATE campaigns SET status='stopped'")
    SerpSupply(cfg, base, queue).tick()
    with base.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM campaigns').fetchone()['n'] == 6


def test_cooling_queries_keep_page_progress_without_consuming_ready_capacity(store):
    from realtime.serp_keyword_pool import refill_capacity

    base, dsn = store
    cid = base.create_campaign('Example Museum', [], 1, 'direct', ['google_web'])
    with base.connect() as db:
        db.execute("INSERT INTO serp_supply_runs(run_id,keyword_offset) VALUES('refill',0)")
        db.execute("INSERT INTO serp_supply_campaigns(run_id,campaign_id,sequence) VALUES('refill',%s,0)", (cid,))
        db.execute("""INSERT INTO google_page_frontier(campaign_id,query_hash,query,locale,max_page,next_page,state,next_run_at)
                      VALUES(%s,%s,'Example Museum','en',11,4,'cooling',now()+interval '20 minutes')""", (cid, 'a'*64))
        assert refill_capacity(db, 'refill', 4) == 8
        frontier = db.execute('SELECT next_page,state FROM google_page_frontier WHERE campaign_id=%s', (cid,)).fetchone()
        assert (frontier['next_page'], frontier['state']) == (4, 'cooling')
        db.execute("INSERT INTO discovery_source_runtime(source,circuit_until) VALUES('google_openserp',now()+interval '1 minute')")
        assert refill_capacity(db, 'refill', 4) == 0
