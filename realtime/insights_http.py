"""Authenticated insight API; suitable as a separate loopback service."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse
from .insights_core import InsightError, fields, integer
from .product import ProductError


def body(handler):
    try:
        size=int(handler.headers.get('Content-Length','0'))
    except ValueError:
        raise InsightError('invalid_body','无效请求长度') from None
    if not 0<size<=900_000 or handler.headers.get('Content-Type','').split(';')[0]!='application/json':
        raise InsightError('invalid_body','请提交不超过900 KB的JSON；大文件请拆分数据集')
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:
                raise ValueError('duplicate')
            out[k]=v
        return out
    try:
        data=json.loads(handler.rfile.read(size),object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        if not isinstance(data,dict):
            raise ValueError('object')
        return data
    except (ValueError,UnicodeError):
        raise InsightError('invalid_body','JSON无效、重复字段或非有限数字') from None


def handle(handler,method,service,tenant):
    parsed=urlparse(handler.path);path=parsed.path
    q=parse_qs(parsed.query,keep_blank_values=True)
    if any(len(v)!=1 for v in q.values()):
        raise InsightError('invalid_parameter','不接受重复查询参数')
    p={k:v[0] for k,v in q.items()}
    intent=handler.headers.get('Idempotency-Key','')
    if path=='/v1/insights/openapi.json' and method=='GET':
        from .insights_schema import openapi
        fields(p,());handler.send_json(openapi());return
    if path=='/v1/insights/capabilities' and method=='GET':
        fields(p,());result=service.capabilities()
    elif path=='/v1/insights/datasets':
        fields(p,())
        if method=='GET':
            result=service.list_datasets(tenant)
        elif method=='POST':
            result=service.create_dataset(tenant,body(handler),intent)
            handler.send_json(result,201);return
        else:
            raise InsightError('method_not_allowed','方法不支持',405)
    elif m:=re.fullmatch(r'/v1/insights/datasets/([a-f0-9]{64})(?:/(records|taxonomy|runs|export|review|delete))?',path):
        did,action=m.groups()
        if not action and method=='GET':
            fields(p,());result=service.dataset(tenant,did)
        elif action=='records' and method=='GET':
            fields(p,('offset','limit','tag_id','unreviewed'))
            if p.get('unreviewed','false') not in ('true','false'):
                raise InsightError('invalid_parameter','unreviewed仅支持true/false')
            try:
                offset=int(p.get('offset',0));limit=int(p.get('limit',30))
            except ValueError:
                raise InsightError('invalid_parameter','分页参数必须为整数') from None
            result=service.records(tenant,did,offset=offset,limit=limit,tag_id=p.get('tag_id'),unreviewed=p.get('unreviewed')=='true')
        elif action=='taxonomy' and method=='POST':
            fields(p,());result=service.save_taxonomy(tenant,did,body(handler))
        elif action=='review' and method=='POST':
            fields(p,());result=service.review(tenant,did,body(handler))
        elif action=='runs':
            fields(p,())
            if method=='GET':
                result=service.list_runs(tenant,did)
            elif method=='POST':
                result=service.submit(tenant,did,body(handler),intent)
                handler.send_json(result,202);return
            else:
                raise InsightError('method_not_allowed','方法不支持',405)
        elif action=='export' and method=='GET':
            fields(p,('format',));fmt=p.get('format','zip');raw,mime=service.export(tenant,did,fmt)
            handler.send_bytes(raw,mime,filename='insights-'+did[:12]+('.topics.csv' if fmt=='topics' else '.'+('json' if fmt=='manifest' else fmt)))
            return
        elif action=='delete' and method=='POST':
            fields(p,());data=body(handler);fields(data,('revision',));result=service.delete(tenant,did,integer(data.get('revision'),'revision',1,1000000))
        else:
            raise InsightError('method_not_allowed','方法不支持',405)
    elif m:=re.fullmatch(r'/v1/insights/runs/([a-f0-9-]{36})(/cancel)?',path):
        fields(p,())
        if method=='GET' and not m[2]:
            result=service.run(tenant,m[1])
        elif method=='POST' and m[2]:
            fields(body(handler),());result=service.cancel(tenant,m[1])
        else:
            raise InsightError('method_not_allowed','方法不支持',405)
    else:
        raise InsightError('not_found','接口不存在',404)
    handler.send_json(result)
