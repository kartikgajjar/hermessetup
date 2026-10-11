"""Browser pool keyed by (site, account) + the `Service` every tool goes through.

* one live browser per key, a per-key lock (same account = serialised),
* a global semaphore caps concurrent browsers (RAM/VRAM-constrained machine),
* idle browsers are closed after `idle_seconds`,
* the profile lock is held for the browser's whole life, so `--login` cannot collide with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from patchright.async_api import Page

from . import config, extract, profiles, sites
from .browser import BrowserSession
from .errors import AuthRequired

logger = logging.getLogger("webmcp.pool")

Key = tuple[str, str]


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session: BrowserSession | None = None
    release: contextlib.ExitStack | None = None
    last_used: float = 0.0


class Service:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.channel = config.channel(cfg)
        self.idle_seconds = float(cfg.get("idle_seconds", 600))
        self._sem = asyncio.Semaphore(int(cfg.get("max_browsers", 2)))
        self._entries: dict[Key, _Entry] = {}
        self._reaper: asyncio.Task | None = None

    # ---- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        for key in list(self._entries):
            await self._close(key)

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            for key, e in list(self._entries.items()):
                if e.session and not e.lock.locked() and now - e.last_used > self.idle_seconds:
                    async with e.lock:
                        await self._close(key)

    async def _close(self, key: Key) -> None:
        e = self._entries.get(key)
        if not e or not e.session:
            return
        session, release = e.session, e.release
        e.session = e.release = None
        try:
            await session.close()
        finally:
            if release:
                release.close()
            self._sem.release()

    # ---- main entry point for tools ----------------------------------------------------------
    async def run(self, site: str, account: str | None, fn: Callable[[Page], Awaitable[dict]]) -> dict:
        adapter = sites.get(site)
        acct = profiles.resolve_account(site, account)
        key = (site, acct)
        entry = self._entries.setdefault(key, _Entry())
        async with entry.lock:
            if entry.session is None:
                await self._sem.acquire()
                stack = contextlib.ExitStack()
                try:
                    stack.enter_context(profiles.profile_lock(site, acct))
                    session = BrowserSession(
                        config.profile_dir(site, acct), headless=adapter.headless,
                        channel=self.channel, offscreen=True,
                    )
                    await session.start()
                except BaseException:
                    stack.close()
                    self._sem.release()
                    raise
                entry.session, entry.release = session, stack
            try:
                page = entry.session.page
                relogin = AuthRequired(
                    f"{site}/{acct}: session expired or logged out. "
                    f"Run: webmcp --login {site} --account {acct}"
                )
                try:
                    result = await fn(page)
                except Exception:
                    # A login page is short, so it would otherwise surface as "rate limited".
                    if adapter.url_needs_login(page.url):
                        raise relogin
                    raise
                if adapter.url_needs_login(page.url) or not await adapter.is_logged_in(page):
                    raise relogin
                profiles.update_meta(site, acct, last_ok=profiles.utcnow())
                return result
            except BaseException:
                # Hung/odd page state is cheaper to discard than to debug.
                await self._close(key)
                raise
            finally:
                entry.last_used = time.monotonic()

    async def read(self, site: str, account: str | None, url: str, *, scrolls: int = 3) -> dict:
        adapter = sites.get(site)
        extract.require_host(url, adapter.hosts)
        out = await self.run(
            site,
            account,
            lambda page: extract.read_page(
                page, url, main_selector=adapter.main_selector, scrolls=scrolls,
                min_chars=adapter.min_chars,
            ),
        )
        return out
