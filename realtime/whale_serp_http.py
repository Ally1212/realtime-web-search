"""Website control of durable Whale uploads and link-only platform jobs."""
import json
import re
from urllib.parse import parse_qs, urlparse

from .whale_serp import WhaleSerpStore


def status(store,config):
    settings=store.settings()
    with store.store.connect() as db:
        counts=db.execute('SELECT state,count(*) n FROM whale_serp_outbox GROUP BY state').fetchall()
    return dict(settings=settings,configured=bool(config.whale_collector_api_key),
                runtime_available=config.whale_serp_enabled,platform_url=config.whale_base_url,
                dataset_id=config.whale_dataset_id,source_platform=config.whale_source_platform,
                supported_task_types=[t for t in config.whale_supported_task_types if t in ('keyword_search','backfill','content_detail')],
                capabilities=['identity','title'],counts={r['state']:r['n'] for r in counts},
                note='queued 表示平台已接收，最终业务入库尚未核验；链接模式不提供正文。')


def handle(handler,method):
    parsed=urlparse(handler.path)
    match=re.fullmatch(r'/api/whale/jobs/([0-9a-f-]+)(?:/(pause|resume|stop|retry|receipts))?',parsed.path)
    if parsed.path not in ('/api/whale/status','/api/whale/settings','/api/whale/jobs') and not match:return False
    if not getattr(handler.config,'whale_serp_enabled',False):
        handler.send_json({'error':'Whale 链接对接尚未部署启用'},503);return True
    store=WhaleSerpStore(handler.config.database_url)
    try:
        f={k:v[-1] for k,v in parse_qs(parsed.query).items()}
        if method=='GET':
            if parsed.path=='/api/whale/status':data=status(store,handler.config)
            elif parsed.path=='/api/whale/jobs':data=store.jobs(f.get('limit',20),f.get('offset',0))
            elif match:
                jid,operation=match.groups()
                if operation=='receipts':data={'receipts':store.receipts(jid,f.get('limit',50),f.get('offset',0))}
                elif operation:raise ValueError('操作仅支持 POST')
                else:data=store.get_job(jid)
                if data is None:handler.send_json({'error':'上传任务不存在'},404);return True
            else:raise ValueError('设置写入仅支持 POST')
        else:
            length=int(handler.headers.get('Content-Length','0'))
            if not 0<length<=65536:raise ValueError('请求大小必须为 1–65536 字节')
            payload=json.loads(handler.rfile.read(length))
            if parsed.path=='/api/whale/settings':
                if (payload.get('uploads_enabled') or payload.get('claims_enabled')) and not handler.config.whale_collector_api_key:
                    raise ValueError('未配置 Whale Collector Key')
                data=store.save_settings(payload)
            elif parsed.path=='/api/whale/jobs':data=store.enqueue(payload,handler.config)
            elif match and match.group(2) in ('pause','resume','stop','retry'):
                data=store.action(*match.groups())
                if data is None:handler.send_json({'error':'上传任务不存在'},404);return True
            else:raise ValueError('无效 POST 路径')
        handler.send_json(data,201 if method=='POST' and parsed.path=='/api/whale/jobs' else 200)
    except (ValueError,TypeError,json.JSONDecodeError) as e:handler.send_json({'error':str(e)},400)
    except Exception as e:
        print('Whale console failed: '+type(e).__name__,flush=True)
        handler.send_json({'error':'Whale 状态暂不可用，请检查服务和数据库'},503)
    return True
