"""Decoupled, tenant-scoped durable analysis store and single-writer worker.

SQLite is separate from collection PostgreSQL. Importing this module has no side effects.
The runtime is explicitly constructed by the authenticated HTTP integration.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4
import zipfile

from .insights_core import (InsightError, VERSION, LEVELS, csv_bytes, digest, dumps,
    draft_taxonomy, fields, html_report, integer, prepare_records, report,
    rule_annotations, taxonomy, text, validate_annotations)
from .insights_model import ResponsesModel, PROMPT_VERSION

SCHEMA = '''
CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,name TEXT NOT NULL,mode TEXT NOT NULL,
 created TEXT NOT NULL,manifest TEXT NOT NULL,records TEXT NOT NULL,quality TEXT NOT NULL,
 tags TEXT NOT NULL,approved INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,
 annotations TEXT NOT NULL DEFAULT '{}',coding TEXT NOT NULL DEFAULT '{}',reviews TEXT NOT NULL DEFAULT '{}',external INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS datasets_tenant ON datasets(tenant,created);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,dataset TEXT NOT NULL,
 intent TEXT NOT NULL,hash TEXT NOT NULL,kind TEXT NOT NULL,method TEXT NOT NULL,status TEXT NOT NULL,
 created TEXT NOT NULL,updated TEXT NOT NULL,revision INTEGER NOT NULL,max_calls INTEGER NOT NULL,calls INTEGER NOT NULL DEFAULT 0,
 processed INTEGER NOT NULL DEFAULT 0,error TEXT,telemetry TEXT NOT NULL DEFAULT '[]',result TEXT,
 UNIQUE(tenant,intent));
CREATE INDEX IF NOT EXISTS runs_tenant ON runs(tenant,created);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,tenant TEXT NOT NULL,dataset TEXT NOT NULL,
 created TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL);
'''


def now():
    return datetime.now(timezone.utc).isoformat()


class InsightService:
    def __init__(self, path, *, source_loader=None, source_validator=None, model=None, start_worker=True):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.loader, self.validator = source_loader, source_validator
        self.model = model or ResponsesModel()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.thread = None
        # One runtime per file/process; no automatic resumption of uncertain paid calls.
        with self.db() as db:
            db.executescript(SCHEMA)
            db.execute("UPDATE runs SET status='interrupted',error='进程重启；未自动重放模型调用',updated=? WHERE status IN ('queued','running','cancel_requested')", (now(),))
        os.chmod(self.path, 0o600)
        if start_worker:
            self.thread = threading.Thread(target=self._loop, name='text-insights', daemon=True)
            self.thread.start()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def close(self):
        self.stop.set(); self.wake.set()
        if self.thread:
            self.thread.join(timeout=3)

    def capabilities(self):
        return dict(version=VERSION, configured=True, model=self.model.capabilities(),
            max_records=5000, max_text_characters=20000, analysis_modes=list(LEVELS),
            collection_triggered=False, persistence='independent_analysis_store')

    @staticmethod
    def _audit(db, tenant, did, action, detail):
        db.execute('INSERT INTO audit(tenant,dataset,created,action,detail) VALUES(?,?,?,?,?)',
                   (tenant,did,now(),action,dumps(detail)))

    def create_dataset(self, tenant, payload, intent):
        fields(payload, ('name','mode','records','filters','thread_cap','allow_external_processing'))
        name = text(payload.get('name'), '数据集名称', 120)
        mode = payload.get('mode', 'search')
        if mode not in LEVELS:
            raise InsightError('invalid_mode', '模式仅支持 search/voc')
        external = payload.get('allow_external_processing', False)
        if type(external) is not bool:
            raise InsightError('invalid_parameter', '外部模型许可必须为布尔值')
        text(intent, '幂等标识', 120)
        did = digest([tenant, 'dataset', intent])
        request_hash = digest(payload)
        with self.db() as db:
            old = db.execute('SELECT manifest FROM datasets WHERE tenant=? AND id=?',(tenant,did)).fetchone()
        if old:
            if json.loads(old['manifest'])['request_hash'] != request_hash:
                raise InsightError('idempotency_conflict', '幂等标识已用于不同参数', 409)
            return self.dataset(tenant,did)
        if ('records' in payload) == ('filters' in payload):
            raise InsightError('invalid_dataset', '请选择已有数据筛选或导入记录，二者仅选一项')
        source_meta = dict(kind='tenant_import', source_scope='own', input_declared_not_independently_verified=True)
        if 'filters' in payload:
            if mode != 'search':
                raise InsightError('invalid_mode', '现有搜索数据仅能用于搜索内容洞察')
            if not self.loader:
                raise InsightError('source_unavailable', '真实数据读取未配置',503)
            rows, source_meta = self.loader(tenant, payload['filters'])
        else:
            rows = payload['records']
        records, quality = prepare_records(rows,mode,payload.get('thread_cap',50))
        manifest = dict(source_meta, request_hash=request_hash, records_hash=digest(records),
                        filters=payload.get('filters'), version=VERSION)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM datasets WHERE tenant=?',(tenant,)).fetchone()[0] >= 100:
                raise InsightError('dataset_limit','每空间最多100个数据集，请先删除不再需要的数据集',409)
            db.execute('INSERT OR IGNORE INTO datasets(id,tenant,name,mode,created,manifest,records,quality,tags,external) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (did,tenant,name,mode,now(),dumps(manifest),dumps(records),dumps(quality),dumps(draft_taxonomy(records,mode)),int(external)))
            saved = db.execute('SELECT manifest FROM datasets WHERE id=? AND tenant=?',(did,tenant)).fetchone()
            if json.loads(saved['manifest'])['request_hash'] != request_hash:
                raise InsightError('idempotency_conflict','幂等标识冲突',409)
            self._audit(db,tenant,did,'dataset_created',{'records_hash':manifest['records_hash']})
        return self.dataset(tenant,did)

    def _row(self, tenant, did, *, validate=True):
        with self.db() as db:
            row = db.execute('SELECT * FROM datasets WHERE tenant=? AND id=?',(tenant,did)).fetchone()
        return self._decode(row,tenant,validate=validate)

    def _decode(self,row,tenant,*,validate=True):
        if not row:
            raise InsightError('not_found','数据集不存在',404)
        value = dict(row)
        for k in ('manifest','records','quality','tags','annotations','coding','reviews'):
            value[k] = json.loads(value[k])
        if digest(value['records']) != value['manifest']['records_hash']:
            raise InsightError('snapshot_corrupt','输入快照校验失败',503)
        if validate and self.validator:
            self.validator(tenant,value['manifest'])
        return value

    @staticmethod
    def _summary(d):
        return {k:d[k] for k in ('id','name','mode','created','quality','tags','approved','revision','manifest','external')}

    def dataset(self, tenant, did):
        d = self._row(tenant,did)
        result = self._summary(d)
        result['report'] = report(d['records'],d['tags'],d['annotations'],d['reviews'])
        result['coded_records']=sum(r['included'] and r['source_id'] in d['coding'] for r in d['records'])
        result['report_hash'] = digest(dict(dataset_id=did,revision=d['revision'],report=result['report'],annotations=d['annotations']))
        return result

    def list_datasets(self, tenant):
        with self.db() as db:
            rows = db.execute('SELECT id FROM datasets WHERE tenant=? ORDER BY created DESC LIMIT 100',(tenant,)).fetchall()
        values=[]
        for r in rows:
            try:
                values.append(self._summary(self._row(tenant,r['id'])))
            except InsightError as e:
                if e.status not in (403,404,410):
                    raise
        return dict(datasets=values)

    def records(self, tenant, did, *, offset=0, limit=30, tag_id=None, unreviewed=False):
        d=self._row(tenant,did)
        integer(offset,'offset',0,5000); integer(limit,'limit',1,100)
        rows=[]
        for r in d['records']:
            sid=r['source_id']
            if tag_id and not any(a['tag_id']==tag_id for a in d['annotations'].get(sid,[])):
                continue
            if unreviewed and sid in d['reviews']:
                continue
            rows.append(dict(r,annotations=d['annotations'].get(sid,[]),review=d['reviews'].get(sid)))
        return dict(records=rows[offset:offset+limit],total=len(rows),offset=offset,limit=limit,
                    has_more=offset+limit<len(rows),revision=d['revision'])

    def _assert_editable(self, db, tenant, did, revision):
        r=db.execute('SELECT revision FROM datasets WHERE tenant=? AND id=?',(tenant,did)).fetchone()
        if not r:
            raise InsightError('not_found','数据集不存在',404)
        if r['revision'] != revision:
            raise InsightError('revision_conflict','数据已改变，请刷新后重新操作',409)
        active=db.execute("SELECT 1 FROM runs WHERE tenant=? AND dataset=? AND status IN ('queued','running','cancel_requested')",(tenant,did)).fetchone()
        if active:
            raise InsightError('run_active','任务执行中，请完成或取消后再修改',409)

    def save_taxonomy(self,tenant,did,payload):
        fields(payload,('tags','revision','approve'))
        d=self._row(tenant,did)
        tags=taxonomy(payload.get('tags'),d['mode'])
        revision=integer(payload.get('revision'),'revision',1,1000000)
        approve=payload.get('approve',False)
        if type(approve) is not bool:
            raise InsightError('invalid_parameter','approve 必须是布尔值')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self._assert_editable(db,tenant,did,revision)
            # Taxonomy replacement explicitly invalidates previous coding.
            changed=dumps(tags)!=dumps(d['tags'])
            db.execute('UPDATE datasets SET tags=?,approved=?,revision=revision+1,annotations=?,reviews=?,coding=? WHERE tenant=? AND id=?',
                (dumps(tags),int(approve),dumps({} if changed else d['annotations']),dumps({} if changed else d['reviews']),dumps({} if changed else d['coding']),tenant,did))
            self._audit(db,tenant,did,'taxonomy_approved' if approve else 'taxonomy_saved',{'tags_hash':digest(tags),'cleared_annotations':changed})
        return self.dataset(tenant,did)

    def review(self,tenant,did,payload):
        fields(payload,('source_id','annotations','revision','exclude','note'))
        d=self._row(tenant,did)
        sid=payload.get('source_id');record=next((r for r in d['records'] if r['source_id']==sid),None)
        if not record:
            raise InsightError('not_found','原始记录不存在',404)
        if not d['approved']:
            raise InsightError('taxonomy_not_approved','请先校准并确认标签',409)
        exclude=payload.get('exclude',False)
        if type(exclude) is not bool:
            raise InsightError('invalid_parameter','exclude 必须为布尔值')
        note=payload.get('note','')
        if not isinstance(note,str) or len(note)>1000:
            raise InsightError('invalid_parameter','复核备注最多1000字符')
        anns=validate_annotations(payload.get('annotations',[]),record,d['tags'])
        revision=integer(payload.get('revision'),'revision',1,1000000)
        records=d['records']
        # Exclusion is reversible for manually excluded rows, never resurrects raw duplicates/capped rows.
        if record['exclusion'] in (None,'manual_excluded'):
            record['included']=not exclude;record['exclusion']='manual_excluded' if exclude else None
        elif not exclude:
            raise InsightError('excluded_record','重复或线程封顶记录不能通过人工复核重新计入',409)
        previous=dict(annotations=d['annotations'].get(sid,[]),review=d['reviews'].get(sid))
        for a in anns:
            a.update(text_hash=record['text_hash'],taxonomy_hash=digest(d['tags']),input_revision=revision)
        d['annotations'][sid]=anns;d['reviews'][sid]=dict(reviewed_at=now(),note=note,excluded=exclude)
        manifest={**d['manifest'],'records_hash':digest(records)}
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self._assert_editable(db,tenant,did,revision)
            db.execute('UPDATE datasets SET records=?,manifest=?,annotations=?,reviews=?,revision=revision+1 WHERE tenant=? AND id=?',
                (dumps(records),dumps(manifest),dumps(d['annotations']),dumps(d['reviews']),tenant,did))
            self._audit(db,tenant,did,'record_reviewed',dict(source_id=sid,previous=previous,annotations=anns,note=note,exclude=exclude))
        return self.dataset(tenant,did)

    def _basis(self,record,d,method):
        config=self.model.config() if method=='ai' else {}
        model=[config.get('base_url'),config.get('model')] if method=='ai' else None
        return digest([record['text_hash'],d['tags'],method,model,PROMPT_VERSION,VERSION])

    def _pending(self,d,method):
        return [r for r in d['records'] if r['included'] and r['source_id'] not in d['reviews']
                and d['coding'].get(r['source_id'],{}).get('basis')!=self._basis(r,d,method)]

    def submit(self,tenant,did,payload,intent):
        fields(payload,('method','kind','max_calls','revision'))
        text(intent,'幂等标识',120)
        d=self._row(tenant,did)
        method=payload.get('method','rules');kind=payload.get('kind','tag')
        if method not in ('rules','ai') or kind not in ('tag','draft') or (kind=='draft' and method!='ai'):
            raise InsightError('invalid_parameter','任务类型或执行方法无效')
        limit=integer(payload.get('max_calls',20),'调用上限',1,500)
        revision=integer(payload.get('revision'),'revision',1,1000000)
        fingerprint=digest([did,payload])
        with self.db() as db:
            old=db.execute('SELECT id,hash FROM runs WHERE tenant=? AND intent=?',(tenant,intent)).fetchone()
        if old:
            if old['hash']!=fingerprint:
                raise InsightError('idempotency_conflict','幂等标识已用于不同任务',409)
            return self.run(tenant,old['id'])
        if kind=='draft' and (d['annotations'] or d['reviews']):
            raise InsightError('draft_would_clear_annotations','已有标注；请另建数据集生成新标签，避免覆盖既有复核',409)
        if kind=='tag' and not d['approved']:
            raise InsightError('taxonomy_not_approved','请先校准并确认标签',409)
        if method=='ai':
            self.model.config()
            if not d['external']:
                raise InsightError('external_processing_not_allowed','该数据集未授权外部模型处理',403)
            needed=1 if kind=='draft' else (len(self._pending(d,method))+9)//10
            if needed>limit:
                raise InsightError('call_budget_exceeded',f'预计需要 {needed} 次调用，超过本次上限；请缩小数据集或调整上限',409)
        rid=str(uuid4())
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            replay=db.execute('SELECT id,hash FROM runs WHERE tenant=? AND intent=?',(tenant,intent)).fetchone()
            if replay:
                if replay['hash']!=fingerprint:
                    raise InsightError('idempotency_conflict','幂等标识冲突',409)
                replay_id=replay['id']
            else:
                replay_id=None
            if replay_id:
                return self.run(tenant,replay_id)
            self._assert_editable(db,tenant,did,revision)
            if db.execute("SELECT count(*) FROM runs WHERE tenant=? AND status IN ('queued','running','cancel_requested')",(tenant,)).fetchone()[0]>=2:
                raise InsightError('concurrency_limit','每空间最多2个等待或执行中的分析任务',429)
            if method=='ai':
                used=db.execute("SELECT coalesce(sum(max_calls),0) FROM runs WHERE tenant=? AND method='ai' AND substr(created,1,10)=?",(tenant,now()[:10])).fetchone()[0]
                if used+limit>1000:
                    raise InsightError('daily_call_budget','超过本空间 UTC 日调用预留上限1000；已终止任务仍保留当日预留',429)
            db.execute('INSERT INTO runs(id,tenant,dataset,intent,hash,kind,method,status,created,updated,revision,max_calls) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (rid,tenant,did,intent,fingerprint,kind,method,'queued',now(),now(),revision,limit))
            self._audit(db,tenant,did,'run_created',dict(run_id=rid,method=method,kind=kind,max_calls=limit))
        self.wake.set()
        return self.run(tenant,rid)

    def run(self,tenant,rid):
        with self.db() as db:
            row=db.execute('SELECT * FROM runs WHERE tenant=? AND id=?',(tenant,rid)).fetchone()
        if not row:
            raise InsightError('not_found','分析任务不存在',404)
        self._row(tenant,row['dataset'])
        value=dict(row)
        for k in ('telemetry','result'):
            value[k]=json.loads(value[k]) if value[k] else None
        value.pop('hash');value.pop('intent')
        return value

    def list_runs(self,tenant,did):
        self._row(tenant,did)
        with self.db() as db:
            rows=db.execute('SELECT id FROM runs WHERE tenant=? AND dataset=? ORDER BY created DESC LIMIT 30',(tenant,did)).fetchall()
        return dict(runs=[self.run(tenant,r['id']) for r in rows])

    def cancel(self,tenant,rid):
        with self.db() as db:
            db.execute("UPDATE runs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,updated=? WHERE tenant=? AND id=? AND status IN ('queued','running')",(now(),tenant,rid))
        return self.run(tenant,rid)

    def _loop(self):
        while not self.stop.is_set():
            try:
                if self.process_next():
                    continue
            except Exception:
                # Errors are converted per job; never print text, provider bodies or credentials.
                pass
            self.wake.wait(1);self.wake.clear()

    def _check(self,tenant,rid,did):
        with self.db() as db:
            row=db.execute('SELECT status FROM runs WHERE tenant=? AND id=?',(tenant,rid)).fetchone()
        if self.stop.is_set() or not row or row['status']=='cancel_requested':
            raise InsightError('cancelled','任务已取消；已发出的模型请求仍可能产生费用',409)
        self._row(tenant,did)

    def _call(self,r,purpose,payload):
        self._check(r['tenant'],r['id'],r['dataset'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            updated=db.execute("UPDATE runs SET calls=calls+1,updated=? WHERE id=? AND calls<max_calls AND status='running'",(now(),r['id'])).rowcount
            if not updated:
                raise InsightError('call_budget_exceeded','达到调用上限或任务已停止',409)
        value,meta=self.model.call(purpose,payload)
        # Record successful transport/usage BEFORE validating semantic output.
        with self.db() as db:
            old=json.loads(db.execute('SELECT telemetry FROM runs WHERE id=?',(r['id'],)).fetchone()[0]);old.append(meta)
            db.execute('UPDATE runs SET telemetry=?,updated=? WHERE id=?',(dumps(old),now(),r['id']))
        return value

    def process_next(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return False
            r=dict(row);db.execute("UPDATE runs SET status='running',updated=? WHERE id=?",(now(),r['id']))
        try:
            d=self._row(r['tenant'],r['dataset'])
            if d['revision']!=r['revision']:
                raise InsightError('revision_conflict','输入或标签版本已改变',409)
            records=self._pending(d,r['method']) if r['kind']=='tag' else [x for x in d['records'] if x['included']]
            if r['kind']=='draft':
                # Spread across fixed input; retain sample IDs in result for audit.
                step=max(1,len(records)//50);sample=records[::step][:50]
                v=self._call(r,'draft',dict(level1=list(LEVELS[d['mode']]),mode=d['mode'],
                    records=[dict(source_id=x['source_id'],text_raw=x['text_raw'][:2000]) for x in sample]))
                tags=taxonomy(v.get('tags'),d['mode']);self._check(r['tenant'],r['id'],r['dataset'])
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('UPDATE datasets SET tags=?,approved=0,annotations=\'{}\',reviews=\'{}\',revision=revision+1 WHERE id=? AND revision=?',
                        (dumps(tags),d['id'],r['revision']))
                    db.execute("UPDATE runs SET status='succeeded',processed=?,result=?,updated=? WHERE id=?",(len(sample),dumps({'sample_ids':[x['source_id'] for x in sample],'draft_only':True}),now(),r['id']))
                return True
            annotations=dict(d['annotations']);coding=dict(d['coding'])
            for start in range(0,len(records),10):
                self._check(r['tenant'],r['id'],r['dataset'])
                batch=records[start:start+10]
                if r['method']=='rules':
                    updates={x['source_id']:rule_annotations(x,d['tags']) for x in batch}
                else:
                    v=self._call(r,'tag',dict(mode=d['mode'],tags=d['tags'],records=[dict(source_id=x['source_id'],text_raw=x['text_raw']) for x in batch]))
                    values=v.get('records')
                    if not isinstance(values,list) or len(values)!=len(batch) or {x.get('source_id') for x in values if isinstance(x,dict)}!={x['source_id'] for x in batch}:
                        raise InsightError('invalid_model_output','模型结果未完整对应输入记录；已保留此前完成批次',502)
                    lookup={x['source_id']:x for x in batch}
                    updates={x['source_id']:validate_annotations(x.get('annotations'),lookup[x['source_id']],d['tags'],method='ai') for x in values}
                self._check(r['tenant'],r['id'],r['dataset'])
                for rec in batch:
                    for ann in updates[rec['source_id']]:
                        ann.update(run_id=r['id'],text_hash=rec['text_hash'],taxonomy_hash=digest(d['tags']),input_revision=r['revision'])
                annotations.update(updates)
                for rec in batch:
                    coding[rec['source_id']]=dict(basis=self._basis(rec,d,r['method']),run_id=r['id'],coded_at=now(),method=r['method'])
                with self.db() as db:
                    db.execute('UPDATE datasets SET annotations=?,coding=? WHERE tenant=? AND id=?',(dumps(annotations),dumps(coding),r['tenant'],d['id']))
                    db.execute('UPDATE runs SET processed=?,updated=? WHERE id=?',(start+len(batch),now(),r['id']))
            self._check(r['tenant'],r['id'],r['dataset'])
            with self.db() as db:
                db.execute('UPDATE datasets SET revision=revision+1 WHERE id=?',(d['id'],))
                db.execute("UPDATE runs SET status='succeeded',updated=?,result=? WHERE id=?",(now(),dumps({'analysis_version':VERSION,'prompt_version':PROMPT_VERSION,'human_review_required':True}),r['id']))
        except Exception as e:
            status='cancelled' if isinstance(e,InsightError) and e.code=='cancelled' else 'failed'
            message=str(e) if isinstance(e,InsightError) else '分析执行失败；已完成批次保留，未自动重试'
            with self.db() as db:
                db.execute("UPDATE runs SET status=?,error=?,updated=? WHERE id=?",(status,message,now(),r['id']))
                db.execute('UPDATE datasets SET revision=revision+1 WHERE id=?',(r['dataset'],))
        return True

    def bundle(self,tenant,did):
        # Freeze input, annotations, audit and call ledger in one read snapshot.
        with self.db() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT * FROM datasets WHERE tenant=? AND id=?',(tenant,did)).fetchone()
            if db.execute("SELECT 1 FROM runs WHERE tenant=? AND dataset=? AND status IN ('queued','running','cancel_requested')",(tenant,did)).fetchone():
                raise InsightError('run_active','分析执行中，请等待终态后导出固定报告',409)
            audit=[dict(x) for x in db.execute('SELECT created,action,detail FROM audit WHERE tenant=? AND dataset=? ORDER BY id',(tenant,did))]
            ledger=[dict(x) for x in db.execute('SELECT id,kind,method,status,created,updated,revision,max_calls,calls,processed,error,telemetry,result FROM runs WHERE tenant=? AND dataset=? ORDER BY created,id',(tenant,did))]
        d=self._decode(row,tenant)
        for run in ledger:
            for k in ('telemetry','result'):
                run[k]=json.loads(run[k]) if run[k] else None
        r=report(d['records'],d['tags'],d['annotations'],d['reviews'])
        bundle=dict(version=VERSION,dataset=self._summary(d),records=d['records'],annotations=d['annotations'],coding=d['coding'],reviews=d['reviews'],report=r,audit=audit,runs=ledger)
        bundle['report_hash']=digest(bundle)
        return bundle

    def export(self,tenant,did,fmt):
        b=self.bundle(tenant,did)
        rows=[dict(r,annotations=b['annotations'].get(r['source_id'],[]),review=b['reviews'].get(r['source_id'])) for r in b['records']]
        files={'report.json':(dumps(b)+'\n').encode(),
               'records.jsonl':(''.join(dumps(x)+'\n' for x in rows)).encode(),
               'records.csv':csv_bytes(rows,('source_id','source','text_raw','url','brand','market','rating','parent_id','included','exclusion','source_fields','annotations','review')),
               'topics.csv':csv_bytes(b['report']['topics'],('id','level1','level2','name','count','n','rate','sentiments','source_ids')),
               'report.html':html_report(b)}
        manifest=dict(version=VERSION,dataset_id=did,report_hash=b['report_hash'],
            files={k:dict(sha256=__import__('hashlib').sha256(v).hexdigest(),bytes=len(v)) for k,v in files.items()})
        files['manifest.json']=(dumps(manifest)+'\n').encode()
        if fmt=='zip':
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                for name,value in files.items():
                    info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,value)
            return out.getvalue(),'application/zip'
        lookup={'json':('report.json','application/json'),'jsonl':('records.jsonl','application/x-ndjson'),
                'csv':('records.csv','text/csv; charset=utf-8'),'topics':('topics.csv','text/csv; charset=utf-8'),
                'html':('report.html','text/html; charset=utf-8'),'manifest':('manifest.json','application/json')}
        if fmt not in lookup:
            raise InsightError('invalid_format','导出支持 json/jsonl/csv/topics/html/zip/manifest')
        filename,mime=lookup[fmt];return files[filename],mime

    def delete(self,tenant,did,revision):
        # Deletion is allowed even when source rights have been revoked.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');self._assert_editable(db,tenant,did,revision)
            db.execute('DELETE FROM audit WHERE tenant=? AND dataset=?',(tenant,did))
            db.execute('DELETE FROM runs WHERE tenant=? AND dataset=?',(tenant,did))
            db.execute('DELETE FROM datasets WHERE tenant=? AND id=?',(tenant,did))
        return {'deleted':True,'external_downloads_recalled':False}
