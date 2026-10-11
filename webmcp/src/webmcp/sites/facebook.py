"""Facebook: your SAVED REELS only (not the feed, not other saved items).

Read tools: structured, filterable, paged listing; collection list; single-reel read; CSV/JSON export.
Write tools (move to a collection, create a collection, unsave): each previews first and only acts
when called again with confirm=true.

Facebook's markup is obfuscated and changes often, so this adapter relies on URL shapes
(`/reel/<id>`, `/saved/?list_id=<id>`), ARIA roles/names and the card's innerText, never on class
names. Button names ("Add to Collection", "Unsave", "Create new collection", "See more") are English;
a different Facebook UI language needs them adjusted in the constants below.

Meta's terms forbid automated collection; an account can get a checkpoint or be restricted. Keep volume
low and use only your own account.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated

from patchright.async_api import Page
from pydantic import Field

from .. import extract
from ..errors import RateLimited, WebmcpError
from .base import SiteAdapter

log = logging.getLogger("webmcp.facebook")

Account = Annotated[str | None, Field(description="Account label; optional if only one is logged in")]
Collection = Annotated[
    str | None,
    Field(
        description="Only reels in this collection (name, case-insensitive). Use 'none' for reels "
        "that are in no collection. Omit for all saved reels. See facebook_collections."
    ),
]

_SAVED_URL = "https://www.facebook.com/saved/"
_REEL_HREF = r"facebook\.com/reel/\d+"
_REEL_URL = re.compile(r"^https://(www\.|m\.)?facebook\.com/reel/(\d+)(/|\?|$)")
_REEL_ID = re.compile(r"(\d{6,})")
_SEE_MORE = re.compile(r"see more", re.I)
_DURATION = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_SAVED_TO = re.compile(r"Saved to ([^\n]+)")
_SAVED_FROM = re.compile(r"Saved from (.+?)['’]s post")
_NO_COLLECTION = {"none", "(none)", "no collection", "uncollected", "unsorted"}
_CAPTION_CHARS = 1200

# Finds the saved-list card for a reel (largest ancestor holding only that reel's link), tags it
# with data-webmcp-card and returns its text, or null if the reel is not rendered (yet).
_CARD_JS = """(rid) => {
  const a = [...document.querySelectorAll('a[href*="/reel/' + rid + '"]')][0];
  if (!a) return null;
  let node = a;
  while (node.parentElement) {
    const up = node.parentElement;
    const others = [...up.querySelectorAll('a[href*="/reel/"]')].filter(x => !x.href.includes(rid));
    if (others.length) break;
    node = up;
  }
  node.setAttribute('data-webmcp-card', rid);
  return node.innerText;
}"""


# ---- parsing --------------------------------------------------------------------------------

def parse_card(text: str) -> dict:
    """Card innerText -> {duration, author, collection, caption}. Missing fields are None."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    duration = next((ln for ln in lines if _DURATION.match(ln)), None)
    meta_i = next((i for i, ln in enumerate(lines) if ln.startswith("Reels")), None)
    start = lines.index(duration) + 1 if duration else 0
    caption = "\n".join(lines[start : meta_i if meta_i is not None else len(lines)])
    meta = lines[meta_i] if meta_i is not None else ""

    saved_to = _SAVED_TO.search(text)
    collection = saved_to.group(1).strip() if saved_to else None

    saved_from = _SAVED_FROM.search(text)
    if saved_from:
        author = saved_from.group(1).strip()
    else:
        rest = meta.split("•", 1)[1].strip() if "•" in meta else ""
        author = None if not rest or rest.startswith("Saved to") else rest
    return {
        "author": author,
        "collection": collection,
        "duration": duration,
        "caption": caption[:_CAPTION_CHARS],
    }


def reel_id(value: str) -> str:
    m = _REEL_ID.search(value or "")
    if not m:
        raise WebmcpError("give a reel id or a facebook.com/reel/<id> URL")
    return m.group(1)


def _is_none_filter(collection: str | None) -> bool:
    return collection is not None and collection.strip().lower() in _NO_COLLECTION


# ---- page helpers ---------------------------------------------------------------------------

async def _expand_caption(page: Page) -> None:
    """Click the caption's "... See more" until it is really expanded.

    The button renders after domcontentloaded and may exist before its click handler is attached,
    so a single early click can be a no-op. Keep clicking and only stop once the button is gone
    after a click (or ~8 s have passed).
    """
    clicked = False
    for i in range(16):
        try:
            target = page.get_by_role("button", name=_SEE_MORE)
            if await target.count() == 0:
                target = page.get_by_text(re.compile(r"^\W*see more$", re.I))
            if await target.count():
                await target.first.click(timeout=3000)
                clicked = True
                await page.wait_for_timeout(700)
                continue  # re-check: gone = expanded, still there = click was a no-op
            if clicked:
                return
        except Exception as exc:
            log.debug("see-more poll %d failed: %s", i, str(exc)[:200])
        await page.wait_for_timeout(500)


