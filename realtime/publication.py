"""Explicit publication-time extraction shared by production and experiments."""
from __future__ import annotations

import json
import time
from datetime import datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, SoupStrainer


def publication_metadata(raw: bytes, *, parser: str = 'html.parser') -> tuple[str | None, str | None]:
    """Only explicit publication fields with timezone; never infer from crawl time."""
    soup = BeautifulSoup(raw, parser, parse_only=SoupStrainer(['meta', 'time', 'script']))
    candidates = []
    for tag in soup.select(
        'meta[property="article:published_time"],meta[property="article:published"],'
        'meta[property="og:article:published_time"],meta[property="og:published_time"],'
        'meta[itemprop="datePublished"],time[itemprop="datePublished"]'
    ):
        candidates.append((tag.get('content') or tag.get('datetime'), 'html:datePublished'))
    for tag in soup.select('meta[name],meta[property],time[pubdate][datetime]'):
        field = (tag.get('name') or tag.get('property') or '').casefold()
        if field in {'pubdate', 'publishdate', 'publish_date', 'publication_date', 'datepublished',
                     'dc.date.issued', 'dcterms.issued', 'parsely-pub-date', 'sailthru.date',
                     'og:article:published_time', 'og:published_time'}:
            candidates.append((tag.get('content'), 'html:' + field))
        elif tag.name == 'time':
            candidates.append((tag.get('datetime'), 'html:time.pubdate'))
    def walk(value):
        if isinstance(value, dict):
            kind = value.get('@type', '')
            if any(t in str(kind) for t in ('Article','BlogPosting','NewsArticle','ScholarlyArticle')):
                candidates.append((value.get('datePublished'), 'jsonld:datePublished'))
            elif 'VideoObject' in str(kind):
                candidates.append((value.get('uploadDate'), 'jsonld:uploadDate'))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            # Accept literal control characters in otherwise valid JSON-LD;
            # publication values still pass the explicit timezone check below.
            walk(json.loads(tag.string or tag.get_text() or '', strict=False))
        except (ValueError, RecursionError):
            continue
    soup.decompose()
    for value, source in candidates:
        try:
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
            except ValueError:
                parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is not None and parsed.timestamp() <= time.time()+86400:
                return parsed.isoformat(), source
        except (ValueError, OverflowError, TypeError):
            continue
    return None, None
