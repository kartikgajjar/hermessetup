"""Substack (substack.com and publication subdomains): read posts (paid ones too, with your
subscription), the Saved list, and the inbox of recent posts from subscriptions.

CALIBRATE on first live login (`webmcp --probe substack --account <a> --url <page>`): the
logged-in heuristic, the inbox/saved URLs and the post-link shape are written from general
knowledge of the site, not verified against a logged-in session. Read-only: no like/subscribe/
comment tools. Publications on custom domains are outside the allowed hosts by design.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote_plus, urlparse

from patchright.async_api import Page
from pydantic import Field

from .. import extract
from ..errors import WebmcpError
from .base import SiteAdapter

# <pub>.substack.com/p/<slug>  or  substack.com/@user/p-<id>  or  substack.com/home/post/p-<id>
_POST_HREF = r"substack\.com/(p/|home/post/p-|@[\w.-]+/p-)[\w-]+"
_SAVED = "https://substack.com/inbox/saved"
_INBOX = "https://substack.com/inbox"

Account = Annotated[str | None, Field(description="Account label; optional if only one is logged in")]


def _check_post_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebmcpError(f"not a Substack post URL: {url!r}")
    return parsed._replace(fragment="").geturl()


class Substack(SiteAdapter):
    name = "substack"
    hosts = ("substack.com",)  # subdomains (<pub>.substack.com) are allowed too
    login_url = "https://substack.com/sign-in"
    home_url = _INBOX
    auth_url_markers = ("/sign-in", "/signin", "/login")
    main_selector = "body"  # post pages and the inbox have no stable <main>
    login_confirm = True  # email-code / Google sign-in is multi-step
    min_chars = 300

    async def is_logged_in(self, page: Page) -> bool:
        if self.url_needs_login(page.url):
            return False
        return await page.locator('a[href*="/sign-in"], a[href*="/login"]').count() == 0

    def register(self, mcp, svc, enabled) -> None:
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

        if enabled("substack_post"):
            @mcp.tool(name="substack_post", annotations=ro)
            async def substack_post(
                url: Annotated[str, Field(description="Substack post URL (*.substack.com/p/...)")],
                account: Account = None,
            ) -> dict:
                """Full text of one Substack post (paid posts included when subscribed)."""
                return await svc.read("substack", account, _check_post_url(url), scrolls=6)

        if enabled("substack_saved"):
            @mcp.tool(name="substack_saved", annotations=ro)
            async def substack_saved(
                account: Account = None,
                limit: Annotated[int, Field(ge=1, le=50)] = 20,
                offset: Annotated[int, Field(ge=0)] = 0,
            ) -> dict:
                """Posts in the Saved list (url + card text), paged."""
                return await _links(svc, account, _SAVED, limit, offset)

        if enabled("substack_inbox"):
            @mcp.tool(name="substack_inbox", annotations=ro)
            async def substack_inbox(
                account: Account = None,
                limit: Annotated[int, Field(ge=1, le=50)] = 20,
                offset: Annotated[int, Field(ge=0)] = 0,
            ) -> dict:
                """Recent posts from the publications this account subscribes to, paged."""
                return await _links(svc, account, _INBOX, limit, offset)

        if enabled("substack_search"):
            @mcp.tool(name="substack_search", annotations=ro)
            async def substack_search(
                query: Annotated[str, Field(min_length=1, max_length=200)],
                account: Account = None,
                limit: Annotated[int, Field(ge=1, le=50)] = 20,
                offset: Annotated[int, Field(ge=0)] = 0,
            ) -> dict:
                """Search Substack for posts and publications; returns url + card text, paged."""
                url = f"https://substack.com/search/{quote_plus(query.strip())}"
                return await _links(svc, account, url, limit, offset)


async def _links(svc, account: str | None, url: str, limit: int, offset: int) -> dict:
    async def work(page: Page) -> dict:
        links = await extract.collect_links(
            page, url, _POST_HREF, limit=(offset + limit) * 2 + 2, max_scrolls=15, main_selector="body"
        )
        items, seen = [], set()
        for item in links:
            key = item["href"].split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            items.append({"url": key, "text": item["text"][:600]})
        window = items[offset : offset + limit]
        has_more = len(items) > offset + limit
        return {
            "offset": offset,
            "count": len(window),
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
            "items": window,
        }

    return await svc.run("substack", account, work)


ADAPTER = Substack()
