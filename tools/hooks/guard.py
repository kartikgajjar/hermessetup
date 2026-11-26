#!/usr/bin/env python
"""guard.py: PreToolUse guard hook (bootstrap v2.6, 2.4). Exit 2 blocks the call; the reason goes to stderr.

It matches command text. It stops mistakes, not a determined bypass.
Git commands are normalised first: global options between 'git' and the subcommand (-C <dir>, -c k=v,
--git-dir, --work-tree, --no-pager) are stripped, and a rule that depends on the branch reads it from
the directory -C names. An internal error lets the call through with a warning (fail open), so a guard
bug cannot lock the session.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

GIT_RX = re.compile(
    r"""(?:^|[\s;&|(])git(?:\.exe)?((?:\s+(?:-C\s+(?:"[^"]*"|'[^']*'|\S+)|-c\s+(?:"[^"]*"|'[^']*'|\S+)|"""
    r"""--git-dir(?:=|\s+)\S+|--work-tree(?:=|\s+)\S+|--no-pager|-P|--bare|--no-optional-locks))*)\s+([a-z][\w-]*)(.*)""",
    re.IGNORECASE)
C_RX = re.compile(r"""-C\s+("[^"]*"|'[^']*'|\S+)""")
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
AI_TRAILER = re.compile(r"(co-authored-by:[^\n\"']*(claude|anthropic|copilot|chatgpt|openai|gpt|gemini|cursor|"
                        r"codex|devin|noreply@anthropic))|generated with \[?claude code", re.IGNORECASE)
POLL_RX = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in (
    r"\b(while|until|for)\b.*\b(sleep|start-sleep|timeout\s+/t)\b", r"\bdo\s*\{.*start-sleep", r"\bwatch\s+-n",
    r"^\s*watch\s", r"\btail\s+(-\w*f|--follow)", r"get-content\b.*-wait\b", r"\binotifywait\b",
    r"\bwhile\s*\(\s*\$true\s*\)")]
STATE_RX = re.compile(r"(\.factory[\\/]|docs[\\/]prd[\\/]verdicts)", re.IGNORECASE)
WRITE_OPS = re.compile(r"(>|\btee\b|\brm\b|\bmv\b|\bcp\b|\bsed\s+-i|\bset-content\b|\badd-content\b|\bout-file\b|"
                       r"\bremove-item\b|\bnew-item\b|\bcopy-item\b|\bmove-item\b|\btouch\b|\bdel\b|\bwrite_text\b|"
                       r"\bopen\(|\btruncate\b)", re.IGNORECASE)
FACTORY_WRITERS = re.compile(r"^\s*(python(3)?|py)(\.exe)?\s+(\S*[\\/])?tools[\\/](status|dispatch)\.py\b", re.IGNORECASE)
CD_RX = re.compile(r"^\s*(cd|pushd|set-location|sl|chdir)(\s|$)", re.IGNORECASE)
PROTECTED_IN_WORKTREE = ("docs/prd/backlog.json", "docs/prd/backlog.schema.json", "VERSION")
DEFAULT_HIGH_RISK = ("AGENTS.md", "CLAUDE.md", ".claude/**", "tools/hooks/**", "tools/agents/**",
                     "tools/skills/**", ".mcp.json", "config/**", ".github/**")


def glob_rx(g):
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return re.compile("^" + out + "$", re.IGNORECASE)


def git_out(args, cwd):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def repo_info(cwd):
    top = git_out(["rev-parse", "--show-toplevel"], cwd)
    if not top:
        return None
    gd = git_out(["rev-parse", "--path-format=absolute", "--git-dir"], cwd)
    cd = git_out(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    branch = git_out(["symbolic-ref", "--short", "-q", "HEAD"], cwd) or git_out(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    default = "main"
    prof = Path(top) / "config" / "factory-profile.yaml"
    high_risk = list(DEFAULT_HIGH_RISK)
    try:
        default = json.loads(prof.read_text(encoding="utf-8")).get("default_branch", "main")
    except Exception:  # noqa: BLE001
        pass
    try:
        high_risk = json.loads((Path(top) / "config" / "risk-paths.yaml").read_text(encoding="utf-8")).get(
            "high_risk", high_risk)
    except Exception:  # noqa: BLE001
        pass
    return {"top": top, "worktree": os.path.normcase(gd) != os.path.normcase(cd), "branch": branch,
            "default": default, "high_risk": high_risk}


def unquote(s):
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'" else s


def git_commands(segment):
    """Yield (subcommand, rest, dir_from_-C) for each git call in a segment."""
    for m in GIT_RX.finditer(segment):
        opts, sub, rest = m.group(1) or "", m.group(2).lower(), m.group(3) or ""
        cdirs = C_RX.findall(opts)
        yield sub, rest, (unquote(cdirs[-1]) if cdirs else None)


def check_command(cmd, cwd, is_agent):
    if CD_RX.match(cmd):
        return "a shell call must not start with cd/pushd/Set-Location; use absolute paths and git -C"
    segments = [s for s in SEGMENT_SPLIT.split(cmd) if s.strip()]
    for s in segments[1:]:
        if CD_RX.match(s):
            return "no cd/pushd/Set-Location inside a command chain; use absolute paths and git -C"
    if AI_TRAILER.search(cmd) and re.search(r"\bgit\b", cmd):
        return "commit messages must not carry an AI co-author trailer or attribution line (U16)"
    for rx in POLL_RX:
        if rx.search(cmd):
            return "no polling loops or file watchers; run the command once and read its result"
    if re.search(r"tools[\\/]status\.py\s+verdict\b", cmd, re.IGNORECASE):
        return "status.py verdict is refused: verdicts are written only by tools/dispatch.py verify"
    if STATE_RX.search(cmd) and WRITE_OPS.search(cmd) and not FACTORY_WRITERS.match(cmd):
        return "the state folder and verdict records are written only by tools/status.py and tools/dispatch.py"
    for seg in segments:
        for sub, rest, cdir in git_commands(seg):
            where = cdir if cdir else cwd
            if cdir and not os.path.isabs(cdir):
                where = os.path.join(cwd, cdir)
            info = repo_info(where)
            if not info:
                continue
            if info["branch"] == info["default"] and sub in ("commit", "merge", "push"):
                return ("raw git %s on the default branch (%s) is refused; work on a branch and integrate with "
                        "tools/dispatch.py integrate" % (sub, info["default"]))
            if info["worktree"]:
                if sub == "stash":
                    return "git stash is refused in a worktree; commit to the packet branch or report"
                if sub == "push":
                    return "git push is refused in a worktree; tools/dispatch.py integrate pushes"
                if sub == "commit" and re.search(r"(^|\s)(-a|--all|-[a-zA-Z]*a[a-zA-Z]*)(\s|$)", rest) \
                        and not re.search(r"(^|\s)--amend(\s|$)", rest):
                    return "git commit -a is refused in a worktree; stage the owned files explicitly"
    return None


def check_edit(path, cwd, is_agent):
    if not path:
        return None
    p = path.replace("\\", "/")
    if STATE_RX.search(p):
        return "the state folder and verdict records are written only by tools/status.py and tools/dispatch.py"
    info = repo_info(os.path.dirname(path) if os.path.isabs(path) and os.path.isdir(os.path.dirname(path)) else cwd)
    if not info:
        return None
    top = info["top"].replace("\\", "/").rstrip("/")
    rel = p[len(top) + 1:] if p.lower().startswith(top.lower() + "/") else p
    if info["worktree"] and rel in PROTECTED_IN_WORKTREE:
        return "%s is protected in a worktree (the backlog spec and version file change on the main tree)" % rel
    if is_agent and any(glob_rx(g).match(rel) for g in info["high_risk"]):
        return "%s is a high-risk file; agents in a product task do not edit it (the lead does, with approval)" % rel
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    try:
        tool = data.get("tool_name", "")
        ti = data.get("tool_input") or {}
        cwd = data.get("cwd") or os.getcwd()
        is_agent = bool(data.get("agent_id")) or str(data.get("agent_type", "")).startswith("hs-")
        if tool in ("Bash", "PowerShell"):
            reason = check_command(ti.get("command", ""), cwd, is_agent)
        elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            reason = check_edit(ti.get("file_path") or ti.get("notebook_path", ""), cwd, is_agent)
        else:
            reason = None
    except Exception as e:  # noqa: BLE001
        print("guard.py internal error (call allowed): %s" % e, file=sys.stderr)
        return 0
    if reason:
        print("BLOCKED by tools/hooks/guard.py: " + reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
