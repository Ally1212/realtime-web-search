import asyncio
import base64
import errno
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch, AsyncMock

from realtime.proxy_pool import ProxyRecord
from realtime.proxy_relay import relay_connection, _reconcile_relays


class ProxyRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_does_not_wait_for_retired_listener_clients(self):
        accepted = asyncio.Event()

        async def old_tunnel(reader, writer):
            accepted.set()
            try:
                writer.write(await reader.readexactly(4))
                await writer.drain()
                await reader.read()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        old = await asyncio.start_server(old_tunnel, '127.0.0.1', 0)
        old_port = old.sockets[0].getsockname()[1]
        client_reader, client_writer = await asyncio.open_connection('127.0.0.1', old_port)
        await accepted.wait()
        temporary = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        free_port = temporary.sockets[0].getsockname()[1]
        temporary.close()
        await temporary.wait_closed()
        record = ProxyRecord('127.0.0.1', 41003, 'http')
        cache = Mock()
        cache.load.return_value = (None, [record])
        args = SimpleNamespace(bind='127.0.0.1', port_start=20000, port_count=30000)
        servers = {'old': (old, old_port)}
        try:
            with patch('realtime.proxy_relay.proxy_relay_ports', return_value={record.key: free_port}):
                pending = await asyncio.wait_for(_reconcile_relays(args, cache, servers, 'user', 'pass'), .5)
            self.assertEqual(pending, 0)
            self.assertFalse(old.is_serving())
            self.assertTrue(servers[record.key][0].is_serving())
            client_writer.write(b'PING')
            await client_writer.drain()
            self.assertEqual(await asyncio.wait_for(client_reader.readexactly(4), 1), b'PING')
        finally:
            client_writer.close()
            await client_writer.wait_closed()
            old.close()
            await old.wait_closed()
            for server, _ in servers.values():
                server.close()
                await server.wait_closed()

    async def test_busy_port_preserves_other_listeners_and_retries_when_free(self):
        occupied = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        busy_port = occupied.sockets[0].getsockname()[1]
        temporary = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        free_port = temporary.sockets[0].getsockname()[1]
        temporary.close()
        await temporary.wait_closed()
        records = [ProxyRecord('127.0.0.1', 41001, 'http'), ProxyRecord('127.0.0.1', 41002, 'http')]
        cache = Mock()
        cache.load.return_value = (None, records)
        args = SimpleNamespace(bind='127.0.0.1', port_start=20000, port_count=30000)
        servers = {}
        mapping = {records[0].key: busy_port, records[1].key: free_port}
        try:
            with patch('realtime.proxy_relay.proxy_relay_ports', return_value=mapping):
                self.assertEqual(await _reconcile_relays(args, cache, servers, 'user', 'pass'), 1)
                healthy = servers[records[1].key][0]
                self.assertTrue(healthy.is_serving())
                self.assertNotIn(records[0].key, servers)
                self.assertEqual(await _reconcile_relays(args, cache, servers, 'user', 'pass'), 1)
                self.assertIs(servers[records[1].key][0], healthy)
                occupied.close()
                await occupied.wait_closed()
                self.assertEqual(await _reconcile_relays(args, cache, servers, 'user', 'pass'), 0)
                self.assertIs(servers[records[1].key][0], healthy)
                self.assertTrue(servers[records[0].key][0].is_serving())
        finally:
            occupied.close()
            await occupied.wait_closed()
            for server, _ in servers.values():
                server.close()
                await server.wait_closed()

    async def test_non_collision_bind_failure_remains_visible(self):
        record = ProxyRecord('127.0.0.1', 41001, 'http')
        cache = Mock()
        cache.load.return_value = (None, [record])
        args = SimpleNamespace(bind='127.0.0.1', port_start=20000, port_count=30000)
        with patch('realtime.proxy_relay.asyncio.start_server', AsyncMock(side_effect=OSError(errno.EACCES, 'denied'))):
            with self.assertRaises(OSError) as caught:
                await _reconcile_relays(args, cache, {}, 'user', 'pass')
        self.assertEqual(caught.exception.errno, errno.EACCES)

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

    async def test_socks_upstream_receives_user_password_auth(self):
        seen = asyncio.get_running_loop().create_future()

        async def upstream(reader, writer):
            self.assertEqual(await reader.readexactly(3), b'\x05\x01\x02')
            writer.write(b'\x05\x02')
            await writer.drain()
            ack = await reader.readexactly(2)
            self.assertEqual(ack, b'\x01\x04')
            size = ack[1]

            user = await reader.readexactly(size)
            size = (await reader.readexactly(1))[0]
            password = await reader.readexactly(size)
            seen.set_result((user, password))
            writer.write(b'\x01\x00')
            await writer.drain()
            request = await reader.readexactly(14)
            self.assertEqual(request, b'\x05\x01\x00\x03\x071.2.3.4\x01\xbb')
            writer.write(b'\x05\x00\x01\x01\x00\x00\x00\x00\x00\x00')
            await writer.drain()
            self.assertEqual(await reader.readexactly(4), b'PING')
            writer.write(b'PONG')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        record = ProxyRecord('127.0.0.1', upstream_port, 'socks5')
        relay_server = await asyncio.start_server(
            lambda reader, writer: relay_connection(reader, writer, record, 'user', 'pass'),
            '127.0.0.1', 0,
        )
        relay_port = relay_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', relay_port)
            writer.write(b'CONNECT 1.2.3.4:443 HTTP/1.1\r\nHost: 1.2.3.4:443\r\n\r\nPING')
            await writer.drain()
            self.assertIn(b'200 Connection Established', await reader.readuntil(b'\r\n\r\n'))
            self.assertEqual(await reader.readexactly(4), b'PONG')
            self.assertEqual(await asyncio.wait_for(seen, 1), (b'user', b'pass'))
            writer.close()
            await writer.wait_closed()
        finally:
            relay_server.close()
            upstream_server.close()
            await relay_server.wait_closed()
            await upstream_server.wait_closed()


if __name__ == '__main__':
    unittest.main()
