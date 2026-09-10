"""Bounded, repeatable AI search benchmark. Never uploads benchmark data to Whale."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .discovery import GoogleBlocked, SearchDiscovery, SearchResult
from .fetcher import LiveFetcher, normalize_url, relevant_to
from .proxy_pool import ProxyPool


AI_QUERIES = (
    "artificial intelligence", "人工智能", "AI agents", "人工智能智能体",
    "large language models", "大语言模型", "generative AI", "生成式人工智能",
    "AI chips", "人工智能芯片", "AI safety", "人工智能安全",
    "open source AI models", "开源大模型", "AI coding assistants", "人工智能编程助手",
    "multimodal AI", "多模态人工智能", "AI regulation", "人工智能监管",
)


def percentile(values: list[float], fraction: float) -> float:
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 3) if values else 0.0


def summarize(rows: list[dict]) -> list[dict]:
    summaries = []
    for provider in dict.fromkeys(row["provider"] for row in rows):
        group = [row for row in rows if row["provider"] == provider]
        attempted = [row for row in group if row["attempted"]]
        good = [row for row in attempted if row["success"] and row["count"] > 0]
        durations = [row["seconds"] for row in good]
        urls = {url for row in good for url in row["urls"]}
        page_overlap = []
        for query in dict.fromkeys(row["query"] for row in good):
            pages = [set(row["urls"]) for row in good if row["query"] == query]
            total = sum(map(len, pages))
            if total:
                page_overlap.append(1 - len(set().union(*pages)) / total)
        summaries.append({
            "provider": provider, "attempted": len(attempted), "skipped": len(group) - len(attempted),
            "valid_responses": sum(row["success"] for row in attempted),
            "empty_pages": sum(row["success"] and row["count"] == 0 for row in attempted),
            "successful": len(good), "success_rate": round(len(good) / max(len(attempted), 1), 4),
            "candidate_count": sum(row["count"] for row in good), "unique_urls": len(urls),
            "p50_seconds": percentile(durations, .5), "p95_seconds": percentile(durations, .95),
            "mean_page_overlap": round(sum(page_overlap) / max(len(page_overlap), 1), 4),
            "errors": {code: sum(row["error"] == code for row in attempted) for code in sorted({row["error"] for row in attempted if row["error"]})},
        })
    return summaries


def run_free_benchmark(args) -> None:
    config = Config()
    providers = tuple(value.strip() for value in args.providers.split(",") if value.strip())
    previous = json.loads(Path(args.resample).read_text(encoding="utf-8")) if args.resample else None
    queries = tuple(previous["queries"]) if previous else (tuple(args.query) if args.query else AI_QUERIES[:args.query_limit])
    pages = tuple(previous["pages"]) if previous else tuple(int(value) for value in args.pages.split(","))
    if not queries or any(page < 1 or page > 11 for page in pages) or not (0 < args.rps <= 2):
        raise ValueError("benchmark needs queries, pages 1–11, and 0 < rps <= 2")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / f"free-google-{stamp}.json"
    rows, bodies = list(previous["rows"]) if previous else [], []
    candidates = {}
    report = {"started_at": stamp, "finished": False, "profile": args.profile,
              "queries": list(queries), "pages": pages, "rps_limit": args.rps,
              "search_engine": "google", "cache_enabled": False,
              "rows": rows, "body_samples": bodies, "summary": []}
    if previous:
        report["search_started_at"] = previous.get("search_started_at", previous["started_at"])
        report["profile"] = previous["profile"]
        report["rps_limit"] = previous["rps_limit"]
        report["resampled_from"] = Path(args.resample).name
        for row in rows:
            for url, title in zip(row["urls"][:2], row["titles"][:2]):
                candidates.setdefault(url, (SearchResult(url, title, ("google_web",)), row["query"]))
    next_request = 0.0

    def slot(source, initial_rps):
        nonlocal next_request
        if source != "google_web":
            return {"allowed": True, "wait": 0}
        now = time.monotonic()
        wait = max(0, next_request - now)
        next_request = now + wait + 1 / args.rps
        return {"allowed": True, "wait": wait}

    def save():
        report["summary"] = summarize(rows)
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, report_path)

    started = time.monotonic()
    for provider in (() if previous else providers):
        clients = {language: SearchDiscovery(
            timeout=args.timeout, providers=(provider,), language=language,
            searxng_url=config.searxng_url, proxy_profile=args.profile,
            proxy_pool=ProxyPool(config) if args.profile != "direct" else None,
            source_slot_acquirer=slot, source_cooldown_seconds=1800,
        ) for language in ("en", "zh")}
        try:
            for query in queries:
                client = clients["zh" if any("\u4e00" <= char <= "\u9fff" for char in query) else "en"]
                for page in pages:
                    before = len(client.attempts)
                    tick = time.monotonic()
                    results, error = [], ""
                    try:
                        results = client._discover_google_page(query, page)
                    except GoogleBlocked as exc:
                        error = exc.reason
                    elapsed = time.monotonic() - tick
                    attempted = len(client.attempts) > before
                    urls = list(dict.fromkeys(normalize_url(row.url) for row in results))
                    rows.append({"provider": provider, "query": query, "page": page,
                                 "attempted": attempted, "success": not error, "error": error,
                                 "seconds": round(elapsed, 3), "count": len(results), "urls": urls,
                                 "titles": [row.title for row in results]})
                    for item in results[:2]:
                        candidates.setdefault(normalize_url(item.url), (item, query))
                    if attempted:
                        print(json.dumps({k: rows[-1][k] for k in ("provider", "query", "page", "seconds", "count", "error")}, ensure_ascii=False), flush=True)
                    save()
        finally:
            for client in clients.values():
                client._close_browser()
    report["search_wall_seconds"] = previous["search_wall_seconds"] if previous else round(time.monotonic() - started, 3)
    # Public page sampling, bounded to two URLs per query and four concurrent downloads.
    # LiveFetcher includes robots checks, public-address validation and size limits.
    fetcher = LiveFetcher(config.user_agent, timeout=args.timeout)

    def fetch_sample(candidate):
        item, query = candidate
        tick = time.monotonic()
        result = fetcher.fetch(item, query, datetime.now(timezone.utc).isoformat())
        document = result.document
        content = document.content if document else ""
        relevant = bool(document and relevant_to(content, document.title, (
            query, "artificial intelligence", "人工智能", "large language model", "大语言模型",
        )))
        return {"url": result.url, "query": query, "title": result.title,
                "status": result.status, "http_status": result.http_status,
                "seconds": round(time.monotonic() - tick, 3), "characters": len(content),
                "relevant": relevant, "sha256": hashlib.sha256(content.encode()).hexdigest() if content else None,
                "preview": content[:350]}

    # Round-robin across queries so the first few queries cannot consume the sample budget.
    grouped = {query: [item for item in candidates.values() if item[1] == query] for query in queries}
    samples = [grouped[query][index] for index in range(max(map(len, grouped.values()), default=0))
               for query in queries if index < len(grouped[query])][:args.fetch_samples]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for row in executor.map(fetch_sample, samples):
            bodies.append(row)
            save()
    report["finished"] = True
    report["total_wall_seconds"] = round(time.monotonic() - started, 3)
    report["valid_unique_bodies"] = len({row["sha256"] for row in bodies if row["relevant"] and row["characters"] >= 100})
    save()
    latest = directory / "latest.json"
    temporary = directory / "latest.tmp"
    temporary.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(temporary, latest)
    print(json.dumps({"report": str(report_path), "summary": report["summary"],
                      "body_samples": len(bodies), "valid_unique_bodies": report["valid_unique_bodies"]}, ensure_ascii=False), flush=True)
