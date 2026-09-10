from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Live search discovery and real-time crawler")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("worker")
    commands.add_parser("whale-worker")
    commands.add_parser("continuous-whale")
    sync = commands.add_parser("sync-proxies")
    sync.add_argument("--profile", choices=("private", "public"), default="private")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--query", required=True)
    benchmark.add_argument("--hours", type=float, default=24)
    benchmark.add_argument("--profile", choices=("private", "public", "direct"), default="private")
    benchmark.add_argument("--target", type=int, default=50_000)
    free = commands.add_parser("benchmark-free")
    free.add_argument("--providers", default="wml_direct,searxng,curl,browser")
    free.add_argument("--query", action="append")
    free.add_argument("--query-limit", type=int, default=20)
    free.add_argument("--pages", default="1,3,11")
    free.add_argument("--profile", choices=("direct", "private", "public"), default="direct")
    free.add_argument("--rps", type=float, default=0.5)
    free.add_argument("--timeout", type=int, default=15)
    free.add_argument("--fetch-samples", type=int, default=20)
    free.add_argument("--output", default="state/benchmarks")
    free.add_argument("--resample", help="Reuse a saved search report and only resample article bodies")
    export = commands.add_parser("export-markdown", help="One-shot Google results and all available bodies; no Whale upload")
    export.add_argument("--query", required=True)
    export.add_argument("--languages", default="zh,en")
    export.add_argument("--pages", type=int, default=11)
    export.add_argument("--profile", choices=("private", "public", "direct"), default="private")
    export.add_argument("--output", required=True, help="Dedicated empty output directory")
    experiment = commands.add_parser("benchmark-google", help="Isolated Google-only daily-yield experiment")
    experiment.add_argument("action", choices=("start", "status", "pause", "resume", "export"))
    experiment.add_argument("--directory", required=True, help="Persistent experiment state directory")
    experiment.add_argument("--output", default="/export", help="Empty Markdown export directory for a new run")
    experiment.add_argument("--hours", type=float, default=24)
    experiment.add_argument("--whale", action="store_true")
    experiment.add_argument("--preflight", action="store_true", help="Bounded two-page pilot, not a 24h result")
    experiment.add_argument("--baseline-run", action="append", default=[])
    experiment.add_argument("--baseline-export", action="append", default=[])
    purge = commands.add_parser("purge-non-google-web")
    purge.add_argument("--apply", action="store_true")
    purge.add_argument("--manifest-directory", type=Path, default=Path("state"))
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
    elif args.command == "continuous-whale":
        from .whale_collector import run_continuous_whale
        run_continuous_whale()
    elif args.command == "sync-proxies":
        from .config import Config
        from .proxy_pool import ProxySynchronizer
        count = ProxySynchronizer(Config()).sync(args.profile, force=True)
        print(f"proxy cache synchronized: profile={args.profile} count={count}")
    elif args.command == "benchmark":
        from .benchmark import run_benchmark
        raise SystemExit(run_benchmark(args.query, args.hours, args.profile, args.target))
    elif args.command == "benchmark-free":
        from .free_benchmark import run_free_benchmark
        run_free_benchmark(args)
    elif args.command == "export-markdown":
        from .markdown_export import run_markdown_export
        run_markdown_export(args)
    elif args.command == "benchmark-google":
        from .google_experiment import command
        command(args)
    elif args.command == "purge-non-google-web":
        from .purge import run_purge
        run_purge(args.manifest_directory, apply=args.apply)


if __name__ == "__main__":
    main()
