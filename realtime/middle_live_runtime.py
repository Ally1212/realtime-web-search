"""Durable, website-owned Google live-query runner. No global collection switches.

Run inside the collector network with its existing configuration and a separately
mounted Consumer key. Only website-owned task IDs may be executed. Every external
side effect has a persisted identity before the next phase; uploads reuse events.
"""
from __future__ import annotations
import copy
import json
import logging
import os
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb
from .config import Config
from .product import ProductStore, ProductError, now
from .middle_platform_client import SearchMiddlePlatformClient, SOURCE_PLATFORMS
from .google_whale_readback import WhaleReadbackClient, ReadbackTarget
from .google_data import digest
from .customer_data import project
from .middle_ingest import WhaleLinkClient, link_message, receipt_query, processing_receipts, task_spec, collector_capabilities
from .campaign_store import CampaignStore
from .serp_worker import run_serp_campaign
from .serp_fields import result_items, normalize_serp_url
from .proxy_pool import ProxyPool
from .live_collection import (
    COLLECTION_ERROR_CODES, RETRYABLE_COLLECTION_CODES, WEB_DELIVERY_RESERVE_SECONDS,
    can_deliver_web_results, check_collection, collection_error, needs_collection, recover_web_pages, retry_interval,
    wait_for_web_capacity,
)

LOG = logging.getLogger(__name__)


def bind_live_observation(message, *, query_id, task_id, record):
    """A query observation is distinct from the globally deduplicated URL."""
    envelope = dict(schema_version='searchflow-live-links-1', middle_query_id=query_id,
                    collection_task_id=task_id, record=copy.deepcopy(record),
                    platform_metrics=dict(result_count=len(record['occurrences'])))
    message = copy.deepcopy(message)
    if 'assets' in message:
        envelope['assets'] = copy.deepcopy(message['assets'])
    # Keep source/canonical URLs, but do not reuse a previous query's document.
    identity = dict(query_id=query_id, task_id=task_id,
                    external_id=message['content']['external_content_id'])
    message['content']['external_content_id'] = 'searchflow-live:' + digest(identity)
    message['content']['platform_payload'] = envelope
    message['source']['payload_hash'] = 'sha256:' + digest(envelope)
    message['metrics'] = dict(snapshot_at=record['completed_at'], platform_metrics=envelope['platform_metrics'])
    message['provided_capabilities'] = list(dict.fromkeys([*message.get('provided_capabilities', []), 'identity', 'title', 'metrics']))
    return message


def verified_record(reader, state, dataset, source_platform='google_search'):
    """Download payload must come from immutable Consumer versions, never metadata."""
    records, proofs = [], []
    for message, receipt in zip(state['messages'], state['receipts'], strict=True):
        if receipt['status'] not in ('accepted', 'duplicate'):
            raise ValueError('Persistence has not completed')
        target = ReadbackTarget(receipt['document_id'], receipt['version_id'], dataset,
                               source_platform, expected_platform_payload_hash=digest(message['content']['platform_payload']))
        document, version = reader._read_pair(target)
        if not isinstance(version.get('platform_payload'), dict):
            source = message['content']['platform_payload']['record']['source']
            if SOURCE_PLATFORMS.get(source) != source_platform:
                raise ValueError('Pinned payload source differs')
            client = SearchMiddlePlatformClient(reader.base_url, reader._key, source=source, dataset_id=dataset)
            payload = client.read_snapshot(target.document_id, target.version_id,
                expected_payload=message['content']['platform_payload'],
                expected_hash=message['source']['payload_hash'])
            version = {**version, 'platform_payload': payload}
        proof = reader._proof(target, document, version)
        if document.get('external_content_id') != message['content'].get('external_content_id'):
            raise ValueError('Pinned result external identity differs')
        proof.update(terminal_receipt_verified=True, receipt_status=receipt['status'],
                     platform_payload_sha256=target.expected_platform_payload_hash)
        payload = version['platform_payload']
        if (payload.get('schema_version') != 'searchflow-live-links-1'
                or payload.get('middle_query_id') != state['middle_query_id']
                or payload.get('collection_task_id') != state['platform_task_ids'][0]):
            raise ValueError('Pinned payload task identity differs')
        records.append(payload['record']); proofs.append(proof)
    if not records or any(digest(r) != digest(records[0]) for r in records):
        raise ValueError('Pinned observation differs across links')
    record = copy.deepcopy(records[0])
    if len(record['occurrences']) != len(records):
        raise ValueError('Pinned result count differs')
    record.update(provenance='middle_consumer_pinned_versions', middle_query_id=state['middle_query_id'],
                  readback_verified=True, middle_versions=proofs)
    return record


