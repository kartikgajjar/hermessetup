---
name: hs-lead
description: Lead playbook for hermessetup factory work - writing a packet brief with its Bar line, status commands, and the clear-session checklist. Use when planning, briefing, reviewing or integrating a backlog item.
---

# hs-lead

## Brief template (one fixed length; constraints are fixed lines, the rest is a hint)

```
Item and rows: <item>/<row> - <task>
Goal: <one sentence>
Why: <who uses the result> / <what goes wrong if it is wrong>
Facts checked at head: <commit>; <facts read from files, with paths>
Where and owned files: <branch or worktree>; owned: <globs>          (fixed)
Interface: <params, outputs, exit codes the change must keep>          (fixed)
Do not touch: <files and systems>                                      (fixed)
Blast radius: <output of git grep for the changed names>
Baseline: <python tools/check.py result at head; known failures>
Defaults: <decisions the agent should not reopen, with U ids>
Proof command: <exact command>                                         (fixed)
Bar: <proof command> | live: <feature ids from docs/path-map.yaml, or none + reason> | verdict: author or verifier   (fixed)
Report: the format in the agent body
Limit: about 20 minutes; stop at 3 failed rounds or 30 minutes
```

- `python tools/status.py ready <item>` prints the proposed Bar line, the risk-path hits and the working level.
  You may raise the bar, never lower it below the tier's floor (T2: verifier on risk paths and interface
  changes; one live drive of each mapped feature the packet touches; a failing repro before a bug fix).
- Model class: cheap (`claude-haiku-5-5`) by default; standard (`claude-sonnet-5-5`) for 02/04/05,
  olk-bulk-mcp.py and chrome-mcp/. One class up on a failed check or review; never a fourth retry on a class;
  a fix round goes to a fresh agent with the original brief and the prior report.
- Only one packet at a time may touch the live Hermes install.

## Status commands

```
python tools/status.py --console
python tools/status.py ready [item]
python tools/status.py add-row <item> --task ... --owner hs-dev --model-class cheap --owned <globs> --proof "<cmd>" [--verdict verifier]
python tools/status.py item-status <item> "in work"|built
python tools/dispatch.py verify <item> <row> [--author]     # the only way a verdict is written
python tools/dispatch.py integrate <item>                    # asks you (permission prompt) at A0/A1
python tools/status.py measures [--release R]
python tools/profile.py level
```

## Clear-session checklist

1. Never clear while agents run (`background tasks: none` from each).
2. Every row shows its true state (`status.py --console`).
3. Write the handover note to `docs/factory/setup-progress.md` (or the item's notes): what is done, next, open
   decisions, Unknowns.
4. Then clear.
