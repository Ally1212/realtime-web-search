"""Explicit bounded News enrichment. Default: offline preview, no data writes.

Run from project root: python -m scripts.backfill_news_links --help
No legacy migrations or collector switches are touched.
"""
import argparse
import json
import os
from pathlib import Path

from realtime.campaign_store import CampaignStore
from realtime.news_backfill import run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--max-requests', type=int, default=20)
    p.add_argument('--max-links', type=int, default=20)
    p.add_argument('--budget-seconds', type=int, default=60)
    p.add_argument('--pause-file', type=Path, default=Path('/tmp/searchflow-news-backfill.pause'))
    p.add_argument('--retry-unresolved', action='store_true', help='Revisit failed outcomes after their retry time')
    p.add_argument('--retry-seconds', type=int, default=300)
    p.add_argument('--apply', action='store_true', help='Write only additive enrichment and progress tables')
    p.add_argument('--online', action='store_true', help='Resolve through bounded Google-only adapter; no publisher requests')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.online and not args.apply:
        p.error('--online requires --apply; preview never makes network calls')
    if args.output.exists():
        p.error('Choose a new output file; existing evidence must not be overwritten')
    store = CampaignStore(os.environ['DATABASE_URL'], initialize=False)
    discovery = None
    if args.online:
        from realtime.discovery import SearchDiscovery
        discovery = SearchDiscovery(proxy_profile='direct')
    result = run(store, discovery, max_requests=args.max_requests, max_links=args.max_links,
                 budget_seconds=args.budget_seconds, pause_file=args.pause_file, apply=args.apply, retry_unresolved=args.retry_unresolved, retry_seconds=args.retry_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    args.output.chmod(0o600)
    print(json.dumps({k: v for k, v in result.items() if k not in ('outcomes', 'completed_requests')}))


if __name__ == '__main__':
    main()