def selected_observation(row, source, limit):
    """Bound delivery without changing saved results or image URL identities."""
    if type(limit) is not int or limit < 1 or (source == 'google_images' and limit > 20):
        raise ValueError('Invalid result limit')
    selected, seen = [], set()
    fields = {'google_web': 'organic_results', 'google_news': 'news_results',
              'google_images': 'image_results'}
    candidates = row['response'].get(fields[source], []) if source in fields else result_items(row['response'])
    for item in candidates:
        url = (item.get('image_url') or item.get('link') or '') if source == 'google_images' else normalize_serp_url(item.get('normalized_link') or item.get('link') or '')
        if url and url not in seen:
            seen.add(url)
            selected.append(copy.deepcopy(item))
        if len(selected) >= limit:
            break
    if not selected:
        raise ProductError('middle_empty_results', '本次搜索没有可入库链接', 409)
    raw = copy.deepcopy(row)
    if source in fields:
        raw['response'][fields[source]] = selected
    else:
        delivered = set()
        for topic in raw['response'].get('trending_searches') or []:
            articles = []
            for item in topic.get('articles') or []:
                url = normalize_serp_url(item.get('normalized_link') or item.get('link') or '')
                if url in seen and url not in delivered:
                    articles.append(item)
                    delivered.add(url)
            topic['articles'] = articles
    return selected, raw


