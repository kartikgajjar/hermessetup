"""Site adapter contract. A new site = one subclass in sites/<name>.py exposing `ADAPTER`.

Auth detection follows the LinkedIn-MCP rules: decide from the URL first, then from structure
(attributes / links), and only last from visible text, because text changes with locale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from patchright.async_api import Page

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from ..pool import Service


class SiteAdapter:
    name: str = ""
    hosts: tuple[str, ...] = ()       # allowed hosts (and subdomains) for this site
    login_url: str = ""               # where --login opens
    home_url: str = ""                # a logged-in-only landing page, used by --status --check
    auth_url_markers: tuple[str, ...] = ()  # URL fragments that mean "not logged in / timed out"
    login_timeout: int = 1800         # seconds to wait for a manual login
    # innerText of this selector is what read_page returns.
    main_selector: str = "main"
    # Some sites (Cloudflare-fronted ones) reject headless Chrome; those set headless = False and
    # run a real window positioned off-screen instead.
    headless: bool = True
    min_chars: int = 200              # shorter innerText than this is treated as a blocked/empty page

    def url_needs_login(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(m in path for m in self.auth_url_markers)

    async def is_logged_in(self, page: Page) -> bool:  # pragma: no cover - per site
        """Called on a page already loaded with `home_url` (or a tool page)."""
        raise NotImplementedError

    def register(self, mcp: "FastMCP", svc: "Service", enabled) -> None:  # pragma: no cover
        """Declare tools. Wrap each in `if enabled('<tool_name>'):` so config can allowlist."""
        raise NotImplementedError
