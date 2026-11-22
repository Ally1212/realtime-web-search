"""Durable Maps live-query phases; persistence precedes every write side effect."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from .middle_maps_contract import (
    CAPABILITIES, MAPS_MESSAGE_BUDGET, task_parameters, query_snapshot, snapshot_message, snapshot_places,
)
from .middle_ingest import receipt_query, processing_receipts
from .product import ProductError


class MapsPhases:
    """save(job,state) must commit before returning; caller owns job/leader locks."""
    def __init__(self, *, middle, collector, save, search):
        self.middle, self.collector, self.save, self.search = middle, collector, save, search

    def task_heartbeat(self, task_id):
        control = self.collector.post(
            "/v1/collection/tasks/" + task_id + "/heartbeat",
            dict(agent_id=self.collector.agent_id))
        if isinstance(control, dict) and control.get("control_signal", control.get("control")) in ("cancel", "stop", "pause"):
            raise ProductError("middle_controlled", "地图任务已由中台停止", 409)
        return control

    def advance(self, job, saved_state):
        state = deepcopy(saved_state or {})
        if not state.get('middle_query_id'):
            self.collector.register()
            state.update(self.middle.create(str(job['account_id']), job['parameters'], job['idempotency_key']))
            self.save(job, state)
            return None
        self.collector.heartbeat(1)
        status = self.middle.status_for_request(state['middle_query_id'], job['parameters'])
        if status['platform_status'] in ('failed', 'canceled'):
            raise ProductError(state.get('collection_error') or 'middle_maps_failed', '中台地图查询已失败或取消', 502)
        if not state.get('task'):
            tasks = self.collector.post('/v1/collection/tasks/claim', dict(
                agent_id=self.collector.agent_id, supported_task_types=['live_query'],
                supported_platforms=['google_maps'], declared_capabilities=CAPABILITIES, limit=1)).get('tasks') or []
            if not tasks:
                return None
            task = tasks[0]
            if (task.get('id') != state['platform_task_ids'][0] and getattr(self, 'direct', None) is not None):
                self.direct.accept(task)
                return None
            task_parameters(task, query_id=state['middle_query_id'],
                            task_id=state['platform_task_ids'][0], expected_parameters=job['parameters'])
            state['task'] = task
            self.save(job, state)
        task_id = state['platform_task_ids'][0]
        if state.get('collection_error'):
            # Persisted before notifying the platform: unknown fail responses
            # must never cause a new paid/provider search on restart.
            self.collector.post('/v1/collection/tasks/' + task_id + '/fail', dict(
                agent_id=self.collector.agent_id, message=state['collection_error'], cursor='', retry=False))
            raise ProductError(state['collection_error'], '地图采集失败，未写入零结果快照', 502)
        if not state.get('message'):
            parameters = task_parameters(state['task'], query_id=state['middle_query_id'],
                                         task_id=task_id, expected_parameters=job['parameters'])
            self.task_heartbeat(task_id)
            if not parameters.get('location'):
                from .maps_location import location_text, resolve
                resolution = state.get('location_resolution') or resolve(location_text(parameters['query']), region='', language=parameters['language'])
                parameters['location'] = resolution['location']
                state['location_resolution'] = resolution
                state['resolved_parameters'] = parameters
                self.save(job,state)
            import threading
            stopped, failures = threading.Event(), []
            def heartbeat():
                while not stopped.wait(10):
                    try:
                        self.task_heartbeat(task_id)
                    except Exception as exc:
                        failures.append(exc)
                        return
            self.task_heartbeat(task_id)
            pulse = threading.Thread(target=heartbeat, daemon=True)
            pulse.start()
            try:
                try:
                    result = self.search(parameters)
                finally:
                    stopped.set()
                    pulse.join()
                if failures:
                    raise failures[0]
                payload = query_snapshot(job_id=job['id'], query_id=state['middle_query_id'], task_id=task_id,
                                         parameters=parameters, result=result,
                                         observed_at=datetime.now(timezone.utc).isoformat())
            except ProductError as exc:
                if not exc.code.startswith('maps_'):
                    raise
                state.update(collection_error=exc.code, stage='collection_failed')
                self.save(job, state)
                return None
            if state.get('location_resolution'):
                payload['location_resolution'] = state['location_resolution']
                payload['original_request'] = state['task']['payload']
            state['message'] = snapshot_message(job_id=job['id'], task_id=task_id, payload=payload)
            state['stage'] = 'ingest_pending'
            self.save(job, state)
        if status['platform_status'] not in ('succeeded', 'partial'):
            self.task_heartbeat(task_id)
        message = state['message']
        row = dict(payload=message, dataset_id=message['dataset_id'], record_key=message['source']['source_record_key'])
        if not state.get('receipt'):
            receipt = processing_receipts([row], self.collector.post('/v1/ingest/receipts', [receipt_query(row)]))[0]
            if receipt['status'] == 'failed':
                raise ProductError('middle_persistence_failed', '地图快照中台入库失败', 502)
            pending_without_version = (
                receipt['status'] == 'pending' and not receipt.get('document_id') and not receipt.get('version_id')
            )
            legacy_rejected_candidate = (
                pending_without_version
                and message['content']['platform_payload'].get('schema_version') == 'searchflow-maps-query-2'
                and len(json.dumps([message]).encode()) > MAPS_MESSAGE_BUDGET
            )
            if receipt['status'] == 'unknown' or legacy_rejected_candidate or (
                    pending_without_version and state.get('repacked_after_413')):
                # Collector transport must attach X-Ingest-Class: realtime.
                try:
                    self.collector.upload_realtime([message])
                except ProductError as exc:
                    if getattr(exc, 'status_code', None) != 413:
                        raise
                    original = message['content']['platform_payload']
                    compact = snapshot_message(job_id=job['id'], task_id=task_id, payload=original)
                    if compact['source']['payload_hash'] == message['source']['payload_hash']:
                        raise ProductError('maps_snapshot_too_large', '中台拒绝地图快照大小', 502) from None
                    state['message'] = compact
                    state['repacked_after_413'] = True
                    self.save(job, state)
                else:
                    if state.pop('repacked_after_413', False):
                        self.save(job, state)
                return None
            if receipt['status'] not in ('accepted', 'duplicate'):
                return None
            if not receipt.get('document_id') or not receipt.get('version_id'):
                raise ProductError('middle_invalid_response', '地图入库回执缺少固定版本', 502)
            state.pop('repacked_after_413', None)
            state['receipt'] = receipt
            self.save(job, state)
        receipt = state['receipt']
        if status['platform_status'] not in ('succeeded', 'partial'):
            from .middle_live_runtime import collection_result
            self.collector.post('/v1/collection/tasks/' + task_id + '/complete', dict(
                agent_id=self.collector.agent_id, collected_count=1,
                ingested_count=int(receipt['status'] == 'accepted'),
                duplicate_count=int(receipt['status'] == 'duplicate'),
                result=collection_result(state, [receipt['document_id']])))
            return None
        results = self.middle.results_page(state['middle_query_id'])
        ids = {r['document'].get('id', r['document'].get('ID')) for r in results['items']}
        if results.get('next_cursor') or ids != {receipt['document_id']}:
            raise ProductError('middle_snapshot_mismatch', '地图实时查询结果与回执不一致', 502)
        payload = self.middle.read_snapshot(receipt['document_id'], receipt['version_id'],
                    expected_payload=message['content']['platform_payload'],
                    expected_hash=message['source']['payload_hash'])
        state.update(stage='readback_verified', readback_verified=True)
        self.save(job, state)
        return dict(payload=payload, receipt=receipt, middle_query_id=state['middle_query_id'], middle_verified=True)


def process_middle_pending(base, phases, *, limit=1):
    """Called by the single Maps leader. Existing completed snapshots stay readable."""
    from .google_local_jobs import LocalJobStore
    from .google_data import digest
    store = LocalJobStore(base)
    with base.connect() as db:
        # Resume running tasks before admitting queued tasks. No local fallback.
        rows = db.execute("SELECT * FROM sf_google_local_jobs WHERE status='running' ORDER BY created_at LIMIT %s", (limit,)).fetchall()
        if not rows:
            rows = db.execute("UPDATE sf_google_local_jobs SET status='running',started_at=now() WHERE id IN (SELECT id FROM sf_google_local_jobs WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s) RETURNING *", (limit,)).fetchall()
    for job in rows:
        try:
            retry_at = (job.get('middle_state') or {}).get('retry_not_before')
            if retry_at and datetime.fromisoformat(retry_at) > datetime.now(timezone.utc):
                continue
            # Upstream binding must be cancelled before releasing local ownership.
            # Browser-depth Maps queries can spend several minutes collecting
            # and then wait for platform ingestion/readback. Preserve the same
            # provider outcome for up to 30 minutes; never re-run the search.
            started = job.get('started_at') or job['created_at']
            if (datetime.now(timezone.utc) - started).total_seconds() > 1800:
                state = job.get('middle_state') or {}
                if state.get('middle_query_id'):
                    phases.middle.cancel(state['middle_query_id'])
                store.finish(job, error='middle_maps_deadline_exceeded')
                continue
            outcome = phases.advance(job, job.get('middle_state'))
            if outcome is None:
                continue
            payload = outcome['payload']
            if outcome.get('middle_verified') is not True:
                raise ProductError('middle_snapshot_mismatch', '地图结果未完成中台读取校验', 502)
            store.finish(job, result=dict(location_resolution=payload.get('location_resolution'),
                search_area={k:payload['query_conditions'][k] for k in ('location','radius_m')} if payload.get('query_conditions') else None,
                places=snapshot_places(payload), coverage=payload.get('collection_coverage'), provider=payload['provider']['name'],
                provider_version=payload['provider']['version'], ordering=payload['ordering'],
                middle_verified=True, notice='已从中台固定版本快照读取并校验。',
                provider_request_id=outcome['middle_query_id'], response_hash=digest(payload),
                field_mask=payload.get('schema_version','searchflow-maps-query-1'), sku='middle_maps_snapshot'))
        except ProductError as exc:
            if exc.code in ('middle_rate_limited', 'middle_transport_unknown', 'middle_collector_failed',
                            'middle_unavailable', 'middle_snapshot_unavailable', 'maps_geocoder_unavailable'):
                from datetime import timedelta
                state = store_middle_state(base, job)
                state['retry_not_before'] = (datetime.now(timezone.utc) + timedelta(
                    seconds=getattr(exc, 'retry_after', 5))).isoformat()
                store.save_middle_state(job, state)
                continue
            state = store_middle_state(base, job)
            if state.get('middle_query_id'):
                phases.middle.cancel(state['middle_query_id'])
            store.finish(job, error=exc.code)


def store_middle_state(base, job):
    with base.connect() as db:
        row = db.execute('SELECT middle_state FROM sf_google_local_jobs WHERE id=%s AND account_id=%s',
                         (job['id'], job['account_id'])).fetchone()
    return (row or {}).get('middle_state') or {}
