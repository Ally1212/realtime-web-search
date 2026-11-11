"""Main API contract/discovery for opt-in new data domains, never source approval."""
from __future__ import annotations

from .data_capability_contract import CAPABILITIES, COMMON, FIELDS, VERSION
from .data_capability_facts import VERSION as FACT_VERSION, VALUE_STATES

OBSERVATION_FIELDS = ('observation_id', 'entity_id', 'capability', 'provider', 'license_id', 'parameters',
                      'revision', 'supersedes', 'payload_hash', 'raw_hash', 'hash_encoding', 'stored_at',
                      'expires_at', 'payload', 'snapshot_hash', 'platform_delivery')
ENVELOPE_FIELDS = ('schema_version', 'source', 'requested_at', 'received_at', 'effective_parameters',
                   'is_partial', 'is_truncated', 'termination_reason', 'warnings', 'raw', 'data')
MODEL_FIELDS = {
    'series': ('id', 'term_or_topic', 'resolved_subject', 'effective_window', 'granularity', 'source_timezone',
               'scale_id', 'measurement_type', 'normalization', 'comparison_group', 'revision', 'points',
               'source_fields', 'regional_interest', 'related_top', 'related_rising'),
    'point': ('id', 'period_start', 'period_end', 'metric', 'partial', 'revision', 'source_ref', 'source_fields'),
    'volume': ('id', 'returned_text', 'close_variant_group', 'measurement_type', 'unit', 'statistic_window',
               'average', 'monthly', 'commercial_fields', 'source_fields'),
    'month': ('year', 'month', 'metric', 'source_fields'),
    'answer': ('id', 'channel', 'state', 'answer_text', 'blocks', 'citations', 'links', 'model', 'expansion', 'source_fields'),
    'block': ('id', 'position', 'type', 'text', 'structure', 'source_fields'),
    'citation': ('id', 'url', 'title', 'role', 'source_ref', 'mapping_status', 'block_ids', 'text_ranges', 'source_fields'),
    'input_mapping': ('input_index', 'record_id', 'status', 'reason'),
}


def catalog():
    return dict(version=VERSION, availability='not_configured', source_verified=False,
                capabilities=[dict(id=c, source_status='requires_tenant_admission',
                                   request_fields=sorted(COMMON | FIELDS[c]), fact_schema_version=FACT_VERSION)
                              for c in CAPABILITIES],
                submit_url='/v1/data/jobs', preview_url='/v1/data/jobs/preview', jobs_url='/v1/jobs?domain=data', observations_url='/v1/data/observations',
                quota='Explicit per-currency monetary reservation; legacy search quota is not used.',
                submission_gate='current_source_admission_and_live_executor_required',
                native_source_binding='explicit_serpapi_trends_aio_ai_mode_or_google_ads_volume;fresh_tenant_approval_json;license_currency_billing_retry_policy_checked;no_fallback_or_credential_discovery;external_approval_not_verified_here',
                deployment='explicit_opt_in_assembly;shared_service_grant_and_response_store;initialize_then_pinned_v3_recovery_then_workers_then_authenticated_server_mount;no_auto_credentials_or_production_enablement',
                restore_gate='opt_in_per_service;closed_on_every_process_start;fresh_v3_replay_and_independent_pin_required;receipt_alone_never_admits',
                subscription_erasure='trusted_only;name_filters_key_and_delivery_records_cleared;410_and_foreign_404;original_key_cannot_recreate;v3_joint_checkpoint;v1_v2_replay_refuses_when_markers_present',
                erasure=dict(status='local_candidate',scope='job_query_content_retry_plans_aliases_descendants_observations_and_response_spools',
                             mutation='trusted_operator_only',erased_reads='410;cross_tenant_404',
                             restore='v3_subscription_job_fact_response_checkpoint;v2_job_fact_response_and_v1_fact_response_compatible;offline_and_independent_latest_hash_required',
                             external_files='already_delivered_copies_cannot_be_remotely_recalled'),
                export_delivery_checks='current_rights_erasure_and_fixed_version_rechecked_after_generation',
                source_error_policy='409_without_source_admission;503_without_ready_executor;never_accept_empty_work',
                scopes=['own'], subscriptions='local_candidate_requires_runtime', subscriptions_url='/v1/data/subscriptions?domain=data', subscription_events=['new_observation', 'revision'],
                source_responses='opt_in_bounded_spool;single_or_sealed_call_journal;not_normalized_facts',source_response_url='/v1/jobs/{id}/response',source_response_export_url='/v1/jobs/{id}/response/export',
                retry_url='/v1/jobs/{id}/retry',lineage_url='/v1/jobs/{id}/lineage',retry_policy='explicit_failed_unbound_keyword_inputs;unique_child_per_parent_observation_including_cache_aliases;separate_budget_and_bill;no_automatic_calls',
                source_retry_policy='google_ads_keyword_volume_and_serpapi_trends_aio_ai_mode;operator_frozen_opt_in;confirmed_bills_error_only_429_503;job_wide_call_count_budget_and_window;retry_only_failed_aio_leg;unknown_or_partial_no_replay',
                platform_delivery='not_connected_for_new_domains',
                platform_candidate='private_complete_observation_envelope;typed_parent_counts_and_hashes;pinned_consumer_readback_oracle;candidate_not_schema_approved;no_uploader_or_receipt_promotion', production_release='not_deployed')