async def _open(page: Page, url: str = _SAVED_URL) -> None:
    await extract.goto(page, url, wait_selector='[role="main"]')
    await page.wait_for_timeout(1500)  # let the list hydrate


async def _collections(page: Page) -> list[dict]:
    """All collections: [{name, id, privacy}], from the Saved page's side navigation."""
    await _open(page)
    more = page.get_by_role("button", name=re.compile(r"^see more$", re.I))
    if await more.count():
        await more.first.click(timeout=3000)
        await page.wait_for_timeout(1200)
    rows = await page.evaluate(
        """() => [...document.querySelectorAll('a[href*="list_id="]')].map(a => ({
              id: a.href.split('list_id=')[1].split('&')[0],
              lines: (a.innerText || '').trim().split(String.fromCharCode(10)).map(s => s.trim()).filter(Boolean)
        }))"""
    )
    seen: dict[str, dict] = {}
    for row in rows:
        if row["lines"] and row["id"] not in seen:
            seen[row["id"]] = {
                "name": row["lines"][0],
                "id": row["id"],
                "privacy": row["lines"][1] if len(row["lines"]) > 1 else None,
            }
    return list(seen.values())


def _resolve(collections: list[dict], name: str) -> dict:
    wanted = name.strip().lower()
    exact = [c for c in collections if c["name"].lower() == wanted]
    if exact:
        return exact[0]
    near = [c for c in collections if c["name"].lower().startswith(wanted)]
    if len(near) == 1:
        return near[0]
    raise WebmcpError(
        f"No collection named {name!r}. Existing: {[c['name'] for c in collections]}"
    )


async def _list(
    page: Page, collection: str | None, limit: int, offset: int, max_scrolls: int
) -> dict:
    url, label, accept = _SAVED_URL, None, None
    if _is_none_filter(collection):
        label = "none"
        accept = lambda item: parse_card(item["text"])["collection"] is None  # noqa: E731
    elif collection:
        target = _resolve(await _collections(page), collection)
        url, label = f"{_SAVED_URL}?list_id={target['id']}", target["name"]

    links = await extract.collect_links(
        page, url, _REEL_HREF, limit=(offset + limit) * 2 + 2, max_scrolls=max_scrolls,
        main_selector='[role="main"]', accept=accept,
    )
    reels, ids = [], set()
    for item in links:
        m = _REEL_URL.match(item["href"])
        if not m or m.group(2) in ids:
            continue
        ids.add(m.group(2))
        parsed = parse_card(item["text"])
        if label and label != "none":
            parsed["collection"] = label  # cards on a collection page may not repeat it
        reels.append({"id": m.group(2), "url": f"https://www.facebook.com/reel/{m.group(2)}", **parsed})

    window = reels[offset : offset + limit]
    has_more = len(reels) > offset + limit
    return {
        "collection_filter": label,
        "offset": offset,
        "count": len(window),
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "reels": window,
    }


async def _find_card(page: Page, rid: str, max_scrolls: int = 40) -> str | None:
    for _ in range(max_scrolls + 1):
        text = await page.evaluate(_CARD_JS, rid)
        if text is not None:
            return text
        await extract.scroll(page, 1)
    return None


async def _pick_in_dialog(page: Page, name: str) -> bool:
    dlg = page.locator('[role="dialog"]').last
    await dlg.wait_for(timeout=8000)
    for button in await dlg.locator('[role="button"]').all():
        lines = [ln.strip() for ln in (await button.inner_text()).splitlines() if ln.strip()]
        if lines and lines[0] == name:
            await button.click(timeout=4000)
            return True
    return False


