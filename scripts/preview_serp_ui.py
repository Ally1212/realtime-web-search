"""Preview current UI with read-only data from an SSH-forwarded production site.

Run: python scripts/preview_serp_ui.py --port 8094 --upstream http://127.0.0.1:8093
Writes are rejected; this process never starts a collector or initializes a database.
"""
import argparse
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


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
            if not (self.path.startswith('/api/') or self.path in ('/articles', '/stats')):
                return self.send(404, b'{}')
            try:
                with urlopen(args.upstream.rstrip('/') + self.path, timeout=30) as response:
                    self.send(response.status, response.read(), response.headers.get('Content-Type', 'application/json'))
            except HTTPError as error:
                self.send(error.code, error.read())
            except (URLError, TimeoutError):
                self.send(502, json.dumps({'error': '无法读取服务器数据，请检查 SSH 转发 8093。'}, ensure_ascii=False).encode())

        def do_POST(self):
            self.send(403, json.dumps({'error': '本地预览只读。此操作未发送到服务器。'}, ensure_ascii=False).encode())

        def log_message(self, *_):
            pass

    print(f'Preview: http://127.0.0.1:{args.port} (read-only)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Preview).serve_forever()


if __name__ == '__main__':
    main()