def selected_pages(rows, source, limit):
    """Select bounded unique links while retaining each original request envelope.

    Rows must be in execution/page order. Never combine later-page fields into
    the first response: rank, features and request identity remain page-scoped.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError('Invalid result limit')
    seen, selections = set(), []
    remaining = limit
    for row in rows:
        try:
            page_limit = limit if source == 'google_images' else max(limit, len(result_items(row['response'])))
            candidates, _ = selected_observation(row, source, page_limit)
        except ProductError as exc:
            if exc.code != 'middle_empty_results':
                raise
            continue
        selected = []
        for item in candidates:
            url = ((item.get('image_url') or item.get('link') or '') if source == 'google_images'
                   else normalize_serp_url(item.get('normalized_link') or item.get('link') or ''))
            if url in seen:
                continue
            seen.add(url)
            selected.append(item)
            remaining -= 1
            if not remaining:
                break
        if selected:
            raw = copy.deepcopy(row)
            field = {'google_web': 'organic_results', 'google_news': 'news_results',
                     'google_images': 'image_results'}.get(source)
            if field:
                raw['response'][field] = selected
            else:
                delivered = set()
                selected_urls = {normalize_serp_url(i.get('normalized_link') or i.get('link') or '') for i in selected}
                for topic in raw['response'].get('trending_searches') or []:
                    articles = []
                    for item in topic.get('articles') or []:
                        url = normalize_serp_url(item.get('normalized_link') or item.get('link') or '')
                        if url in selected_urls and url not in delivered:
                            articles.append(item)
                            delivered.add(url)
                    topic['articles'] = articles
            selections.append((row, selected, raw))
        if not remaining:
            break
    return selections


def collection_coverage(rows, requested, page_budget, delivered, *, deadline_exceeded=False):
    """Describe observed work without inferring upstream exhaustion or relevance."""
    pages = sorted({r['page'] for r in rows})
    successful = sorted({r['page'] for r in rows if r.get('classification') in ('results', 'empty')})
    attempted_failures = {r['page'] for r in rows if r.get('classification') not in ('results', 'empty')}
    failed = sorted(attempted_failures - set(successful))
    recovered = sorted(attempted_failures & set(successful))
    expected = set(range(1, page_budget + 1))
    missing = sorted(expected - set(successful))
    empty = any(r.get('classification') == 'empty' for r in rows)
    scope_complete = not deadline_exceeded and not missing
    reason = ('max_items_reached' if delivered >= requested else 'deadline_exceeded' if deadline_exceeded
              else 'empty_page' if empty
              else 'partial_failure' if failed
              else 'page_budget_exhausted' if len(pages) >= page_budget
              else 'collection_incomplete')
    coverage = dict(requested_max_items=requested, delivered_entities=delivered,
                entity_unit='links', pages_searched=pages, page_budget=page_budget,
                stop_reason=reason, has_more=None, deadline_exceeded=deadline_exceeded,
                partial_failure=bool(failed), scope_complete=scope_complete,
                successful_pages=successful, failed_pages=failed, missing_pages=missing,
                retry_recovered_pages=recovered, request_attempt_count=len(rows),
                failure_observation='persisted_request_classifications',
                failures=[dict(page=r['page'], classification=r.get('classification'),
                    http_status=r.get('http_status'), error_code=r.get('error_code'), recovered=r['page'] in successful)
                    for r in rows if r.get('classification') not in ('results', 'empty')],
                relevance_verified=False)
    series = next((row['response']['keyword_trends'] for row in rows
                   if isinstance((row.get('response') or {}).get('keyword_trends'), dict)), None)
    if series is not None and delivered:
        coverage.update(entity_unit='trend_series', point_count=series.get('point_count'),
                        stop_reason='trend_series_collected')
    return coverage


def collection_result(state, document_ids):
    """Report delivery scope alongside receipt-confirmed document IDs."""
    result = dict(document_ids=document_ids)
    if 'web_partial' in state:
        result.update(partial=state['web_partial'], returned_count=len(document_ids))
    messages = state.get('messages') or ([state['message']] if 'message' in state else [])
    if messages:
        payload = messages[0]['content'].get('platform_payload') or {}
        coverage = payload.get('collection_coverage')
        record_coverage = (payload.get('record') or {}).get('collection_coverage') or {}
        if 'web_partial' in state or record_coverage.get('entity_unit') == 'trend_series':
            coverage = record_coverage
        if coverage:
            result.update(collection_coverage=coverage, stop_reason=coverage.get('stop_reason'))
    return result


def observation_quality(record):
    """Field availability is measurable; relevance needs a separate review."""
    items = record.get('occurrences') or []
    counts = dict(title=0, snippet=0, link=0, publisher=0)
    for item in items:
        counts['title'] += bool(item.get('title'))
        counts['snippet'] += bool(item.get('snippet'))
        counts['link'] += bool((item.get('url') or {}).get('original_url'))
        counts['publisher'] += bool((item.get('source_fields') or {}).get('publisher'))
    result = dict(entity_count=len(items), entity_unit='links', field_present_counts=counts,
                relevance_status='not_independently_verified',
                publisher_page_verified=False, article_body_collected=False,
                ordering='rss_feed_order' if record.get('source') == 'google_news' else 'source_observed_order')

    series=[t.get('numeric_trend_series') for t in record.get('topics',[]) if isinstance(t.get('numeric_trend_series'),dict)]
    if series:result.update(entity_unit='keyword_trend_series',entity_count=len(series),time_point_count=sum(len(s.get('points',[])) for s in series),measurement='relative_interest_index_0_100',ordering='chronological_utc')
    return result


def combined_observation(selections):
    """Keep the download envelope shared while retaining page-scoped evidence."""
    records = [{**project(raw), **({'page': raw['page']} if 'page' in raw else {})} for _, _, raw in selections]
    if not records:
        raise ValueError('No selected observations')
    record = copy.deepcopy(records[0])
    record['delivery_scope'] = 'selected_link_observations'
    if len(records) > 1:
        record['page_observations'] = records
        record['request_ids'] = [r['request_id'] for r in records]
        record['occurrences'] = [item for r in records for item in r['occurrences']]
        record['topics'] = [item for r in records for item in r.get('topics', [])]
        record['completed_at'] = max(r['completed_at'] for r in records)
        record['envelope_scope'] = 'first_page_metadata_with_combined_occurrences'
        record['source_response_hash_scope'] = 'first_page'
        record['page_response_hashes'] = [dict(request_id=r['request_id'],
            source_response_hash=r.get('source_response_hash')) for r in records]
        if any(r.get('field_adaptation') for r in records):
            adaptation = copy.deepcopy(record.get('field_adaptation') or {})
            for section in ('items', 'topics', 'features'):
                adaptation[section] = [item for r in records
                    for item in (r.get('field_adaptation') or {}).get(section, [])]
            record['field_adaptation'] = adaptation
    record['quality_observation'] = observation_quality(record)
    return record


def owned_claim_job(task, jobs, config):
    """Resolve a platform claim to an already bound website job, across Google modes."""
    for job in jobs:
        state = job.get('response') or {}
        if state.get('platform_task_ids') != [task.get('id')]:
            continue
        if state.get('middle_query_id') != task.get('payload', {}).get('live_query_id'):
            raise ValueError('Claim query identity differs')
        source = job['parameters'].get('source', 'google_web')
        spec = task_spec(task, config, source=source)
        expected_count = job['parameters'].get('max_items', job['parameters'].get('depth', 20 if source == 'google_images' else 10))
        if spec['max_items'] != expected_count:
            raise ValueError('Claim result limit differs')
        for field in ('query', 'region', 'language'):
            if task['payload'].get(field) != job['parameters'].get(field):
                raise ValueError('Claim parameters differ')
        return job
    return None


class LiveRunner:
    def __init__(self, config, consumer_key, *, source=None):
        self.config = replace(config, whale_supported_task_types=('live_query',),
                              content_fetch_enabled=False, google_web_max_pages=1, google_web_pages_per_batch=1)
        self.store = ProductStore(config.database_url)
        self.links = CampaignStore(config.database_url, initialize=False)
        self.proxy_pool = ProxyPool(config) if config.default_proxy_profile != 'direct' else None
        self.source = source or {'google_search':'google_web', 'google_news':'google_news', 'google_trends':'google_trends'}.get(config.whale_source_platform)
        if self.source is None or SOURCE_PLATFORMS.get(self.source) != config.whale_source_platform:
            raise ValueError('Unsupported middle runner source')
        self.collector = WhaleLinkClient(self.config, source='google_images' if config.whale_source_platform == 'google_search' else self.source)
        self.middle = SearchMiddlePlatformClient(self.collector.base_url, consumer_key,
                                                source=self.source, dataset_id=config.whale_dataset_id)
        self.reader = WhaleReadbackClient(self.collector.base_url, consumer_key, min_interval_seconds=2)

    def stop_campaign(self, state, status='canceled'):
        if state.get('local_campaign_id'):
            with self.store.base.connect() as db:
                db.execute("UPDATE campaigns SET status='stopped' WHERE id=%s", (state['local_campaign_id'],))
                db.execute('UPDATE whale_task_runs SET status=%s,updated_at=now() WHERE campaign_id=%s', (status,state['local_campaign_id']))

    def save(self, job, state):
        with self.store.base.connect() as db:
            db.execute("UPDATE sf_jobs SET response=%s,status=CASE WHEN status='queued' THEN 'running' ELSE status END WHERE id=%s AND status IN('queued','running','cancelled')", (Jsonb(state), job['id']))

    def heartbeat(self, job, state):
        current = self.store.job(job['account_id'], str(job['id']))
        if current['status'] == 'cancelled' or job['deadline_at'] < now():
            self.middle.cancel(state['middle_query_id'])
            self.stop_campaign(state)
            raise ProductError('cancelled', '中台请求已取消', 409)
        result = self.collector.post('/v1/collection/tasks/'+state['platform_task_ids'][0]+'/heartbeat',
                                     dict(agent_id=self.collector.agent_id))
        # A platform control signal is not permission to finish a cancelled task.
        if result.get('control_signal', result.get('control')) in ('cancel', 'stop', 'pause'):
            raise ProductError('middle_controlled', '中台任务已暂停或取消', 409)

    def fail_collection(self, job, state, code):
        try:
            self.collector.post('/v1/collection/tasks/'+state['platform_task_ids'][0]+'/fail',
                                dict(agent_id=self.collector.agent_id, message=code, retry=False))
        except RuntimeError as exc:
            if getattr(exc, 'status_code', None) != 409:
                raise
            status = self.middle.status(state['middle_query_id'])['platform_status']
            if status not in ('failed', 'canceled'):
                if job['deadline_at'] > now():
                    raise
                self.middle.cancel(state['middle_query_id'])
        self.stop_campaign(state, 'failed')
        self.store.finish(job['id'], 'failed', state, code)

    def collect(self, job, state):
        spec = task_spec(state['task'], self.config, source=self.source)
        with self.store.base.connect() as db:
            owned = db.execute('SELECT campaign_id,status FROM sf_jobs WHERE id=%s FOR UPDATE', (job['id'],)).fetchone()
            if owned['status'] not in ('queued','running'):
                raise ProductError('cancelled','请求已取消',409)
            cid = str(owned['campaign_id']) if owned['campaign_id'] else str(uuid4())
            if not owned['campaign_id']:
                db.execute("INSERT INTO campaigns(id,query,aliases,sources,daily_target,proxy_profile,status) VALUES(%s,%s,'[]',%s,10,%s,'active')", (cid,job['parameters']['query'],Jsonb([self.source]),self.config.default_proxy_profile))
                db.execute("INSERT INTO whale_task_runs(task_id,campaign_id,dataset_id,source_platform,task_type,payload,status) VALUES(%s,%s,%s,%s,'live_query',%s,'running')", (state['platform_task_ids'][0],cid,self.config.whale_dataset_id,self.config.whale_source_platform,Jsonb(state['task']['payload'])))
                db.execute('INSERT INTO serp_task_options(campaign_id,options) VALUES(%s,%s)', (cid,Jsonb(spec['options'])))
                db.execute('UPDATE sf_jobs SET campaign_id=%s WHERE id=%s', (cid,job['id']))
            db.execute("UPDATE campaigns SET status='active' WHERE id=%s", (cid,))
        # No legacy whale_serp_jobs/outbox entry: the old runner cannot heartbeat,
        # complete or change registration for these independently owned tasks.
        state['local_campaign_id'] = cid
        state['stage'] = 'collecting'
        local = dict(campaign_id=cid, specification=spec)
        self.save(job,state)
        def saved():
            with self.links.connect() as db:
                return db.execute("SELECT r.* FROM serp_campaign_requests cr JOIN serp_requests r USING(request_id) WHERE cr.campaign_id=%s ORDER BY page, completed_at, request_id", (local['campaign_id'],)).fetchall()
        rows = saved()
        collection_errors = ()
        stop_at = job['deadline_at'].timestamp() - (WEB_DELIVERY_RESERVE_SECONDS if self.source == 'google_web' else 0)
        initial_pass = needs_collection(self.source, rows, spec['google_pages'])
        if time.time() < stop_at and (initial_pass or self.source == 'google_web'):
            pages = {r['page'] for r in rows if r['classification'] == 'results'}
            stopped = threading.Event(); errors = []
            def pulse():
                while not stopped.wait(10):
                    try: self.heartbeat(job, state)
                    except Exception as exc:
                        errors.append(exc)
                        with self.links.connect() as db:
                            db.execute("UPDATE campaigns SET status='paused' WHERE id=%s", (local['campaign_id'],))
                        return
            thread = threading.Thread(target=pulse, daemon=True); thread.start()
            collection_config = replace(self.config, google_web_max_pages=spec['google_pages'],
                                        google_web_pages_per_batch=spec['google_pages'],
                                        content_fetch_enabled=False)
            resume = {'skip_web_pages': pages} if self.source == 'google_web' and pages else {}
            if getattr(self, 'proxy_pool', None) is not None:
                resume['proxy_pool'] = self.proxy_pool
            if self.source == 'google_web':
                resume['collection_stop_at'] = stop_at
            if self.source in ('google_news', 'google_trends'):
                def show_proxy_switch():
                    if state.get('stage') != 'switching_proxy':
                        state.update(stage='switching_proxy')
                        self.save(job, state)
                resume['on_proxy_switch'] = show_proxy_switch
            def run(**overrides):
                return run_serp_campaign(str(local['campaign_id']), collection_config,
                                         should_continue=lambda: time.time() < stop_at and not errors,
                                         **(resume | overrides))
            try:
                if initial_pass:
                    collection_errors = run()
                    rows = saved()
                if self.source == 'google_web':
                    rows, collection_errors = recover_web_pages(
                        rows, collection_errors, spec=spec, state=state, save=lambda: self.save(job, state),
                        run=run, read=saved, can_start=lambda: time.time()+5 < stop_at and not errors)
            finally: stopped.set(); thread.join()
            if errors: raise errors[0]
        cutoff = time.time() >= stop_at
        if cutoff and not collection_errors:
            collection_errors = (state.get('collection_retry_code') or 'search_deadline_exceeded',)
        candidates = [r for r in rows if r['classification'] in ('results', 'empty')]
        selections = selected_pages(candidates, self.source, state['task']['payload']['max_items'])
        count = sum(len(items) for _, items, _ in selections)
        if self.source == 'google_web':
            wait_for_web_capacity(collection_errors, state=state, count=count, requested=spec['max_items'],
                                  remaining=stop_at-time.time())
        deliver_saved = self.source == 'google_web' and can_deliver_web_results(
            rows, count, spec['max_items'], collection_finished=cutoff)
        check_collection(self.source, rows, collection_errors, spec['google_pages'], deliver_saved=deliver_saved)
        if not selections:
            raise ProductError('middle_empty_results', '本次搜索没有可入库链接', 409)
        record = combined_observation(selections)
        coverage = collection_coverage(rows, spec['max_items'], spec['google_pages'], count,
                                       deadline_exceeded=cutoff)
        record['collection_coverage'] = coverage
        if self.source == 'google_web':
            if collection_errors:
                coverage['interruption_code'] = collection_error(rows, collection_errors)
            coverage['recovery_rounds'] = state.get('web_recovery_rounds', 0)
            state['web_partial'] = 0 < count < spec['max_items']
        messages = []
        for row, selected, raw in selections:
            for item in selected:
                _, message = link_message(row, item, self.config, state['platform_task_ids'][0])
                message = bind_live_observation(message, query_id=state['middle_query_id'],
                                                task_id=state['platform_task_ids'][0], record=record)
                message['discovery']['acquisition_type'] = 'live_query'
                message['discovery']['collector'] = self.collector.agent_id
                messages.append(message)
        state.update(messages=messages, stage='ingest_pending')
        state.pop('collection_retry_at', None)
        state.pop('collection_retry_code', None)
        # Persist event keys/payload BEFORE submitting. Restart reuses these.
        self.save(job, state)
        with self.links.connect() as db:
            db.execute("UPDATE campaigns SET status='paused' WHERE id=%s", (local['campaign_id'],))

    def upload(self, state):
        # No global uploads flag and no unrelated outbox: only this owned batch.
        r = self.collector.session.post(self.collector.base_url+'/v1/documents/bulk',
                json=state['messages'], headers={'Authorization':'Bearer '+self.config.whale_collector_api_key,
                                                'X-Ingest-Class':'realtime'}, timeout=20, allow_redirects=False)
        if r.status_code in (400,401,403):
            raise ProductError('middle_ingest_rejected', '中台拒绝入库，请检查任务契约', 503)
        if r.status_code not in (200, 202):
            raise ProductError('middle_ingest_'+str(r.status_code), '中台入库尚未确认', 503)
        response = r.json()
        if not isinstance(response, list) or len(response) != len(state['messages']):
            raise ValueError('Bulk response length differs')

    def advance(self, job):
        state = job['response'] or {}
        if not state.get('middle_query_id'):
            if job['status'] == 'cancelled': return
            if job['deadline_at'] < now():
                self.store.finish(job['id'], 'failed', error='middle_submission_deadline_exceeded'); return
            self.collector.register()
            p = {k:v for k,v in job['parameters'].items() if k in ('query','source','region','language','trends_mode','trends_time_range','max_items')}
            state = self.middle.create(str(job['account_id']), p, job['idempotency_key'])
            self.save(job, state)
        if job['status'] == 'cancelled' or job['deadline_at'] < now():
            code = state.get('collection_failure_code') or state.get('collection_retry_code')
            if job['status'] != 'cancelled' and code and state.get('task'):
                self.fail_collection(job, state, code)
                return
            self.middle.cancel(state['middle_query_id'])
            self.stop_campaign(state)
            if job['status'] != 'cancelled':
                self.store.finish(job['id'], 'failed', state, code or 'search_deadline_exceeded')
            else:
                with self.store.base.connect() as db:
                    db.execute("UPDATE sf_jobs SET response=response||%s WHERE id=%s", (Jsonb(dict(stage='cancelled_synced')), job['id']))
            return
        self.collector.heartbeat(1)
        status = self.middle.status(state['middle_query_id'])
        if status['platform_status'] in ('failed','canceled'):
            self.stop_campaign(state, 'failed' if status['platform_status']=='failed' else 'canceled')
            code = (state.get('collection_failure_code') or state.get('collection_retry_code')
                    if status['platform_status'] == 'failed' else None)
            self.store.finish(job['id'], 'failed' if status['platform_status']=='failed' else 'cancelled', state, code or 'middle_'+status['platform_status']); return
        if self.source == 'google_web' and status.get('deadline_at') and status['platform_status'] not in ('succeeded','partial'):
            deadline = min(job['deadline_at'], datetime.fromisoformat(status['deadline_at']))
            if deadline <= now():
                if state.get('task'):
                    self.fail_collection(job, state, 'search_deadline_exceeded')
                else:
                    self.middle.cancel(state['middle_query_id'])
                    self.store.finish(job['id'], 'failed', state, 'search_deadline_exceeded')
                return
            job = dict(job, deadline_at=deadline)
        if status['platform_status'] not in ('succeeded','partial'):
            if not state.get('task'):
                self.collector.register()
                claimed = self.collector.post('/v1/collection/tasks/claim', dict(agent_id=self.collector.agent_id,
                    supported_task_types=['live_query'], supported_platforms=[self.config.whale_source_platform],
                    declared_capabilities=collector_capabilities(self.collector.source), limit=1)).get('tasks') or []
                if not claimed: return
                task = claimed[0]
                if (task['id'] != state['platform_task_ids'][0]
                        or task['payload']['live_query_id'] != state['middle_query_id']):
                    # Shared Google platform: bind to its owned job, not the
                    # job whose iteration happened to perform the claim.
                    with self.store.base.connect() as db:
                        candidates = db.execute("SELECT * FROM sf_jobs WHERE parameters->>'provider'='middle' AND status IN('queued','running') AND response->'platform_task_ids' ? %s", (task['id'],)).fetchall()
                    target = owned_claim_job(task, candidates, self.config)
                    if target is None:
                        self.direct.accept(task)
                        return
                    target_state = copy.deepcopy(target['response'])
                    target_state['task'] = {k:task[k] for k in ('id','task_type','dataset_id','source_platform','payload','required_capabilities') if k in task}
                    self.save(target, target_state)
                    return
                state['task'] = {k:task[k] for k in ('id','task_type','dataset_id','source_platform','payload','required_capabilities') if k in task}; self.save(job,state)
            try: self.heartbeat(job, state)
            except RuntimeError as exc:
                if getattr(exc,'status_code',None)==409:
                    state.pop('task',None);self.save(job,state)
                raise
            if not state.get('messages'):
                if state.get('collection_retry_at', 0) > time.time():
                    return
                code = state.get('collection_failure_code')
                if not code:
                    try:
                        self.collect(job,state)
                    except ProductError as exc:
                        if exc.code not in COLLECTION_ERROR_CODES or self.source == 'google_images':
                            raise
                        if exc.code in RETRYABLE_COLLECTION_CODES:
                            delay = retry_interval(exc.code)
                            state.update(stage='waiting_for_collection', collection_retry_code=exc.code,
                                         collection_retry_at=min(time.time()+delay,
                                             job['deadline_at'].timestamp()-WEB_DELIVERY_RESERVE_SECONDS)
                                             if self.source=='google_web' else time.time()+delay)
                            self.save(job, state)
                            return
                        code = exc.code
                        state.update(stage='collection_failure_pending', collection_failure_code=code)
                        self.save(job, state)
                if code:
                    self.fail_collection(job, state, code)
                    return
            self.heartbeat(job,state)
            rows=[dict(payload=m, dataset_id=m['dataset_id'], record_key=m['source']['source_record_key']) for m in state['messages']]
            entries=processing_receipts(rows,self.collector.post('/v1/ingest/receipts',[receipt_query(r) for r in rows]))
            if any(e['status']=='failed' for e in entries):
                raise ProductError('middle_persistence_failed','中台持久化失败',503)
            if any(e['status'] in ('unknown',) for e in entries):
                self.upload(state)
                return
            if any(e['status'] not in ('accepted','duplicate') for e in entries): return
            state['receipts']=entries;self.save(job,state)
            self.heartbeat(job,state)
            ids=list(dict.fromkeys(e['document_id'] for e in entries))
            result = collection_result(state, ids)
            try:
                self.collector.post('/v1/collection/tasks/'+state['platform_task_ids'][0]+'/complete',
                    dict(agent_id=self.collector.agent_id,collected_count=len(entries),
                         ingested_count=sum(e['status']=='accepted' for e in entries),
                         duplicate_count=sum(e['status']=='duplicate' for e in entries),
                         cursor=state['local_campaign_id'],result=result))
            except RuntimeError as exc:
                if getattr(exc,'status_code',None)==400:
                    raise ProductError('middle_completion_invalid', '中台完成校验失败，已停止重试', 503) from None
                raise
            status=self.middle.status(state['middle_query_id'])
        if status['platform_status'] not in ('succeeded','partial'): return
        # Independent authorization check through live results, then pin receipt
        # versions (not whichever version happens to be current now).
        results=self.middle.results_page(state['middle_query_id'],limit=100)
        returned={i['document'].get('ID',i['document'].get('id')) for i in results['items']}
        if returned != {e['document_id'] for e in state['receipts']}:
            raise ValueError('Consumer live result identity differs')
        record=verified_record(self.reader,state,self.config.whale_dataset_id,self.config.whale_source_platform)
        response=dict(provider='middle',middle_query_id=state['middle_query_id'],
                      platform_task_ids=state['platform_task_ids'],readback_verified=True,
                      download_ready=True,stage='readback_verified',middle_record=record,
                      fetched_at=record['completed_at'], persistence_receipts=state['receipts'])
        self.stop_campaign(state, 'succeeded')
        self.store.finish(job['id'],'succeeded',response)

    def run(self):
        with self.store.base.connect() as leader:
            lock_id = {'google_search': 11, 'google_news': 12, 'google_trends': 13}[SOURCE_PLATFORMS[self.source]]
            if not leader.execute('SELECT pg_try_advisory_lock(20261111,%s) acquired', (lock_id,)).fetchone()['acquired']:
                raise RuntimeError('Middle runner is already running')
            leader.commit()
            self.collector.register()
            sources = ('google_web', 'google_images') if SOURCE_PLATFORMS[self.source] == 'google_search' else (self.source,)
            runners = {}
            for source in sources:
                if source == self.source:
                    runners[source] = self
                    continue
                runner = copy.copy(self)
                runner.source = source
                runner.middle = SearchMiddlePlatformClient(self.middle.base_url, self.middle._key,
                    source=source, dataset_id=self.config.whale_dataset_id)
                runners[source] = runner
            from .middle_direct import DirectTasks
            direct_source = 'google_web' if SOURCE_PLATFORMS[self.source] == 'google_search' else self.source
            middle = getattr(self, 'middle', None)
            lookup = middle.collection_deadline if middle is not None and direct_source == 'google_web' else None
            self.direct = DirectTasks(self.store.base, self.collector, source=direct_source,
                                      config=self.config, deadline_lookup=lookup)
            for runner in runners.values():
                runner.direct = self.direct
            next_heartbeat = 0
            while True:
                with self.store.base.connect() as db:
                    jobs=db.execute("SELECT * FROM sf_jobs WHERE parameters->>'provider'='middle' AND coalesce(parameters->>'source','google_web')=ANY(%s) AND (status IN('queued','running') OR (status='cancelled' AND response IS NOT NULL AND response->>'stage'<>'cancelled_synced')) ORDER BY created_at LIMIT 10", (list(sources),)).fetchall()
                if time.monotonic() >= next_heartbeat:
                    try:
                        self.collector.heartbeat(int(any(job['status'] in ('queued', 'running') for job in jobs)))
                        next_heartbeat = time.monotonic() + 15
                    except Exception as exc:
                        LOG.warning('middle collector heartbeat: %s', type(exc).__name__)
                        time.sleep(5)
                        continue
                try:
                    if self.direct.poll(allow_claim=not any(job['status'] in ('queued','running') for job in jobs)):
                        time.sleep(2)
                        continue
                except Exception as exc:
                    LOG.warning('direct dispatch: %s', type(exc).__name__)
                for job in jobs:
                    try: runners[job['parameters'].get('source', 'google_web')].advance(job)
                    except Exception as exc:
                        if isinstance(exc, ProductError) and exc.code in ('middle_unauthorized','middle_entitlement_denied','middle_credit_required','middle_persistence_failed','middle_empty_results','middle_completion_invalid','middle_ingest_rejected'):
                            current = self.store.job(job['account_id'],str(job['id']))
                            state = current['response'] or {}
                            if state.get('middle_query_id'):
                                try: self.middle.cancel(state['middle_query_id'])
                                except Exception: continue  # preserve binding until cancellation confirmed
                            self.stop_campaign(state,'failed')
                            self.store.finish(job['id'],'failed',state,exc.code)
                        # No upstream bodies, credentials or exception strings.
                        LOG.warning('middle job %s: %s %s', job['id'], type(exc).__name__, getattr(exc,'code',getattr(exc,'status_code','')))
                time.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    key=Path(os.getenv('SEARCHFLOW_MIDDLE_CONSUMER_KEY_FILE','/app/state/private/middle-consumer-key')).read_text().strip()
    LiveRunner(Config(),key,source=os.getenv('SEARCHFLOW_MIDDLE_SOURCE') or None).run()
