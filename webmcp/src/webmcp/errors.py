"""Typed errors. They subclass ToolError so FastMCP shows the message instead of masking it."""

from fastmcp.exceptions import ToolError


class WebmcpError(ToolError):
    """Base class; the message is safe to show to the model."""


class UnknownSite(WebmcpError):
    pass


class UnknownAccount(WebmcpError):
    pass


class AccountRequired(WebmcpError):
    """Several accounts exist for the site and the caller did not pick one."""


class AuthRequired(WebmcpError):
    """No usable session: never logged in, expired, or hit a login/checkpoint page."""


class RateLimited(WebmcpError):
    """The site served an error-shaped page (no content). Back off before retrying."""


class ProfileBusy(WebmcpError):
    """Another process (usually `--login`) holds this account's browser profile."""


class BotChallenge(WebmcpError):
    """A bot check (Cloudflare "Just a moment...") did not clear. Not a logout: retry later, or
    check the site is configured to run a (hidden) headed browser."""


class SiteBlocked(WebmcpError):
    """URL is outside the site's allowed hosts."""
