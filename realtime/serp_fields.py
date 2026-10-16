"""A stable organic SERP contract; preserve observed data, never invent fields."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import ipaddress
import re

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

FEATURE_MODULES = {
    "ad": "ads", "knowledge_panel": "knowledge_graph", "people_also_ask": "related_questions",
    "related_questions": "related_questions", "related_searches": "related_searches",
    "ai_summary": "ai_overview", "local": "local_results", "shopping": "shopping_results",
    "news": "top_stories", "image": "images", "images_inline": "images",
    "video": "videos", "videos": "videos", "sitelinks": "sitelinks",
    "featured_snippet": "featured_snippets", "answer_box": "answer_boxes",
    "calculator": "calculators", "weather": "weather", "dictionary": "dictionary",
}


def public_fields(value):
    """Exclude transport/credential material from nested upstream public fields."""
    if isinstance(value, dict):
        return {k: public_fields(v) for k, v in value.items()
                if not re.search(r'token|password|cookie|authorization|secret|proxy|headers|raw_html', k, re.I)}
    if isinstance(value, list):
        return [public_fields(v) for v in value]
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        try:
            p = urlsplit(value)
            if p.username or p.password:
                return None
            query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                               if not re.search(r'token|password|secret|api_key|authorization', k, re.I)])
            return urlunsplit((p.scheme, p.netloc, p.path, query, p.fragment))
        except ValueError:
            return None
    return value


def link_fields(link, upstream_domain=None):
    p = urlsplit(link)
    return dict(target_host=p.hostname, domain=p.hostname, upstream_domain=upstream_domain,
                url_scheme=p.scheme, url_path=p.path or '/',
                link_status='google_news_redirect' if p.hostname == 'news.google.com' else 'target_url')


def field_status(row, derived=()):
    return {k: 'derived' if k in derived and v is not None else
            'not_provided' if v is None else 'observed'
            for k, v in row.items() if k != 'field_status'}


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
    row = {
        "type": "organic", "position": rank, "rank_absolute": absolute,
        "upstream_position": position if isinstance(position, dict) else None,
        "page": page, "title": item.get("title") or None, "link": url,
        "redirect_link": item.get("url") if item.get("url") != url else None,
        "snippet": item.get("snippet") if isinstance(item.get("snippet"), str) else None,
        "displayed_link": item.get("display_url") or None,
        **link_fields(url, item.get("domain")),
        "upstream_result_id": item.get("id"), "upstream_type": item.get("type"),
        "upstream_engine": item.get("engine"),
        "upstream_domain_info": public_fields(item.get("domain_info")),
        "classification": public_fields(item.get("classification")),
        **{key: item.get(key) for key in OPTIONAL_FIELDS},
    }
    # A favicon for google.com must not masquerade as the target site's icon.
    row['upstream_favicon'] = row['favicon']
    try:
        icon_host = urlsplit(row['favicon']).hostname if isinstance(row['favicon'], str) else None
    except ValueError:
        icon_host = None
    if icon_host and icon_host.removeprefix('www.') == 'google.com' and (row['target_host'] or '').removeprefix('www.') != 'google.com':
        row['favicon'] = None
    row['field_status'] = field_status(row, ('target_host','domain','url_scheme','url_path','link_status'))
    if row['upstream_favicon'] and not row['favicon']:
        row['field_status']['favicon'] = 'upstream_host_mismatch'
    return public_fields(row)


def response_envelope(payload: dict, params: dict, results: list) -> dict[str, Any]:
    # Only public SERP fields cross this boundary: no headers, proxy config or bodies.
    features = payload.get("serp_features")
    features = features if isinstance(features, list) else []
    observed = bool(params.get("features") == "true")
    modules = {name: None for name in (*MODULES, 'sitelinks', 'featured_snippets', 'answer_boxes', 'calculators', 'weather', 'dictionary')}
    statuses = {name: 'not_implemented' for name in modules}
    for name in modules:
        if payload.get(name) is not None:
            modules[name] = public_fields(payload[name])
            statuses[name] = 'upstream_observed'
    for feature in features:
        if not isinstance(feature, dict):
            continue
        name = FEATURE_MODULES.get(feature.get('type'))
        if name and modules[name] is None:
            modules[name] = []
        if name and isinstance(modules[name], list):
            modules[name].append(public_fields(feature))
            statuses[name] = 'upstream_observed'
    return {
        "schema_version": SCHEMA_VERSION,
        "field_schema_version": "google-fields-v2",
        "search_parameters": {"engine": "google", "q": params["text"],
                              "hl": params["lang"].lower(), "gl": params["region"].lower(),
                              "start": params["start"], "num": params["limit"],
                              "device": params.get('device'), "location": params.get('location')},
        "search_information": {"total_results": None},
        "organic_results": [r.serp_fields for r in results],
        "pagination": payload.get("pagination"),
        "serp_features": public_fields(features),
        **modules,
        "module_status": statuses,
        "upstream_query": public_fields(payload.get('query')),
        "upstream_metadata": public_fields(payload.get('meta')),
        "capabilities": {"organic_results": "supported",
                         "serp_features": "upstream_passthrough" if observed else "disabled",
                         "article_content": "optional"},
    }
