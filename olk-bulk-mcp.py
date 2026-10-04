"""Bulk move/delete MCP server wrapping the `olk` CLI.

olk's own `mail move`/`mail delete` (and its `olk mcp` tool equivalents) each
act on exactly one message ID -- there's no native bulk mode. This exposes
two additional tools, `bulk_move_messages` and `bulk_delete_messages`, that
take a KQL search query and act on every match, so Hermes gets bulk
semantics in one tool call instead of one call per message.

Registered as its own MCP server (alongside the base `olk` server, not a
replacement for it) via:
    hermes mcp add olk-bulk --command "<hermes>\\tools\\python-*\\python.exe" \\
        --args "<hermes.repo>\\olk-bulk-mcp.py" --account <email>

It also hosts the sender-policy "learning" loop for the email-cleanup-at-scale
skill (2026-10-04):
  - `sender_engagement_report` (read-only) pages the inbox via `olk mail list`
    and scores each sender by how much of its mail is left unread, then
    suggests senders to approve / flags approved senders the user has started
    reading again.
  - `record_sender_decision` stores the user's verdict in a JSON policy file
    (the skill's assets/sender-policy.json by default; `--policy <path>`).
  - `cleanup_approved_senders` deletes ONLY for senders whose policy status is
    "approved", building every KQL query in code.
  Why in code: on 2026-10-04 the weekly cron agent hand-wrote
  `receivedDateTime:<2026-09-20` (not a valid KQL property). Graph returned 0
  instead of an error, and the run reported "0 found" while 400+ messages
  matched the correct `received<` query.

Safety:
  - bulk_* tools default to dry_run=True: they report exactly what WOULD move
    or be deleted without acting, until called again with dry_run=False.
  - readOnlyHint=False on write tools only triggers Hermes's approval gate when
    the server is registered with `trust: untrusted` (tools/mcp_tool.py
    trust-tier gating). With no `trust:` key -- the setup here -- the server is
    trusted and write calls run WITHOUT a prompt, cron included. The safeguards
    that actually hold are the ones in this file.
  - `top` is capped at 200 per call, to bound how much a single call (or a
    single approval) can affect.
  - cleanup_approved_senders always excludes calendar items (NOT kind:meetings)
    and excludes attachments unless that sender's policy has attachments_ok.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

ACCOUNT: str | None = None
if "--account" in sys.argv:
    ACCOUNT = sys.argv[sys.argv.index("--account") + 1]

MAX_TOP = 200

POLICY_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "skills", "productivity",
                           "email-cleanup-at-scale", "assets", "sender-policy.json")
if "--policy" in sys.argv:
    POLICY_PATH = sys.argv[sys.argv.index("--policy") + 1]

DEFAULT_THRESHOLDS = {
    "lookback_days": 365,             # how far back the engagement scan looks
    "min_messages": 10,               # ignore senders with less history than this
    "approve_unread_ratio": 0.90,     # suggest approval at/above this unread share
    "attachments_ok_unread_ratio": 0.98,  # also suggest attachments_ok if every attachment mail is unread and ratio >= this
    "reject_cooldown_days": 90,       # don't re-suggest a rejected sender for this long
    "default_retention_days": 14,
}
STATUSES = ("approved", "review", "keep", "rejected")
# Real people mostly send from consumer mailboxes; never auto-suggest these.
CONSUMER_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
                    "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com"}
EMAIL_RE = re.compile(r"^[^@\s\"]+@[^@\s\"]+\.[^@\s\"]+$")

server = MCPServer(name="olk-bulk")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_policy() -> dict[str, Any]:
    try:
        with open(POLICY_PATH, encoding="utf-8") as f:
            policy = json.load(f)
    except FileNotFoundError:
        policy = {}
    policy.setdefault("version", 1)
    policy["thresholds"] = {**DEFAULT_THRESHOLDS, **policy.get("thresholds", {})}
    policy.setdefault("senders", {})
    return policy


def _save_policy(policy: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(POLICY_PATH), exist_ok=True)
    tmp = POLICY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(policy, f, indent=2, ensure_ascii=False)
    os.replace(tmp, POLICY_PATH)


def _olk(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    cmd = ["olk", *args]
    if ACCOUNT:
        cmd += ["--account", ACCOUNT]
    # Explicit UTF-8: subprocess.run's default text-mode encoding is the platform's
    # (cp1252 on Windows), which routinely can't decode real mail content (curly
    # quotes, em-dashes, emoji, etc.) -- confirmed 2026-09-28, a UnicodeDecodeError
    # crashed a reader thread on a Deleted Items listing. errors="replace" so a truly
    # undecodable byte degrades to a replacement char instead of crashing the call.
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _search(query: str, top: int) -> list[dict[str, Any]]:
    top = max(1, min(top, MAX_TOP))
    proc = _olk("mail", "search", query, "-n", str(top), "--json", "--results-only", "--select=id,subject,from")
    if proc.returncode != 0:
        raise RuntimeError(f"olk mail search failed: {(proc.stderr or proc.stdout).strip()}")
    return json.loads(proc.stdout or "[]")


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def bulk_move_messages(query: str, folder: str, top: int = 50, dry_run: bool = True) -> dict[str, Any]:
    """Move every message matching a KQL search query to a destination folder.

    Args:
        query: KQL search query (from:, subject:, hasAttachment:, etc. -- same syntax as `olk mail search`)
        folder: Destination folder ID, well-known name, or path (e.g. "Archive", "Inbox/2026")
        top: Max number of matching messages to act on in this call (safety cap, max 200)
        dry_run: If true (default), only report what WOULD move -- moves nothing. Call again
            with dry_run=false, after reviewing the dry-run result, to actually move messages.
    """
    matches = _search(query, top)
    if dry_run:
        return {
            "dry_run": True,
            "matched": len(matches),
            "would_move_to": folder,
            "messages": [{"id": m["id"], "subject": m.get("subject", ""), "from": m.get("from", "")} for m in matches],
        }
    moved: list[str] = []
    failed: list[dict[str, str]] = []
    for m in matches:
        proc = _olk("mail", "move", m["id"], folder)
        label = m.get("subject") or m["id"]
        if proc.returncode == 0:
            moved.append(label)
        else:
            failed.append({"subject": label, "error": (proc.stderr or proc.stdout).strip()})
    return {"dry_run": False, "moved_to": folder, "moved_count": len(moved), "failed_count": len(failed),
            "moved": moved, "failed": failed}


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
def bulk_delete_messages(query: str, top: int = 50, dry_run: bool = True) -> dict[str, Any]:
    """Delete every message matching a KQL search query.

    This is Microsoft Graph's standard delete (moves to Deleted Items), not a hard purge --
    deleting from Deleted Items itself needs a second pass with a query scoped to that folder.

    Args:
        query: KQL search query (from:, subject:, hasAttachment:, etc. -- same syntax as `olk mail search`)
        top: Max number of matching messages to act on in this call (safety cap, max 200)
        dry_run: If true (default), only report what WOULD be deleted -- deletes nothing. Call
            again with dry_run=false, after reviewing the dry-run result, to actually delete.
    """
    matches = _search(query, top)
    if dry_run:
        return {
            "dry_run": True,
            "matched": len(matches),
            "messages": [{"id": m["id"], "subject": m.get("subject", ""), "from": m.get("from", "")} for m in matches],
        }
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for m in matches:
        # `olk mail delete` refuses without --force (confirmed 2026-09-27: fails fast and
        # cleanly, "use --force to confirm deletion", exit 1 -- it does not hang or prompt).
        proc = _olk("mail", "delete", "--force", m["id"])
        label = m.get("subject") or m["id"]
        if proc.returncode == 0:
            deleted.append(label)
        else:
            failed.append({"subject": label, "error": (proc.stderr or proc.stdout).strip()})
    return {"dry_run": False, "deleted_count": len(deleted), "failed_count": len(failed),
            "deleted": deleted, "failed": failed}


def _list_inbox(after: datetime, max_messages: int) -> list[dict[str, Any]]:
    """Page the inbox newest-first via `olk mail list` (1000/page; search caps at 275 total)."""
    seen: dict[str, dict[str, Any]] = {}
    before: datetime | None = None
    while len(seen) < max_messages:
        args = ["mail", "list", "-n", "1000", "--after", after.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "--json", "--results-only", "--select=id,from,isRead,hasAttachments,receivedDateTime"]
        if before:
            # --before is exclusive; +1s so same-second messages aren't skipped (dedup by id below).
            args += ["--before", (before + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")]
        proc = _olk(*args, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"olk mail list failed: {(proc.stderr or proc.stdout).strip()}")
        page = json.loads(proc.stdout or "[]")
        new = [m for m in page if m["id"] not in seen]
        for m in new:
            seen[m["id"]] = m
        if len(page) < 1000 or not new:
            break
        before = min(_parse_ts(m["receivedDateTime"]) for m in page)
    return list(seen.values())


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def sender_engagement_report(lookback_days: int = 0, min_messages: int = 0, max_messages: int = 5000,
                             limit: int = 25) -> dict[str, Any]:
    """Score inbox senders by how much of their mail stays unread, and suggest policy changes.

    Read-only. Merges live inbox stats with the sender policy file and returns:
      - suggest_approve: unknown senders whose unread share >= approve_unread_ratio over at least
        min_messages emails (consumer-mailbox senders like gmail.com are never suggested).
        suggest_attachments_ok is true when every attachment email from them is also unread.
      - engagement_detected: approved senders with mail the user READ after it was approved --
        a sign the approval may be wrong.
      - review: senders with status "review", with current stats, for the user to decide.

    Args:
        lookback_days: Days of inbox history to scan (0 = policy default, 365).
        min_messages: Minimum emails before a sender is scored (0 = policy default, 10).
        max_messages: Upper bound on messages scanned.
        limit: Max entries per output list.
    """
    policy = _load_policy()
    th = policy["thresholds"]
    lookback = lookback_days or th["lookback_days"]
    min_n = min_messages or th["min_messages"]
    msgs = _list_inbox(_now() - timedelta(days=lookback), max_messages)

    stats: dict[str, dict[str, Any]] = {}
    for m in msgs:
        sender = (m.get("from") or "").lower()
        if not sender:
            continue
        s = stats.setdefault(sender, {"sender": sender, "total": 0, "unread": 0, "attachments": 0,
                                      "unread_attachments": 0, "newest": "", "last_read": "", "_read_ts": []})
        s["total"] += 1
        ts = m["receivedDateTime"]
        s["newest"] = max(s["newest"], ts)
        if m.get("hasAttachments"):
            s["attachments"] += 1
        if m.get("isRead"):
            s["last_read"] = max(s["last_read"], ts)
            s["_read_ts"].append(ts)
        else:
            s["unread"] += 1
            if m.get("hasAttachments"):
                s["unread_attachments"] += 1
    for s in stats.values():
        s["unread_ratio"] = round(s["unread"] / s["total"], 3)

    def public(s: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in s.items() if not k.startswith("_")}

    suggest, engaged, review = [], [], []
    for sender, s in stats.items():
        entry = policy["senders"].get(sender)
        status = entry["status"] if entry else None
        if status == "approved":
            decided = entry.get("decided_at", "")
            read_after = [t for t in s["_read_ts"] if t > decided]
            if read_after:
                engaged.append({**public(s), "read_since_approved": len(read_after), "decided_at": decided})
            continue
        if status == "review":
            review.append({**public(s), "note": entry.get("note", "")})
            continue
        if status == "keep":
            continue
        if status == "rejected":
            decided = entry.get("decided_at", "")
            if decided and _now() - _parse_ts(decided) < timedelta(days=th["reject_cooldown_days"]):
                continue
        if s["total"] < min_n or s["unread_ratio"] < th["approve_unread_ratio"]:
            continue
        if sender.rsplit("@", 1)[-1] in CONSUMER_DOMAINS:
            continue
        att_ok = (s["attachments"] > 0 and s["unread_attachments"] == s["attachments"]
                  and s["unread_ratio"] >= th["attachments_ok_unread_ratio"])
        suggest.append({**public(s), "suggest_attachments_ok": att_ok,
                        "previously_rejected": status == "rejected"})

    suggest.sort(key=lambda s: (-s["total"], -s["unread_ratio"]))
    review.sort(key=lambda s: -s["total"])
    engaged.sort(key=lambda s: -s["read_since_approved"])
    return {
        "scanned_messages": len(msgs),
        "lookback_days": lookback,
        "min_messages": min_n,
        "thresholds": th,
        "policy_file": POLICY_PATH,
        "suggest_approve": suggest[:limit],
        "suggest_approve_total": len(suggest),
        "engagement_detected": engaged[:limit],
        "review": review[:limit],
    }


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def record_sender_decision(sender: str, status: str, attachments_ok: bool = False,
                           retention_days: int = 0, note: str = "") -> dict[str, Any]:
    """Record the USER's decision about a sender in the policy file (the learning memory).

    Only call this with a decision the user actually made -- never on your own judgement.
    "approved" senders are deleted by cleanup_approved_senders; "review"/"keep"/"rejected" are
    never deleted and "rejected" is not re-suggested for reject_cooldown_days.

    Args:
        sender: Exact sender email address (no wildcards or domains).
        status: approved | review | keep | rejected
        attachments_ok: approved only -- also delete this sender's mail that has attachments.
        retention_days: approved only -- keep mail newer than this many days (0 = policy default, 14).
        note: Why (e.g. "user: 98% unread over 300 emails, never opens them").
    """
    sender = sender.strip().lower()
    if not EMAIL_RE.match(sender) or "*" in sender:
        raise ValueError(f"sender must be one exact email address, got {sender!r}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    policy = _load_policy()
    entry: dict[str, Any] = {"status": status, "decided_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "note": note}
    if status == "approved":
        entry["attachments_ok"] = bool(attachments_ok)
        entry["retention_days"] = retention_days or policy["thresholds"]["default_retention_days"]
    previous = policy["senders"].get(sender)
    if previous:
        entry["history"] = (previous.get("history") or []) + [
            {k: v for k, v in previous.items() if k != "history"}]
    policy["senders"][sender] = entry
    _save_policy(policy)
    return {"sender": sender, "saved": entry, "policy_file": POLICY_PATH}


def _cleanup_query(sender: str, entry: dict[str, Any], default_retention: int) -> str:
    cutoff = (_now() - timedelta(days=entry.get("retention_days") or default_retention)).strftime("%Y-%m-%d")
    q = f'from:"{sender}" AND received<{cutoff} AND NOT kind:meetings'
    if not entry.get("attachments_ok"):
        q += " AND hasattachments:false"
    return q


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
def cleanup_approved_senders(dry_run: bool = True, max_passes: int = 10) -> dict[str, Any]:
    """Delete old mail from every sender with policy status "approved" -- and nothing else.

    Queries are built here, never by the caller: per sender
    `from:"<sender>" AND received<<today - retention_days> AND NOT kind:meetings`
    plus `AND hasattachments:false` unless the sender is attachments_ok. Unread mail IS deleted
    for approved senders (user decision 2026-10-04). Repeats each sender while a pass hits the
    200 cap, up to max_passes. Stops a sender on the first failed delete.

    Args:
        dry_run: If true (default), report match counts only (search caps at 275 per sender).
        max_passes: Safety bound on 200-message passes per sender.
    """
    policy = _load_policy()
    default_ret = policy["thresholds"]["default_retention_days"]
    results = []
    for sender, entry in sorted(policy["senders"].items()):
        if entry.get("status") != "approved":
            continue
        query = _cleanup_query(sender, entry, default_ret)
        row: dict[str, Any] = {"sender": sender, "query": query}
        try:
            if dry_run:
                proc = _olk("mail", "search", query, "-n", "275", "--json", "--results-only", "--select=id")
                if proc.returncode != 0:
                    raise RuntimeError((proc.stderr or proc.stdout).strip())
                n = len(json.loads(proc.stdout or "[]"))
                row["matched"] = f"{n}+" if n >= 275 else n
            else:
                deleted = failed = passes = 0
                errors: list[dict[str, str]] = []
                while passes < max_passes:
                    passes += 1
                    r = bulk_delete_messages(query, top=MAX_TOP, dry_run=False)
                    deleted += r["deleted_count"]
                    failed += r["failed_count"]
                    errors += r["failed"][:3]
                    if r["failed_count"] or r["deleted_count"] < MAX_TOP:
                        break
                row.update(deleted_count=deleted, failed_count=failed, passes=passes,
                           more_remaining=passes == max_passes and not failed, errors=errors)
        except Exception as e:  # keep going with the other senders
            row["error"] = str(e)
        results.append(row)
    total_key = "matched" if dry_run else "deleted_count"
    total = sum(int(str(r.get(total_key, 0)).rstrip("+")) for r in results)
    return {"dry_run": dry_run, "approved_senders": len(results), f"total_{total_key}": total,
            "errors": sum(1 for r in results if r.get("error") or r.get("failed_count")),
            "results": results}


if __name__ == "__main__":
    server.run(transport="stdio")
