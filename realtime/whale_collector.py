from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import subprocess
import sys
import time
from typing import Any

import requests

from .campaign_store import CampaignStore
from .config import Config


RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
logger = logging.getLogger(__name__)


def _diagnostic(event: str, **fields: object) -> None:
    """Emit safe collector diagnostics even when the host has no log config."""
    rendered = " ".join(f"{name}={value}" for name, value in fields.items())
    print(f"{event} {rendered}".rstrip(), file=sys.stderr, flush=True)


def _sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def whale_message(item: dict[str, object], task: dict[str, Any], config: Config) -> tuple[str, dict[str, Any]]:
    """Convert one extracted public web page to the documented whale.ingest.v1 envelope."""
    url = str(item["url"])
    content = str(item["content"])
    title = str(item.get("title") or "").strip()
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    external_id = f"url:{url_hash}"
    received_at = str(item["fetched_at"])
    raw_payload = {
        'url': url, 'title': title, 'content': content,
        'source_engines': list(item.get('source_engines') or ()),
    }
    event_hash = _sha256(raw_payload)
    record_key = f"{task['source_platform']}:{url_hash}:{event_hash}"
    payload_hash = f"sha256:{event_hash}"
    capabilities = ["identity", "body"]
    normalized_content: dict[str, Any] = {
        "external_content_id": external_id,
        "content_type": "article",
        "body_text": content,
        "body_format": "plain",
        "language": str(item.get("language") or "unknown"),
        "source_url": url,
        "canonical_url": url,
        "published_at": received_at,
        "status": "active",
    }
    if title:
        normalized_content["title"] = title
        capabilities.insert(1, "title")
    message: dict[str, Any] = {
        "schema_version": "whale.ingest.v1",
        "dataset_id": str(task["dataset_id"]),
        "source": {
            "source_platform": str(task["source_platform"]),
            "source_name": config.whale_source_name,
            "source_record_key": record_key,
            "payload_hash": payload_hash,
            "received_at": received_at,
            "trace_id": str(task["task_id"]),
        },
        "discovery": {
            "collector": config.whale_agent_id,
            "acquisition_type": "backfill" if task["task_type"] == "backfill" else "crawler",
            "source_name": config.whale_source_name,
            "source_url": url,
            "discovered_at": str(item.get("discovered_at") or received_at),
            "metadata": {
                "query": str(item.get("query") or ""),
                "source_engines": list(item.get("source_engines") or ()),
                "campaign_id": str(item["campaign_id"]),
            },
        },
        "content": normalized_content,
        "provided_capabilities": capabilities,
    }
    return record_key, message


