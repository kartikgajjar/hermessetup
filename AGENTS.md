# AGENTS.md - hermessetup (factory bootstrap v2.6)

One source for every agent. `CLAUDE.md` adds Claude-only lines and the operational knowledge of this repo.
One fact per bullet; decision ids in brackets refer to `docs/DECISIONS.md`.

## Start here
- `CLAUDE.md` - how Hermes really behaves here (read before touching any script).
- `docs/ENGINEERING.md` - definition of done, release flow, how briefs are written.
- `docs/DECISIONS.md`, `docs/INVARIANTS.md`, `docs/learning-process.md`.
- `docs/features/` and `docs/path-map.yaml` - what a change touches for a user.
- `config/factory-profile.yaml` - tier T2, autonomy ceiling A3, earned A0 [U8-U14].

## Commands (repo root; no `cd`)
- Proof (default): `python tools/check.py` - all .ps1 parse, all .py compile.
- With factory self-tests: `python tools/check.py --factory`; chrome-mcp suite: `python tools/check.py --chrome`.
- Status: `python tools/status.py --console` / `ready` / `measures`.
- Verdict: `python tools/dispatch.py verify <item> <row> [--author]` (the only writer of verdicts).
- Gate: `python tools/gate.py --base main`. Integrate: `python tools/dispatch.py integrate <item>`.
- Level: `python tools/profile.py level`. Regenerate copies: `python tools/gen.py`; settings: `python tools/profile.py settings --write`.
- Fresh clone: `python tools/setup_clone.py` (commit-msg hook, state folder, drift checks).
- External pulls (all on the wait list): `05-update-hermes.ps1 -Plan`, `07-check-chrome-mcp-upstream.ps1`,
  `npm install`/`npm audit`, anything reaching Graph, GitHub API or the Hermes upstream.

## Architecture (non-obvious only)
- The scripts mutate the live Hermes install at `%LOCALAPPDATA%\hermes`; there is no sandbox copy.
- Hermes runs on a bundled, versioned Python; olk-bulk is registered with an absolute, version-pinned interpreter.
- `chrome-mcp/` is a vendored MIT copy (see `chrome-mcp/VENDORED.md`); `dist/` is gitignored and built by its suite.
- Factory state lives in `.factory/` of the main worktree (git-ignored, shared by worktrees via the git common dir).
  It is local to this machine and its history is not kept; work spread over machines needs a different store first.
- Config files under `config/` and `docs/path-map.yaml` use JSON syntax (valid YAML) so the standard library reads them.

## Critical rules
- Never run 01, 02, 04 (destructive, no dry run), 03, 06, 08, 09, 10, or 05 without -Plan, without the user's go.
- Only one packet at a time may touch the live Hermes install, whatever the parallel cap (3) [U7].
- Never patch Hermes's own Python source; drive Hermes through its CLI (see CLAUDE.md).
- No AI co-author trailer or attribution line in any commit [U16].
- A row reaches `done` only with a standing verdict from the role its bar names (2.5).
- Verdicts are written only by `tools/dispatch.py verify`; rows only by `tools/status.py`.
- The profile, risk paths, hooks, agent/skill sources, settings, AGENTS.md and CLAUDE.md are high-risk:
  only the lead changes them, with the user's approval recorded as a U<n> [U21].
- Wait list at every level: a release; a deploy or a push/merge that triggers one; data deletion; a force-push;
  a message to anyone outside the run; an external pull; any change to a high-risk file.

## Working conventions
- Branches: `factory-install` for setup, `factory-rehearsal/*` for rehearsals [U23]; packet branches `<item>-r<row>`.
- Shell: absolute paths and `git -C <dir>`; scripts written with the file tool; keep line endings (mostly CRLF).
- Every PowerShell script starts with `#requires -Version 5.1` and comment-based help; root-cause traces go in .DESCRIPTION.
- Native command checks under `$ErrorActionPreference = "Stop"` need try/catch, not just an exit-code check.
- Model classes: strong `claude-opus-5-5` (lead), standard `claude-sonnet-5-5`, cheap `claude-haiku-5-5` [U17].

## Tools
- Claude Code only [U4]. Agents: `hs-dev`, `hs-runner`, `hs-verifier` (sources in `tools/agents/`).
- Skills: `hs-lead`, `verify-hermessetup` (sources in `tools/skills/`).
- Enforcement: `tools/hooks/guard.py` (PreToolUse), `tools/hooks/stop_guard.py` (Stop/SubagentStop),
  git `commit-msg` hook, `.claude/settings.json` permissions, CI job `factory-gate`.
