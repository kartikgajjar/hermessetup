"""Paths and user config.

Layout under the data root (default %USERPROFILE%\\.webmcp, override with WEBMCP_HOME):

    config.json                      optional, see `load_config`
    <site>/<account>/profile/        Chromium user-data-dir (the saved login)
    <site>/<account>/.lock           held while a browser owns the profile
    <site>/<account>/meta.json       last login / last verified timestamps

Sessions are credential-equivalent: keep this tree out of git and out of backups you share.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def data_root() -> Path:
    return Path(os.environ.get("WEBMCP_HOME") or Path.home() / ".webmcp")


def valid_label(label: str) -> str:
    """Account labels become directory names, so they are restricted."""
    if not _LABEL.match(label):
        raise ValueError(
            f"invalid account label {label!r}: use 1-32 letters, digits, '-' or '_'"
        )
    return label


def account_dir(site: str, account: str) -> Path:
    return data_root() / site / valid_label(account)


def profile_dir(site: str, account: str) -> Path:
    return account_dir(site, account) / "profile"


def load_config() -> dict:
    """config.json (all keys optional):

    {
      "max_browsers": 2,
      "idle_seconds": 600,
      "channel": "chrome",                    # "chromium" = patchright's bundled build
      "sites": {"etrade": {"enabled": true, "tools": ["etrade_accounts"]}}
    }

    A site's "tools" list, when present, is an allowlist; absent means every read-only tool.
    """
    path = data_root() / "config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def channel(cfg: dict) -> str | None:
    value = os.environ.get("WEBMCP_CHANNEL") or cfg.get("channel") or "chrome"
    return None if value == "chromium" else value
