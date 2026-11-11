"""Immutable, tenant-owned source facts and atomic completion for new domains.

The envelope is the authority; relational child rows are rebuildable projections.
All public reads recheck current source rights and expiry. A cache reuse is a
job association, never a new observation. No old SERP table is read or changed.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from psycopg.types.json import Jsonb

from .data_capability_contract import admit_request, request_digest
from .data_capability_facts import validate_result, timestamp
from .data_capability_jobs import CapabilityJobs, SCHEMA as JOB_SCHEMA, digest, job_id, required_text
from .product import ProductError

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_capability_observations (
 id uuid PRIMARY KEY, tenant_id text NOT NULL, event_key text NOT NULL,
 entity_id text NOT NULL, capability text NOT NULL, provider text NOT NULL,
 license_id text NOT NULL, admission jsonb NOT NULL, parameters jsonb NOT NULL,
 revision text, supersedes uuid REFERENCES sf_capability_observations(id),
 payload jsonb NOT NULL, payload_hash text NOT NULL, raw_hash text,
 created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 UNIQUE(tenant_id,event_key)
);
CREATE INDEX IF NOT EXISTS sf_capability_observations_owner
 ON sf_capability_observations(tenant_id,capability,created_at,id);
CREATE TABLE IF NOT EXISTS sf_capability_job_observations (
 job_id uuid PRIMARY KEY REFERENCES sf_capability_jobs(id),
 observation_id uuid NOT NULL REFERENCES sf_capability_observations(id),
 cache_reused boolean NOT NULL, cache_age_seconds numeric,
 attached_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS sf_capability_fact_rows (
 observation_id uuid NOT NULL REFERENCES sf_capability_observations(id),
 kind text NOT NULL CHECK(kind IN ('series','point','volume','month','answer','block','citation','link')),
 parent_id text NOT NULL, source_id text NOT NULL, position integer NOT NULL,
 payload jsonb NOT NULL,
 PRIMARY KEY(observation_id,kind,parent_id,source_id),
 UNIQUE(observation_id,kind,parent_id,position)
);
"""
KINDS = ('series', 'point', 'volume', 'month', 'answer', 'block', 'citation', 'link')


def projections(result):
    """Preserve every record and parent relation; no URL/entity dedup here."""
    data = result['data']
    if 'series' in data:
        for i, series in enumerate(data['series']):
            yield ('series', '', series['id'], i, {k: v for k, v in series.items() if k != 'points'})
            for j, point in enumerate(series['points']):
                yield ('point', series['id'], point['id'], j, point)
    elif 'groups' in data:
        for i, group in enumerate(data['groups']):
            yield ('volume', '', group['id'], i, {k: v for k, v in group.items() if k != 'monthly'})
            for j, point in enumerate(group['monthly']):
                yield ('month', group['id'], f"{point['year']:04d}-{point['month']:02d}", j, point)
    else:
        answer = data['answer']
        yield ('answer', '', answer['id'], 0, {k: v for k, v in answer.items() if k not in ('blocks', 'citations', 'links')})
        for key, kind in (('blocks', 'block'), ('citations', 'citation'), ('links', 'link')):
            for i, value in enumerate(answer[key]):
                yield (kind, answer['id'], value.get('id', str(i)), i, value)


