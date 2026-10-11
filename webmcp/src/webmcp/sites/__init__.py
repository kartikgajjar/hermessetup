"""Auto-discovery: every sites/<name>.py exposing ADAPTER is a site."""

from __future__ import annotations

import importlib
import pkgutil

from ..errors import UnknownSite
from .base import SiteAdapter


def discover() -> dict[str, SiteAdapter]:
    found: dict[str, SiteAdapter] = {}
    for mod in pkgutil.iter_modules(__path__):
        if mod.name == "base":
            continue
        adapter = getattr(importlib.import_module(f"{__name__}.{mod.name}"), "ADAPTER", None)
        if isinstance(adapter, SiteAdapter):
            found[adapter.name] = adapter
    return found


def get(name: str) -> SiteAdapter:
    sites = discover()
    if name not in sites:
        raise UnknownSite(f"Unknown site {name!r}. Available: {sorted(sites)}")
    return sites[name]