def fields():
    return dict(version=FACT_VERSION, implementation='local_candidate_not_source_acceptance',
                capabilities=list(CAPABILITIES), observation_fields=list(OBSERVATION_FIELDS),
                envelope_fields=list(ENVELOPE_FIELDS), source_response_fields=['response_id','job_id','http_status','scope_verified','response_complete','raw_status','redacted_paths','reason','payload_hash','snapshot_hash','payload','expires_at','normalized_fact_status','capture_kind','call_count'],
                source_call_journal_fields=['phase','http_status','requested_at','received_at','scope_verified','response_complete','raw_status','redacted_paths','reason','payload','payload_hash'],
                admission_proof_fields=['admission_evidence_ref','admission_verified_at'],
                progress_charge_fields=['source_retry_policy','confirmed_call_cost','confirmed_call_charges','source_call_attempts','call_charge_basis'],
                recovery_admission='configured_gate_returns_503_restore_required_until_fresh_v3_replay;trusted_fee_audit_remains_available',
                subscription_content_erasure=dict(removes=['name','filters','plaintext_idempotency_key','batches','seen_membership'],preserves=['subscription_id','request_hash','first_erasure_marker','independent_observations','monetary_ledgers'],restore='v3_joint_checkpoint'),
                job_content_erasure=dict(reads='410;cross_tenant_404',lists='erased_jobs_excluded',
                    removes=['parameters','plaintext_idempotency_key','reconciliation_free_text','event_details','retry_plan'],
                    preserves=['job_id','request_hash','reservation','actual_cost','terminal_status','invoice_audit']),
                call_charge_basis='confirmed_receipts_may_be_partial;not_final_actual_cost',models={k: list(v) for k, v in MODEL_FIELDS.items()},
                retry_policy='explicit_settled_partial_keyword_volume_unbound_source_error_inputs_only;unique_child_per_parent_version;fresh_budget_no_automatic_calls',
                volume_average_window='known_two_month_boundaries_or_both_null_with_nonempty_reason;never_infer_from_requested_window_or_returned_month_subset',
                metric_fields=['value', 'status', 'reason', 'range_min', 'range_max', 'rounded', 'censored', 'source_fields'],
                value_statuses=list(VALUE_STATES), numeric_encoding='finite_nonnegative_decimal_strings;null_is_not_zero',
                full_export_formats=['json', 'jsonl', 'csv', 'zip', 'manifest'],
                subscription_events=['new_observation', 'revision'], subscription_mode='observations',
                subscription_batch_limit=1, subscription_progress='ID ledger advances only on checksum-confirmed acknowledgement; no cache-reuse events',
                citations='explicit_source_role_only;unicode_codepoint_ranges;not_inferred_from_ordinary_links',
                source_record_id_max_characters=1024,
                pagination='HMAC cursor bound to tenant, filters, route, sort and immutable child snapshot; 1 hour validity',
                source_coverage='Source dependent; model field presence is not a coverage promise.')


def request_schemas():
    common = dict(country=dict(type='string', pattern='^[A-Za-z]{2}$'),
                  language=dict(type='string', pattern='^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$'),
                  budget=dict(type='object', required=['maximum_cost', 'currency'], additionalProperties=False,
                              properties=dict(maximum_cost=dict(oneOf=[dict(type='string', minLength=1, maxLength=100),
                                                                       dict(type='integer', minimum=0, maximum=1000000000000)],
                                                                description='Finite nonnegative decimal, at most 9 fractional places; float JSON is rejected.'),
                                              currency=dict(type='string', pattern='^[A-Z]{3}$'))))
    output = []
    for cap in CAPABILITIES:
        properties = dict(common, capability=dict(type='string', enum=[cap]))
        if cap == 'trends_history':
            properties.update(terms=dict(type='array', minItems=1, maxItems=5, items=dict(type='string', minLength=1, maxLength=300)),
                              start_date=dict(type='string', format='date'), end_date=dict(type='string', format='date'),
                              category=dict(type='integer', minimum=0),
                              search_type=dict(type='string', enum=['web', 'images', 'news', 'shopping', 'youtube']))
        elif cap == 'keyword_volume':
            properties.update(keywords=dict(type='array', minItems=1, maxItems=100, items=dict(type='string', minLength=1, maxLength=300)),
                              start_month=dict(type='string', pattern='^[0-9]{4}-[0-9]{2}$'),
                              end_month=dict(type='string', pattern='^[0-9]{4}-[0-9]{2}$'),
                              network=dict(type='string', enum=['google', 'google_and_partners']))
        else:
            properties.update(prompt=dict(type='string', minLength=1, maxLength=10000), device=dict(type='string', enum=['desktop', 'mobile']))
        output.append(dict(type='object', additionalProperties=False, required=sorted(properties), properties=properties))
    return output


