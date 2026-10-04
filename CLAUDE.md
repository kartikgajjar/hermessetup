# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository.

## What this is

A **separate, independent git repo** (github.com/kartikgajjar/hermessetup), nested inside the
parent `C:\LocalCode\ai` repo (which has its own unrelated CLAUDE.md for a different project,
TurboQuant/llama-server). The parent repo does not track this directory — it shows as `?? hermes/`
in the parent's `git status` by design. Always `cd` here / use `git -C hermes` for anything in this
directory; never assume the parent repo's git state applies.

PowerShell scripts that install, back up, restore, and update **Hermes Agent**
(hermes-agent.nousresearch.com) — a separate AI-agent product from Claude Code — on this Windows
machine, plus two personal MCP integrations and operational knowledge accumulated from real,
hands-on debugging. See `README.md` for the script table and the clean-reinstall sequence; this
file is about the non-obvious things found by actually tracing failures, not what the scripts do on
their face.

## Core workflow order

```
.\03-backup-hermes-state.ps1          # snapshot state to OneDrive first
.\01-cleanup-hermes.ps1 -Force        # wipe
.\02-install-hermes-secure.ps1        # fresh install + hardened baseline
.\04-restore-hermes-state.ps1         # restore state, packages, gateway
```

`.\05-update-hermes.ps1` is the normal day-to-day updater (not a reinstall) — prefer it over raw
`hermes update` even for a quick check; it self-heals two real gaps a bare `hermes update` leaves
behind (below). `.\06-reap-hermes-containers.ps1 -Install` is a one-time setup step (registers a
scheduled task); you don't run it repeatedly.

## Verifying changes (there is no test suite)

These scripts mutate the real, live Hermes install — there's no sandbox copy. Safe checks, in order:

```powershell
# Parse-only syntax check (no execution)
$null = [System.Management.Automation.Language.Parser]::ParseFile("$PWD\05-update-hermes.ps1", [ref]$null, [ref]$errs); $errs

.\05-update-hermes.ps1 -Plan     # read-only: runs `hermes update --plan` once, no mutation
.\06-reap-hermes-containers.ps1  # a single sweep; -Now reaps all idle containers immediately
python -m py_compile olk-bulk-mcp.py
```

`olk-bulk`'s tools default to `dry_run=true` — exercise them that way first. Destructive scripts
(`01` cleanup, `02` install, `04` restore) have no dry-run mode; don't run them just to "test" an
edit without the user's go-ahead.

## Script conventions

- Every script starts with `#requires -Version 5.1` and comment-based help (`.SYNOPSIS` /
  `.DESCRIPTION` / `.PARAMETER`). The `.DESCRIPTION` blocks are where root-cause traces live — dated,
  with file/function/line references into Hermes's own source at `%LOCALAPPDATA%\hermes\hermes-agent`.
  When a fix is based on reading Hermes source, record the trace there the same way.
- Wrap Hermes as a black box: drive it through the `hermes` CLI (`hermes config set` one key at a
  time in `02`, never overwriting `config.yaml` wholesale) and never patch Hermes's Python source —
  the next `hermes update` overwrites it.
- `05-update-hermes.ps1` has a documented invariant: it **never calls `Stop-Process` and never
  deletes/moves any Hermes file** (e.g. `gateway.lock`, `.hermes-update-in-progress`). It handles
  two upstream failures by waiting/retrying instead: a transient `gateway.lock` sharing-violation
  race in Hermes's gateway discovery (bounded retry), and exit code 2 (another update holds the
  update lock — Hermes's own `update_lock.py` self-heals stale markers after 20 min, so wait, don't
  kill). Adding the leftover-`python.exe` pre-flight described below means deliberately breaking
  this invariant — confirm with the user and update the header's statement if you do.

## Gotchas confirmed by direct investigation (not guesses)

**Before running `hermes update`, check for leftover `python.exe` processes.** The real root cause
of a confirmed update failure here: 40+ accumulated `hermes`-owned `python.exe` processes (from
repeated gateway restarts/test scripts) kept the bundled interpreter's DLLs open, so Windows
refused to rename the directory during a Python version swap (`Access is denied:
...\DLLs\libcrypto-3-x64.dll`). `hermes update` compounds this: when it fails mid-swap, its
attempt to resume the paused gateway afterward can *also* fail, leaving the gateway down. Fix:
stop all hermes-owned `python.exe`/`hermes.exe` processes first, then retry. This is exactly what a
pre-flight check in `05-update-hermes.ps1` should do but does not yet — a known, real gap, not
hypothetical.

