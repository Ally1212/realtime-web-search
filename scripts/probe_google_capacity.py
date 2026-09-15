"""Bounded Google-only proxy diagnostic; no body fetch or Whale accounting."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time

from realtime.config import Config
from realtime.campaign_store import CampaignStore, _POOLS
from realtime.discovery import SearchDiscovery
from realtime.proxy_pool import ProxyPool, ProxySynchronizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=['private', 'public_google'], default='public_google')
    parser.add_argument('--attempts', type=int, choices=range(1, 65), default=24)
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--successful-only', action='store_true', help='Restrict to endpoints with a real Google success in the last 30 minutes')
    parser.add_argument('--interval', type=float, default=0, help='Seconds between scheduling probes, in addition to all shared limits')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    config = Config()
    production = CampaignStore(config.database_url)
    count = ProxySynchronizer(config).sync(args.profile, force=True)
    pool = ProxyPool(config)
    _, records = pool.cache.load(args.profile)
    if args.successful_only:
        with production.connect() as connection:
            healthy = {r['proxy_key_hash'] for r in connection.execute(
                "SELECT proxy_key_hash FROM google_proxy_sessions WHERE last_success_at>now()-interval '30 minutes' AND (cooldown_until IS NULL OR cooldown_until<=now())").fetchall()}
        import hashlib
        reload_records = pool._reload
        pool._reload = lambda profile: [r for r in reload_records(profile) if hashlib.sha256(r.key.encode()).hexdigest() in healthy]
        records = pool._reload(args.profile)
    report = dict(started=time.time(), profile=args.profile, cached=count,
                  hosts=len({r.host for r in records}), rows=[],
                  scope='Shared production rate limits and inherited cooldowns; excluded from experiment document and Whale counts')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps({k:v for k,v in report.items() if k != 'rows'}), flush=True)

    def probe(index):
        sites = ['www.163.com', 'finance.sina.com.cn', 'www.53ai.com', 'www.unite.ai']
        term = '(人工智能 OR AI OR 大模型 OR ChatGPT OR 机器学习)' if index % 2 else '人工智能'
        query = f'site:{sites[(index//2)%len(sites)]} {term} after:2026-08-01 before:2026-09-01'
        client = SearchDiscovery(
            timeout=15, providers=('openserp',), proxy_pool=pool, proxy_profile=args.profile,
            openserp_url=config.openserp_url,
            openserp_request_timeout_seconds=config.openserp_request_timeout_seconds,
            language='zh', proxy_provider_attempts=0,
            source_slot_acquirer=production.acquire_discovery_slot,
            source_result_recorder=production.record_discovery_result,
            proxy_group_reserver=production.reserve_google_proxy_group,
            proxy_result_recorder=production.record_google_proxy_result,
            serp_attempt_recorder=production.record_google_serp_attempt)
        result = dict(index=index, query=query)
        try:
            rows = client._attempt('openserp', query, 1)
            result['urls'] = [r.url for r in rows]
        except Exception as exc:
            result['error'] = getattr(exc, 'reason', type(exc).__name__)
        finally:
            result['attempts'] = client.attempts
            client.close()
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        tasks = []
        for i in range(args.attempts):
            if i and args.interval > 0:
                time.sleep(min(args.interval, 60))
            tasks.append(executor.submit(probe, i))
        for task in as_completed(tasks):
            result = task.result()
            report['rows'].append(result)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(dict(index=result['index'], results=len(result.get('urls', [])),
                                  error=result.get('error'), http_attempts=len(result['attempts']))), flush=True)
    attempts = [a for r in report['rows'] for a in r['attempts']]
    report.update(finished=time.time(), actual_attempts=len(attempts),
                  successful=sum(a['success'] for a in attempts),
                  result_urls=len({url for r in report['rows'] for url in r.get('urls', [])}),
                  errors=dict(Counter(a['error'] for a in attempts if a['error'])))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'rows'}), flush=True)
    for connection_pool in _POOLS.values():
        connection_pool.close()


if __name__ == '__main__':
    main()
