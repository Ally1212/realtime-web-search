"""Google News search and Trending Now RSS, without visiting article sites."""
from __future__ import annotations

import time
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .discovery import SearchDiscovery, SearchResult
from .serp_fields import SCHEMA_VERSION, public_serp_url, result_items, link_fields, field_status


def text(node, name: str) -> str | None:
    for child in node:
        if child.tag.rsplit('}',1)[-1].lower() == name:
            return ''.join(child.itertext()).strip() or None
    return None


def date(value: str | None) -> str | None:
    try:
        parsed = parsedate_to_datetime(value) if value else None
        return parsed.isoformat() if parsed and parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def article(link: str | None, title: str | None, *, snippet=None, publisher=None,
            publisher_url=None, published_at=None, date_raw=None, thumbnail=None,
            feed_position=None) -> dict | None:
    if not link or not public_serp_url(link):
        return None
    # Only an offline token decode: no requests or DNS to the publisher.
    target = SearchDiscovery._offline_google_news_url(link)
    if not target or not public_serp_url(target):
        target = link
    wrapper = (urlsplit(target).hostname or '').lower() == 'news.google.com'
    row = dict(type='news', title=title, link=target, snippet=snippet,
                source=publisher, publisher=publisher, publisher_url=publisher_url,
                **link_fields(target), date=published_at, date_raw=date_raw,
                thumbnail=thumbnail, position=None, rank_absolute=None,
                feed_position=feed_position, redirect_link=link if target != link else None,
                google_news_link=link if (urlsplit(link).hostname or '').lower() == 'news.google.com' else None,
                publisher_link=None if wrapper else target,
                publisher_host=urlsplit(publisher_url).hostname if publisher_url else None,
                original_link=link,
                resolution_method='unresolved' if wrapper else 'offline_decode' if target != link else 'direct',
                is_resolved=not wrapper)
    row['link_status'] = 'google_news_redirect' if wrapper else 'publisher_url'
    row['field_status'] = field_status(row, ('target_host','domain','url_scheme','url_path','publisher_host','resolution_method','is_resolved'))
    if wrapper:
        row['field_status']['publisher_link'] = 'unresolved_google_redirect'
    return row


def traffic_fields(value):
    """A lower-bound marker is not an exact count or a Trends interest score."""
    match = re.fullmatch(r'\s*([\d,]+(?:\.\d+)?)\s*([KMB]?)\s*(\+?)\s*', value or '', re.I)
    if not match:
        return dict(traffic_value=None, traffic_is_lower_bound=None, traffic_unit=None)
    amount = float(match[1].replace(',', '')) * {'':1,'K':1000,'M':1000000,'B':1000000000}[match[2].upper()]
    return dict(traffic_value=int(amount), traffic_is_lower_bound=bool(match[3]),
                traffic_unit='upstream_approximate_search_volume')


def parse_feed(content: bytes, source: str, query: str, locale: str) -> dict:
    if source not in {'google_news','google_trends'}:
        raise ValueError('unsupported_google_feed')
    root = ElementTree.fromstring(content)
    if root.tag.rsplit('}',1)[-1].lower() != 'rss':
        raise ValueError('unexpected_google_feed_format')
    channel = next((n for n in root if n.tag.rsplit('}',1)[-1].lower() == 'channel'), None)
    if channel is None:
        raise ValueError('missing_feed_channel')
    news,topics = [],[]
    for position,entry in enumerate((n for n in channel if n.tag.rsplit('}',1)[-1].lower() == 'item'),1):
        title = text(entry,'title')
        raw_date = text(entry,'pubdate')
        if source == 'google_news':
            publisher = next((n for n in entry if n.tag.rsplit('}',1)[-1].lower() == 'source'),None)
            row = article(text(entry,'link'),title,
                          snippet=BeautifulSoup(text(entry,'description') or '', 'html.parser').get_text(' ',strip=True) or None,
                          publisher=''.join(publisher.itertext()).strip() if publisher is not None else None,
                          publisher_url=publisher.get('url') if publisher is not None else None,
                          published_at=date(raw_date),date_raw=raw_date,feed_position=position)
            if row:
                row.update(guid=text(entry,'guid'), categories=[text(n,'label') or ''.join(n.itertext()).strip()
                           for n in entry if n.tag.rsplit('}',1)[-1].lower()=='category'],
                           requested_language=locale.split(':',1)[1] if ':' in locale else None,
                           collection_method='google_news_rss')
                row['field_status'].update(field_status({k:row[k] for k in ('guid','categories','requested_language','collection_method')}))
                news.append(row)
        else:
            articles = []
            for child in entry:
                if child.tag.rsplit('}',1)[-1].lower() != 'news_item':
                    continue
                row = article(text(child,'news_item_url'),text(child,'news_item_title'),
                              snippet=text(child,'news_item_snippet'),publisher=text(child,'news_item_source'),
                              thumbnail=text(child,'news_item_picture'),
                              published_at=date(text(child,'news_item_pubdate')),
                              date_raw=text(child,'news_item_pubdate'))
                if row:
                    articles.append(row)
            topic = dict(keyword=title,feed_position=position,
                               approx_traffic=text(entry,'approx_traffic'),
                               published_at=date(raw_date),date_raw=raw_date,
                               picture=text(entry,'picture'),picture_source=text(entry,'picture_source'),
                               articles=articles, geo=locale.split(':',1)[0].upper(),
                               collection_method='google_trending_now_rss',
                               started_at=text(entry,'started'),ended_at=text(entry,'ended'),
                               **traffic_fields(text(entry,'approx_traffic')))
            topic['field_status'] = field_status(topic, ('traffic_value','traffic_is_lower_bound','traffic_unit'))
            topics.append(topic)
    return dict(schema_version=SCHEMA_VERSION,source=source,
                field_schema_version='google-fields-v2',
                search_parameters=dict(engine=source,q=query if source=='google_news' else None,
                                       locale=locale,gl=locale.split(':',1)[0].lower(),
                                       hl=locale.split(':',1)[1] if ':' in locale else None),
                feed_metadata=dict(title=text(channel,'title'),last_build_date=text(channel,'lastbuilddate'),
                                   language=text(channel,'language'),description=text(channel,'description'),
                                   link=text(channel,'link'),copyright=text(channel,'copyright')),
                news_results=news,trending_searches=topics,
                capabilities=dict(news_results='supported' if source=='google_news' else 'not_applicable',
                                  trending_searches='supported' if source=='google_trends' else 'not_applicable',
                                  interest_over_time='not_implemented',related_queries='not_implemented',
                                  publisher_content='disabled'))