**`hermes update`'s own stdout is effectively invisible when piped/captured** (Python's non-TTY
stdout buffering). `05-update-hermes.ps1`'s `Invoke-HermesUpdateWithProgress` works around this by
tailing `logs\update.log` live instead of trusting captured stdout for display — but still uses the
real captured `$text`/exit code for retry classification. If you touch this function again: the
three bugs already found and fixed there are a checklist for what *not* to reintroduce —
(1) `$UpdateArgs` must allow an empty array (`[AllowEmptyCollection()]`), (2) the literal `update`
subcommand must be hardcoded inside the function, never folded into the args parameter (dropping it
silently launches the interactive TUI chat instead, which crashes instantly inside `Start-Job`'s
console-less environment — this exact regression shipped and went undetected for a while because
nothing printed the raw error), (3) line-counting for the tail must use the same method
(`@(Get-Content ...).Count`) in both the baseline and the comparison — `Measure-Object -Line`
silently undercounts blank lines and will make stale log content look "new" again. After any change
to this function, actually run a real update end-to-end and watch it reach "Hermes update
finished." — a clean syntax check and an isolated unit test are not sufficient; the Start-Job/native
command interaction here is exactly where bugs hid in practice.

**Native command calls under `$ErrorActionPreference = "Stop"` can throw on expected non-zero exits
and stderr.** `*> $null` suppresses *display* but not PowerShell's own error escalation for a
failing external command. `Test-McpPythonPackage` (checks `python -c "import mcp"`) and the `uv pip
install` reinstall call both needed explicit `try/catch` for this — a legitimately-failing native
command (missing module, pip warning on stderr) crashed the whole script instead of being handled
by the surrounding `if ($LASTEXITCODE ...)` logic. Any new native-command check added to this repo's
scripts needs the same treatment, not just an exit-code check.

