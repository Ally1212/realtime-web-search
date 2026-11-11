"""Full immutable response envelope exports; never normalized fact records."""
import csv
import io
import json
import zipfile
from .data_capability_export import _EXPORT_SLOTS, _safe_cell, sha
from .data_capability_jobs import digest
from .product import ProductError

VERSION='searchflow-source-response-export-1'
MAX_BYTES=128*1024*1024
COLUMNS=('record_type','job_id','response_id','record_json')


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def build_bundle(snapshot):
    if not _EXPORT_SLOTS.acquire(blocking=False):
        raise ProductError('export_busy','完整导出并发已满',429)
    try:
        record=dict(record_type='source_response',job_id=snapshot['job_id'],response_id=snapshot['response_id'],payload=snapshot)
        text=io.StringIO(newline='');writer=csv.writer(text);writer.writerow(COLUMNS)
        writer.writerow([_safe_cell(record[k]) for k in COLUMNS[:-1]]+[encoded(record).decode()])
        files={'response.json':encoded(snapshot)+b'\n','response.jsonl':encoded(record)+b'\n',
               'response.csv':b'\xef\xbb\xbf'+text.getvalue().encode()}
        manifest=dict(version=VERSION,response_id=snapshot['response_id'],job_id=snapshot['job_id'],snapshot_hash=snapshot['snapshot_hash'],
                      payload_hash=snapshot['payload_hash'],raw_status=snapshot['raw_status'],response_complete=snapshot['response_complete'],
                      normalized_fact_count=0,record_count=1,files={name:dict(bytes=len(data),sha256=sha(data)) for name,data in files.items()})
        files['manifest.json']=encoded(manifest)+b'\n'
        if sum(map(len,files.values()))>MAX_BYTES:
            raise ProductError('export_too_large','响应导出超过上限，未发布文件',413)
        target=io.BytesIO()
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,data in files.items():
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100600<<16
                archive.writestr(info,data)
        if target.tell()>MAX_BYTES:
            raise ProductError('export_too_large','响应导出超过上限，未发布文件',413)
        files['bundle.zip']=target.getvalue()
        return dict(files=files,manifest=manifest)
    finally:
        _EXPORT_SLOTS.release()


def verify_bundle(content, *, expected_response_id, expected_snapshot_hash):
    try:
        if len(content)>MAX_BYTES:raise ValueError()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos=archive.infolist();required={'response.json','response.jsonl','response.csv','manifest.json'}
            if len(infos)!=4 or {i.filename for i in infos}!=required or sum(i.file_size for i in infos)>MAX_BYTES:raise ValueError()
            files={i.filename:archive.read(i) for i in infos}
        snapshot=json.loads(files['response.json']);manifest=json.loads(files['manifest.json'])
        if (snapshot['response_id']!=expected_response_id or snapshot['snapshot_hash']!=expected_snapshot_hash
                or digest({k:v for k,v in snapshot.items() if k!='snapshot_hash'})!=expected_snapshot_hash
                or snapshot['payload_hash']!=(digest(snapshot['payload']) if snapshot['payload'] is not None else None)):
            raise ValueError()
        for name,data in files.items():
            if name!='manifest.json' and manifest['files'][name]!=dict(bytes=len(data),sha256=sha(data)):raise ValueError()
        expected=dict(record_type='source_response',job_id=snapshot['job_id'],response_id=snapshot['response_id'],payload=snapshot)
        if [json.loads(line) for line in files['response.jsonl'].splitlines()]!=[expected]:raise ValueError()
        if csv.field_size_limit()<MAX_BYTES:csv.field_size_limit(MAX_BYTES)
        reader=csv.DictReader(io.StringIO(files['response.csv'].decode('utf-8-sig')))
        rows=list(reader)
        if tuple(reader.fieldnames or ())!=COLUMNS or len(rows)!=1:raise ValueError()
        if json.loads(rows[0]['record_json'])!=expected or any(rows[0][k]!=str(_safe_cell(expected[k])) for k in COLUMNS[:-1]):raise ValueError()
        if (manifest['version']!=VERSION or manifest['response_id']!=expected_response_id or manifest['snapshot_hash']!=expected_snapshot_hash
                or manifest['job_id']!=snapshot['job_id'] or manifest['payload_hash']!=snapshot['payload_hash']
                or manifest['raw_status']!=snapshot['raw_status'] or manifest['response_complete']!=snapshot['response_complete']
                or manifest['normalized_fact_count']!=0 or manifest['record_count']!=1):raise ValueError()
        return manifest
    except Exception:
        raise ProductError('corrupt_response_bundle','响应文件、身份、摘要或结构校验失败',409) from None
