"""Medium (medium.com): read articles (member-only ones too, with your login), the reading list, search.

CALIBRATE on first live login (`webmcp --probe medium --account <a> --url <page>`): the logged-in
heuristic, the reading-list URL and the article-link shape below are written from general knowledge
of the site, not verified against a logged-in session. Read-only: no clap/follow/publish tools.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote_plus, urlparse

from patchright.async_api import Page
from pydantic import Field

from .. import extract
from ..errors import WebmcpError
from .base import SiteAdapter

# /@author/some-title-0123456789ab  or  /publication/some-title-0123456789ab
_ARTICLE_HREF = r"medium\.com/(@[\w.-]+|[\w.-]+)/[\w%-]*-[0-9a-f]{10,12}(\?|#|$)"
_READING_LIST = "https://medium.com/me/list/reading-list"

Account = Annotated[str | None, Field(description="Account label; optional if only one is logged in")]


def _check_article_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebmcpError(f"not a Medium article URL: {url!r}")
    return parsed._replace(fragment="").geturl()


class Medium(SiteAdapter):
    name = "medium"
    hosts = ("medium.com",)
    login_url = "https://medium.com/m/signin"
    home_url = "https://medium.com/"
    auth_url_markers = ("/m/signin", "/signin", "/m/connect")
    main_selector = "body"  # article pages have no stable <main>
    headless = False  # Medium sits behind Cloudflare; headed-off-screen like TipRanks
    login_confirm = True  # Google / email-link sign-in is multi-step
    min_chars = 300

    async def is_logged_in(self, page: Page) -> bool:
        if self.url_needs_login(page.url):
            return False
        # Structural: a logged-out page links to the sign-in route.
        return await page.locator('a[href*="/m/signin"], a[href*="/signin"]').count() == 0

    def register(self, mcp, svc, enabled) -> None:
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

        if enabled("medium_article"):
            @mcp.tool(name="medium_article", annotations=ro)
            async def medium_article(
                url: Annotated[str, Field(description="medium.com article URL")],
                account: Account = None,
            ) -> dict:
                """Full text of one Medium article (member-only articles included when the account
                is a member)."""
                return await svc.read("medium", account, _check_article_url(url), scrolls=6)

        if enabled("medium_reading_list"):
            @mcp.tool(name="medium_reading_list", annotations=ro)
            async def medium_reading_list(
                account: Account = None,
                limit: Annotated[int, Field(ge=1, le=50)] = 20,
                offset: Annotated[int, Field(ge=0)] = 0,
            ) -> dict:
                """Articles saved to the reading list (url + card text), paged."""
                return await _links(svc, account, _READING_LIST, limit, offset)

        if enabled("medium_search"):
            @mcp.tool(name="medium_search", annotations=ro)
            async def medium_search(
                query: Annotated[str, Field(min_length=1, max_length=200)],
                account: Account = None,
                limit: Annotated[int, Field(ge=1, le=50)] = 20,
                offset: Annotated[int, Field(ge=0)] = 0,
            ) -> dict:
                """Search Medium articles; returns url + card text, paged."""
                return await _links(svc, account, f"https://medium.com/search?q={quote_plus(query.strip())}", limit, offset)


async def _links(svc, account: str | None, url: str, limit: int, offset: int) -> dict:
    async def work(page: Page) -> dict:
        links = await extract.collect_links(
            page, url, _ARTICLE_HREF, limit=(offset + limit) * 2 + 2, max_scrolls=15, main_selector="body"
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

    return await svc.run("medium", account, work)


ADAPTER = Medium()
