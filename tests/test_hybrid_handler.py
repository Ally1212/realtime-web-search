import asyncio
import unittest

from realtime.hybrid_handler import HybridDownloadHandler


class HybridHandlerCloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_awaits_asyncio_cleanup_for_both_transports(self):
        closed = []

        class Handler:
            def __init__(self, name):
                self.name = name

            async def close(self):
                # Native asyncio futures cannot be driven by maybeDeferred.
                await asyncio.sleep(0.001)
                closed.append(self.name)

        handler = object.__new__(HybridDownloadHandler)
        handler.curl = Handler('curl')
        handler.playwright = Handler('playwright')
        await handler.close()
        self.assertCountEqual(closed, ['curl', 'playwright'])

    async def test_shutdown_accepts_disabled_browser_and_sync_cleanup(self):
        closed = []

        class Handler:
            def close(self):
                closed.append('curl')

        handler = object.__new__(HybridDownloadHandler)
        handler.curl = Handler()
        handler.playwright = None
        await handler.close()
        self.assertEqual(closed, ['curl'])
