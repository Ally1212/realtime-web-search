"""HTTP routes for the collector console, shared by production and integration tests."""
import csv
import io
import json
import re
from urllib.parse import parse_qs, urlparse

from .serp_console import ConsoleStore, NEWS_LOCALES, REGIONS
from .serp_fields import GOOGLE_SOURCES


def settings_payload(store, config):
    return dict(store.settings(config), supported_sources=list(GOOGLE_SOURCES),
                regions=list(REGIONS), news_editions=list(NEWS_LOCALES),
                web_languages=['auto', 'en', 'zh'], trends_mode='trending_now_rss',
                effective='后续采集批次生效；持续供给最多 30 秒读取；已发出请求允许完成')


def post(handler):
    from .whale_serp_http import handle
    if handle(handler, 'POST'):
        return True
    path = urlparse(handler.path).path
    match = re.fullmatch(r'/api/serp/tasks/([0-9a-f-]+)/(start|pause|resume|stop)', path)
    if path not in ('/api/collector-settings', '/api/serp/tasks') and not match:
        return False
    store = ConsoleStore(handler.config.database_url)
    try:
        if match:
            result = store.action(*match.groups())
            if not result:
                handler.send_json({'error': '任务不存在'}, 404)
                return True
            if result['status'] == 'active':
                handler.queue.enqueue(result['campaign_id'], priority=True)
            handler.send_json(result)
            return True
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 65536:
            raise ValueError('请求大小必须为 1–65536 字节')
        payload = json.loads(handler.rfile.read(length))
        if path == '/api/collector-settings':
            store.save_settings(payload, handler.config)
            handler.send_json(settings_payload(store, handler.config))
        else:
            task = store.create_task(payload, handler.config)
            if task['status'] == 'active':
                handler.queue.enqueue(task['campaign_id'], priority=True)
            handler.send_json(task, 201)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        handler.send_json({'error': str(exc)}, 400)
    except Exception as exc:
        print('console POST failed: '+type(exc).__name__, flush=True)
        handler.send_json({'error': '保存失败，请检查数据库或队列连接后重试'}, 503)
    return True


def get(handler):
    from .whale_serp_http import handle
    if handle(handler, 'GET'):
        return True
    parsed = urlparse(handler.path)
    routes = ('/api/collector-settings', '/api/serp/tasks', '/api/serp/results', '/api/serp/browse-requests', '/api/serp/stats', '/api/serp/export')
    if parsed.path not in routes:
        return False
    store = ConsoleStore(handler.config.database_url)
    f = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    try:
        if parsed.path == '/api/collector-settings':
            data = settings_payload(store, handler.config)
        elif parsed.path == '/api/serp/tasks':
            data = store.tasks(**{k: v for k, v in f.items() if k in ('kind', 'status', 'q', 'offset', 'limit')})
        elif parsed.path == '/api/serp/stats':
            data = store.overview()
        elif parsed.path == '/api/serp/export':
            export(handler, store, f)
            return True
        else:
            data = store.browse(f, 'requests' if parsed.path.endswith('browse-requests') else 'results')
        handler.send_json(data)
    except (ValueError, TypeError) as exc:
        handler.send_json({'error': str(exc)}, 400)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        print('console GET failed: '+type(exc).__name__, flush=True)
        handler.send_json({'error': '数据暂不可用，请稍后刷新'}, 503)
    return True


def export(handler, store, f):
    fmt, mode = f.get('format', 'jsonl'), f.get('mode', 'results')
    if fmt not in ('csv', 'jsonl') or mode not in ('results', 'requests'):
        raise ValueError('导出仅支持 results/requests 和 csv/jsonl')
    rows = store.export(f, mode)
    # Start the query before sending headers so validation/database errors remain JSON errors.
    first = next(rows, None)
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/csv; charset=utf-8' if fmt == 'csv' else 'application/x-ndjson; charset=utf-8')
    handler.send_header('Content-Disposition', f'attachment; filename="google-{mode}.{fmt}"')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    try:
        if fmt == 'csv':
            handler.wfile.write(b'\xef\xbb\xbf')
            output = io.StringIO()
            writer = csv.writer(output)
            header = ['request_id', 'source', 'query', 'collected_at', 'title_or_keyword', 'link', 'snippet', 'publisher', 'position', 'feed_position', 'is_new_url', 'status', 'json']
            writer.writerow(header)
            handler.wfile.write(output.getvalue().encode()); output.seek(0); output.truncate(0)
        def write(row):
            if fmt == 'jsonl':
                handler.wfile.write((json.dumps(row, ensure_ascii=False, default=str)+'\n').encode())
            else:
                item = row.get('data', {})
                vals = [str(row['id']), row['source'], row['query'], str(row['completed_at']), item.get('title') or item.get('keyword'), item.get('link'), item.get('snippet'), item.get('publisher'), item.get('position'), item.get('feed_position'), item.get('is_new_url'), row.get('status'), json.dumps(row.get('response', item), ensure_ascii=False, default=str)]
                # CSV is commonly opened in a spreadsheet; preserve values as text.
                vals = ["'"+x if isinstance(x, str) and x.lstrip().startswith(('=', '+', '-', '@')) else x for x in vals]
                writer.writerow(vals)
                handler.wfile.write(output.getvalue().encode()); output.seek(0); output.truncate(0)
        if first is not None:
            write(first)
        for row in rows:
            write(row)
    finally:
        rows.close()
        handler.close_connection = True