**`hermes-agent` runs on a bundled, versioned, Windows-native standalone Python** at
`%LOCALAPPDATA%\hermes\tools\python-<version>\python.exe` (no venv/`pyvenv.cfg`). `hermes update`
only reinstalls its *base* dependencies — optional extras like `mcp` (needed for any MCP server to
work) are not part of that set and get silently stranded after an update or fresh install. Fix:
`uv pip install -e ".[mcp]" --python "<that python.exe>"` from inside `hermes-agent\`.
`05-update-hermes.ps1` already self-heals this automatically post-update.

## MCP servers configured here (personal, optional — see `notes-mcp-*.md` for full detail)

- **expertassist** — content-discovery service. Auth gotcha: needs a raw `x-api-key: <key>`
  header, not `Authorization: Bearer <key>` (the client-default olk would guess wrong).
- **olk** — Outlook (mail/calendar/contacts) via Microsoft Graph, OAuth. `olk mcp` (the MCP-serving
  subcommand) requires a recent build — `go install github.com/rlrghb/olkcli/cmd/olk@latest`.
  `olk auth login --account <email>` does **not** restrict which Microsoft account the device-code
  flow actually authenticates — whoever completes the code in the browser picks the account;
  always verify with `olk auth status --account <email>` afterward, don't trust the flag.
  Write/destructive tools (`mail_move`, `mail_delete`) are opt-in via
  `--allow-write=mail_move --allow-destructive=mail_delete` on re-registration, and still gated by
  Hermes's own per-call approval (`approvals.mode: manual`) since neither is `readOnlyHint: true`.
- **olk-bulk** (`olk-bulk-mcp.py`, this repo) — `olk`'s move/delete only ever act on one message ID;
  this wraps repeated `olk mail search` + `move`/`delete` behind `bulk_move_messages` /
  `bulk_delete_messages` (KQL query → acts on every match, `dry_run` default, `top` capped at 200).
  `olk mail delete` **requires `--force`** or it fails cleanly (not silently) with exit 1 — a real
  bug here (missing flag) meant an entire batch of real deletes reported `deleted_count: 0,
  failed_count: 50` for a while; verify a `dry_run=false` call's `deleted_count` in its JSON result,
  never assume success from "it didn't error." Also: `_olk()`'s subprocess capture needs explicit
  `encoding="utf-8", errors="replace"` — the Windows default (cp1252) crashes on ordinary mail
  content (curly quotes, emoji). Registered with an **absolute, version-pinned** path to the bundled
  interpreter (`hermes mcp add --command` stores it verbatim, never re-resolves) — `05-update-hermes.ps1`
  detects and repoints this automatically if a Hermes update bumps the Python version.

## Model routing

- `model.default` / `model.provider` in `config.yaml` is the **global** default for every platform.
  Live-switchable from any chat (including Discord) with the real slash command `/model <name>`
  (session-scoped) or `/model <name> --global` (persists to config.yaml) — this is a gateway-level
  command handled before the LLM ever sees it, not something the agent can invoke on its own from
  inside a turn ("I can't switch models myself" is Hermes being accurate about that specific
  limitation, not a bug).
- `platforms.discord.channel_overrides.<chat_id>` can give Discord specifically a different default
  than other platforms/CLI, independent of the global default. Precedence: session override >
  channel override > global config.
- The `auxiliary.*` config block (title generation, mail scoring, approval classification, etc.) is
  *separate* from the main model and defaults to `provider: auto` (= inherit main model) — meaning
  high-volume simple tasks silently ran on the expensive main model until routed explicitly. 12 of
  these blocks are routed to `claude-haiku-4-5-20251001` here; `review`, `kanban_decomposer`, and
  `compression` are deliberately left on the main model per hermes-agent's own source comments
  flagging those as more capability-sensitive. See `notes-auxiliary-models.md`.

## Known-broken: native email platform, on this account type

`platforms.email` (IMAP/SMTP via `.env` `EMAIL_*` vars) cannot work against a Hotmail/Outlook.com
consumer account — Microsoft deprecated Basic Authentication for these account types account-wide;
the adapter only implements `imap.login(address, password)`, no OAuth path exists in it. Confirmed
via `logs/gateway.log`: `AUTHENTICATE failed. Provided authentication mechanism is not supported.`,
retrying forever. Disabled here via `platforms.email.enabled: false` (an explicit override that
beats env-var auto-enable — confirmed in `gateway/config_env.py`). Use the `olk` MCP server for
email instead; it's OAuth-based and already works.

## Known-broken: `browser.backend: browser-use`, on Windows

Crashes with `ModuleNotFoundError: No module named 'fcntl'`. Not a Docker-sandbox limitation
despite Hermes's own error text suggesting that — confirmed `tools/browser_use_cli.py` launches the
browser subprocess with Windows-native `subprocess.STARTUPINFO`/`CREATE_NEW_PROCESS_GROUP` flags,
i.e. it runs directly on the Windows host, never touching the Docker terminal sandbox at all. Some
dependency in the `browser-use`/Playwright chain imports the POSIX-only `fcntl` module without a
platform guard. Workaround in use: launch a separate debug Chrome profile
(`chrome.exe --remote-debugging-port=9222 --user-data-dir=<separate dir>`, must be a genuinely
separate profile or an already-running Chrome just silently ignores the new flags) and point
`browser.cdp_url` at it (`http://localhost:9222`) so Hermes attaches via CDP instead of spawning its
own subprocess. Untested alternative: `browser.backend: off` uses a completely different,
Windows-native implementation (`tools/browser_tool.py`, backed by a dedicated
`tools\agent-browser-*-win32-x64\` binary) that may not hit this bug at all.

## Backup/restore coverage — what's deliberately NOT included, and why that's usually fine

`03`/`04` back up `config.yaml`, `.env`, `auth.json`, memories, skills, sessions, databases, cron,
platform pairings, plugins — deliberately excluding `tools\`, `installs\`, `node\`, `hermes-agent\`,
`cache\` as "large code/runtime, not state." Two real consequences, both already self-healed by
`05-update-hermes.ps1` rather than needing manual fixing after a restore:
1. The `mcp` Python extra (see above) isn't state — a fresh install/restore won't have it.
2. `olk-bulk`'s pinned interpreter path (see above) can go stale across a reinstall that lands on a
   different Python version.

Not covered at all, and not a gap: `olk`'s own OAuth credentials live in **Windows Credential
Manager** (confirmed — no token file exists anywhere under its config dir, just metadata), entirely
outside Hermes's state and untouched by any Hermes reinstall/backup/restore cycle.

## Sandbox persistence: `terminal.container_persistent: true` is deliberate

With `false`, every gateway message/session gets its own container with tmpfs `/root` and
`/workspace` — files a Discord session writes (e.g. "saved to /root/report.csv") vanish when the
container is reaped, and no other session can see them. With `true` (set by `02`/`04`), CLI and
default-profile gateway sessions share ONE container whose `/root` and `/workspace` are bind-mounted
from `%LOCALAPPDATA%\hermes\sandboxes\docker\default\{home,workspace}` (traced in
`tools\terminal_tool.py` `_resolve_container_task_id` and `tools\environments\docker.py`
`_mount_args`). `sandboxes\` is therefore in `03`/`04`'s `$StateDirs`. `06`'s reaper may still
remove the idle shared container — harmless, the bind-mounted files survive and Hermes recreates it.
Discord's conversational memory (`memory`, `session_search` toolsets) is separate and was already
enabled in `platform_toolsets.discord`.

Data a skill depends on (e.g. `email-culprit-database`'s CSV) belongs in the skill's own
`assets/`/`references/` dir under `%LOCALAPPDATA%\hermes\skills\`, not in `/root`. Skills are
bind-mounted **read-only** into the container (`/root/.hermes/skills`), so the agent can't write
there from the terminal — it must use `skill_manage(action='write_file', ...)`, which runs on the
host. A file a skill merely *describes* but that was only ever written inside a container is how
that CSV got lost. Every past tool call is stored in `state.db` (`messages.tool_calls`), which is
how it was recovered.

## Gateway restart needed after config changes — except when it isn't

Most `config.yaml` changes (MCP server registration/changes, `model.default`) are read once at
gateway-process-start and need `wscript.exe gateway-service\Hermes_Gateway.vbs` (restart) or
`/reload-mcp` (MCP only, in an open session) to take effect. `SOUL.md` is the exception — confirmed
via `agent/prompt_builder.py`'s `load_soul_md()`: no caching, read fresh on every turn's system
prompt build. Don't restart the gateway just for a SOUL.md edit; do restart for anything in
`config.yaml`.
