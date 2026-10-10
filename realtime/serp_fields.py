"""A stable organic SERP contract; preserve observed data, never invent fields."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import ipaddress

SCHEMA_VERSION = "google-serp-v1"
URL_NORMALIZATION_VERSION = "google-tracking-v2"
GOOGLE_SOURCES = ("google_web", "google_news", "google_trends")


def result_items(response: dict) -> list[dict]:
    """Link-bearing entries, retaining references into the response for annotation."""
    rows = list(response.get('organic_results') or []) + list(response.get('news_results') or [])
    for topic in response.get('trending_searches') or []:
        rows.extend(topic.get('articles') or [])
    return [row for row in rows if isinstance(row, dict)]
MODULES = ("ads", "knowledge_graph", "related_questions", "related_searches",
           "ai_overview", "local_results", "shopping_results", "top_stories",
           "images", "videos")
OPTIONAL_FIELDS = ("source", "thumbnail", "favicon", "date", "author", "sitelinks",
                   "snippet_highlighted_words", "rich_snippet", "cited_by", "extracted_cited_by",
                   "rating", "reviews", "extensions", "cached_page_link", "about_page_link", "related_pages_link")


def public_serp_url(url: str) -> bool:
    """Check address syntax without visiting/resolving the article website.

    Downloads still use the stricter DNS-aware fetcher validation.
    """
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower().rstrip('.')
        if p.scheme.lower() not in {'http', 'https'} or not host or p.username or p.password:
            return False
        if p.port is not None and not 1 <= p.port <= 65535:
            return False
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            return False
        if host in {'google.com', 'www.google.com'} and p.path.rstrip('/') in {'/search', '/url', '/goto', '/sorry'}:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return '.' in host and not any(c.isspace() for c in host)
    except ValueError:
        return False


def normalize_serp_url(url: str) -> str:
    p = urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or '').lower()
    if ':' in host:
        host = '['+host+']'
    port = p.port
    netloc = host + (f':{port}' if port and (scheme, port) not in {('http', 80), ('https', 443)} else '')
    query = urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                       if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid','mc_cid','mc_eid','srsltid'}])
    return urlunsplit((scheme,netloc,p.path or '/',query,''))


def organic_result(item: dict, *, url: str, page: int) -> dict[str, Any]:
    rank = item.get("rank")
    rank = rank if type(rank) is int and rank > 0 else None
    position = item.get("position")
    absolute = position.get("absolute") if isinstance(position, dict) else None
    absolute = absolute if type(absolute) is int and absolute > 0 else None
    return {
        "type": "organic", "position": rank, "rank_absolute": absolute,
        "upstream_position": position if isinstance(position, dict) else None,
        "page": page, "title": item.get("title") or None, "link": url,
        "redirect_link": item.get("url") if item.get("url") != url else None,
        "snippet": item.get("snippet") if isinstance(item.get("snippet"), str) else None,
        "displayed_link": item.get("display_url") or None,
        "domain": item.get("domain") or urlsplit(url).hostname,
        **{key: item.get(key) for key in OPTIONAL_FIELDS},
    }


def response_envelope(payload: dict, params: dict, results: list) -> dict[str, Any]:
    # Only public SERP fields cross this boundary: no headers, proxy config or bodies.
    features = payload.get("serp_features")
    features = features if isinstance(features, list) else []
    observed = bool(params.get("features") == "true")
    return {
        "schema_version": SCHEMA_VERSION,
        "search_parameters": {"engine": "google", "q": params["text"],
                              "hl": params["lang"].lower(), "gl": params["region"].lower(),
                              "start": params["start"], "num": params["limit"],
                              "device": None},
        "search_information": {"total_results": None},
        "organic_results": [r.serp_fields for r in results],
        "pagination": payload.get("pagination"),
        "serp_features": features,
        **{name: None for name in MODULES},
        "module_status": {name: "not_implemented" for name in MODULES},
        "capabilities": {"organic_results": "supported",
                         "serp_features": "upstream_passthrough" if observed else "disabled",
                         "article_content": "optional"},
    }
