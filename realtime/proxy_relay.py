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
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(record.host, record.port), timeout=15,
        )
        upstream_writer.write(_with_proxy_auth(header, username, password))
        await upstream_writer.drain()
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
        records_by_key = {record.key: record for record in records if record.protocol == "http"}
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