def _output_dir() -> Path:
    """Same folder the install scripts mount as /output: $HERMES_OUTPUT_DIR, else
    <OneDrive>/Documents/Hermes-Output, else ~/Documents/Hermes-Output. The server's environment
    may lack the OneDrive variables, so an existing OneDrive folder is also looked for directly."""
    env = os.environ.get("HERMES_OUTPUT_DIR")
    if env:
        base = Path(env)
    else:
        home = Path.home()
        roots = [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
        candidates = [Path(r) / "Documents" / "Hermes-Output" for r in roots if r]
        candidates.append(home / "OneDrive" / "Documents" / "Hermes-Output")
        base = next((c for c in candidates if c.is_dir()), home / "Documents" / "Hermes-Output")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower() or "x"


def _confirm_msg(what: str) -> dict:
    return {
        "needs_confirm": True,
        "message": f"Nothing was changed. {what} Ask the user, and only then call again with confirm=true.",
    }


# ---- adapter --------------------------------------------------------------------------------

class Facebook(SiteAdapter):
    name = "facebook"
    hosts = ("facebook.com",)
    login_url = "https://www.facebook.com/login/"
    home_url = _SAVED_URL
    auth_url_markers = ("/login", "/checkpoint", "/two_step_verification", "/recover")
    main_selector = '[role="main"]'
    min_chars = 20  # a reel page can legitimately be just a short caption

    async def is_logged_in(self, page: Page) -> bool:
        if self.url_needs_login(page.url):
            return False
        # Structural: the session cookie exists and no password field is showing.
        cookies = await page.context.cookies("https://www.facebook.com")
        if not any(c["name"] == "c_user" for c in cookies):
            return False
        return await page.locator('input[name="pass"]').count() == 0

    def register(self, mcp, svc, enabled) -> None:
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
        write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
        destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
        run = lambda account, fn: svc.run("facebook", account, fn)  # noqa: E731

        if enabled("facebook_collections"):
            @mcp.tool(name="facebook_collections", annotations=ro)
            async def facebook_collections(account: Account = None) -> dict:
                """List your saved-items collections (name, id, privacy)."""
                async def work(page: Page) -> dict:
                    cols = await _collections(page)
                    return {"count": len(cols), "collections": cols}

                return await run(account, work)

        if enabled("facebook_saved_reels"):
            @mcp.tool(name="facebook_saved_reels", annotations=ro)
            async def facebook_saved_reels(
                account: Account = None,
                collection: Collection = None,
                limit: Annotated[int, Field(ge=1, le=50, description="Page size")] = 20,
                offset: Annotated[int, Field(ge=0, description="Skip this many (use next_offset)")] = 0,
                max_scrolls: Annotated[int, Field(ge=0, le=40)] = 15,
            ) -> dict:
                """Saved reels as structured fields (id, url, author, collection, duration, caption),
                optionally for one collection, in pages. Pass next_offset back as offset for more."""
                return await run(account, lambda page: _list(page, collection, limit, offset, max_scrolls))

        if enabled("facebook_export_saved_reels"):
            @mcp.tool(name="facebook_export_saved_reels", annotations=write)
            async def facebook_export_saved_reels(
                account: Account = None,
                collection: Collection = None,
                format: Annotated[str, Field(description="csv or json")] = "csv",
                limit: Annotated[int, Field(ge=1, le=500)] = 200,
            ) -> dict:
                """Write saved reels to a dated CSV/JSON file in the Hermes output folder; returns its Windows path."""
                fmt = format.lower()
                if fmt not in ("csv", "json"):
                    raise WebmcpError("format must be csv or json")

                async def work(page: Page) -> dict:
                    data = await _list(page, collection, limit, 0, min(60, 10 + limit // 5))
                    rows = data["reels"]
                    stem = f"facebook-saved-reels-{date.today().isoformat()}"
                    if collection:
                        stem += f"-{_slug(collection)}"
                    path = _output_dir() / f"{stem}.{fmt}"
                    if fmt == "json":
                        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                    else:
                        buf = io.StringIO()
                        w = csv.DictWriter(
                            buf, ["id", "url", "author", "collection", "duration", "caption"],
                            lineterminator="\r\n",
                        )
                        w.writeheader()
                        w.writerows(rows)
                        path.write_text(buf.getvalue(), encoding="utf-8-sig")  # BOM: Excel reads emoji
                    return {"path": str(path), "count": len(rows), "has_more": data["has_more"]}

                return await run(account, work)

        if enabled("facebook_reel"):
            @mcp.tool(name="facebook_reel", annotations=ro)
            async def facebook_reel(
                url: Annotated[str, Field(description="A facebook.com/reel/<id> URL, e.g. from facebook_saved_reels")],
                account: Account = None,
            ) -> dict:
                """Full caption, author and visible text of one reel page."""
                if not _REEL_URL.match(url):
                    raise WebmcpError("url must look like https://www.facebook.com/reel/<digits>")
                extract.require_host(url, self.hosts)

                async def work(page: Page) -> dict:
                    await extract.goto(page, url, wait_selector=self.main_selector)
                    await _expand_caption(page)
                    text = await extract.body_text(page, self.main_selector)
                    if len(text) < self.min_chars:
                        raise RateLimited(f"{page.url} returned only {len(text)} characters")
                    return {"url": page.url, "title": await page.title(), "text": text}

                return await run(account, work)

        if enabled("facebook_move_reel"):
            @mcp.tool(name="facebook_move_reel", annotations=write)
            async def facebook_move_reel(
                reel: Annotated[str, Field(description="Reel id or URL")],
                collection: Annotated[str, Field(description="Target collection name")],
                account: Account = None,
                confirm: Annotated[bool, Field(description="Must be true to act; the user must have agreed")] = False,
            ) -> dict:
                """Put a saved reel into a collection. Previews unless confirm=true."""
                rid = reel_id(reel)

                async def work(page: Page) -> dict:
                    target = _resolve(await _collections(page), collection)
                    await _open(page)
                    text = await _find_card(page, rid)
                    if text is None:
                        raise WebmcpError(f"Reel {rid} is not in your saved list (or too far down to find).")
                    current = parse_card(text)["collection"]
                    plan = {"reel": rid, "from": current, "to": target["name"]}
                    if current and current.lower() == target["name"].lower():
                        return {"changed": False, "message": "Already in that collection.", **plan}
                    if not confirm:
                        return {**_confirm_msg(f"Would move reel {rid} from {current or 'no collection'} to {target['name']}."), **plan}

                    card = page.locator(f'[data-webmcp-card="{rid}"]')
                    await card.get_by_role("button", name=re.compile(r"add to collection", re.I)).click(timeout=5000)
                    if not await _pick_in_dialog(page, target["name"]):
                        await page.keyboard.press("Escape")
                        raise WebmcpError(f"Collection {target['name']!r} not offered in the dialog.")
                    await page.wait_for_timeout(1500)
                    await page.keyboard.press("Escape")

                    await _open(page)  # re-read from scratch to verify
                    after = await _find_card(page, rid)
                    now = parse_card(after)["collection"] if after else None
                    return {"changed": bool(now and now.lower() == target["name"].lower()),
                            "collection_now": now, **plan}

                return await run(account, work)

        if enabled("facebook_create_collection"):
            @mcp.tool(name="facebook_create_collection", annotations=write)
            async def facebook_create_collection(
                name: Annotated[str, Field(min_length=1, max_length=60, description="New collection name")],
                account: Account = None,
                confirm: Annotated[bool, Field(description="Must be true to act; the user must have agreed")] = False,
            ) -> dict:
                """Create an empty collection. Previews unless confirm=true."""
                clean = " ".join(name.split())
                if not clean:
                    raise WebmcpError("name is empty")

                async def work(page: Page) -> dict:
                    existing = await _collections(page)
                    if any(c["name"].lower() == clean.lower() for c in existing):
                        return {"created": False, "message": f"Collection {clean!r} already exists."}
                    if not confirm:
                        return _confirm_msg(f"Would create a new collection named {clean!r}.")

                    button = page.get_by_role("button", name=re.compile(r"create new collection", re.I))
                    if await button.count() == 0:
                        button = page.get_by_text(re.compile(r"create new collection", re.I))
                    await button.first.click(timeout=5000)
                    dlg = page.locator('[role="dialog"]').last
                    box = dlg.get_by_placeholder(re.compile(r"name", re.I))
                    await box.click()
                    await box.press_sequentially(clean, delay=25)
                    await dlg.get_by_role("button", name="Create", exact=True).click(timeout=5000)
                    await page.wait_for_timeout(2000)

                    after = await _collections(page)
                    made = next((c for c in after if c["name"].lower() == clean.lower()), None)
                    return {"created": made is not None, "collection": made}

                return await run(account, work)

        if enabled("facebook_unsave_reel"):
            @mcp.tool(name="facebook_unsave_reel", annotations=destructive)
            async def facebook_unsave_reel(
                reel: Annotated[str, Field(description="Reel id or URL")],
                account: Account = None,
                confirm: Annotated[bool, Field(description="Must be true to act; the user must have agreed")] = False,
            ) -> dict:
                """Remove a reel from your saved items. Previews unless confirm=true."""
                rid = reel_id(reel)

                async def work(page: Page) -> dict:
                    await _open(page)
                    text = await _find_card(page, rid)
                    if text is None:
                        raise WebmcpError(f"Reel {rid} is not in your saved list (or too far down to find).")
                    info = {"reel": rid, **parse_card(text)}
                    if not confirm:
                        return {**_confirm_msg(f"Would unsave reel {rid} ({(info['caption'] or '')[:60]!r})."), "reel_info": info}

                    card = page.locator(f'[data-webmcp-card="{rid}"]')
                    await card.get_by_role("button", name=re.compile(r"more options", re.I)).click(timeout=5000)
                    await page.get_by_role("menuitem", name=re.compile(r"^unsave", re.I)).first.click(timeout=5000)
                    await page.wait_for_timeout(2000)

                    await _open(page)
                    gone = await _find_card(page, rid, max_scrolls=15) is None
                    return {"unsaved": gone, "reel": rid}

                return await run(account, work)


ADAPTER = Facebook()
