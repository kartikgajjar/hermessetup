"""Account discovery, profile ownership lock, and per-account metadata."""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .errors import ProfileBusy, UnknownAccount

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - this repo targets Windows, kept for portability
    import fcntl


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_accounts(site: str) -> list[str]:
    """Accounts with a saved profile directory (logged in at least once)."""
    root = config.data_root() / site
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "profile").is_dir())


def resolve_account(site: str, account: str | None) -> str:
    accounts = list_accounts(site)
    if account:
        if account not in accounts:
            raise UnknownAccount(
                f"No account {account!r} for {site}. Known: {accounts or 'none'}. "
                f"Add one with: webmcp --login {site} --account {account}"
            )
        return account
    if len(accounts) == 1:
        return accounts[0]
    from .errors import AccountRequired, AuthRequired

    if not accounts:
        raise AuthRequired(
            f"No {site} account is logged in. Run: webmcp --login {site} --account <label>"
        )
    raise AccountRequired(f"Several {site} accounts exist; pass account= one of {accounts}")


@contextmanager
def profile_lock(site: str, account: str):
    """Exclusive OS-level lock on the account. Released automatically if the process dies,
    so there is no stale-lock cleanup (and no PID probing, which is unsafe on Windows)."""
    adir = config.account_dir(site, account)
    adir.mkdir(parents=True, exist_ok=True)
    handle = open(adir / ".lock", "a+b")
    try:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ProfileBusy(
                f"{site}/{account} is open in another webmcp process "
                "(a --login window or a second server). Close it and retry."
            ) from exc
        yield
    finally:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def read_meta(site: str, account: str) -> dict:
    path = config.account_dir(site, account) / "meta.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update_meta(site: str, account: str, **fields) -> None:
    meta = read_meta(site, account)
    meta.update(fields)
    path = config.account_dir(site, account) / "meta.json"
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def remove_account(site: str, account: str) -> None:
    adir = config.account_dir(site, account)
    if not adir.is_dir():
        raise UnknownAccount(f"No account {account!r} for {site}.")
    with profile_lock(site, account):
        shutil.rmtree(adir / "profile", ignore_errors=True)
    shutil.rmtree(adir, ignore_errors=True)
