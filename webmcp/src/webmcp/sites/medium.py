"""Medium (medium.com): read articles (member-only ones too, with your login), the reading list, search.

CALIBRATE on first live login (`webmcp --probe medium --account <a> --url <page>`): the logged-in
heuristic, the reading-list URL and the article-link shape below are written from general knowledge
of the site, not verified against a logged-in session. Read-only: no clap/follow/publish tools.
"""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import quote_plus, urlparse

from patchright.async_api import Page
from pydantic import Field

from .. import extract
from ..errors import WebmcpError
from .base import SiteAdapter

# /@author/some-title-0123456789ab  or  /publication/some-title-0123456789ab
_ARTICLE_HREF = r"medium\.com/(@[\w.-]+|[\w.-]+)/[\w%-]*-[0-9a-f]{10,12}(\?|#|$)"
_LIBRARY = "https://medium.com/me/lists"
_NOT_FOUND = re.compile(r"^\s*page not found\s*$", re.I | re.M)

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
    main_selector = "article"  # falls back to <body> when a page has no <article>
    headless = False  # Cloudflare bot-checks headless Chrome (confirmed): hidden headed window instead
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

        if enabled("medium_lists"):
            @mcp.tool(name="medium_lists", annotations=ro)
            async def medium_lists(account: Account = None) -> dict:
                """Your Medium library: the Reading list plus your custom lists (name + url)."""
                async def work(page: Page) -> dict:
                    return {"lists": await _library(page)}

                return await svc.run("medium", account, work)

        if enabled("medium_reading_list"):
            @mcp.tool(name="medium_reading_list", annotations=ro)
            async def medium_reading_list(
                account: Account = None,
                list_url: Annotated[
                    str | None,
                    Field(description="A list URL from medium_lists; omit for the default Reading list"),
                ] = None,
                limit: Annotated[int, Field(ge=1, le=50)] = 20,
                offset: Annotated[int, Field(ge=0)] = 0,
            ) -> dict:
                """Articles in the Reading list (default) or in another list from medium_lists
                (url + card text), paged."""
                async def resolve(page: Page) -> str:
                    if list_url:
                        return _check_article_url(list_url)
                    lists = await _library(page)
                    first = next((x for x in lists if x["default"]), None)
                    if not first:
                        raise WebmcpError("Could not find the Reading list in the Medium library")
                    return first["url"]

                return await _links(svc, account, resolve, limit, offset)

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


async def _library(page: Page) -> list[dict]:
    """[{name, url, default}] from /me/lists. The Reading list is the entry whose link carries
    Medium's own READING_LIST marker; its URL is /@<you>/list/reading-list."""
    await extract.goto(page, _LIBRARY, wait_selector="body")
    for _ in range(16):  # the lists render a few seconds after the page itself
        if await page.locator('a[href*="/list/"]:not([href*="reading-list"])').count():
            break
        await page.wait_for_timeout(500)
    await extract.scroll(page, 2)
    rows = await page.evaluate(
        """() => [...document.querySelectorAll('a[href]')].map(a => ({
              href: a.href.split('?')[0],
              marker: a.href.includes('READING_LIST'),
              name: (a.innerText || '').trim().split(String.fromCharCode(10))[0].trim()
        }))"""
    )
    out, seen = [], set()
    for row in rows:
        if row["marker"]:
            owner = urlparse(row["href"]).path.strip("/").split("/")[0]  # "@you"
            if not owner.startswith("@"):
                continue
            url, name, default = f"https://medium.com/{owner}/list/reading-list", "Reading list", True
        elif "/@" in row["href"] and "/list/" in row["href"]:
            slug = re.sub(r"-[0-9a-f]{10,12}$", "", row["href"].rstrip("/").rsplit("/", 1)[-1])
            url, default = row["href"], False
            name = row["name"] or slug.replace("-", " ")  # cover-image links carry no text
        else:
            continue
        if url not in seen:
            seen.add(url)
            out.append({"name": name, "url": url, "default": default})
    return out


async def _links(svc, account: str | None, target, limit: int, offset: int) -> dict:
    """`target` is a URL, or an async callable (page -> URL) for lists that need looking up first."""
    async def work(page: Page) -> dict:
        url = target if isinstance(target, str) else await target(page)
        links = await extract.collect_links(
            page, url, _ARTICLE_HREF, limit=(offset + limit) * 2 + 2, max_scrolls=15, main_selector="body"
        )
        if _NOT_FOUND.search(await extract.body_text(page, "body")):
            # Medium's 404 page lists suggested stories, which would pass as results
            raise WebmcpError(f"Medium says page not found: {url}")
        items, seen = [], set()
        for item in links:
            key = item["href"].split("?")[0]
            if key in seen or item["text"].startswith("Sidebar menu"):  # sidebar/nav links, not results
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
