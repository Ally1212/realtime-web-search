"""Durable Collector-side live tasks, independent of website accounts and quotas.

Only the authenticated Collector claim grants execution authority. Never create a
Consumer query or impersonate a website account for a platform-originated task.
"""
import copy
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb
from .product import ProductError
from .proxy_pool import ProxyPool
from .live_collection import (
    COLLECTION_ERROR_CODES, RETRYABLE_COLLECTION_CODES, WEB_DELIVERY_RESERVE_SECONDS,
    can_deliver_web_results, check_collection, collection_error, needs_collection, retry_interval,
)
from .middle_collector import image_conditions
from .middle_ingest import task_spec, link_message, receipt_query, processing_receipts, collector_capabilities, retry_delay


LOG = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_middle_direct_tasks (
 task_id text PRIMARY KEY, source_platform text NOT NULL, state jsonb NOT NULL,
 status text NOT NULL DEFAULT 'running', updated_at timestamptz NOT NULL DEFAULT now()
);
"""


def maps_parameters(task):
    from .middle_maps_contract import CAPABILITIES, maps_request
    from .google_local_jobs import parameters as validate
    from .google_data import digest
    from .middle_platform_client import _identifier
    if (task.get('task_type') != 'live_query' or task.get('dataset_id') != 'google_maps_raw'
            or task.get('source_platform') != 'google_maps'
            or set(task.get('required_capabilities') or []) - set(CAPABILITIES)):
        raise ProductError('middle_claim_mismatch','Maps task scope or capabilities differ',400)
    _identifier(task['id'])
    p=task['payload']
    if not isinstance(p,dict) or set(p)-{'query','region','language','max_items','live_query_id','resource_kind','url','external_id','conditions'}:
        raise ProductError('invalid_parameter','Unsupported Maps task parameters',400)
    _identifier(p['live_query_id'])
    if p.get('resource_kind') != 'content' or p.get('url') or p.get('external_id'):
        raise ProductError('invalid_parameter','Maps supports keyword content tasks only',400)
    count=p.get('max_items',10)
    if type(count) is not int or not 1<=count<=100:
        raise ProductError('invalid_parameter','max_items must be an integer between 1 and 100',400)
    conditions=p.get('conditions') or []
    if not isinstance(conditions,list) or len(conditions)>2:
        raise ProductError('middle_conditions_mismatch','Unsupported Maps conditions',400)
    candidate=dict(mode='text',query=p.get('query'),language=p.get('language') or 'en',region=p.get('region') or 'US')
    if conditions:
        try:
            location=conditions[0]['value']
            candidate.update(mode='nearby' if len(conditions)==2 else 'text',location={k:location[k] for k in ('lat','lng')},radius_m=location['radius_m'])
            if len(conditions)==2:candidate['included_type']=conditions[1]['value']
        except (KeyError,TypeError):
            raise ProductError('middle_conditions_mismatch','Invalid Maps location/type conditions',400) from None
        result=validate(candidate)
        if digest(maps_request(result)['conditions'])!=digest(conditions):
            raise ProductError('middle_conditions_mismatch','Maps conditions cannot be ignored',400)
        # Preserve the already deployed one-query-snapshot contract. For text
        # tasks max_items limits places; legacy max_items=1+conditions is a bundle.
        if count!=1:result['max_places']=count
        return result
    candidate['max_places']=count
    return validate(candidate)


class DirectTasks:
    def __init__(self, base, collector, *, source, config=None, search=None, deadline_lookup=None):
        self.base, self.collector, self.source = base, collector, source
        self.platform = 'google_search' if source == 'google_web' else source
        self.config, self.search = config, search
        self.deadline_lookup = deadline_lookup
        self.proxy_pool = ProxyPool(config) if config and config.default_proxy_profile != 'direct' else None
        with base.connect() as db:
            db.execute(SCHEMA)

    def save(self, state, status='running'):
        with self.base.connect() as db:
            db.execute("INSERT INTO sf_middle_direct_tasks(task_id,source_platform,state,status) VALUES(%s,%s,%s,%s) ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,status=excluded.status,updated_at=now()",
                       (state['task']['id'], self.platform, Jsonb(state), status))

    def accept(self, task):
        # Claims can include an already-bound website query. Preserve its owner
        # and let the original website path perform fixed-version readback.
        with self.base.connect() as db:
            if self.platform == 'google_maps':
                rows = db.execute("SELECT id,middle_state FROM sf_google_local_jobs WHERE status IN('queued','running') AND middle_state->'platform_task_ids' ? %s", (task['id'],)).fetchall()
                for row in rows:
                    state = row['middle_state']
                    if state['middle_query_id'] != task['payload']['live_query_id']:
                        raise ValueError('Website query identity differs')
                    from .middle_maps_contract import task_parameters
                    params = db.execute('SELECT parameters FROM sf_google_local_jobs WHERE id=%s', (row['id'],)).fetchone()['parameters']
                    task_parameters(task, query_id=state['middle_query_id'], task_id=task['id'], expected_parameters=params)
                    state['task'] = task
                    db.execute('UPDATE sf_google_local_jobs SET middle_state=%s WHERE id=%s', (Jsonb(state), row['id']))
                    return False
            else:
                rows = db.execute("SELECT id,response,parameters FROM sf_jobs WHERE status IN('queued','running') AND parameters->>'provider'='middle' AND response->'platform_task_ids' ? %s", (task['id'],)).fetchall()
                for row in rows:
                    state = row['response']
                    if state['middle_query_id'] != task['payload']['live_query_id']:
                        raise ValueError('Website query identity differs')
                    task_spec(task, self.config, source=row['parameters'].get('source', 'google_web'))
                    state['task'] = task
                    db.execute('UPDATE sf_jobs SET response=%s WHERE id=%s', (Jsonb(state), row['id']))
                    return False
            old = db.execute('SELECT state,status FROM sf_middle_direct_tasks WHERE task_id=%s', (task['id'],)).fetchone()
        if old:
            if old['state']['task'] != task:
                # Lease metadata may change; payload and scope must not.
                for key in ('payload','dataset_id','source_platform','task_type'):
                    if old['state']['task'].get(key) != task.get(key):
                        raise ValueError('Reclaimed task identity differs')
            if old['status'] in ('running','lease_lost'):
                state = old['state']
                state['task'] = task
                state.pop('retry_at', None)
                self.save(state)
                return True
            return False
        self.save(dict(task=task, stage='claimed'))
        return True

    def pulse(self, state):
        result = self.collector.post('/v1/collection/tasks/'+state['task']['id']+'/heartbeat', dict(agent_id=self.collector.agent_id))
        if result.get('control_signal', result.get('control')) in ('pause','cancel','stop'):
            self.stop(state)
            raise ProductError('middle_controlled', 'Task stopped by platform', 409)

    def stop(self, state):
        if state.get('campaign_id'):
            with self.base.connect() as db:
                db.execute("UPDATE campaigns SET status='paused' WHERE id=%s", (state['campaign_id'],))

    def web_deadline(self, state):
        task = state['task']
        lookup = getattr(self, 'deadline_lookup', None)
        if lookup:
            try:
                deadline = lookup(task['payload']['live_query_id'], task['id'])
            except ProductError:
                deadline = None
            if deadline is not None:
                return min(deadline.timestamp(), time.time()+300)
        # The Collector claim does not include the Consumer deadline. Cap the
        # fallback from task creation, so reclaims cannot reset the budget.
        created_at = task.get('created_at')
        try:
            stamp = datetime.fromisoformat(created_at)
            created = stamp.timestamp() if stamp.tzinfo is not None else time.time()
        except (ValueError, TypeError):
            created = time.time()
        return min(created, time.time())+120

    def collect(self, state):
        task = state['task']
        if self.platform == 'google_maps':
            from .middle_maps_contract import query_snapshot, snapshot_message
            parameters = state.get('resolved_parameters')
            if parameters is None:
                parameters = maps_parameters(task)
                if not parameters.get('location'):
                    from .maps_location import location_text, resolve
                    resolved = resolve(location_text(parameters['query']), region=task['payload'].get('region') or '', language=parameters['language'])
                    parameters['location'] = resolved['location']
                    state['location_resolution'] = resolved
                state['resolved_parameters'] = parameters
                self.save(state)
            # A saved start marker avoids silently repeating an unknown Maps
            # request after process death (provider has no replay identity).
            if state.get('collection_started'):
                raise ValueError('Maps collection outcome unknown after restart')
            state['collection_started'] = True
            self.save(state)
            result = self.search(parameters)
            jid = str(uuid5(NAMESPACE_URL, task['id']))
            payload = query_snapshot(job_id=jid, query_id=task['payload']['live_query_id'], task_id=task['id'],
                parameters=parameters, result=result, observed_at=datetime.now(timezone.utc).isoformat())
            if state.get('location_resolution'):
                payload['location_resolution'] = copy.deepcopy(state['location_resolution'])
            payload['original_request'] = copy.deepcopy(task['payload'])
            return [snapshot_message(job_id=jid, task_id=task['id'], payload=payload)]
        from .serp_worker import run_serp_campaign
        from .customer_data import project
        from .middle_live_runtime import bind_live_observation, selected_pages, collection_coverage, observation_quality
        from .serp_fields import result_items, normalize_serp_url
        source = self.source
        if self.platform == 'google_search' and task['payload'].get('conditions') == image_conditions():
            source = 'google_images'
        spec = task_spec(task, self.config, source=source)
        cid = state.get('campaign_id') or str(uuid5(NAMESPACE_URL, 'whale-direct:'+task['id']))
        with self.base.connect() as db:
            db.execute("INSERT INTO campaigns(id,query,aliases,sources,daily_target,proxy_profile,status) VALUES(%s,%s,'[]',%s,10,%s,'active') ON CONFLICT(id) DO NOTHING", (cid,spec['keywords'][0],Jsonb(spec['sources']),self.config.default_proxy_profile))
            db.execute('INSERT INTO serp_task_options(campaign_id,options) VALUES(%s,%s) ON CONFLICT(campaign_id) DO NOTHING', (cid,Jsonb(spec['options'])))
        state['campaign_id'] = cid
        self.save(state)
        def saved():
            with self.base.connect() as db:
                return db.execute("SELECT r.* FROM serp_campaign_requests cr JOIN serp_requests r USING(request_id) WHERE cr.campaign_id=%s ORDER BY page, completed_at, request_id", (cid,)).fetchall()
        if source == 'google_web' and 'web_hard_deadline_at' not in state:
            deadline = self.web_deadline(state)
            state['web_hard_deadline_at'] = deadline
            state['collection_deadline_at'] = min(
                state.get('collection_deadline_at', deadline), deadline-WEB_DELIVERY_RESERVE_SECONDS)
            self.save(state)
        elif 'collection_deadline_at' not in state:
            state['collection_deadline_at'] = time.time() + spec['max_duration_minutes'] * 60
            self.save(state)
        rows = saved()
        errors = ()
        stop_at = state['collection_deadline_at']
        if time.time() < stop_at and needs_collection(source, rows, spec['google_pages']):
            pages = {r['page'] for r in rows if r['classification'] == 'results'}
            resume = {'skip_web_pages': pages} if source == 'google_web' and pages else {}
            if getattr(self, 'proxy_pool', None) is not None:
                resume['proxy_pool'] = self.proxy_pool
            if source == 'google_web' and getattr(self, 'proxy_pool', None) is not None:
                resume['collection_stop_at'] = stop_at
            errors = run_serp_campaign(cid, replace(self.config, content_fetch_enabled=False,
                              google_web_max_pages=spec['google_pages'],
                              google_web_pages_per_batch=spec['google_pages']),
                              should_continue=lambda: time.time() < state['collection_deadline_at'],
                              **resume)
            rows = saved()
        cutoff = time.time() >= stop_at
        if cutoff and not errors:
            errors = (state.get('collection_retry_code') or 'search_deadline_exceeded',)
        candidates = [r for r in rows if r['classification'] in ('results', 'empty')]
        selections = selected_pages(candidates, source, spec['max_items'])
        count = sum(len(items) for _, items, _ in selections)
        deliver_saved = source == 'google_web' and can_deliver_web_results(
            rows, count, spec['max_items'], collection_finished=cutoff)
        try:
            check_collection(source, rows, errors, spec['google_pages'], deliver_saved=deliver_saved)
        except ProductError:
            if source != 'google_images':
                raise
            raise ValueError('No successful upstream response') from None
        messages = []
        coverage = collection_coverage(rows, spec['max_items'], spec['google_pages'],
                                       count, deadline_exceeded=cutoff)
        if source == 'google_web':
            if errors:
                coverage['interruption_code'] = collection_error(rows, errors)
            state['web_partial'] = bool(0 < count < spec['max_items'] and (
                len(coverage['successful_pages']) < spec['google_pages'] or coverage['partial_failure']))
        for row, selected, raw in selections:
            record = project(raw)
            record['page'] = row['page']
            record['quality_observation'] = observation_quality(record)
            record['delivery_scope'] = 'selected_link_observations'
            record['collection_coverage'] = coverage
            for item in selected:
                _, message = link_message(row,item,self.config,task['id'])
                message = bind_live_observation(message,query_id=task['payload']['live_query_id'],task_id=task['id'],record=record)
                message['discovery'].update(acquisition_type='live_query',collector=self.collector.agent_id)
                messages.append(message)
        return messages

    def advance(self, state):
        task = state['task']
        self.pulse(state)
        retry_at = state.get('completion_retry_at')
        if retry_at and datetime.fromisoformat(retry_at) > datetime.now(timezone.utc):
            return
        state.pop('completion_retry_at', None)
        if state.get('failure_pending'):
            self.collector.post('/v1/collection/tasks/'+task['id']+'/fail',dict(agent_id=self.collector.agent_id,message=state.get('failure_message','SearchFlow direct collection rejected or failed'),retry=False))
            self.save(state,'failed')
            return
        if 'messages' not in state:
            if ('web_hard_deadline_at' not in state and state.get('collection_retry_code')
                    and time.time() >= state['collection_deadline_at']):
                code = state['collection_retry_code']
                raise ProductError(code, code, 503)
            stopped, errors = threading.Event(), []
            def heartbeat():
                while not stopped.wait(10):
                    try:
                        self.collector.heartbeat(1)
                        self.pulse(state)
                    except Exception as exc:
                        errors.append(exc); self.stop(state); return
            thread = threading.Thread(target=heartbeat,daemon=True)
            thread.start()
            try:
                state['messages'] = self.collect(state)
            finally:
                stopped.set(); thread.join()
            if errors:
                raise errors[0]
            state.pop('collection_retry_code', None)
            state['stage'] = 'ingest_pending'
            self.save(state)
            self.stop(state)
        hard_deadline = state.get('web_hard_deadline_at')
        if hard_deadline is not None:
            if time.time() >= hard_deadline:
                raise ProductError('search_deadline_exceeded', 'search_deadline_exceeded', 503)
            self.pulse(state)
        rows = [dict(payload=m,dataset_id=m['dataset_id'],record_key=m['source']['source_record_key']) for m in state['messages']]
        receipts = processing_receipts(rows,self.collector.post('/v1/ingest/receipts',[receipt_query(r) for r in rows])) if rows else []
        if any(r['status'] == 'failed' for r in receipts):
            raise ValueError('Persistence failed')
        if any(r['status'] == 'unknown' for r in receipts):
            if hard_deadline is not None and time.time() >= hard_deadline:
                raise ProductError('search_deadline_exceeded', 'search_deadline_exceeded', 503)
            if self.platform == 'google_maps':
                self.collector.upload_realtime(state['messages'])
            else:
                r = self.collector.session.post(self.collector.base_url+'/v1/documents/bulk',json=state['messages'],headers={'Authorization':'Bearer '+self.config.whale_collector_api_key,'X-Ingest-Class':'realtime'},timeout=20,allow_redirects=False)
                if r.status_code not in (200,202):
                    error = RuntimeError('Ingest transport unconfirmed'); error.status_code=r.status_code; raise error
            return
        if any(r['status'] not in ('accepted','duplicate') or not r.get('document_id') or not r.get('version_id') for r in receipts):
            return
        state['receipts'] = receipts
        state['stage'] = 'completion_pending'
        self.save(state)
        self.pulse(state)
        if hard_deadline is not None and time.time() >= hard_deadline:
            raise ProductError('search_deadline_exceeded', 'search_deadline_exceeded', 503)
        completion = dict(
            agent_id=self.collector.agent_id,
            collected_count=len(rows),
            ingested_count=sum(r['status'] == 'accepted' for r in receipts),
            duplicate_count=sum(r['status'] == 'duplicate' for r in receipts),
            result=dict(document_ids=list(dict.fromkeys(r['document_id'] for r in receipts))),
        )
        if 'web_partial' in state:
            completion['result'].update(returned_count=len(completion['result']['document_ids']),
                                        partial=state['web_partial'])
        try:
            self.collector.post('/v1/collection/tasks/'+task['id']+'/complete', completion)
        except (RuntimeError, ProductError) as exc:
            code = getattr(exc, 'whale_code', '')
            if getattr(exc, 'status_code', None) != 409 or code == 'task_lease_lost':
                raise
            now = datetime.now(timezone.utc)
            delay = retry_delay(2, getattr(exc, 'whale_retry_after', None), minimum=5, now=now)
            state['completion_retry_at'] = (now + timedelta(seconds=delay)).isoformat()
            self.save(state)
            LOG.info('direct completion deferred platform=%s task=%s code=%s',
                     self.platform, task['id'], code or 'unknown_conflict')
            return
        state['stage'] = 'succeeded'
        self.save(state,'succeeded')
        LOG.info('direct task completed platform=%s task=%s documents=%s',self.platform,task['id'],len(rows))

    def poll(self, *, allow_claim):
        with self.base.connect() as db:
            row = db.execute("""SELECT state FROM sf_middle_direct_tasks WHERE source_platform=%s
                AND status='running'
                AND coalesce((state->>'retry_at')::timestamptz, now()) <= now()
                ORDER BY updated_at LIMIT 1""", (self.platform,)).fetchone()
            if not row and allow_claim:
                # A deferred task still owns the collector's platform lease.
                allow_claim = not db.execute("""SELECT 1 FROM sf_middle_direct_tasks
                    WHERE source_platform=%s AND status='running' LIMIT 1
                    """, (self.platform,)).fetchone()
        if not row and allow_claim:
            from .middle_maps_contract import CAPABILITIES
            capability_source = 'google_images' if self.platform == 'google_search' else self.source
            capabilities = CAPABILITIES if self.platform == 'google_maps' else collector_capabilities(capability_source)
            tasks = self.collector.post('/v1/collection/tasks/claim',dict(agent_id=self.collector.agent_id,supported_task_types=['live_query'],supported_platforms=[self.platform],declared_capabilities=capabilities,limit=1)).get('tasks') or []
            if tasks and self.accept(tasks[0]):
                with self.base.connect() as db:
                    row = db.execute('SELECT state FROM sf_middle_direct_tasks WHERE task_id=%s', (tasks[0]['id'],)).fetchone()
        if not row:
            return False
        state = row['state']
        if state.get('retry_at') and datetime.fromisoformat(state['retry_at']) > datetime.now(timezone.utc):
            return False
        state.pop('retry_at', None)
        try:
            self.collector.heartbeat(1)
            self.advance(state)
        except Exception as exc:
            code = getattr(exc,'code','')
            status = getattr(exc,'status_code',None)
            deadline = state.get('collection_deadline_at')
            if code in RETRYABLE_COLLECTION_CODES and deadline and time.time() < deadline:
                state['collection_retry_code'] = code
                state['stage'] = 'waiting_for_collection'
                state['retry_at'] = datetime.fromtimestamp(
                    min(deadline, time.time() + retry_interval(code)), timezone.utc).isoformat()
                self.save(state)
                return False
            upstream_failed = bool(deadline and isinstance(exc, ProductError) and code in COLLECTION_ERROR_CODES)
            if upstream_failed or (isinstance(exc,(ValueError,KeyError,TypeError)) and not isinstance(exc,ProductError)) or (code.startswith('maps_') and code not in ('maps_geocoder_unavailable',)) or code in ('middle_controlled','middle_claim_mismatch','middle_conditions_mismatch','invalid_parameter','invalid_query'):
                self.stop(state)
                if code != 'middle_controlled':
                    state['failure_pending'] = True
                    state['failure_code'] = code or 'collection_failed'
                    if upstream_failed:
                        state['failure_message'] = code
                    if code in ('maps_location_required','maps_location_not_found','maps_location_ambiguous','maps_location_invalid'):
                        state['failure_message'] = code+': '+str(exc)
                    self.save(state)
                    self.collector.post('/v1/collection/tasks/'+state['task']['id']+'/fail',dict(agent_id=self.collector.agent_id,message=state.get('failure_message','SearchFlow direct collection rejected or failed'),retry=False))
                state['stage'] = code or 'collection_failed'
                self.save(state,'failed')
            elif status == 409:
                # Completed POST can have succeeded before a lost response. Do
                # not re-collect or claim completion; preserve it for diagnosis.
                self.stop(state)
                state['stage'] = 'lease_lost'
                self.save(state,'lease_lost')
            else:
                delay = retry_delay(2, getattr(exc, 'retry_after', None), minimum=5)
                state['retry_at'] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                self.save(state)
                LOG.warning('direct task retry platform=%s task=%s error=%s code=%s',self.platform,state['task']['id'],type(exc).__name__,code or status or '')
        return True
