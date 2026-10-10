from __future__ import annotations

from typing import Any
import asyncio
import inspect

from twisted.internet.defer import Deferred
from scrapy.utils.defer import maybe_deferred_to_future


def abort_browser_resource(request: Any) -> bool:
    return request.resource_type in {"image", "media", "font"} or any(
        marker in request.url.lower()
        for marker in ("doubleclick", "googletagmanager", "google-analytics", "/ads/")
    )


class HybridDownloadHandler:
    """Keep curl-cffi on the hot path and invoke Playwright only when requested."""

    # The nested Playwright handler subscribes to engine_started during init.
    # Construct it before that signal, even if the first request uses curl.
    lazy = False

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

    async def close(self):  # type: ignore[no-untyped-def]
        handlers = [self.curl, self.playwright]

        async def close_handler(handler):  # type: ignore[no-untyped-def]
            result = handler.close()
            if isinstance(result, Deferred):
                await maybe_deferred_to_future(result)
            elif inspect.isawaitable(result):
                await result

        await asyncio.gather(*(
            close_handler(handler) for handler in handlers
            if handler is not None and hasattr(handler, "close")
        ))