def fact_models():
    def ref(name):
        return {'$ref': '#/components/schemas/' + name}
    def arr(name):
        return dict(type='array', items=ref(name))
    def model(required, **properties):
        return dict(type='object', additionalProperties=False, required=list(required), properties=properties)
    text = dict(type='string')
    nullable = dict(type='string', nullable=True)
    source_fields = dict(type='object', additionalProperties=True)
    definitions = {}
    definitions['CapabilityMetric'] = model(('value', 'status'), value=dict(type='string', nullable=True,
        description='Finite nonnegative decimal string; available rounded 0 is not promoted to true_zero.'),
        status=dict(type='string', enum=list(VALUE_STATES)), reason=text,
        range_min=text, range_max=text, rounded=dict(type='boolean'), censored=dict(type='boolean'), source_fields=source_fields)
    definitions['CapabilityWindow'] = model(('start', 'end'), start=dict(type='string', format='date-time'), end=dict(type='string', format='date-time'))
    definitions['CapabilityInputMapping'] = model(MODEL_FIELDS['input_mapping'], input_index=dict(type='integer', minimum=0), record_id=nullable,
                                                 status=dict(type='string', enum=list(VALUE_STATES)), reason=nullable)
    definitions['CapabilityPoint'] = model(MODEL_FIELDS['point'], id=dict(type='string', maxLength=1024),
        period_start=dict(type='string', format='date-time'), period_end=dict(type='string', format='date-time'),
        metric=ref('CapabilityMetric'), partial=dict(type='boolean'), revision=text, source_ref=text, source_fields=source_fields)
    extension = model(('status', 'rows', 'reason'), status=dict(type='string', enum=list(VALUE_STATES)),
                      rows=dict(type='array', items={}), reason=nullable)
    definitions['CapabilitySeries'] = model(tuple(v for v in MODEL_FIELDS['series'] if v not in ('regional_interest', 'related_top', 'related_rising')),
        id=dict(type='string', maxLength=1024), term_or_topic=text, resolved_subject=dict(nullable=True),
        effective_window=ref('CapabilityWindow'), granularity=text, source_timezone=text, scale_id=text,
        measurement_type=dict(type='string', enum=['relative_index', 'same_scale_index', 'estimated_absolute']),
        normalization=text, comparison_group=nullable, revision=text, points=arr('CapabilityPoint'), source_fields=source_fields,
        regional_interest=extension, related_top=extension, related_rising=extension)
    definitions['CapabilityMonth'] = model(MODEL_FIELDS['month'], year=dict(type='integer'), month=dict(type='integer', minimum=1, maximum=12),
                                           metric=ref('CapabilityMetric'), source_fields=source_fields)
    monetary = model(('metric', 'currency', 'unit'), metric=ref('CapabilityMetric'), currency=dict(type='string', pattern='^[A-Z]{3}$'),
                     unit=dict(type='string', enum=['currency', 'micros']))
    definitions['CapabilityVolume'] = model(MODEL_FIELDS['volume'], id=dict(type='string', maxLength=1024), returned_text=text,
        close_variant_group=nullable, measurement_type=dict(type='string', enum=['source_reported', 'estimated', 'rounded', 'range', 'thresholded', 'unknown']),
        unit=dict(type='string', enum=['searches']), statistic_window=dict(anyOf=[model(('start_month', 'end_month'),
            start_month=dict(type='string', pattern='^[0-9]{4}-[0-9]{2}$'), end_month=dict(type='string', pattern='^[0-9]{4}-[0-9]{2}$')),
            model(('start_month','end_month','reason'),start_month=dict(type='string',nullable=True,enum=[None]),end_month=dict(type='string',nullable=True,enum=[None]),reason=text)]),
        average=ref('CapabilityMetric'), monthly=arr('CapabilityMonth'), source_fields=source_fields,
        commercial_fields=model((), competition=text, competition_index=ref('CapabilityMetric'), cpc=monetary, bid_low=monetary, bid_high=monetary))
    definitions['CapabilityBlock'] = model(MODEL_FIELDS['block'], id=dict(type='string', maxLength=1024), position=dict(type='integer', minimum=0),
        type=text, text=text, structure=dict(type='object'), source_fields=source_fields)
    definitions['CapabilityCitation'] = model(MODEL_FIELDS['citation'], id=dict(type='string', maxLength=1024), url=dict(type='string', format='uri'),
        title=nullable, role=dict(type='string', enum=['citation']), source_ref=text,
        mapping_status=dict(type='string', enum=['source_explicit', 'not_covered']), block_ids=dict(type='array', items=text),
        text_ranges=dict(type='array', items=model(('start', 'end'), start=dict(type='integer', minimum=0), end=dict(type='integer', minimum=1))),
        source_fields=source_fields)
    definitions['CapabilityLink'] = model(('url', 'title', 'source_fields'), url=dict(type='string', format='uri'), title=nullable, source_fields=source_fields)
    definitions['CapabilityAnswer'] = model(MODEL_FIELDS['answer'], id=dict(type='string', maxLength=1024), channel=dict(type='string', enum=['aio', 'ai_mode']),
        state=dict(type='string', enum=['complete', 'partial', 'not_observed', 'refused', 'expired', 'expansion_failed', 'source_error']),
        answer_text=nullable, blocks=arr('CapabilityBlock'), citations=arr('CapabilityCitation'), links=arr('CapabilityLink'), model=nullable,
        expansion=model(('state', 'end_reason'), state=dict(type='string', enum=['not_required', 'complete', 'partial', 'expired', 'failed']), end_reason=text),
        source_fields=source_fields)
    definitions['CapabilityTrendsData'] = model(('series', 'input_mapping'), series=arr('CapabilitySeries'), input_mapping=arr('CapabilityInputMapping'))
    definitions['CapabilityVolumesData'] = model(('groups', 'input_mapping'), groups=arr('CapabilityVolume'), input_mapping=arr('CapabilityInputMapping'))
    definitions['CapabilityAnswersData'] = model(('answer',), answer=ref('CapabilityAnswer'))
    # Root lists intentionally omit large child arrays; detail and job/data
    # include all children. Both are declared instead of pretending summaries
    # are complete facts.
    import copy
    for name, children in (('CapabilitySeries', ('points',)), ('CapabilityVolume', ('monthly',)), ('CapabilityAnswer', ('blocks', 'citations', 'links'))):
        summary = copy.deepcopy(definitions[name])
        summary['required'] = [k for k in summary['required'] if k not in children]
        for k in children:
            summary['properties'].pop(k)
        definitions[name + 'Summary'] = summary
    definitions['CapabilityTypedRecord'] = model(('object_id', 'observation_id', 'payload_hash', 'snapshot_hash', 'capability', 'record', 'includes_children', 'schema_version'),
        object_id=text, observation_id=dict(type='string', format='uuid'), payload_hash=text, snapshot_hash=text,
        capability=dict(type='string', enum=list(CAPABILITIES)), includes_children=dict(type='boolean'), schema_version=text,
        record=dict(oneOf=[ref(name + suffix) for name in ('CapabilitySeries', 'CapabilityVolume', 'CapabilityAnswer') for suffix in ('', 'Summary')]))
    return definitions


