from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .config import Config
from .discovery import SearchDiscovery
from .fetcher import LiveDocument, LiveFetcher
from .search import SearchIndex
from .state import StateStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawlCoordinator:
    def __init__(self, config: Config):
        self.config = config
        self.store = StateStore(config.state_db)

    def start(self, query: str, pages: int, workers: int) -> str:
        job_id = self.store.create_job(query, pages, workers)
        threading.Thread(target=self._run, args=(job_id, query, pages, workers), daemon=True).start()
        return job_id

    def _run(self, job_id: str, query: str, pages: int, workers: int) -> None:
        counts = {"fetched": 0, "indexed": 0, "failed": 0, "blocked": 0}
        self.store.update_job(job_id, status="running")
        try:
            discovery = SearchDiscovery(self.config.searxng_url, self.config.request_timeout)
            results, engine_errors = discovery.discover(query, pages)
            self.store.update_job(
                job_id, discovered=len(results),
                engine_errors=json.dumps(engine_errors, ensure_ascii=False),
            )
            if not results:
                self.store.update_job(job_id, status="failed", error="搜索引擎没有返回 URL")
                return
            index = SearchIndex(self.config.opensearch_url, self.config.index_name, self.config.request_timeout)
            index.ensure_index()
            fetcher = LiveFetcher(self.config.user_agent, self.config.request_timeout)
            discovered_at = _now()
            batch: list[LiveDocument] = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(fetcher.fetch, result, query, discovered_at) for result in results]
                for future in as_completed(futures):
                    item = future.result()
                    if item.status == "success" and item.document:
                        counts["fetched"] += 1
                        batch.append(item.document)
                    elif item.status == "blocked":
                        counts["blocked"] += 1
                    else:
                        counts["failed"] += 1
                    self.store.add_event(job_id, item.url, item.title, item.status, item.http_status, item.error)
                    if len(batch) >= 20:
                        indexed, errors = index.bulk_index(batch)
                        counts["indexed"] += indexed
                        counts["failed"] += len(errors)
                        batch.clear()
                    self.store.update_job(job_id, **counts)
            if batch:
                indexed, errors = index.bulk_index(batch)
                counts["indexed"] += indexed
                counts["failed"] += len(errors)
            index.refresh()
            self.store.update_job(job_id, status="completed", **counts)
        except Exception as exc:
            self.store.update_job(job_id, status="failed", error=str(exc), **counts)
