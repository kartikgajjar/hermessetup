"""TipRanks (tipranks.com): analyst ratings, price targets, smart score.

CALIBRATE on first live login (run `webmcp --probe tipranks --account <a> --url <page>`): the
logged-in heuristic and the URL paths below are written from general knowledge of the site, not
verified against a logged-in session.
"""

from __future__ import annotations

import re
from typing import Annotated

from patchright.async_api import Page
from pydantic import Field

from ..errors import WebmcpError
from .base import SiteAdapter

_TICKER = re.compile(r"^[A-Za-z]{1,5}([.-][A-Za-z])?$")

Ticker = Annotated[str, Field(description="Stock ticker, e.g. AAPL")]
Account = Annotated[str | None, Field(description="Account label; optional if only one is logged in")]


def _ticker(value: str) -> str:
    value = value.strip().upper()
    if not _TICKER.match(value):
        raise WebmcpError(f"not a ticker symbol: {value!r}")
    return value


class TipRanks(SiteAdapter):
    name = "tipranks"
    hosts = ("tipranks.com",)
    login_url = "https://www.tipranks.com/sign-in"
    home_url = "https://www.tipranks.com/"
    auth_url_markers = ("/sign-in", "/login", "/sign-up")
    headless = False  # Cloudflare serves "Just a moment..." to headless Chrome indefinitely

    async def is_logged_in(self, page: Page) -> bool:
        # Structural, locale-independent: a logged-out page links to the sign-in route.
        if self.url_needs_login(page.url):
            return False
        return await page.locator('a[href*="/sign-in"], a[href*="/login"]').count() == 0

    def register(self, mcp, svc, enabled) -> None:
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

        if enabled("tipranks_stock"):
            @mcp.tool(name="tipranks_stock", annotations=ro)
            async def tipranks_stock(ticker: Ticker, account: Account = None) -> dict:
                """TipRanks overview page text for a ticker (smart score, consensus, news)."""
                t = _ticker(ticker)
                return await svc.read("tipranks", account, f"https://www.tipranks.com/stocks/{t.lower()}")

        if enabled("tipranks_forecast"):
            @mcp.tool(name="tipranks_forecast", annotations=ro)
            async def tipranks_forecast(ticker: Ticker, account: Account = None) -> dict:
                """Analyst ratings and price-target forecast page text for a ticker."""
                t = _ticker(ticker)
                return await svc.read(
                    "tipranks", account, f"https://www.tipranks.com/stocks/{t.lower()}/forecast", scrolls=5
                )


ADAPTER = TipRanks()
