from __future__ import annotations

import json
from dataclasses import asdict
from html import escape
from typing import Any, Iterable

import requests

from .fetcher import LiveDocument


INDEX_MAPPING = {
    "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
    "mappings": {"properties": {
        "url": {"type": "keyword", "ignore_above": 2048},
        "title": {"type": "text"},
        "content": {"type": "text"},
        "summary": {"type": "text", "index": False},
        "query": {"type": "keyword"},
        "source_engines": {"type": "keyword"},
        "discovered_at": {"type": "date"},
        "fetched_at": {"type": "date"},
        "http_status": {"type": "integer"},
        "content_hash": {"type": "keyword", "index": False},
    }},
}


class SearchIndex:
    def __init__(self, base_url: str, index_name: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.index_name = index_name
        self.timeout = timeout

    def ensure_index(self) -> None:
        response = requests.head(f"{self.base_url}/{self.index_name}", timeout=self.timeout)
        if response.status_code == 404:
            response = requests.put(f"{self.base_url}/{self.index_name}", json=INDEX_MAPPING, timeout=self.timeout)
        response.raise_for_status()

    def bulk_index(self, documents: Iterable[LiveDocument]) -> tuple[int, list[str]]:
        lines: list[str] = []
        for document in documents:
            lines.append(json.dumps({"index": {"_index": self.index_name, "_id": document.document_id}}))
            lines.append(json.dumps(asdict(document), ensure_ascii=False))
        if not lines:
            return 0, []
        response = requests.post(
            f"{self.base_url}/_bulk", data="\n".join(lines) + "\n",
            headers={"Content-Type": "application/x-ndjson"}, timeout=self.timeout,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        errors = [str(item["index"]["error"]) for item in items if item.get("index", {}).get("error")]
        return len(items) - len(errors), errors

    def refresh(self) -> None:
        response = requests.post(f"{self.base_url}/{self.index_name}/_refresh", timeout=self.timeout)
        if response.status_code != 404:
            response.raise_for_status()

    def count(self) -> int:
        response = requests.get(f"{self.base_url}/{self.index_name}/_count", timeout=self.timeout)
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return int(response.json()["count"])

    @staticmethod
    def _snippet(value: str) -> str:
        return escape(value).replace("&lt;em&gt;", "<em>").replace("&lt;/em&gt;", "</em>")

    def search(self, query: str, size: int = 20) -> dict[str, Any]:
        response = requests.post(f"{self.base_url}/{self.index_name}/_search", json={
            "size": min(max(size, 1), 100),
            "query": {"multi_match": {"query": query, "fields": ["title^4", "content", "url^2"]}},
            "sort": ["_score", {"fetched_at": "desc"}],
            "highlight": {"fields": {"content": {"fragment_size": 240, "number_of_fragments": 1}}},
            "_source": ["url", "title", "summary", "fetched_at", "http_status", "source_engines", "query"],
        }, timeout=self.timeout)
        if response.status_code == 404:
            return {"total": 0, "took_ms": 0, "results": []}
        response.raise_for_status()
        payload = response.json()
        hits = payload.get("hits", {})
        results = []
        for item in hits.get("hits", []):
            source = item.get("_source", {})
            snippet = (item.get("highlight", {}).get("content") or [source.get("summary", "")])[0]
            results.append({**source, "score": item.get("_score"), "snippet": self._snippet(snippet)})
        return {"total": hits.get("total", {}).get("value", 0), "took_ms": payload.get("took", 0), "results": results}
