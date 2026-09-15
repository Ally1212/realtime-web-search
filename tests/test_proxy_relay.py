import asyncio
import base64
import unittest

from realtime.proxy_pool import ProxyRecord
from realtime.proxy_relay import relay_connection


class ProxyRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_injects_auth_and_tunnels_bytes(self):
        seen = asyncio.get_running_loop().create_future()

        async def upstream(reader, writer):
            header = await reader.readuntil(b'\r\n\r\n')
            seen.set_result(header)
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            self.assertEqual(await reader.readexactly(4), b'PING')
            writer.write(b'PONG')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        record = ProxyRecord('127.0.0.1', upstream_port, 'http')
        relay_server = await asyncio.start_server(
            lambda reader, writer: relay_connection(reader, writer, record, 'user', 'pass'),
            '127.0.0.1', 0,
        )
        relay_port = relay_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', relay_port)
            writer.write(b'CONNECT www.google.com:443 HTTP/1.1\r\nHost: www.google.com:443\r\n\r\nPING')
            await writer.drain()
            response = await reader.readuntil(b'\r\n\r\n')
            self.assertIn(b'200 Connection Established', response)
            self.assertEqual(await reader.readexactly(4), b'PONG')
            writer.close()
            await writer.wait_closed()
            header = await asyncio.wait_for(seen, 1)
            token = base64.b64encode(b'user:pass')
            self.assertIn(b'Proxy-Authorization: Basic ' + token, header)
        finally:
            relay_server.close()
            upstream_server.close()
            await relay_server.wait_closed()
            await upstream_server.wait_closed()


if __name__ == '__main__':
    unittest.main()
