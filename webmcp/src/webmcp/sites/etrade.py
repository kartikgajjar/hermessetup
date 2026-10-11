"""E*Trade (us.etrade.com) - READ-ONLY by construction: no order/transfer tools exist in this module.

CALIBRATE on first live login (`webmcp --probe etrade --account <a> --url <page>`): URLs and the
logged-in heuristic are not verified against a real session. E*Trade also offers an official OAuth
API - prefer it for balances/positions once you want anything beyond page text.

E*Trade logs sessions out after short idle periods, so expect frequent "re-login needed" results;
that is reported cleanly rather than retried.
"""

from __future__ import annotations

from typing import Annotated

from patchright.async_api import Page
from pydantic import Field

from ..errors import WebmcpError
from .base import SiteAdapter

Account = Annotated[str | None, Field(description="Account label; optional if only one is logged in")]

# Hard allowlist of read-only pages; `etrade_page` can only visit these keys.
_PAGES = {
    "accounts": "https://us.etrade.com/etx/pxy/accounts-overview",
    "portfolio": "https://us.etrade.com/etx/pxy/portfolios",
    "orders": "https://us.etrade.com/etx/pxy/orders",
    "balances": "https://us.etrade.com/etx/pxy/balances",
}


class ETrade(SiteAdapter):
    name = "etrade"
    hosts = ("etrade.com",)
    login_url = "https://us.etrade.com/e/t/user/login"
    home_url = _PAGES["accounts"]
    auth_url_markers = ("/user/login", "/user/logoff", "/user/timeout", "/login")
    main_selector = "body"

    async def is_logged_in(self, page: Page) -> bool:
        if self.url_needs_login(page.url):
            return False
        # A password field means we are looking at a sign-in form.
        return await page.locator('input[type="password"]').count() == 0

    def register(self, mcp, svc, enabled) -> None:
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

        if enabled("etrade_accounts"):
            @mcp.tool(name="etrade_accounts", annotations=ro)
            async def etrade_accounts(account: Account = None) -> dict:
                """Accounts overview page text (balances per brokerage account)."""
                return await svc.read("etrade", account, _PAGES["accounts"], scrolls=1)

        if enabled("etrade_page"):
            @mcp.tool(name="etrade_page", annotations=ro)
            async def etrade_page(
                page: Annotated[str, Field(description=f"One of {sorted(_PAGES)}")],
                account: Account = None,
            ) -> dict:
                """Read-only page text for one of a fixed set of E*Trade pages."""
                if page not in _PAGES:
                    raise WebmcpError(f"page must be one of {sorted(_PAGES)}")
                return await svc.read("etrade", account, _PAGES[page], scrolls=2)


ADAPTER = ETrade()
