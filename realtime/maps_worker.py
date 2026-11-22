"""Single leader for manually requested Maps searches, separate from SERP worker."""
import os
import time
import logging
from .product import ProductStore
from .google_local_jobs import LocalJobStore, SCHEMA
from .middle_maps_runtime import MapsPhases, process_middle_pending
from .middle_platform_client import MapsMiddlePlatformClient
from .middle_maps_collector import MapsCollector
from .maps_provider import search, config
from pathlib import Path


def main():
    os.environ['SEARCHFLOW_MAPS_WORKER']='true'
    store=ProductStore(os.environ['DATABASE_URL'])
    settings = config()
    base_url = settings.get('middle_base_url')
    if not base_url:
        raise RuntimeError('Maps middle endpoint is required; local fallback is disabled')
    consumer = Path(settings.get('consumer_key_file', '/app/state/private/maps-middle-consumer-key')).read_text().strip()
    collector = Path(settings.get('collector_key_file', '/app/state/private/maps-middle-collector-key')).read_text().strip()
    phases = MapsPhases(middle=MapsMiddlePlatformClient(base_url, consumer),
                        collector=MapsCollector(base_url, collector),
                        save=LocalJobStore(store.base).save_middle_state, search=search)
    with store.base.connect() as db:db.execute(SCHEMA)
    with store.base.connect() as leader:
        if not leader.execute('SELECT pg_try_advisory_lock(20261111,21) acquired').fetchone()['acquired']:
            raise RuntimeError('Maps worker already running')
        leader.commit()
        phases.collector.register()
        from .middle_direct import DirectTasks
        direct = DirectTasks(store.base, phases.collector, source='google_maps', search=search,
                             deadline_lookup=phases.middle.collection_deadline)
        phases.direct = direct
        next_heartbeat = 0
        while True:
            try:
                if time.monotonic() >= next_heartbeat:
                    with store.base.connect() as db:
                        busy = db.execute("SELECT EXISTS(SELECT 1 FROM sf_google_local_jobs WHERE status='running') busy").fetchone()['busy']
                    phases.collector.heartbeat(int(busy))
                    next_heartbeat = time.monotonic() + 15
                with store.base.connect() as db:
                    local_busy = db.execute("SELECT EXISTS(SELECT 1 FROM sf_google_local_jobs WHERE status IN('queued','running')) busy").fetchone()['busy']
                if not direct.poll(allow_claim=not local_busy):
                    process_middle_pending(store.base, phases)
            except Exception:logging.exception('Maps queue iteration failed')
            time.sleep(2)

if __name__=='__main__':main()
