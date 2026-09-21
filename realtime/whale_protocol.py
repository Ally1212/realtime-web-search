from __future__ import annotations

import copy
import hashlib
from typing import Any, Iterable


SUPPORTED_TASK_TYPES = ("keyword_search", "content_detail", "backfill")
SUPPORTED_CAPABILITIES = ("identity", "title", "body", "metrics", "subtitles")
DEFAULT_REQUIRED_CAPABILITIES = ("identity", "body")


def _clean_capabilities(values: Iterable[object] | None) -> list[str]:
    return list(dict.fromkeys(
        value for value in (str(item).strip() for item in (values or ()))
        if value in SUPPORTED_CAPABILITIES
    ))


def stable_record_key(source_platform: str, canonical_url: str) -> str:
    """Return the identity of the remote entity, independent of content versions."""
    url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return f"{source_platform}:{url_hash}"


def payload_hash(value: object) -> str:
    import json

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def readiness(
    provided_capabilities: Iterable[object] | None,
    required_capabilities: Iterable[object] | None = None,
) -> dict[str, Any]:
    provided = _clean_capabilities(provided_capabilities)
    required = _clean_capabilities(required_capabilities) or list(DEFAULT_REQUIRED_CAPABILITIES)
    missing = [capability for capability in required if capability not in provided]
    return {
        "required_capabilities": required,
        "provided_capabilities": provided,
        "missing_capabilities": missing,
        "ready": not missing,
    }


def merge_messages(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge partial capability payloads for one stable source_record_key."""
    if (
        existing.get("source", {}).get("source_record_key")
        != incoming.get("source", {}).get("source_record_key")
    ):
        raise ValueError("cannot merge different source_record_key values")

    merged = copy.deepcopy(existing)
    for section in ("source", "discovery", "content"):
        merged.setdefault(section, {})
        merged[section].update(copy.deepcopy(incoming.get(section) or {}))
    merged["provided_capabilities"] = list(dict.fromkeys(
        _clean_capabilities(existing.get("provided_capabilities"))
        + _clean_capabilities(incoming.get("provided_capabilities"))
    ))
    merged["source"]["payload_hash"] = incoming.get("source", {}).get(
        "payload_hash", merged["source"].get("payload_hash")
    )
    metadata = merged["discovery"].setdefault("metadata", {})
    metadata.update(copy.deepcopy(incoming.get("discovery", {}).get("metadata") or {}))
    current_readiness = dict(metadata.get("readiness") or {})
    metadata["readiness"] = readiness(
        merged["provided_capabilities"],
        current_readiness.get("required_capabilities"),
    )
    return merged
