"""Preview current UI with read-only data from an SSH-forwarded production site.

Run: python scripts/preview_serp_ui.py --port 8094 --upstream http://127.0.0.1:8093
Writes are rejected; this process never starts a collector or initializes a database.
"""
import argparse
import importlib.util
import json
import csv
import io
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from urllib.parse import urlparse, parse_qs, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from realtime.serp_presentation import USER_COLUMNS, user_result_rows, csv_value


def page():
    spec = importlib.util.spec_from_file_location('preview_page', ROOT / 'realtime/serp_page.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PAGE.replace('<body>', '<body><div class="preview-banner">本地新版预览 · 服务器真实数据只读 · 操作不影响服务器</div>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8094)
    parser.add_argument('--upstream', default='http://127.0.0.1:8093')
    args = parser.parse_args()

    class Preview(BaseHTTPRequestHandler):
        def send(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split('?')[0] == '/':
                return self.send(200, page().encode(), 'text/html; charset=utf-8')
            parsed = urlparse(self.path)
            filters = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            if parsed.path == '/api/serp/export' and filters.get('view') == 'user':
                return self.user_export(filters)
            if not (self.path.startswith('/api/') or self.path in ('/articles', '/stats')):
                return self.send(404, b'{}')
            try:
                with urlopen(args.upstream.rstrip('/') + self.path, timeout=30) as response:
                    self.send(response.status, response.read(), response.headers.get('Content-Type', 'application/json'))
            except HTTPError as error:
                self.send(error.code, error.read())
            except (URLError, TimeoutError):
                self.send(502, json.dumps({'error': '无法读取服务器数据，请检查 SSH 转发 8093。'}, ensure_ascii=False).encode())

        def user_export(self, filters):
            filters.update(mode='results', format='jsonl')
            filters.pop('view', None)
            try:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(USER_COLUMNS)
                contexts = {}
                with urlopen(args.upstream.rstrip('/') + '/api/serp/export?' + urlencode(filters), timeout=30) as response:
                    for line in response:
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        if not row.get('search_parameters'):
                            rid = str(row['id'])
                            if rid not in contexts:
                                with urlopen(args.upstream.rstrip('/')+'/api/serp/requests/'+rid, timeout=30) as detail:
                                    contexts[rid] = json.load(detail).get('search_parameters') or {}
                            row['search_parameters'] = contexts[rid]
                        for record in user_result_rows(row):
                            writer.writerow([csv_value(record[k]) for k in USER_COLUMNS])
                self.send(200, ('\ufeff'+output.getvalue()).encode(), 'text/csv; charset=utf-8')
            except (HTTPError, URLError, TimeoutError, ValueError):
                self.send(502, json.dumps({'error':'导出数据暂不可用，请缩小筛选范围后重试。'}, ensure_ascii=False).encode())

        def do_POST(self):
            self.send(403, json.dumps({'error': '本地预览只读。此操作未发送到服务器。'}, ensure_ascii=False).encode())

        def log_message(self, *_):
            pass

    print(f'Preview: http://127.0.0.1:{args.port} (read-only)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Preview).serve_forever()


if __name__ == '__main__':
    main()
