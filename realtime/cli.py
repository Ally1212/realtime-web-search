from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Live search discovery and real-time crawler")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("worker")
    commands.add_parser("whale-worker")
    sync = commands.add_parser("sync-proxies")
    sync.add_argument("--profile", choices=("private", "public"), default="private")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--query", required=True)
    benchmark.add_argument("--hours", type=float, default=24)
    benchmark.add_argument("--profile", choices=("private", "public", "direct"), default="private")
    benchmark.add_argument("--target", type=int, default=50_000)
    args = parser.parse_args()
    if args.command == "serve":
        from .web import serve
        serve()
    elif args.command == "worker":
        from .worker import run_worker
        run_worker()
    elif args.command == "whale-worker":
        from .whale_collector import run_whale_worker
        run_whale_worker()
    elif args.command == "sync-proxies":
        from .config import Config
        from .proxy_pool import ProxySynchronizer
        count = ProxySynchronizer(Config()).sync(args.profile, force=True)
        print(f"proxy cache synchronized: profile={args.profile} count={count}")
    elif args.command == "benchmark":
        from .benchmark import run_benchmark
        raise SystemExit(run_benchmark(args.query, args.hours, args.profile, args.target))


if __name__ == "__main__":
    main()
