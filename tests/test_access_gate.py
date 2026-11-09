import hashlib
import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

from realtime.access_gate import AccessGate, COOKIE, TTL, handler


class AccessGateTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.gate = AccessGate(dict(key_sha256=hashlib.sha256(b'test-access-key').hexdigest(),
                                   cookie_secret='ab'*32, origin='https://searchflow.example'), lambda: self.now)

    def test_missing_wrong_and_valid_credentials(self):
        self.assertFalse(self.gate.authorized())
        self.assertFalse(self.gate.authorized('wrong'))
        self.assertTrue(self.gate.authorized('test-access-key'))
        self.assertFalse(self.gate.authorized(cookie=COOKIE+'=bad'))

    def test_signed_cookie_expiry_tampering_and_rotation(self):
        value = self.gate.issue_cookie()
        self.assertTrue(self.gate.authorized(cookie=COOKIE+'='+value))
        self.assertFalse(self.gate.authorized(cookie=COOKIE+'='+value[:-1]+'x'))
        self.now += TTL
        self.assertFalse(self.gate.authorized(cookie=COOKIE+'='+value))
        self.now -= TTL
        self.gate.signer = b'z'*32
        self.assertFalse(self.gate.authorized(cookie=COOKIE+'='+value))

    def test_failed_login_rate_limit_is_per_ip_and_recovers(self):
        for _ in range(10):
            self.assertEqual(self.gate.login('bad', 'one'), 401)
        self.assertEqual(self.gate.login('test-access-key', 'one'), 429)
        self.assertEqual(self.gate.login('test-access-key', 'two'), 303)
        self.now += 300
        self.assertEqual(self.gate.login('test-access-key', 'one'), 303)

    def test_http_login_cookie_origin_logout_and_no_secret_echo(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.gate))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            conn.close()
            return result
        try:
            self.assertEqual(request('GET', '/check')[0], 401)
            self.assertEqual(request('GET', '/check', headers={'X-SearchFlow-Access-Key':'test-access-key'})[0], 204)
            self.assertEqual(request('GET', '/denied')[0], 401)
            self.assertEqual(request('GET', '/denied', headers={'Accept':'text/html'})[:1], (303,))
            headers = {'Content-Type':'application/x-www-form-urlencoded','Origin':'https://evil.example'}
            self.assertEqual(request('POST', '/_access/login', urlencode({'key':'test-access-key'}), headers)[0], 403)
            headers['Origin'] = 'https://searchflow.example'
            code, _, body = request('POST', '/_access/login', urlencode({'key':'wrong-secret'}), headers)
            self.assertEqual(code, 401)
            self.assertNotIn(b'wrong-secret', body)
            code, response, _ = request('POST', '/_access/login', urlencode({'key':'test-access-key'}), headers)
            self.assertEqual(code, 303)
            cookie = response['Set-Cookie']
            for field in ['Secure','HttpOnly','SameSite=Strict','Path=/']:
                self.assertIn(field, cookie)
            self.assertEqual(request('GET', '/check', headers={'Cookie':cookie})[0], 204)
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertEqual(request('POST', '/_access/logout', headers={'Origin':'https://evil.example'})[0], 403)
            code, response, _ = request('POST', '/_access/logout', headers={'Origin':'https://searchflow.example'})
            self.assertEqual(code, 303)
            self.assertIn('Max-Age=0', response['Set-Cookie'])
        finally:
            server.shutdown()
            server.server_close()
