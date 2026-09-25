"""Credential-hiding HTTP CONNECT relays for OpenSERP per-context proxies."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import signal
from pathlib import Path

from .proxy_pool import ProxyCache, ProxyRecord, proxy_relay_ports


HEADER_LIMIT = 65536


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


def _with_proxy_auth(header: bytes, username: str, password: str) -> bytes:
    head, separator, tail = header.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("incomplete proxy request")
    lines = head.split(b"\r\n")
    if not lines or b" " not in lines[0]:
        raise ValueError("invalid proxy request")
    lines = [line for line in lines if not line.lower().startswith(b"proxy-authorization:")]
    token = base64.b64encode(f"{username}:{password}".encode())
    lines.append(b"Proxy-Authorization: Basic " + token)
    return b"\r\n".join(lines) + separator + tail


def _parse_connect_target(header: bytes) -> tuple[str, int]:
    request = header.split(b"\r\n", 1)[0].decode("ascii", "strict")
    parts = request.split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT":
        raise ValueError("only HTTP CONNECT is supported")
    host, separator, port = parts[1].rpartition(":")
    if not separator or not host:
        raise ValueError("invalid CONNECT target")
    try:
        parsed_port = int(port)
    except ValueError as exc:
        raise ValueError("invalid CONNECT port") from exc
    return host.strip("[]"), parsed_port

async def _socks5_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    port: int,
    username: str,
    password: str,
) -> None:
    writer.write(b"\x05\x01\x02")
    await writer.drain()
    method = await asyncio.wait_for(reader.readexactly(2), timeout=15)
    if method != b"\x05\x02":
        raise OSError("socks5 username/password authentication is unavailable")
    user, secret = username.encode(), password.encode()
    if len(user) > 255 or len(secret) > 255:
        raise ValueError("socks5 credentials are too long")
    writer.write(b"\x01" + len(user).to_bytes(1, "big") + user + len(secret).to_bytes(1, "big") + secret)
    await writer.drain()
    auth = await asyncio.wait_for(reader.readexactly(2), timeout=15)
    if auth != b"\x01\x00":
        raise OSError("socks5 authentication failed")
    host_bytes = host.encode("idna")
    if len(host_bytes) > 255:
        raise ValueError("socks5 target host is too long")
    writer.write(
        b"\x05\x01\x00\x03" + len(host_bytes).to_bytes(1, "big") + host_bytes
        + port.to_bytes(2, "big")
    )
    await writer.drain()
    reply = await asyncio.wait_for(reader.readexactly(4), timeout=15)
    if len(reply) != 4 or reply[1] != 0:
        raise OSError(f"socks5 connect failed: {reply[1]}")
    address_type = reply[3]
    if address_type == 1:
        await reader.readexactly(4)
    elif address_type == 3:
        size = (await reader.readexactly(1))[0]
        await reader.readexactly(size)
    elif address_type == 4:
        await reader.readexactly(16)
    else:
        raise OSError("invalid socks5 bind address")
    await reader.readexactly(2)

async def relay_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    record: ProxyRecord,
    username: str,
    password: str,
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        header = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), timeout=15)
        if len(header) > HEADER_LIMIT:
            raise ValueError("proxy header too large")
        host, port = _parse_connect_target(header)
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(record.host, record.port), timeout=15,
        )
        if record.protocol == "http":
            upstream_writer.write(_with_proxy_auth(header, username, password))
            await upstream_writer.drain()
        else:
            await _socks5_connect(
                upstream_reader, upstream_writer, host, port, username, password,
            )
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
        await asyncio.gather(
            _pipe(client_reader, upstream_writer),
            _pipe(upstream_reader, client_writer),
        )
    except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        if not client_writer.is_closing():
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            with contextlib.suppress(Exception):
                await client_writer.drain()
    finally:
        for writer in (upstream_writer, client_writer):
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()


async def _health(
    _reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    servers: dict[str, tuple[asyncio.Server, int]],
) -> None:
    body = json.dumps({"status": "ready", "relays": len(servers)}).encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def run(args: argparse.Namespace) -> None:
    username = os.getenv("SHARED_PROXY_USERNAME", "")
    password = os.getenv("SHARED_PROXY_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("shared proxy credentials are required")
    cache = ProxyCache(Path(args.cache_dir))
    servers: dict[str, tuple[asyncio.Server, int]] = {}
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(getattr(signal, name), stop.set)

    async def reconcile() -> None:
        _, records = cache.load("private")
        records_by_key = {record.key: record for record in records if record.protocol in {"http", "socks5"}}
        ports = proxy_relay_ports(
            list(records_by_key.values()), port_start=args.port_start, port_count=args.port_count,
        )
        obsolete = [
            key for key, (_, current_port) in servers.items()
            if key not in ports or ports[key] != current_port
        ]
        for key in obsolete:
            server, _ = servers.pop(key)
            server.close()
            await server.wait_closed()
        for key, port in ports.items():
            if key in servers:
                continue
            record = records_by_key[key]
            server = await asyncio.start_server(
                lambda reader, writer, row=record: relay_connection(
                    reader, writer, row, username, password,
                ),
                args.bind, port,
            )
            servers[key] = (server, port)

    await reconcile()
    health = await asyncio.start_server(
        lambda reader, writer: _health(reader, writer, servers), args.bind, args.health_port,
    )
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.refresh_seconds)
            except asyncio.TimeoutError:
                await reconcile()
    finally:
        health.close()
        for server, _ in servers.values():
            server.close()
        await health.wait_closed()
        await asyncio.gather(*(server.wait_closed() for server, _ in servers.values()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=os.getenv("PROXY_CACHE_DIR", "/app/state/proxies"))
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--health-port", type=int, default=19000)
    parser.add_argument("--port-start", type=int, default=20000)
    parser.add_argument("--port-count", type=int, default=30000)
    parser.add_argument("--refresh-seconds", type=float, default=10)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