class CapabilityData(CapabilityJobs):
    def initialize(self):
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(20261110,20)')
            db.execute('SELECT pg_advisory_xact_lock(20261110,21)')
            db.execute(JOB_SCHEMA)
            db.execute(SCHEMA)
            from .data_capability_subscriptions import SCHEMA as SUBSCRIPTION_SCHEMA
            db.execute(SUBSCRIPTION_SCHEMA)
            from .data_capability_retry import SCHEMA as RETRY_SCHEMA
            db.execute(RETRY_SCHEMA)
            from .data_capability_fact_retention import SCHEMA as ERASURE_SCHEMA
            db.execute(ERASURE_SCHEMA)

    def _authorize(self, row, grant, now):
        admitted = admit_request(row['parameters'], grant, grant.get('maximum_cost') if grant else 0,
                                 grant.get('currency') if grant else '', now=now)
        if (admitted['provider'] != row['provider'] or admitted['license_id'] != row['license_id']
                or any(grant[k] != row['admission'][k] for k in ('allowed_use', 'retention_days', 'billing_unit', 'currency'))):
            raise ProductError('admission_changed', '事实的来源准入已变化', 409)
        if row['expires_at'] <= now:
            raise ProductError('expired', '事实已超过保留期限', 410)

    @staticmethod
    def _authorize_raw(payload, grant):
        # A normalized-only fact needs no raw permission. Stored or redacted
        # source content still does; missing permission must fail closed.
        if payload['raw']['payload'] is not None and (not grant or grant.get('raw_response_retention_allowed') is not True):
            raise ProductError('raw_retention_not_admitted', '当前来源未允许保存或交付原文', 409)

    @staticmethod
    def _public_observation(row):
        snapshot = dict(observation_id=str(row['id']), entity_id=row['entity_id'], capability=row['capability'],
                    provider=row['provider'], license_id=row['license_id'], parameters=row['parameters'],
                    revision=row['revision'], supersedes=str(row['supersedes']) if row['supersedes'] else None,
                    payload_hash=row['payload_hash'], raw_hash=row['raw_hash'],
                    hash_encoding='sha256-canonical-json-utf8-not-source-bytes',
                    stored_at=row['created_at'].isoformat(), expires_at=row['expires_at'].isoformat(), payload=row['payload'],
                    platform_delivery='not_submitted')
        snapshot['snapshot_hash'] = digest(snapshot)
        return snapshot

    def _observation(self, db, tenant_id, oid, grant):
        self.require_recovered()
        row = db.execute('SELECT * FROM sf_capability_observations WHERE id=%s AND tenant_id=%s FOR SHARE',
                         (job_id(oid), tenant_id)).fetchone()
        if not row:
            raise ProductError('not_found', '观测不存在', 404)
        from .data_capability_fact_retention import assert_live
        assert_live(db,tenant_id,oid=oid)
        self._authorize(row, grant, self.now())
        self._check_snapshot(row)
        self._authorize_raw(row['payload'], grant)
        return row

    @staticmethod
    def _check_snapshot(row):
        if digest(row['payload']) != row['payload_hash']:
            raise ProductError('corrupt_snapshot', '不可变事实摘要不一致，拒绝交付', 503)
        raw = row['payload']['raw']['payload']
        if row['raw_hash'] != (digest(raw) if raw is not None else None):
            raise ProductError('corrupt_snapshot', '原响应摘要不一致，拒绝交付', 503)

    def _validate_result_reference(self, db, row, result_ref, status):
        saved = db.execute('SELECT o.* FROM sf_capability_observations o JOIN sf_capability_job_observations a '
                           'ON a.observation_id=o.id WHERE a.job_id=%s AND o.id=%s AND o.tenant_id=%s',
                           (row['id'], job_id(result_ref), row['tenant_id'])).fetchone()
        if not saved or saved['expires_at'] <= self.now():
            raise ProductError('result_not_saved', '缺少有效的任务事实快照', 409)
        from .data_capability_fact_retention import assert_live
        assert_live(db,row['tenant_id'],oid=saved['id'])
        self._check_snapshot(saved)
        expected = 'partial' if saved['payload']['is_partial'] else 'succeeded'
        if status != expected:
            raise ProductError('result_state_mismatch', '任务终态必须与保存事实的完整性一致', 409)

    def complete(self, tenant_id, jid, lease_token, result, actual_cost, grant, *, event_key, revision=None, supersedes=None):
        """Atomically save source facts, projections, association and settlement."""
        self.require_recovered()
        required_text(event_key, 'event_key')
        if revision is not None:
            required_text(revision, 'revision')
        now = self.now()
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock_shared(20261110,30)')
            job = self._get(db, tenant_id, jid, lock=True)
            # Cancellation still accounts for an actual billed response, but
            # must not persist its content or publish a new observation.
            if job['cancel_requested']:
                return self._settle(tenant_id, jid, 'canceled', actual_cost, None, None,
                                    lease_token, None, connection=db)
            if job['started_at'] is None or job['status'] not in ('running', 'succeeded', 'partial'):
                raise ProductError('stale_lease', '事实保存需要已执行任务', 409)
            from .data_capability_job_retention import assert_live
            assert_live(db,tenant_id,jid)
            clean = validate_result(job['parameters'], result)
            admitted = admit_request(job['parameters'], grant, job['reserved'], job['currency'], now=now)
            self._authorize_raw(clean, grant)
            if admitted['provider'] != job['provider'] or admitted['license_id'] != job['license_id'] or clean['source']['provider'] != job['provider']:
                raise ProductError('admission_changed', '任务、事实和当前来源不符', 409)
            if any(grant[k] != job['admission'][k] for k in ('allowed_use', 'retention_days', 'billing_unit')):
                raise ProductError('admission_changed', '保存用途或保留规则已变化', 409)
            received = timestamp(clean['received_at'])
            if received > now or timestamp(clean['requested_at']) < job['started_at']:
                raise ProductError('invalid_fact', '新观测时间不在本次任务执行范围')
            try:
                expiry = min(received + timedelta(days=grant['retention_days']), timestamp(grant['expires_at']))
            except OverflowError:
                raise ProductError('admission_invalid', '来源保留期限超出日期范围', 409) from None
            if expiry <= now:
                raise ProductError('expired', '接收的事实已过期', 410)
            entity_scope = dict(parameters_hash=request_digest(job['parameters']), provider=job['provider'],
                                license_id=job['license_id'])
            if job['capability'] == 'trends_history':
                # Different effective windows/scales/comparison groups can
                # never be revisions of the same measured entity.
                entity_scope['scales'] = [{k: s[k] for k in ('id', 'scale_id', 'measurement_type', 'normalization',
                                                             'comparison_group', 'effective_window', 'term_or_topic', 'resolved_subject',
                                                             'granularity', 'source_timezone')}
                                          for s in clean['data']['series']]
            entity_id = digest(dict(tenant_id=tenant_id, scope=entity_scope))
            prior_id = job_id(supersedes) if supersedes is not None else None
            if prior_id:
                from .data_capability_fact_retention import assert_live
                assert_live(db,tenant_id,oid=prior_id)
                prior = db.execute('SELECT * FROM sf_capability_observations WHERE tenant_id=%s AND id=%s',
                                   (tenant_id, prior_id)).fetchone()
                if not prior or prior['entity_id'] != entity_id or revision is None:
                    raise ProductError('invalid_revision', '修订必须引用相同租户、来源、条件和尺度的实体并提供版本', 409)
            payload_hash = digest(clean)
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,20261111))', (tenant_id + ':' + event_key,))
            from .data_capability_fact_retention import assert_live
            assert_live(db,tenant_id,event_key=event_key)
            row = db.execute('SELECT * FROM sf_capability_observations WHERE tenant_id=%s AND event_key=%s',
                             (tenant_id, event_key)).fetchone()
            if row:
                link = db.execute('SELECT job_id FROM sf_capability_job_observations WHERE observation_id=%s AND job_id=%s',
                                  (row['id'], job['id'])).fetchone()
                if (not link or row['payload_hash'] != payload_hash or row['revision'] != revision
                        or (str(row['supersedes']) if row['supersedes'] else None) != prior_id):
                    raise ProductError('event_conflict', '同一事件不能更换payload、任务或修订关系', 409)
            else:
                if job['status'] != 'running' or str(job['lease_token']) != lease_token or job['lease_expires_at'] <= now:
                    raise ProductError('stale_lease', '保存事实需要有效的执行lease', 409)
                existing = db.execute('SELECT 1 FROM sf_capability_job_observations WHERE job_id=%s', (job['id'],)).fetchone()
                if existing:
                    raise ProductError('event_conflict', '本任务已关联其他观测', 409)
                row = db.execute('INSERT INTO sf_capability_observations(id,tenant_id,event_key,entity_id,capability,provider,'
                                 'license_id,admission,parameters,revision,supersedes,payload,payload_hash,raw_hash,created_at,expires_at) '
                                 'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                                 (str(uuid4()), tenant_id, event_key, entity_id, job['capability'], job['provider'], job['license_id'], Jsonb(job['admission']),
                                  Jsonb(job['parameters']), revision, prior_id, Jsonb(clean), payload_hash,
                                  digest(clean['raw']['payload']) if clean['raw']['payload'] is not None else None, now, expiry)).fetchone()
                self._project(db, row['id'], clean)
                db.execute('INSERT INTO sf_capability_job_observations VALUES(%s,%s,false,NULL,%s)',
                           (job['id'], row['id'], now))
            status = 'partial' if clean['is_partial'] else 'succeeded'
            return self._settle(tenant_id, jid, status, actual_cost, str(row['id']), None,
                                lease_token, None, connection=db)

    def reuse(self, tenant_id, jid, lease_token, observation_id, actual_cost, grant):
        """Associate an authorized unexpired original, without a new sample."""
        self.require_recovered()
        now = self.now()
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock_shared(20261110,30)')
            job = self._get(db, tenant_id, jid, lock=True)
            if job['cancel_requested']:
                return self._settle(tenant_id, jid, 'canceled', actual_cost, None, None,
                                    lease_token, None, connection=db)
            from .data_capability_job_retention import assert_live
            assert_live(db,tenant_id,jid)
            observation = self._observation(db, tenant_id, observation_id, grant)
            if (job['parameters'] != observation['parameters'] or job['provider'] != observation['provider']
                    or job['license_id'] != observation['license_id']):
                raise ProductError('cache_scope_mismatch', '缓存来源或条件不同', 409)
            existing = db.execute('SELECT * FROM sf_capability_job_observations WHERE job_id=%s', (job['id'],)).fetchone()
            if existing:
                if not existing['cache_reused'] or str(existing['observation_id']) != str(observation['id']):
                    raise ProductError('event_conflict', '本任务已关联其他观测', 409)
            else:
                if job['status'] != 'running' or str(job['lease_token']) != lease_token or job['lease_expires_at'] <= now:
                    raise ProductError('stale_lease', '缓存复用需要有效lease', 409)
                source_time = observation['payload']['source']['source_observed_at'] or observation['payload']['received_at']
                delta = now - timestamp(source_time)
                age = (Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000) if delta >= timedelta(0) else None
                db.execute('INSERT INTO sf_capability_job_observations VALUES(%s,%s,true,%s,%s)',
                           (job['id'], observation['id'], str(age) if age is not None else None, now))
            return self._settle(tenant_id, jid, 'partial' if observation['payload']['is_partial'] else 'succeeded',
                                actual_cost, str(observation['id']), None, lease_token, None, connection=db)

    @staticmethod
    def _project(db, oid, payload):
        with db.cursor() as cursor:
            cursor.executemany('INSERT INTO sf_capability_fact_rows VALUES(%s,%s,%s,%s,%s,%s)',
                               ((oid, kind, parent, source_id, position, Jsonb(record))
                                for kind, parent, source_id, position, record in projections(payload)))

    def rebuild_projections(self, tenant_id, oid, grant):
        """Trusted repair port; only child projections change, never source facts."""
        with self.connect() as db:
            db.execute('SELECT id FROM sf_capability_observations WHERE id=%s AND tenant_id=%s FOR UPDATE', (job_id(oid),tenant_id))
            observation = self._observation(db, tenant_id, oid, grant)
            db.execute('DELETE FROM sf_capability_fact_rows WHERE observation_id=%s', (observation['id'],))
            self._project(db, observation['id'], observation['payload'])
            return dict(observation_id=str(observation['id']), payload_hash=observation['payload_hash'])

    def observation(self, tenant_id, oid, grant):
        self.require_recovered()
        with self.connect() as db:
            return self._public_observation(self._observation(db, tenant_id, oid, grant))

    def job_data(self, tenant_id, jid, grant):
        self.require_recovered()
        with self.connect() as db:
            job = self._get(db, tenant_id, jid)
            if job['status'] not in ('succeeded', 'partial'):
                raise ProductError('job_not_ready', '任务尚无可交付事实', 409)
            link = db.execute('SELECT * FROM sf_capability_job_observations WHERE job_id=%s', (job['id'],)).fetchone()
            if not link:
                raise ProductError('result_not_saved', '缺少保存的任务事实', 409)
            observation = self._observation(db, tenant_id, link['observation_id'], grant)
            return dict(job_id=str(job['id']), local_cache_reuse=link['cache_reused'],
                        cache_hit=True if link['cache_reused'] else None,
                        cache_hit_basis='local_observation_reuse' if link['cache_reused'] else 'source_cache_status_unknown',
                        cache_age_seconds=str(link['cache_age_seconds']) if link['cache_age_seconds'] is not None else None,
                        cache_age_basis=('unknown_source_clock_ahead' if link['cache_age_seconds'] is None else
                                         'source_observed_at' if observation['payload']['source']['source_observed_at'] else 'received_at')
                                        if link['cache_reused'] else None,
                        observation=self._public_observation(observation))

    def records(self, tenant_id, oid, kind, grant, *, parent_id='', offset=0, limit=100):
        if kind not in KINDS or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ProductError('invalid_parameter', '无效记录类型或分页参数')
        if not isinstance(parent_id, str):
            raise ProductError('invalid_parameter', 'parent_id必须为文本')
        with self.connect() as db:
            observation = self._observation(db, tenant_id, oid, grant)
            rows = db.execute('SELECT source_id,position,payload FROM sf_capability_fact_rows WHERE observation_id=%s '
                              'AND kind=%s AND parent_id=%s ORDER BY position LIMIT %s OFFSET %s',
                              (observation['id'], kind, parent_id, limit + 1, offset)).fetchall()
            expected = []
            for k, parent, source_id, position, payload in projections(observation['payload']):
                if k == kind and parent == parent_id and position >= offset:
                    expected.append((source_id, position, digest(payload)))
                    if len(expected) == limit + 1:
                        break
            actual = [(r['source_id'], r['position'], digest(r['payload'])) for r in rows]
            if actual != expected:
                raise ProductError('corrupt_projection', '子记录与不可变事实不一致，须重建投影', 503)
            return dict(observation_id=str(observation['id']), kind=kind, parent_id=parent_id,
                        records=[r['payload'] for r in rows[:limit]], has_more=len(rows) > limit,
                        next_offset=offset + limit if len(rows) > limit else None,
                        payload_hash=observation['payload_hash'])
