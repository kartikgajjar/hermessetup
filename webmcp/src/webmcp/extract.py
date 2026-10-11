"""Generic page capture: navigate by URL, scroll, read innerText (never class-name selectors)."""

from __future__ import annotations

import asyncio
import random
from urllib.parse import urlparse

from patchright.async_api import Page
from patchright.async_api import TimeoutError as PlaywrightTimeout

from .errors import BotChallenge, RateLimited, SiteBlocked

_MIN_BODY_CHARS = 200  # an error-shaped page carries only site chrome


def host_allowed(url: str, hosts: tuple[str, ...]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def require_host(url: str, hosts: tuple[str, ...]) -> None:
    if urlparse(url).scheme not in ("http", "https") or not host_allowed(url, hosts):
        raise SiteBlocked(f"{url!r} is outside this site's allowed hosts {list(hosts)}")


async def _challenge_active(page: Page) -> bool:
    try:
        title = (await page.title()).lower()
        return "just a moment" in title or "attention required" in title
    except Exception:
        return False


async def clear_challenge(page: Page, patience: float = 20.0) -> None:
    """Give a bot check time to resolve itself; raise BotChallenge if it does not."""
    waited = 0.0
    while await _challenge_active(page):
        if waited >= patience:
            raise BotChallenge(
                f"{page.url} is stuck on a bot check ('Just a moment...'). Not a logout - retry "
                "later; headless browsers are usually refused, so the site must run a headed browser."
            )
        await asyncio.sleep(1)
        waited += 1


async def goto(page: Page, url: str, *, wait_selector: str = "body") -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await clear_challenge(page)
    try:
        await page.wait_for_selector(wait_selector, timeout=10_000)
    except PlaywrightTimeout:
        pass  # judged by the body-length check below, not here


async def scroll(page: Page, times: int = 3) -> None:
    for _ in range(times):
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(0.5, 1.0))


async def body_text(page: Page, selector: str = "main") -> str:
    """innerText of `selector`, falling back to the whole body."""
    js = (
        "(sel) => (document.querySelector(sel) || document.body)?.innerText || ''"
    )
    return (await page.evaluate(js, selector)).strip()


async def read_page(
    page: Page, url: str, *, main_selector: str = "main", scrolls: int = 3, min_chars: int = _MIN_BODY_CHARS
) -> dict:
    await goto(page, url, wait_selector=main_selector)
    await scroll(page, scrolls)
    text = await body_text(page, main_selector)
    if len(text) < min_chars:
        raise RateLimited(
            f"{page.url} returned only {len(text)} characters - likely rate-limited, blocked or "
            "not rendered. Wait a few minutes before retrying."
        )
    return {"url": page.url, "title": await page.title(), "text": text}


async def collect_links(
    page: Page,
    url: str,
    href_regex: str,
    *,
    limit: int = 20,
    max_scrolls: int = 15,
    main_selector: str = "main",
    accept=None,
) -> list[dict]:
    """Open `url`, scroll an infinite list, return unique anchors whose href matches `href_regex`.

    Stops as soon as `limit` links are found or scrolling stops yielding new ones. Returns
    [{"href", "text"}] in page order; `text` is the surrounding card's innerText (caption, author, ...).
    """
    await goto(page, url, wait_selector=main_selector)
    # `text` = innerText of the largest ancestor that still contains only this one matching link,
    # i.e. the list card (caption, author...), found structurally rather than by class name.
    js = """(re) => {
        const rx = new RegExp(re);
        const base = h => h.split('#')[0].split('?')[0].replace(/[/]$/, '');
        const links = [...document.querySelectorAll('a[href]')].filter(a => rx.test(a.href));
        return links.map(a => {
            const me = base(a.href);
            let node = a;
            while (node.parentElement) {
                const up = node.parentElement;
                const others = [...up.querySelectorAll('a[href]')]
                    .filter(x => rx.test(x.href) && base(x.href) !== me);
                if (others.length) break;
                node = up;
            }
            return {href: a.href.split('#')[0], text: (node.innerText || a.innerText || '').trim()};
        });
    }"""
    seen: dict[str, dict] = {}
    stale = 0
    for _ in range(max_scrolls + 1):
        before = len(seen)
        for item in await page.evaluate(js, href_regex):
            if accept is None or accept(item):  # e.g. keep only cards in no collection
                seen.setdefault(item["href"], item)
        if len(seen) >= limit:
            break
        stale = stale + 1 if len(seen) == before else 0
        if stale >= 2:
            break
        await scroll(page, 1)
    return list(seen.values())[:limit]
