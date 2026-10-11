"""One persistent patchright browser context bound to a single (site, account) profile.

Rules carried over from linkedin-mcp-server (see its AGENTS.md), deliberately kept:
  * never override the user agent or inject headers - an override changes the UA string but not
    the client hints, and is the easiest way to look like a bot;
  * a headed window reports its real size (no_viewport); only headless needs an explicit viewport;
  * closing the browser is shielded from cancellation so a tool timeout cannot leave Chromium
    running against a profile that is then handed to someone else.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from patchright.async_api import BrowserContext, Page, Playwright, async_playwright

logger = logging.getLogger("webmcp.browser")

_HEADLESS_VIEWPORT = {"width": 1366, "height": 900}


class BrowserSession:
    def __init__(
        self, profile_dir: Path, *, headless: bool, channel: str | None, offscreen: bool = False
    ):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.channel = channel
        self.offscreen = offscreen and not headless  # headed but parked off-screen
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> Page:
        assert self._page is not None, "browser not started"
        return self._page

    def _options(self, channel: str | None) -> dict:
        opts: dict = {"user_data_dir": str(self.profile_dir), "headless": self.headless}
        if channel:
            opts["channel"] = channel
        if self.headless:
            opts["viewport"] = _HEADLESS_VIEWPORT
        else:
            opts["no_viewport"] = True
            if self.offscreen:
                opts["args"] = ["--window-position=-32000,-32000"]
        return opts

    async def start(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        try:
            try:
                self._context = await self._pw.chromium.launch_persistent_context(
                    **self._options(self.channel)
                )
            except Exception as exc:
                if not self.channel:
                    raise
                # Real Chrome not installed: fall back to patchright's bundled Chromium.
                logger.warning("channel %r unavailable (%s); using bundled chromium", self.channel, exc)
                self._context = await self._pw.chromium.launch_persistent_context(
                    **self._options(None)
                )
            self._context.set_default_timeout(15_000)
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        async def _close() -> None:
            try:
                if self._context is not None:
                    await self._context.close()
            except Exception:
                logger.debug("context close failed", exc_info=True)
            try:
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:
                logger.debug("playwright stop failed", exc_info=True)
            self._context = self._page = self._pw = None

        task = asyncio.ensure_future(_close())
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
                if task.done():
                    break
        if cancelled:
            raise asyncio.CancelledError
