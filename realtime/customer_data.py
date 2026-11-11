"""Customer-owned and public saved data. Reads never collect or upload data."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .google_domain import DomainError
from .google_field_adaptation import adapt_request_fields, validate_field_contract
from .google_data import normalize_request
from .google_legacy import _filters, _cursor_encode
from .product import ProductError, identifier

VERSION = 'searchflow-data-1'
COLUMNS = ('r.request_id,r.source,r.query,r.page,r.provider,r.classification,'
           'r.completed_at,r.response')
PUBLIC = ("NOT EXISTS (SELECT 1 FROM serp_task_options o WHERE "
          "o.campaign_id=r.campaign_id AND o.options->>'customer_private'='true')")


def selection(parameters, account_id):
    parameters = dict(parameters)
    scope = parameters.pop('scope', 'own')
    if scope not in ('own', 'public', 'accessible'):
        raise ProductError('invalid_scope', 'scope 仅支持 own、public、accessible')
    match = parameters.pop('query_match', 'contains')
    if match not in ('contains', 'exact'):
        raise ProductError('invalid_parameter', 'query_match 仅支持 contains、exact')
    exact = parameters.pop('query', None) if match == 'exact' else None
    if exact is not None and (not isinstance(exact, str) or len(exact) > 300):
        raise ProductError('invalid_parameter', '关键词过长')
    new_only = parameters.pop('new_only', 'false')
    if new_only not in ('true', 'false'):
        raise ProductError('invalid_parameter', 'new_only 仅支持 true、false')
    parameters.setdefault('field_contract', 'prd-v1')
    where, args, limit = _filters(parameters)
    # A historical cross-campaign cache association is not permission to read
    # another customer's private origin request.
    owned = ("EXISTS (SELECT 1 FROM sf_jobs j WHERE j.account_id=%s AND "
             "(j.campaign_id=r.campaign_id OR (" + PUBLIC + " AND EXISTS "
             "(SELECT 1 FROM serp_campaign_requests cr WHERE cr.campaign_id=j.campaign_id "
             "AND cr.request_id=r.request_id))))")
    if where[1] != PUBLIC:
        raise RuntimeError('legacy visibility predicate changed')
    where[1] = PUBLIC if scope == 'public' else owned if scope == 'own' else '(' + PUBLIC + ' OR ' + owned + ')'
    if scope != 'public':
        args.insert(0, identifier(account_id))
    if exact:
        where.append('r.query=%s'); args.append(exact)
    if new_only == 'true':
        where.append("CASE WHEN jsonb_typeof(r.response->'new_unique_links')='number' THEN (r.response->>'new_unique_links')::numeric ELSE 0 END>0")
    return where, args, limit, scope, parameters['field_contract']


def project(row, contract='prd-v1'):
    validate_field_contract(contract)
    return adapt_request_fields(row) if contract == 'prd-v1' else normalize_request(row)


def bounded_integer(value, default, low, high, name):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = int(value) if value is not None else default
    except (TypeError, ValueError):
        raise ProductError('invalid_parameter', name + ' 必须为整数') from None
    if not low <= result <= high:
        raise ProductError('invalid_parameter', f'{name} 必须为 {low}–{high}')
    return result


class CustomerDataStore:
    def __init__(self, store, account_id):
        self.store, self.account_id = store, account_id

    def requests(self, parameters):
        where, args, limit, scope, contract = selection(parameters, self.account_id)
        with self.store.base.connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout='20s'")
            rows = db.execute('SELECT ' + COLUMNS + ' FROM serp_requests r WHERE ' +
                              ' AND '.join(where) + ' ORDER BY r.completed_at DESC,r.request_id DESC LIMIT %s',
                              args + [limit + 1]).fetchall()
        visible = rows[:limit]
        return dict(version=VERSION, scope=scope, field_contract=contract,
                    requests=[project(row, contract) for row in visible],
                    limit=limit, has_more=len(rows) > limit,
                    next_cursor=_cursor_encode(visible[-1]) if len(rows) > limit else None,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    provenance='saved_serp_requests_not_whale_readback')

    def request(self, rid, parameters=None):
        p = dict(parameters or {})
        if set(p) - {'field_contract', 'scope'}:
            raise ProductError('invalid_parameter', '详情仅支持 scope、field_contract')
        # Default detail may read own or public, never another customer's data.
        p.setdefault('scope', 'accessible')
        where, args, _, _, contract = selection(p, self.account_id)
        with self.store.base.connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout='10s'")
            row = db.execute('SELECT ' + COLUMNS + ' FROM serp_requests r WHERE r.request_id=%s AND ' +
                             ' AND '.join(where), [identifier(rid)] + args).fetchone()
        if not row:
            raise ProductError('not_found', '记录不存在', 404)
        return project(row, contract)

    def job_data(self, jid, contract='prd-v1'):
        job = self.store.job(self.account_id, jid)
        response = job.get('response')
        if job['status'] != 'succeeded' or not response:
            raise ProductError('job_not_ready', '任务尚未成功完成', 409)
        rid = response.get('origin_request_id')
        if rid:
            # Re-check origin visibility as well as job ownership.
            return self.request(rid, {'field_contract': contract})
        # Paid providers may have no legacy request. Use only the owned frozen
        # job response; do not invent a legacy request ID or a Whale version.
        p = job['parameters']
        record = project(dict(request_id=str(job['id']), source=p['source'], query=p['query'],
                              page=1, provider=response.get('provider'), classification='results',
                              completed_at=response['fetched_at'], response=response.get('data') or {}), contract)
        record.update(provenance='owned_frozen_job_response_not_legacy_request',
                      identity_basis='customer_job_id', job_id=str(job['id']))
        return record

    def section(self, rid, section, parameters):
        p = dict(parameters)
        limit = bounded_integer(p.pop('limit', None), 100, 1, 500, 'limit')
        offset = bounded_integer(p.pop('offset', None), 0, 0, 1000000, 'offset')
        record = self.request(rid, p)
        if section == 'features':
            values = record.get('field_adaptation', {}).get('features')
            if values is None:
                raise ProductError('unsupported_contract', 'features 请使用 prd-v1')
        else:
            values = record['occurrences' if section == 'items' else 'topics']
        return dict(request_id=record['request_id'], section=section,
                    items=values[offset:offset + limit], total=len(values),
                    limit=limit, offset=offset, has_more=offset + limit < len(values),
                    next_offset=offset + limit if offset + limit < len(values) else None)

    def progress(self, jid):
        from .serp_console import ConsoleStore
        self.store.job(self.account_id, jid)
        with self.store.base.connect() as db:
            job = db.execute('SELECT campaign_id,status,deadline_at FROM sf_jobs WHERE id=%s AND account_id=%s',
                             (identifier(jid), self.account_id)).fetchone()
        progress = ConsoleStore(self.store.base.dsn).collection_progress(str(job['campaign_id']), public_only=False) if job['campaign_id'] else None
        return dict(job_id=str(jid), status=job['status'], deadline_at=job['deadline_at'],
                    progress=progress, note='暂停不延长原任务期限；已发出的请求仍可能完成。')

    def quality(self, parameters):
        p = dict(parameters)
        if set(p) & {'limit', 'cursor', 'field_contract'}:
            raise ProductError('invalid_parameter', '质量统计不接受 limit、cursor、field_contract')
        sample_limit = bounded_integer(p.pop('sample_limit', None), 100, 1, 1000, 'sample_limit')
        where, args, _, scope, _ = selection(p, self.account_id)
        with self.store.base.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            db.execute("SET LOCAL statement_timeout='20s'")
            totals = db.execute("SELECT count(*) requests,count(*) FILTER(WHERE r.classification IN ('results','empty')) successful "
                                'FROM serp_requests r WHERE ' + ' AND '.join(where), args).fetchone()
            rows = db.execute('SELECT ' + COLUMNS + ' FROM serp_requests r WHERE ' + ' AND '.join(where) +
                              ' ORDER BY r.completed_at DESC,r.request_id DESC LIMIT %s', args + [sample_limit]).fetchall()
        occurrences = urls = wrappers = 0
        unique = set()
        fields = Counter()
        run_fields, sources = Counter(), Counter()
        topics = features = 0
        for row in rows:
            record = project(row)
            sources[row['source']] += 1
            topics += len(record['topics'])
            features += len(record['field_adaptation']['features'])
            run_fields.update({k: int(v['availability'] == 'available') for k, v in record['field_adaptation']['run'].items()})
            for item in record['occurrences']:
                occurrences += 1
                urls += bool(item['url']['url_id'])
                if item['url']['url_id']:
                    unique.add(item['url']['url_id'])
                wrappers += item['resolution_status'] == 'unresolved_wrapper'
                for key in ('title', 'snippet', 'organic_position', 'source_absolute_rank', 'article_url'):
                    fields[key] += item.get(key) is not None and item.get(key) != ''
                for key in ('author', 'canonical_url', 'thumbnail', 'publisher', 'date'):
                    fields[key] += item['source_fields'].get(key) is not None and item['source_fields'].get(key) != ''
        return dict(version=VERSION, scope=scope, totals=dict(totals),
                    sample=dict(requests=len(rows), sources=dict(sources), occurrences=occurrences,
                                topics=topics, features=features, valid_url_occurrences=urls,
                                unique_urls=len(unique), duplicate_url_occurrences=urls - len(unique),
                                unresolved_news_wrappers=wrappers, field_present=dict(fields),
                                run_field_available=dict(run_fields)),
                    sample_limit=sample_limit, truncated=totals['requests'] > len(rows),
                    methodology='Totals cover the filter; field quality and unique URLs cover only the newest sample. Not historical first contributions.')


def field_catalog():
    from .data_capability_schema import fields as capability_fields
    from .customer_export import FULL_COLUMNS
    # Derive schema keys from the real adapter, without returning fixture facts.
    sample = project(dict(request_id='00000000-0000-0000-0000-000000000000', source='google_web',
                          query='', page=1, classification='empty', completed_at=None, response={
                              'organic_results': [dict(link='https://example.com/')],
                              'serp_features': [dict(type='unknown')]}))
    return dict(version=VERSION, data_capabilities=capability_fields(), default_field_contract='prd-v1',
                field_contracts=['legacy', 'prd-v1'],
                request_fields=list(sample), occurrence_fields=list(dict.fromkeys([*sample['occurrences'][0],
                    'image_url', 'source_page_url', 'thumbnail_url', 'width', 'height',
                    'upstream_rank', 'observed_order', 'rank_basis', 'media_downloaded'])),
                enhanced_run_fields=list(sample['field_adaptation']['run']),
                enhanced_item_fields=list(sample['field_adaptation']['items'][0]),
                feature_fields=list(sample['field_adaptation']['features'][0]),
                full_csv_columns=list(FULL_COLUMNS),
                unknown_policy='Missing source facts stay null with availability and missing_reason; a field is not a coverage promise.',
                scopes={'own': 'Requests belonging to this customer, excluding foreign private cache origins',
                        'public': 'Saved public history only', 'accessible': 'Public history plus own requests'})