def collect(discovery, store, serp, campaign_id: str, source: str, query: str,
            locale: str, ttl: int) -> tuple[list[SearchResult], list[str]]:
    """Persist before caching; shared leases/cache prevent per-campaign Trends storms."""
    key = discovery._cache_key(source,query,locale+':google-feeds-v2')
    cached = serp.cached_feed(key,campaign_id)
    if cached is not None:
        return rows(cached,source), []
    token = store.acquire_query_lease(key)
    if not token:
        return [], []
    try:
        cached = serp.cached_feed(key,campaign_id)
        if cached is not None:
            return rows(cached,source), []
        started = time.monotonic()
        status,error_code = None,None
        response = dict(source=source,news_results=[],trending_searches=[],
                        search_parameters=dict(engine=source,q=query if source=='google_news' else None,locale=locale))
        url = discovery._news_url(query,locale) if source=='google_news' else 'https://trends.google.com/trending/rss?geo='+locale
        try:
            http = discovery._external_get(url,timeout=min(discovery.timeout,30),stream=True,allow_redirects=False)
            try:
                status = http.status_code
                content = discovery._read_limited(http)
                if status != 200:
                    raise ValueError('feed_http_status')
                response = parse_feed(content,source,query,locale)
            finally:
                http.close()
            classification = 'results' if result_items(response) or response['trending_searches'] else 'empty'
        except Exception as exc:
            classification = 'rate_limited' if status==429 else 'http_error' if status and status!=200 else 'parse_error' if status==200 else 'transport_error'
            error_code = type(exc).__name__
        # Storage exceptions must escape; never cache an unpersisted success/error.
        request_id = serp.record(campaign_id,source=source,provider='google_rss',query=query,page=1,
                                 language=discovery.language,results=[],response=response,
                                 elapsed_seconds=time.monotonic()-started,classification=classification,
                                 http_status=status,error_code=error_code)
        serp.cache_feed(key,request_id,ttl if error_code is None else 120)
        return rows(response,source), [] if error_code is None else [source+': '+error_code]
    finally:
        store.release_query_lease(key,token)


def rows(response: dict, source: str) -> list[SearchResult]:
    return [SearchResult(row['link'],row.get('title') or row['link'],(source,),row)
            for row in result_items(response) if isinstance(row.get('link'),str)]


def collect_sources(discovery,store,serp,campaign_id,queries,sources,config):
    found,errors = {},[]
    targets = []
    if 'google_news' in sources:
        opts = getattr(discovery, 'task_options', {})
        locales = (opts['news_locale'],) if opts.get('news_locale') else config.google_news_locales or ('US:en','SG:zh-Hans')
        if not opts.get('news_locale'):
            locales = tuple(l for l in locales if ('zh' in l.lower())==(discovery.language=='zh')) or locales[:1]
        targets.extend(('google_news',query,locale,config.google_news_base_interval_seconds)
                       for query in queries for locale in locales)
    if 'google_trends' in sources:
        opts = getattr(discovery, 'task_options', {})
        geos = (opts['trends_geo'],) if opts.get('trends_geo') else config.google_trends_geos
        targets.extend(('google_trends','trending-now',geo.upper(),config.google_trends_interval_seconds)
                       for geo in dict.fromkeys(geos) if len(geo)==2 and geo.isalpha())
    for source,query,locale,ttl in targets:
        check = getattr(discovery, 'should_continue', None)
        if check and not check():
            break
        results,failures = collect(discovery,store,serp,campaign_id,source,query,locale,ttl)
        errors.extend(failures)
        for row in results:
            found.setdefault(row.url,row)
    return list(found.values()),errors
