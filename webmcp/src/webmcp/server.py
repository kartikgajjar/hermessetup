"""FastMCP server: generic tools + whatever site adapters are enabled."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import config, profiles, sites
from .pool import Service

_RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def build(only_sites: list[str] | None = None) -> FastMCP:
    cfg = config.load_config()
    svc = Service(cfg)

    @asynccontextmanager
    async def lifespan(_server):
        svc.start()
        try:
            yield
        finally:
            await svc.shutdown()

    mcp = FastMCP("webmcp", lifespan=lifespan)
    site_cfg = cfg.get("sites", {})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def webmcp_accounts(
        site: Annotated[str | None, Field(description="Limit to one site")] = None,
    ) -> dict:
        """List sites and the account labels that have a saved login."""
        names = [site] if site else sorted(sites.discover())
        return {n: profiles.list_accounts(n) for n in names}

    loaded = []
    for name, adapter in sorted(sites.discover().items()):
        sc = site_cfg.get(name, {})
        if only_sites is not None and name not in only_sites:
            continue
        if not sc.get("enabled", True):
            continue
        allow = sc.get("tools")
        adapter.register(mcp, svc, lambda tool, _a=allow: _a is None or tool in _a)
        loaded.append(name)

    if loaded:

        @mcp.tool(annotations=_RO)
        async def web_read(
            site: Annotated[str, Field(description=f"One of {loaded}")],
            url: Annotated[str, Field(description="Page URL; must be on the site's own domain")],
            account: Annotated[str | None, Field(description="Account label")] = None,
            scrolls: Annotated[int, Field(ge=0, le=10)] = 3,
        ) -> dict:
            """Read the visible text of any page on a logged-in site (no clicking, no forms)."""
            if site not in loaded:
                from .errors import UnknownSite

                raise UnknownSite(f"Unknown or disabled site {site!r}. Enabled: {loaded}")
            return await svc.read(site, account, url, scrolls=scrolls)

    return mcp
