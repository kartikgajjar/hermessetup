"""webmcp CLI. With no action flag it serves MCP over stdio (what Hermes launches).

  webmcp --login tipranks --account personal     headed browser; log in by hand (2FA ok)
  webmcp --status [--check]                      table of site/account; --check probes live
  webmcp --logout tipranks --account personal    delete that account's saved session
  webmcp --probe tipranks --account personal --url https://www.tipranks.com/stocks/aapl
                                                 print what the adapter sees (for calibration)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import threading

from . import config, extract, profiles, sites
from .browser import BrowserSession
from .errors import WebmcpError


async def _login(site: str, account: str) -> int:
    adapter = sites.get(site)
    config.valid_label(account)
    cfg = config.load_config()
    with profiles.profile_lock(site, account):
        session = BrowserSession(
            config.profile_dir(site, account), headless=False, channel=config.channel(cfg)
        )
        await session.start()
        try:
            page = session.page
            await page.goto(adapter.login_url, wait_until="domcontentloaded")
            if adapter.login_confirm:
                how = "When you can see your account pages, press Enter HERE to save the session."
            else:
                how = (
                    "This finishes by itself once the site looks logged in, "
                    "or press Enter here when you are logged in."
                )
            print()
            print(f"Log in to {site} as '{account}' in the browser window (2FA/captcha are fine).")
            print(f"{how} Timeout: {adapter.login_timeout // 60} min.")
            print()
            # Daemon thread, not asyncio.to_thread: an executor thread stuck in readline() blocks
            # interpreter exit until another Enter arrives.
            loop = asyncio.get_running_loop()
            enter = asyncio.Event()

            def _wait_enter() -> None:
                try:
                    sys.stdin.readline()
                except Exception:
                    return
                loop.call_soon_threadsafe(enter.set)

            threading.Thread(target=_wait_enter, daemon=True).start()
            deadline = asyncio.get_running_loop().time() + adapter.login_timeout
            ok = False
            while asyncio.get_running_loop().time() < deadline:
                if enter.is_set():
                    ok = True
                    break
                try:
                    if (
                        not adapter.login_confirm
                        and not adapter.url_needs_login(page.url)
                        and await adapter.is_logged_in(page)
                    ):
                        await asyncio.sleep(3)  # let the session cookies settle
                        ok = True
                        break
                except Exception:
                    pass  # mid-navigation; look again
                await asyncio.sleep(2)
            if not ok:
                print("Timed out waiting for login.")
                return 1
            profiles.update_meta(site, account, last_login=profiles.utcnow())
            print(f"Saved at: {page.url}")
            print(f"Saved session for {site}/{account} under {config.profile_dir(site, account)}")
            return 0
        finally:
            await session.close()


async def _check(site: str, account: str) -> str:
    adapter = sites.get(site)
    cfg = config.load_config()
    try:
        with profiles.profile_lock(site, account):
            session = BrowserSession(
                config.profile_dir(site, account), headless=adapter.headless,
                channel=config.channel(cfg), offscreen=True,
            )
            await session.start()
            try:
                await session.page.goto(adapter.home_url, wait_until="domcontentloaded")
                await asyncio.sleep(3)
                alive = not adapter.url_needs_login(session.page.url) and await adapter.is_logged_in(
                    session.page
                )
                return "logged in" if alive else "LOGGED OUT"
            finally:
                await session.close()
    except WebmcpError as exc:
        return f"busy ({exc})"


async def _status(check: bool) -> int:
    rows = []
    for site in sorted(sites.discover()):
        accounts = profiles.list_accounts(site)
        if not accounts:
            rows.append((site, "-", "no accounts", ""))
        for acct in accounts:
            meta = profiles.read_meta(site, acct)
            state = await _check(site, acct) if check else "(not checked; use --check)"
            rows.append((site, acct, state, meta.get("last_ok") or meta.get("last_login") or ""))
    print(f"{'SITE':<10} {'ACCOUNT':<14} {'STATE':<30} LAST OK / LOGIN")
    for r in rows:
        print(f"{r[0]:<10} {r[1]:<14} {r[2]:<30} {r[3]}")
    return 0


async def _probe(site: str, account: str, url: str, headed: bool) -> int:
    adapter = sites.get(site)
    cfg = config.load_config()
    with profiles.profile_lock(site, account):
        session = BrowserSession(
            config.profile_dir(site, account), headless=adapter.headless and not headed,
            channel=config.channel(cfg), offscreen=not headed,
        )
        await session.start()
        try:
            page = session.page
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(4)
            try:
                await extract.clear_challenge(page, 15)
            except WebmcpError as exc:
                print("BOT CHALLENGE:", exc)
            text = await page.evaluate("document.body?.innerText || ''")
            print("final url      :", page.url)
            print("title          :", await page.title())
            print("needs_login url:", adapter.url_needs_login(page.url))
            print("is_logged_in   :", await adapter.is_logged_in(page))
            print("body chars     :", len(text))
            hrefs = await page.evaluate("[...document.querySelectorAll('a[href]')].map(a => a.href)")
            shapes: dict[str, int] = {}
            for h in hrefs:
                m = re.match(r"https?://[^/]+(/[^/?#]*(?:/[^/?#]*)?)", h)
                if m:
                    key = re.sub(r"\d{6,}", "<id>", m.group(1))
                    shapes[key] = shapes.get(key, 0) + 1
            print("link shapes    :", dict(sorted(shapes.items(), key=lambda kv: -kv[1])[:15]))
            print("--- first 800 chars ---")
            print(text[:800])
            return 0
        finally:
            await session.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="webmcp", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--login", metavar="SITE")
    p.add_argument("--logout", metavar="SITE")
    p.add_argument("--status", action="store_true")
    p.add_argument("--check", action="store_true", help="with --status: open each profile and test it")
    p.add_argument("--probe", metavar="SITE")
    p.add_argument("--account", metavar="LABEL")
    p.add_argument("--url")
    p.add_argument("--headed", action="store_true", help="with --probe")
    p.add_argument("--sites", help="serve only these sites (comma separated)")
    p.add_argument("--log-level", default="WARNING")
    a = p.parse_args()
    for stream in (sys.stdout, sys.stderr):  # page text has emoji; cp1252 consoles would crash
        stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=a.log_level.upper(), stream=sys.stderr)

    try:
        if a.login or a.logout or a.probe:
            if not a.account:
                p.error("--account is required")
        if a.login:
            sys.exit(asyncio.run(_login(a.login, a.account)))
        if a.logout:
            sites.get(a.logout)
            profiles.remove_account(a.logout, config.valid_label(a.account))
            print(f"Removed {a.logout}/{a.account}")
            return
        if a.probe:
            if not a.url:
                p.error("--probe needs --url")
            sys.exit(asyncio.run(_probe(a.probe, a.account, a.url, a.headed)))
        if a.status:
            sys.exit(asyncio.run(_status(a.check)))
    except WebmcpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    from .server import build

    build([s.strip() for s in a.sites.split(",")] if a.sites else None).run()  # stdio


if __name__ == "__main__":
    main()