class WhaleClient:
    def __init__(self, config: Config, session: requests.Session | None = None):
        if not config.whale_collector_api_key:
            raise ValueError("WHALE_COLLECTOR_API_KEY is required")
        self.config = config
        self.base_url = config.whale_base_url.rstrip("/")
        self.session = session or requests.Session()
        self.agent_id = config.whale_agent_id

    def post(self, path: str, payload: object) -> requests.Response:
        return self.session.post(
            f"{self.base_url}{path}", json=payload,
            headers={"Authorization": f"Bearer {self.config.whale_collector_api_key}"},
            timeout=self.config.request_timeout,
        )

    def register(self, current_load: int = 0) -> None:
        # Agent IDs may be bound when a Collector Key is issued.  Let Whale
        # resolve that binding instead of making a local name guess.
        response = self.post("/v1/collectors/register", {
            "name": "Realtime Web Search Collector",
            "agent_type": self.config.whale_source_platform,
            "runtime": f"python{sys.version_info.major}.{sys.version_info.minor}",
            "version": "0.2.0",
            "supported_platforms": [self.config.whale_source_platform],
            "supported_task_types": list(self.config.whale_supported_task_types),
            "declared_capabilities": ["identity", "title", "body"],
            "max_concurrency": self.config.whale_max_concurrency,
            "current_load": current_load,
            "metadata": {"crawler": "scrapy", "discovery": ["google", "google-news-rss"]},
        })
        response.raise_for_status()
        data = response.json()
        bound_id = (
            data.get("agent_id")
            or dict(data.get("data") or {}).get("agent_id")
            or dict(data.get("agent") or {}).get("id")
        )
        if bound_id:
            self.agent_id = str(bound_id)

    def heartbeat(self, current_load: int) -> None:
        self.post("/v1/collectors/heartbeat", {
            "agent_id": self.agent_id, "current_load": current_load,
        }).raise_for_status()

    def claim(self, limit: int) -> list[dict[str, Any]]:
        response = self.post("/v1/collection/tasks/claim", {
            "agent_id": self.agent_id,
            "declared_capabilities": ["identity", "title", "body"],
            "limit": limit,
        })
        response.raise_for_status()
        return list(response.json().get("tasks") or [])

    def task_heartbeat(self, task_id: str, cursor: str, stats: dict[str, int]) -> dict[str, Any]:
        response = self.post(f"/v1/collection/tasks/{task_id}/heartbeat", {
            "agent_id": self.agent_id, "cursor": cursor, "stats": stats,
        })
        response.raise_for_status()
        return dict(response.json())

    def complete(self, task_id: str, *, collected: int, ingested: int, duplicates: int, cursor: str) -> None:
        self.post(f"/v1/collection/tasks/{task_id}/complete", {
            "agent_id": self.agent_id, "collected_count": collected,
            "ingested_count": ingested, "duplicate_count": duplicates, "cursor": cursor,
        }).raise_for_status()

    def fail(self, task_id: str, message: str, cursor: str, retry: bool) -> None:
        self.post(f"/v1/collection/tasks/{task_id}/fail", {
            "agent_id": self.agent_id, "message": message[:500],
            "cursor": cursor, "retry": retry,
        }).raise_for_status()

    def bulk_ingest(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = self.post("/v1/documents/bulk", messages)
        if response.status_code >= 400:
            error = RuntimeError(f"Whale ingest HTTP {response.status_code}")
            setattr(error, "status_code", response.status_code)
            raise error
        return list(response.json())


class WhaleRunner:
    def __init__(self, config: Config):
        self.config = config
        self.store = CampaignStore(config.database_url)
        self.client = WhaleClient(config)

    @staticmethod
    def _payload(task: dict[str, Any]) -> tuple[str, list[str], int, str]:
        payload = dict(task.get("payload") or {})
        task_type = str(task["task_type"])
        urls = payload.get("urls") or []
        # Whale validates keyword_search tasks using keyword/keywords.  Keep
        # query as a backward-compatible alias for already-created tasks.
        keyword_values = payload.get("keywords") or []
        if isinstance(keyword_values, str):
            keyword_values = [keyword_values]
        query = str(
            payload.get("keyword")
            or next((value for value in keyword_values if str(value).strip()), "")
            or payload.get("query")
            or ""
        ).strip()
        if task_type == "content_detail":
            if not isinstance(urls, list) or not urls:
                raise ValueError("content_detail requires payload.urls")
            query = query or "content detail"
        elif not query:
            raise ValueError(f"{task_type} requires payload.keyword, payload.keywords, or payload.query")
        aliases = [str(value) for value in payload.get("aliases") or [] if str(value).strip()]
        # The Whale form produces max_items/max_pages.  Honor max_items when
        # a collector-specific daily_target is absent so small UI test jobs
        # remain small.  Direct is the safe default for a generic task: a
        # project can still opt into its managed pool explicitly.
        target = min(max(int(payload.get("daily_target") or payload.get("max_items") or 1000), 1), 1_000_000)
        profile = str(payload.get("proxy_profile") or "direct")
        if profile not in {"private", "public", "direct"}:
            raise ValueError("payload.proxy_profile must be private, public, or direct")
        return query, aliases, target, profile

    def _flush_outbox(self, task_id: str) -> tuple[int, int, bool]:
        rows = self.store.whale_outbox(task_id, self.config.whale_ingest_batch_size)
        if not rows:
            return 0, 0, True
        ids = [int(row["id"]) for row in rows]
        try:
            results = self.client.bulk_ingest([dict(row["payload"]) for row in rows])
        except Exception as exc:
            status = int(getattr(exc, "status_code", 0) or 0)
            if status and status not in RETRYABLE_STATUS:
                self.store.mark_whale_outbox(ids, status="rejected", error=str(exc))
                return 0, 0, False
            self.store.retry_whale_outbox(ids, type(exc).__name__)
            return 0, 0, False
        if len(results) != len(rows):
            self.store.retry_whale_outbox(ids, "unexpected_bulk_response_length")
            return 0, 0, False
        delivered: list[int] = []
        rejected: list[int] = []
        ingested = duplicates = 0
        for row, result in zip(rows, results):
            receipt = str(result.get("receipt_status") or "")
            if receipt in {"queued", "accepted"}:
                delivered.append(int(row["id"]))
                ingested += 1
            elif receipt == "duplicate":
                delivered.append(int(row["id"]))
                duplicates += 1
            else:
                rejected.append(int(row["id"]))
        self.store.mark_whale_outbox(delivered, status="delivered")
        self.store.mark_whale_outbox(rejected, status="rejected", error="Whale rejected message")
        return ingested, duplicates, not rejected

    def _stats(self, campaign_id: str, task_id: str) -> dict[str, int]:
        campaign = self.store.campaign(campaign_id) or {}
        outbox = self.store.whale_outbox_counts(task_id)
        return {
            "collected_count": int(campaign.get("fetched") or 0),
            "ingested_count": int(outbox.get("delivered") or 0),
            "duplicate_count": int(campaign.get("duplicates") or 0),
        }

    def execute(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
        if not task_id:
            _diagnostic("whale_task_rejected", reason="missing_task_id")
            return
        try:
            dataset_id = str(task.get("dataset_id") or "").strip()
            source_platform = str(task.get("source_platform") or "").strip()
            task_type = str(task.get("task_type") or "").strip()
            _diagnostic(
                "whale_task_received", task_id=task_id, dataset_id=dataset_id,
                source_platform=source_platform, task_type=task_type,
            )
            if dataset_id != self.config.whale_dataset_id:
                raise ValueError("task dataset does not match WHALE_DATASET_ID")
            if source_platform != self.config.whale_source_platform:
                raise ValueError("task platform does not match WHALE_SOURCE_PLATFORM")
            query, aliases, target, profile = self._payload(task)
            campaign_id = self.store.create_whale_campaign(
                task_id=task_id, dataset_id=dataset_id,
                source_platform=source_platform, task_type=task_type,
                query=query, aliases=aliases, daily_target=target, proxy_profile=profile,
                task_payload=dict(task.get("payload") or {}),
            )
        except Exception as exc:
            # Return the validation reason to the task detail.  It contains
            # only local field names/values, never credentials or payload data.
            reason = str(exc).strip() or type(exc).__name__
            logger.warning("whale_task_rejected task_id=%s reason=%s", task_id, reason)
            _diagnostic("whale_task_rejected", task_id=task_id, reason=reason)
            self.client.fail(task_id, reason, "", retry=False)
            return
        process = subprocess.Popen([sys.executable, "-m", "realtime.scrapy_runner", campaign_id])
        cursor = ""
        ingested = duplicates = 0
        control = "none"
        target_reached = False
        try:
            while process.poll() is None:
                added, duplicate, healthy = self._flush_outbox(task_id)
                ingested += added
                duplicates += duplicate
                stats = self._stats(campaign_id, task_id)
                cursor = json.dumps({"campaign_id": campaign_id, "outbox_pending": self.store.whale_outbox_counts(task_id).get("pending", 0)})
                self.client.heartbeat(1)
                reply = self.client.task_heartbeat(task_id, cursor, stats)
                control = str(reply.get("control_signal") or "none")
                if stats["ingested_count"] >= target:
                    target_reached = True
                    process.terminate()
                    break
                if control in {"pause", "cancel"}:
                    process.terminate()
                    break
                time.sleep(self.config.whale_heartbeat_seconds)
            process.wait(timeout=30)
            while self.store.whale_outbox_counts(task_id).get("pending", 0):
                added, duplicate, healthy = self._flush_outbox(task_id)
                ingested += added
                duplicates += duplicate
                if not healthy:
                    raise RuntimeError("Whale outbox delivery failed")
            stats = self._stats(campaign_id, task_id)
            if control == "pause":
                self.store.update_whale_task(task_id, status="paused", cursor=cursor)
                self.store.set_status(campaign_id, "paused")
                return
            if control == "cancel":
                self.store.update_whale_task(task_id, status="canceled", cursor=cursor)
                self.store.set_status(campaign_id, "stopped")
                return
            outbox = self.store.whale_outbox_counts(task_id)
            if (process.returncode and not target_reached) or outbox.get("pending", 0) or outbox.get("rejected", 0):
                raise RuntimeError(f"crawler exit={process.returncode}")
            self.client.complete(task_id, collected=stats["collected_count"], ingested=stats["ingested_count"],
                                 duplicates=duplicates, cursor=cursor)
            self.store.update_whale_task(task_id, status="succeeded", cursor=cursor)
            self.store.set_status(campaign_id, "stopped")
        except Exception as exc:
            self.store.update_whale_task(task_id, status="failed", cursor=cursor)
            self.store.set_status(campaign_id, "failed", type(exc).__name__)
            self.client.fail(task_id, type(exc).__name__, cursor, retry=True)
            if process.poll() is None:
                process.terminate()

    def run(self) -> None:
        if not self.config.whale_enabled:
            raise ValueError("WHALE_ENABLED must be true to run whale-worker")
        retry_seconds = 5
        registered = False
        while True:
            try:
                if not registered:
                    self.client.register()
                    registered = True
                    _diagnostic("whale_collector_registered", agent_id=self.client.agent_id)
                tasks = self.client.claim(self.config.whale_claim_limit)
                self.client.heartbeat(len(tasks))
                retry_seconds = 5
                if tasks:
                    logger.warning("whale_tasks_claimed task_ids=%s", [str(task.get("id") or "") for task in tasks])
                for task in tasks:
                    self.execute(task)
                if not tasks:
                    time.sleep(self.config.whale_heartbeat_seconds)
            except requests.RequestException as exc:
                # A temporary Whale/API gateway outage must not make the
                # collector lose its process or require a manual restart.
                registered = False
                logger.warning("whale_api_unavailable retry_in_seconds=%s error=%s", retry_seconds, type(exc).__name__)
                time.sleep(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 300)


class ContinuousWhaleRunner:
    TASK_PREFIX = "continuous"

    def __init__(self, config: Config):
        self.config = config
        self.store = CampaignStore(config.database_url)
        self.runner = WhaleRunner(config)

    def _task_id(self, keyword: str) -> str:
        keyword_hash = hashlib.sha256(keyword.encode()).hexdigest()[:12]
        return f"{self.TASK_PREFIX}:{keyword_hash}"

    def _flush_pending(self) -> None:
        for task_id in self.store.pending_whale_task_ids(f"{self.TASK_PREFIX}:"):
            while self.store.whale_outbox_counts(task_id).get("pending", 0):
                _, _, healthy = self.runner._flush_outbox(task_id)
                if not healthy:
                    return

    def _run_keyword(self, keyword: str) -> None:
        target = min(max(self.config.continuous_max_items_per_keyword, 1), 1_000_000)
        profile = self.config.continuous_proxy_profile
        if profile not in {"private", "public", "direct"}:
            raise ValueError("CONTINUOUS_PROXY_PROFILE must be private, public, or direct")
        task_id = self._task_id(keyword)
        campaign_id = self.store.create_whale_campaign(
            task_id=task_id,
            dataset_id=self.config.whale_dataset_id,
            source_platform=self.config.whale_source_platform,
            task_type="keyword_search",
            query=keyword,
            aliases=[],
            daily_target=target,
            proxy_profile=profile,
            task_payload={"keyword": keyword, "max_items": target, "continuous": True},
            reactivate_existing=True,
        )
        _diagnostic("continuous_whale_keyword_started", task_id=task_id, keyword=keyword, target=target)
        process = subprocess.Popen([sys.executable, "-m", "realtime.scrapy_runner", campaign_id])
        try:
            while process.poll() is None:
                self.runner._flush_outbox(task_id)
                try:
                    self.runner.client.heartbeat(1)
                except requests.RequestException as exc:
                    _diagnostic("continuous_whale_heartbeat_failed", error=type(exc).__name__)
                time.sleep(self.config.whale_heartbeat_seconds)
            process.wait(timeout=30)
            while self.store.whale_outbox_counts(task_id).get("pending", 0):
                _, _, healthy = self.runner._flush_outbox(task_id)
                if not healthy:
                    raise RuntimeError("Whale outbox delivery failed")
            if process.returncode:
                raise RuntimeError(f"crawler exit={process.returncode}")
            stats = self.runner._stats(campaign_id, task_id)
            _diagnostic(
                "continuous_whale_keyword_finished", task_id=task_id,
                collected=stats["collected_count"], ingested=stats["ingested_count"],
            )
        except Exception as exc:
            self.store.update_whale_task(task_id, status="failed")
            self.store.set_status(campaign_id, "failed", type(exc).__name__)
            if process.poll() is None:
                process.terminate()
            _diagnostic("continuous_whale_keyword_failed", task_id=task_id, error=type(exc).__name__)

    def _run_keyword_with_agent(self, keyword: str, agent_id: str) -> None:
        child = ContinuousWhaleRunner(self.config)
        child.runner.client.agent_id = agent_id
        child._run_keyword(keyword)

    def run(self) -> None:
        if not self.config.continuous_whale_enabled:
            raise ValueError("CONTINUOUS_WHALE_ENABLED must be true to run continuous-whale")
        if not self.config.continuous_ai_keywords:
            raise ValueError("CONTINUOUS_AI_KEYWORDS must contain at least one keyword")
        self.runner.client.register()
        _diagnostic(
            "continuous_whale_registered", agent_id=self.runner.client.agent_id,
            keywords=len(self.config.continuous_ai_keywords),
        )
        while True:
            started = time.monotonic()
            self._flush_pending()
            workers = min(
                max(self.config.continuous_keyword_concurrency, 1),
                len(self.config.continuous_ai_keywords),
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(self._run_keyword_with_agent, keyword, self.runner.client.agent_id)
                    for keyword in self.config.continuous_ai_keywords
                ]
                for future in concurrent.futures.as_completed(futures):
                    future.result()
                    self._flush_pending()
            elapsed = time.monotonic() - started
            sleep_seconds = max(0, self.config.continuous_interval_seconds - elapsed)
            _diagnostic("continuous_whale_round_sleep", seconds=round(sleep_seconds, 2))
            time.sleep(sleep_seconds)


def run_whale_worker() -> None:
    WhaleRunner(Config()).run()


def run_continuous_whale() -> None:
    ContinuousWhaleRunner(Config()).run()