def extend_openapi(doc):
    doc['info']['version'] = '1.9.0'
    schemas = doc['components'].setdefault('schemas', {})
    schemas.update(fact_models())
    schemas['CapabilitySourceRetryPolicy']=dict(type='object',nullable=True,additionalProperties=False,required=['maximum_attempts','initial_delay_seconds','maximum_delay_seconds','maximum_window_seconds'],properties=dict(maximum_attempts=dict(type='integer',minimum=1,maximum=8),initial_delay_seconds=dict(type='integer',minimum=1,maximum=86400),maximum_delay_seconds=dict(type='integer',minimum=1,maximum=86400),maximum_window_seconds=dict(type='integer',minimum=1,maximum=86400)))
    schemas['CapabilityJobRequest'] = dict(oneOf=request_schemas())
    schemas['CapabilityObservation'] = dict(type='object', required=list(OBSERVATION_FIELDS),
        properties={**{k: dict(type='string') for k in ('observation_id', 'entity_id', 'provider', 'license_id', 'payload_hash', 'snapshot_hash', 'hash_encoding')},
                    'capability': dict(type='string', enum=list(CAPABILITIES)),
                    'revision': dict(type='string', nullable=True), 'supersedes': dict(type='string', nullable=True),
                    'raw_hash': dict(type='string', nullable=True), 'stored_at': dict(type='string', format='date-time'),
                    'expires_at': dict(type='string', format='date-time'), 'parameters': dict(type='object'),
                    'payload': {'$ref': '#/components/schemas/CapabilityFactEnvelope'},
                    'platform_delivery': dict(type='string', enum=['not_submitted'])})
    schemas['CapabilityFactEnvelope'] = dict(type='object', required=list(ENVELOPE_FIELDS),
        properties={'schema_version': dict(type='string', enum=[FACT_VERSION]), 'source': dict(type='object',
                        required=['provider', 'api_version', 'parser_version', 'source_request_id', 'source_observed_at', 'unknown_time_reason'],
                        properties={k: dict(type='string', nullable=k in ('source_request_id', 'source_observed_at', 'unknown_time_reason'))
                                    for k in ('provider', 'api_version', 'parser_version', 'source_request_id', 'source_observed_at', 'unknown_time_reason')}),
                    **{k: dict(type='string', format='date-time') for k in ('requested_at', 'received_at')},
                    'effective_parameters': dict(type='object'), 'is_partial': dict(type='boolean'), 'is_truncated': dict(type='boolean'),
                    'termination_reason': dict(type='string'), 'warnings': dict(type='array', items=dict(type='string')),
                    'raw': dict(type='object', required=['status', 'payload', 'redacted_paths', 'reason'],
                                properties=dict(status=dict(type='string', enum=['stored', 'redacted', 'not_retained']),
                                                payload=dict(nullable=True), redacted_paths=dict(type='array', items=dict(type='string')),
                                                reason=dict(type='string', nullable=True))),
                    'data': dict(oneOf=[{'$ref': '#/components/schemas/' + name} for name in ('CapabilityTrendsData', 'CapabilityVolumesData', 'CapabilityAnswersData')])})
    schemas['CapabilityJobData'] = dict(type='object', required=['job_id', 'local_cache_reuse', 'cache_hit', 'cache_hit_basis', 'cache_age_seconds', 'cache_age_basis', 'observation'], properties=dict(
        job_id=dict(type='string', format='uuid'), local_cache_reuse=dict(type='boolean'), cache_hit=dict(type='boolean', nullable=True),
        cache_hit_basis=dict(type='string'), cache_age_seconds=dict(type='string', nullable=True), cache_age_basis=dict(type='string', nullable=True),
        observation={'$ref': '#/components/schemas/CapabilityObservation'}))
    page_properties = dict(schema_version=dict(type='string'), has_more=dict(type='boolean'), next_cursor=dict(type='string', nullable=True),
                           records=dict(type='array', items=dict(type='object')), limit=dict(type='integer'), sort=dict(type='string'))
    schemas['CapabilityRecordPage'] = dict(type='object', required=['schema_version', 'records', 'has_more', 'next_cursor', 'limit', 'sort'], properties=page_properties)
    schemas['CapabilityJob'] = dict(type='object', required=['id', 'capability', 'parameters', 'status', 'reserved', 'actual_cost', 'reservation_status', 'platform_delivery'],
        properties=dict(id=dict(type='string', format='uuid'), capability=dict(type='string', enum=list(CAPABILITIES)), parameters=dict(type='object'),
                        status=dict(type='string', enum=['queued', 'running', 'succeeded', 'partial', 'failed', 'canceled', 'reconciliation_required']),
                        source_retry_policy={'$ref':'#/components/schemas/CapabilitySourceRetryPolicy'},
                        reserved=dict(type='string'), actual_cost=dict(type='string', nullable=True),
                        reservation_status=dict(type='string', enum=['held', 'settled']), platform_delivery=dict(type='string', enum=['not_submitted'])))

    def param(name, *, type='string', **schema):
        return dict(name=name, **{'in': 'query'}, schema=dict(type=type, **schema))

    filters = [param('capability', enum=list(CAPABILITIES)), param('provider'), param('country', pattern='^[A-Za-z]{2}$'), param('language'),
               param('from', format='date-time', description='Stored/created timestamp inclusive, with timezone'),
               param('to', format='date-time', description='Stored/created timestamp exclusive, with timezone'),
               param('limit', type='integer', minimum=1, maximum=50, default=20), param('cursor', maxLength=16384)]
    child_params = [param('limit', type='integer', minimum=1, maximum=500, default=100), param('cursor', maxLength=16384)]
    formats = ['json', 'jsonl', 'csv', 'zip', 'manifest']
    response_error = dict(description='Structured error; current admission/rights required. Not configured is 409/503.',
                          content={'application/json': dict(schema={'$ref': '#/components/schemas/Error'})})

    def operation(summary, *, method='get', parameters=(), schema=None, code='200', body=None, binary=False):
        content = ({mime: dict(schema=dict(type='string', format='binary')) for mime in
                    ('application/json', 'application/x-ndjson', 'text/csv', 'application/zip')} if binary else
                   {'application/json': dict(schema=schema or dict(type='object'))})
        value = dict(summary=summary, tags=['新数据域（需单独准入）'], parameters=list(parameters),
                     description='Local candidate contract; no real source or production release is claimed. Scope own only. Existing site-access key + customer Bearer authentication is required. Erased observations cannot be read, reused, rebuilt or delivered (410); cross-tenant IDs remain 404. Download generation rechecks current rights, erasure and fixed version before sending. Already delivered external files cannot be remotely recalled.',
                     responses={code: dict(description='受理，尚未完成' if code == '202' else '成功', content=content),
                                **{str(s): dict(response_error) for s in (400, 401, 404, 405, 409, 410, 413, 429, 503)}})
        if body is not None:
            value['requestBody'] = dict(required=True, content={'application/json': dict(schema=body)})
        return {method: value}

    paths = doc['paths']
    paths['/v1/data/jobs'] = operation('幂等提交新数据任务（需要预算及可用执行器）', method='post', code='202',
        parameters=[dict(name='Idempotency-Key', **{'in': 'header'}, required=True, schema=dict(type='string', minLength=1, maxLength=120))],
        body={'$ref': '#/components/schemas/CapabilityJobRequest'}, schema={'$ref': '#/components/schemas/CapabilityJob'})
    import copy
    preview_schemas = copy.deepcopy(request_schemas())
    for preview in preview_schemas:
        preview['required'].remove('budget')
        preview['properties'].pop('budget')
    paths['/v1/data/jobs/preview'] = operation('只读来源与费用上界预览，不创建任务或预留', method='post', body=dict(oneOf=preview_schemas))
    paths['/v1/data/jobs/preview']['post']['responses']['200']['content']['application/json']['schema']=dict(type='object',properties=dict(source_retry_policy={'$ref':'#/components/schemas/CapabilitySourceRetryPolicy'}))
    paths['/v1/data/jobs/preview']['post']['description'] += ' source_retry_policy is trusted operator configuration or null; customer JSON cannot enable automatic retries. It is frozen on creation and rechecked before every extra source call.'
    paths['/v1/data/jobs/preview']['post']['description'] += ' Returns the approved complete-job cost ceiling, unit, currency, source/license/use/retention, ready executor flag and a read-only UTC budget snapshot. Valid at most 60 seconds; submission always rechecks. It is not an actual source invoice or an acquired price quote.'
    paths['/v1/data/jobs']['post']['description'] += ' Budget upper bound includes approved retries/expansion; no legacy search quota debit. Queue timeout is reported by the accepted job. Unknown result of an in-flight call requires reconciliation, not automatic retry.'
    schemas['CapabilitySourceResponse']=dict(type='object',required=['response_id','job_id','raw_status','payload','snapshot_hash','normalized_fact_status'],
        properties=dict(response_id=dict(type='string',format='uuid'),job_id=dict(type='string',format='uuid'),
                        raw_status=dict(type='string',enum=['stored','redacted','not_retained']),payload=dict(nullable=True),
                        http_status=dict(type='integer',nullable=True),scope_verified=dict(type='boolean'),response_complete=dict(type='boolean'),
                        capture_kind=dict(type='string',enum=['source_call_journal']),call_count=dict(type='integer',minimum=1,maximum=8),
                        normalized_fact_status=dict(type='string',enum=['not_asserted']),snapshot_hash=dict(type='string'),payload_hash=dict(type='string',nullable=True),
                        expires_at=dict(type='string',format='date-time')))
    job_identity=dict(name='id',**{'in':'path'},required=True,schema=dict(type='string',format='uuid'))
    schemas['CapabilityRetryPlan']=dict(type='object',required=['parent_job_id','parent_observation_id','parent_snapshot_hash','parent_payload_hash','plan_hash','capability','eligible','parameters','input_mapping','excluded','policy','billing','parent_facts'],properties=dict(
        parent_job_id=dict(type='string',format='uuid'),parent_observation_id=dict(type='string',format='uuid'),parent_snapshot_hash=dict(type='string'),parent_payload_hash=dict(type='string'),plan_hash=dict(type='string'),capability=dict(type='string',enum=['keyword_volume']),eligible=dict(type='boolean'),parameters=dict(type='object',nullable=True),input_mapping=dict(type='array',items=dict(type='object',required=['child_input_index','parent_input_index','requested_query'],properties=dict(child_input_index=dict(type='integer'),parent_input_index=dict(type='integer'),requested_query=dict(type='string')))),excluded=dict(type='array',items=dict(type='object')),policy=dict(type='string'),billing=dict(type='string'),parent_facts=dict(type='string'),existing_child_job_id=dict(type='string',nullable=True),creation_available=dict(type='boolean'),reservation_created=dict(type='boolean')))
    paths['/v1/jobs/{id}/retry']=operation('只读明确失败关键词重试范围，不调用来源或预留',parameters=[job_identity],schema={'$ref':'#/components/schemas/CapabilityRetryPlan'})
    paths['/v1/jobs/{id}/retry'].update(operation('显式创建仅失败输入的独立计费子任务',method='post',code='202',parameters=[job_identity,dict(name='Idempotency-Key',**{'in':'header'},required=True,schema=dict(type='string',minLength=1,maxLength=120))],body=dict(type='object',required=['plan_hash','budget'],additionalProperties=False,properties=dict(plan_hash=dict(type='string',minLength=64,maxLength=64),budget=dict(type='object',required=['maximum_cost','currency'],additionalProperties=False,properties=dict(maximum_cost=dict(type='string'),currency=dict(type='string',pattern='^[A-Z]{3}$'))))),schema=dict(allOf=[{'$ref':'#/components/schemas/CapabilityJob'},dict(type='object',required=['retry'],properties=dict(retry={'$ref':'#/components/schemas/CapabilityRetryPlan'}))])))
    lineage_schema = dict(anyOf=[
        dict(type='object',required=['is_retry'],additionalProperties=False,
             properties=dict(is_retry=dict(type='boolean',enum=[False]))),
        dict(allOf=[{'$ref':'#/components/schemas/CapabilityRetryPlan'},
             dict(type='object',required=['is_retry'],properties=dict(is_retry=dict(type='boolean',enum=[True])))])])
    paths['/v1/jobs/{id}/lineage']=operation('重试子任务与固定父版本及原输入关联；复查父/子当前权限',parameters=[job_identity],schema=lineage_schema)
    for op in paths['/v1/jobs/{id}/retry'].values():
        op['description'] += ' Only settled partial keyword_volume with explicit source_error and no saved group is eligible. Unknown billing, missing/low/zero or successful inputs never authorize retries. The server derives keywords and immutable parent version; one child per parent observation (including cache aliases) is committed atomically with its fresh reservation. Same key/plan/budget replays; different key409 retry_already_created, changed plan409 version_conflict. No provider call is retried automatically; no merge/overwrite of parent facts or source bill.'
    paths['/v1/jobs/{id}/response']=operation('独立来源响应；不证明任务或标准事实成功',parameters=[job_identity],schema={'$ref':'#/components/schemas/CapabilitySourceResponse'})
    paths['/v1/jobs/{id}/response/export']=operation('固定来源响应五格式完整导出',parameters=[job_identity,param('format',enum=formats,default='zip'),param('snapshot_hash')],binary=True)
    for suffix in ('response','response/export'):
        paths['/v1/jobs/{id}/'+suffix]['get']['description'] += ' Requires explicit response spool and current source/raw-retention rights. 409 while pending, 410 after expiry/erase/cancel; no source retry or normalized observation is created. ZIP contains full response.json/jsonl/csv/manifest with independently pinned response_id/snapshot_hash. Files already downloaded cannot be remotely recalled.'
        paths['/v1/jobs/{id}/'+suffix]['get']['description'] += ' A sealed source_call_journal preserves up to 8 ordered immutable receipts within the SAME total payload reservation. Each receipt has its own scope/completeness/redaction status; an unretainable phase has payload=null and does not erase earlier scoped receipts. A sealed journal does not establish full answer expansion or final billing.'
    paths['/v1/data/observations'] = operation('新域观测列表（元数据、不含大响应）', parameters=filters, schema={'$ref': '#/components/schemas/CapabilityRecordPage'})
    paths['/v1/data/observations/{id}'] = operation('固定观测完整事实及摘要', schema={'$ref': '#/components/schemas/CapabilityObservation'})
    for prefix, caps, children in (('/v1/data/trends/series', ['trends_history'], ['points']),
                                   ('/v1/data/keyword-volumes', ['keyword_volume'], ['months']),
                                   ('/v1/data/ai/answers', ['aio_answer', 'ai_mode_answer'], ['blocks', 'citations', 'links'])):
        narrowed = [dict(p, schema=dict(p['schema'], enum=caps)) if p['name'] == 'capability' else p for p in filters]
        paths[prefix] = operation('独立模型列表（子记录单独分页）', parameters=narrowed, schema={'$ref': '#/components/schemas/CapabilityRecordPage'})
        paths[prefix + '/{id}'] = operation('完整模型详情；id为列表返回的object_id，保留原始record.id', schema={'$ref': '#/components/schemas/CapabilityTypedRecord'})
        for child in children:
            paths[prefix + '/{id}/' + child] = operation('固定观测的' + child + '（源顺序）', parameters=child_params, schema={'$ref': '#/components/schemas/CapabilityRecordPage'})
    for path, operations in paths.items():
        if path.startswith(('/v1/data/observations/', '/v1/data/trends/', '/v1/data/keyword-volumes/', '/v1/data/ai/')) and '{id}' in path:
            for value in operations.values():
                value.setdefault('parameters', []).append(dict(name='id', **{'in': 'path'}, required=True,
                                                              schema=dict(type='string', maxLength=8192)))
    paths['/v1/jobs']['get'].setdefault('parameters', []).append(param('domain', enum=['search', 'data'], default='search'))
    paths['/v1/jobs']['get'].setdefault('description', '')
    paths['/v1/jobs']['get']['description'] += ' domain=search(default) preserves existing behavior. domain=data uses capability/provider/country/language/from/to/status/limit(1–50)/cursor with independent money reservations.'
    existing = {p['name'] for p in paths['/v1/jobs']['get']['parameters']}
    paths['/v1/jobs']['get']['parameters'] += [p for p in filters if p['name'] not in existing]
    for action in ('', '/progress', '/data', '/cancel', '/export'):
        operation_map = paths.get('/v1/jobs/{id}' + action, {})
        for value in operation_map.values():
            value['description'] = value.get('description', '') + ' New data jobs use the same owned-job UUID path. Source rights are rechecked; canceled in-flight work may still have actual cost. /data returns the immutable observation and local cache association; source cache status may be unknown. New-domain /export supports json/jsonl/csv/zip/manifest with optional snapshot_hash; 410 after retention expiry or observation erasure. Export rechecks erasure and current rights after file generation. Pause/resume are not supported for new domains.'
    charge=dict(type='object',required=['phase','reference','actual_cost','currency','billing_unit'],properties=dict(
        phase=dict(type='string'),reference=dict(type='string'),actual_cost=dict(type='string'),currency=dict(type='string'),billing_unit=dict(type='string')))
    schemas['CapabilityProgress']=dict(type='object',required=['id','domain','status','confirmed_call_charges','confirmed_call_cost','call_charge_basis'],properties=dict(
        id=dict(type='string',format='uuid'),domain=dict(type='string',enum=['data']),status=dict(type='string'),
        source_retry_policy={'$ref':'#/components/schemas/CapabilitySourceRetryPolicy'},
        source_call_attempts=dict(type='array',maxItems=8,items=dict(type='object',properties=dict(phase=dict(type='string'),maximum_cost=dict(type='string'),entered_at=dict(type='string',format='date-time'),bill_status=dict(type='string',enum=['confirmed','unconfirmed'])))),
        confirmed_call_charges=dict(type='array',maxItems=8,items=charge),confirmed_call_cost=dict(type='string',nullable=True),
        call_charge_basis=dict(type='string',enum=['confirmed_call_receipts_not_final_settlement','not_reported']),actual_cost=dict(type='string',nullable=True)))
    progress=paths['/v1/jobs/{id}/progress']['get']
    legacy_progress=progress['responses']['200']['content']['application/json']['schema']
    progress['responses']['200']['content']['application/json']['schema']=dict(anyOf=[legacy_progress,{'$ref':'#/components/schemas/CapabilityProgress'}])
    progress['description'] += ' source_retry_policy is frozen trusted operator policy or null (no extra retry permission; native AIO may still use search plus expansion). Extra Google Ads/SerpApi calls require confirmed invoices and error-only HTTP429/503 within total budget/count/window. SerpApi query_retry_N/search_retry_N/expand_retry_N retry only the failed native leg; AIO shares count/window across search and expansion, never repeats a successful search to recover expansion. Unknown billing or returned successful data is never automatically replayed.'
    progress['description'] += ' New-domain confirmed_call_charges/cost show only durable per-call invoices; null means no receipt, never a free call. They may be incomplete and do not replace actual_cost or authorize retries. Unknown later calls keep reservations held pending reconciliation.'
    paths['/v1/jobs/{id}/data']['get']['responses']['200']['content'] = {'application/json': {'schema': {'oneOf': [
        {'$ref': '#/components/schemas/DataRecord'}, {'$ref': '#/components/schemas/CapabilityJobData'}]}}}
    for suffix in ('','/progress','/data','/export','/cancel','/lineage','/retry'):
        for operation_value in paths.get('/v1/jobs/{id}'+suffix,{}).values():
            operation_value.setdefault('responses',{}).update({'410':dict(response_error),'404':dict(response_error),'503':dict(response_error)})
            operation_value['description']=operation_value.get('description','')+' A configured restore gate returns 503/restore_required before fresh pinned v3 recovery; a stored replay receipt alone cannot admit this process. For new data jobs, trusted query-content erasure returns 410 (foreign tenant 404), excludes the job from lists and blocks the old idempotency key. Erasure cancels queued jobs at zero cost; in-flight actual charges remain held/settled separately, never assumed free. Already settled fees/status stay unchanged; no public erasure endpoint is provided.'
    export = paths['/v1/jobs/{id}/export']['get']
    export.setdefault('parameters', []).append(param('snapshot_hash'))
    for p in export['parameters']:
        if p['name'] == 'format':
            p['schema']['enum'] = formats
    snapshot = paths['/v1/data/export']['get']
    snapshot['parameters'] += [param('capability', enum=list(CAPABILITIES)), param('observation_id', format='uuid'), param('snapshot_hash')]
    for p in snapshot['parameters']:
        if p['name'] == 'format':
            p['schema']['enum'] = formats
    snapshot['description'] += ' New domains require capability + observation_id; only format/snapshot_hash are additionally allowed. This selects exactly one immutable observation, not legacy URL-domain filters. json/manifest are only supported for this new-domain form; multi-observation data-domain snapshots are not connected. New-domain pull subscriptions use domain=data at /v1/data/subscriptions.'
    # Extend the main subscription contract without changing legacy defaults.
    subscription = dict(type='object', required=['domain', 'name', 'filters'], additionalProperties=False,
        properties=dict(domain=dict(type='string', enum=['data']), name=dict(type='string', minLength=1, maxLength=200),
            mode=dict(type='string', enum=['observations'], default='observations'),
            filters=dict(type='object', required=['capability'], additionalProperties=False,
                properties={p['name']: p['schema'] for p in filters if p['name'] in ('capability', 'provider', 'country', 'language', 'from', 'to')}),
            start_mode=dict(type='string', enum=['history', 'now'], default='history'),
            interval_seconds=dict(type='integer', minimum=60, maximum=604800, default=3600)))
    doc['components']['schemas']['CapabilitySubscriptionRequest'] = subscription
    root = paths['/v1/data/subscriptions']
    root['get'].setdefault('parameters', []).append(param('domain', enum=['search', 'data'], default='search'))
    root['get']['description'] += ' domain=data selects owned new-domain subscriptions; default preserves legacy. Explicit opt-in runtime required.'
    request = root['post']['requestBody']['content']['application/json']
    request['schema'] = dict(oneOf=[request['schema'], {'$ref': '#/components/schemas/CapabilitySubscriptionRequest'}])
    root['post']['description'] += ' New domains: domain=data, mode=observations, required filters.capability, optional provider/country/language/from/to. Creates paused. No source calls, task or budget reservation. Same key/different normalized parameters returns409.'
    event = dict(type='object', required=['schema_version', 'observation_id', 'event_id', 'event_type', 'supersedes', 'capability', 'payload_hash', 'snapshot_hash'],
        additionalProperties=False, properties=dict(schema_version=dict(type='string', enum=['searchflow-observation-batch-1']),
            observation_id=dict(type='string', format='uuid'), event_id=dict(type='string', format='uuid'),
            event_type=dict(type='string', enum=['new_observation', 'revision']), supersedes=dict(type='string', format='uuid', nullable=True),
            capability=dict(type='string', enum=list(CAPABILITIES)), payload_hash=dict(type='string'), snapshot_hash=dict(type='string')))
    doc['components']['schemas']['CapabilitySubscriptionEvent'] = event
    doc['components']['schemas']['CapabilitySubscriptionBatch'] = dict(type='object', required=['batch_id', 'sha256', 'payload', 'observation', 'checksum_encoding', 'created_at', 'acknowledged_at', 'discarded_at'],
        properties=dict(batch_id=dict(type='string', format='uuid'), sha256=dict(type='string'),
            payload={'$ref': '#/components/schemas/CapabilitySubscriptionEvent'}, observation={'$ref': '#/components/schemas/CapabilityObservation'},
            checksum_encoding=dict(type='string'), created_at=dict(type='string', format='date-time'),
            acknowledged_at=dict(type='string', format='date-time', nullable=True), discarded_at=dict(type='string', format='date-time', nullable=True)))
    for path, operations in paths.items():
        if path.startswith('/v1/data/subscriptions/{id}/'):
            for op in operations.values():
                op.setdefault('responses',{}).update({'410':dict(response_error),'404':dict(response_error),'503':dict(response_error)})
                op['description'] += ' Trusted subscription erasure clears name, filters, plaintext key and delivery records; all subsequent owned reads/actions return 410, foreign tenant 404. No public erasure route. Subscription markers are included in v3 joint checkpoints. Legacy v1/v2 replay refuses while subscription markers exist and cannot admit a configured v3 startup gate. For domain=data subscription UUIDs: one complete immutable observation per batch, event_type=new_observation/revision; no URL dedup or cache-reuse event. Pending batch retries are stable, including after pause. Current source admission and retention are checked on read/export/ack; owner may pause/discard after revocation. Only checksum-confirmed ack advances ID ledger. now excludes the creation snapshot, not a time watermark. pull does not accept limit; empty polls honor interval, ack permits immediate backlog drain. Batch history cursors bind tenant/subscription. Counts include unacknowledged eligible observations. Files remain identical after ack; metadata lists omit source contents.'
    bexport = paths['/v1/data/subscriptions/{id}/batches/{batch_id}/export']['get']
    for p in bexport['parameters']:
        if p['name'] == 'format':
            p['schema']['enum'] = formats
    bexport['description'] += ' New-domain formats=json/jsonl/csv/zip/manifest. Every data file contains batch event and full observation; CSV stores one full envelope JSON cell. ZIP includes observation.zip with independent existing hash verifier, batch files and outer manifest SHA256/bytes. Legacy formats remain csv/jsonl/manifest only. 256MiB aggregate bound rejects without truncation/ack.'
    return doc
