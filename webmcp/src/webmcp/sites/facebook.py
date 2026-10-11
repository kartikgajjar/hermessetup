"""Facebook: your SAVED REELS only (not the feed, not other saved items), read-only.

Facebook is the most bot-hostile site here and its markup is obfuscated and changes often, so this
adapter relies only on URL shapes (`/reel/<id>` links) and the `c_user` login cookie, never on class
names. CALIBRATE after first login: `webmcp --probe facebook --account <a> --url https://www.facebook.com/saved/`.
If saved reels show up under a different URL shape (e.g. /watch/?v=), adjust `_REEL_HREF`.

Meta's terms forbid automated collection; an account can get a checkpoint or be restricted. Keep volume
low (a handful of scrolls, occasionally) and use only your own account.
"""

from __future__ import annotations

import re
from typing import Annotated

from patchright.async_api import Page
from pydantic import Field

from .. import extract
from ..errors import RateLimited, WebmcpError
from .base import SiteAdapter

Account = Annotated[str | None, Field(description="Account label; optional if only one is logged in")]

_SAVED_URL = "https://www.facebook.com/saved/"
_REEL_HREF = r"facebook\.com/reel/\d+"
_REEL_URL = re.compile(r"^https://(www\.|m\.)?facebook\.com/reel/(\d+)(/|\?|$)")


_SEE_MORE = re.compile(r"see more", re.I)


async def _expand_caption(page: Page) -> None:
    """Click the caption's "... See more" so the full text is in the DOM.

    The button renders after domcontentloaded, so poll for it (up to ~5 s) instead of checking once;
    a single immediate check races the page and silently leaves the caption truncated.
    """
    for _ in range(10):
        try:
            target = page.get_by_role("button", name=_SEE_MORE)
            if await target.count() == 0:
                target = page.get_by_text(re.compile(r"^\W*see more$", re.I))
            if await target.count():
                await target.first.click(timeout=3000)
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass
        await page.wait_for_timeout(500)


class Facebook(SiteAdapter):
    name = "facebook"
    hosts = ("facebook.com",)
    login_url = "https://www.facebook.com/login/"
    home_url = _SAVED_URL
    auth_url_markers = ("/login", "/checkpoint", "/two_step_verification", "/recover")
    main_selector = '[role="main"]'
    min_chars = 20  # a reel page can legitimately be just a short caption

    async def is_logged_in(self, page: Page) -> bool:
        if self.url_needs_login(page.url):
            return False
        # Structural: the session cookie exists and no password field is showing.
        cookies = await page.context.cookies("https://www.facebook.com")
        if not any(c["name"] == "c_user" for c in cookies):
            return False
        return await page.locator('input[name="pass"]').count() == 0

    def register(self, mcp, svc, enabled) -> None:
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

        if enabled("facebook_saved_reels"):
            @mcp.tool(name="facebook_saved_reels", annotations=ro)
            async def facebook_saved_reels(
                account: Account = None,
                limit: Annotated[int, Field(ge=1, le=100, description="Max reels to return")] = 20,
                max_scrolls: Annotated[int, Field(ge=0, le=30)] = 10,
            ) -> dict:
                """List reels you saved: id, URL and the card text (caption, author, collection), in Facebook's order."""
                async def work(page: Page) -> dict:
                    links = await extract.collect_links(
                        page, _SAVED_URL, _REEL_HREF, limit=limit, max_scrolls=max_scrolls,
                        main_selector=self.main_selector,
                    )
                    reels, ids = [], set()
                    for item in links:
                        m = _REEL_URL.match(item["href"])
                        if m and m.group(2) not in ids:
                            ids.add(m.group(2))
                            reels.append({"id": m.group(2), "url": f"https://www.facebook.com/reel/{m.group(2)}",
                                          "card_text": item["text"][:4000]})
                    return {"count": len(reels), "reels": reels}

                return await svc.run("facebook", account, work)

        if enabled("facebook_reel"):
            @mcp.tool(name="facebook_reel", annotations=ro)
            async def facebook_reel(
                url: Annotated[str, Field(description="A facebook.com/reel/<id> URL, e.g. from facebook_saved_reels")],
                account: Account = None,
            ) -> dict:
                """Caption, author and visible text of one reel page."""
                if not _REEL_URL.match(url):
                    raise WebmcpError("url must look like https://www.facebook.com/reel/<digits>")

                async def work(page: Page) -> dict:
                    await extract.goto(page, url, wait_selector=self.main_selector)
                    await _expand_caption(page)
                    text = await extract.body_text(page, self.main_selector)
                    if len(text) < self.min_chars:
                        raise RateLimited(f"{page.url} returned only {len(text)} characters")
                    return {"url": page.url, "title": await page.title(), "text": text}

                svc_adapter_hosts = self.hosts
                extract.require_host(url, svc_adapter_hosts)
                return await svc.run("facebook", account, work)


ADAPTER = Facebook()
