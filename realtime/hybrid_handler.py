from __future__ import annotations

from typing import Any
import inspect

from twisted.internet.defer import Deferred, DeferredList, maybeDeferred
from scrapy.utils.defer import maybe_deferred_to_future


def abort_browser_resource(request: Any) -> bool:
    return request.resource_type in {"image", "media", "font"} or any(
        marker in request.url.lower()
        for marker in ("doubleclick", "googletagmanager", "google-analytics", "/ads/")
    )


class HybridDownloadHandler:
    """Keep curl-cffi on the hot path and invoke Playwright only when requested."""

    lazy = True

    def __init__(self, crawler: Any):
        from scrapy.utils.misc import build_from_crawler
        from scrapy_curl_cffi.handlers import CurlCffiDownloadHandler

        self.curl = build_from_crawler(CurlCffiDownloadHandler, crawler)
        self.playwright = None
        if crawler.settings.getbool("BROWSER_FALLBACK_ENABLED"):
            from scrapy_playwright.handler import ScrapyPlaywrightDownloadHandler

            self.playwright = build_from_crawler(ScrapyPlaywrightDownloadHandler, crawler)

    @classmethod
    def from_crawler(cls, crawler: Any) -> "HybridDownloadHandler":
        return cls(crawler)

    async def download_request(self, request: Any):  # type: ignore[no-untyped-def]
        if request.meta.get("playwright") and self.playwright is not None:
            result = self.playwright.download_request(request)
        else:
            result = self.curl.download_request(request)
        if isinstance(result, Deferred):
            return await maybe_deferred_to_future(result)
        if inspect.isawaitable(result):
            return await result
        return result

    def close(self):  # type: ignore[no-untyped-def]
        handlers = [self.curl, self.playwright]
        return DeferredList([
            maybeDeferred(handler.close) for handler in handlers
            if handler is not None and hasattr(handler, "close")
        ])
