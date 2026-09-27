# olk (Outlook) MCP — setup notes

> Personal/optional: this documents one machine's specific MCP integration.
> Nothing in `01`/`02`/`04`/`05` requires or assumes it — a fresh Hermes
> install has zero MCP servers configured, and stays that way unless you
> deliberately add one. Only relevant if you're setting up Outlook access
> specifically.

[olk](https://github.com/rlrghb/olkcli) is a Go CLI for Outlook (mail/calendar/
contacts/todo/drive) via Microsoft Graph. `olk mcp` runs it as a stdio MCP server.
Registered with Hermes as `olk`, exposing 40 tools, all read-only by default (write/
send/destructive tools are opt-in via `--allow-write` / `--allow-send` /
`--allow-destructive`, which we did not pass).

## Gotcha 1: `olk mcp` requires a recent build

The version installed via `go install github.com/rlrghb/olkcli/cmd/olk@latest` some
time ago (reporting `olk dev (commit: none, built: unknown)`) had **no `mcp`
subcommand at all** (`olk mcp --help` → `unexpected argument mcp`). Re-running
`go install github.com/rlrghb/olkcli/cmd/olk@latest` pulled a newer build (v1.15.1,
pulling in `github.com/modelcontextprotocol/go-sdk`) that added it. If `olk mcp` is
missing, update first.

## Gotcha 2: `--account` on `olk auth login` does not restrict the browser sign-in

`olk auth login --account <email>` is only a label for olk's own bookkeeping — it does
**not** constrain which Microsoft account the device-code flow actually signs into.
Whoever completes the code in the browser picks the account, and olk just records
whatever came back. On this machine, running `olk auth login --account
kartik.gajjar@hotmail.com` actually authenticated a *different* stored account
(`ekartka@hotmail.com`) because that's the one signed into the browser that completed
the code. Check `olk auth status --account <email>` / `olk whoami --account <email>`
after login to confirm which account actually got the fresh token — don't trust the
`--account` flag you passed to `login`.

Currently authenticated for Hermes's `olk` integration: **ekartka@hotmail.com**.
`kartik.gajjar@hotmail.com`'s token remains expired (unchanged) — re-run `olk auth
login --account kartik.gajjar@hotmail.com` and sign into *that* account specifically
in the browser if that mailbox is needed instead.

## Gotcha 3: hermes-agent needs the `mcp` Python extra installed manually

hermes-agent runs on a bundled standalone Python interpreter at
`%LOCALAPPDATA%\hermes\tools\python-<version>\python.exe` (no venv/`pyvenv.cfg` —
just `-I` isolated mode with `hermes-agent\` on `sys.path`). That interpreter had
**zero site-packages installed** and was missing the `mcp` package entirely
(`ModuleNotFoundError: No module named 'mcp'`) until running:

```
uv pip install -e ".[mcp]" --python "<path to bundled python.exe>"
```

from inside `hermes-agent\` (pyproject.toml has an `mcp` extra:
`mcp==2.0.0`, `httpx2==2.7.0`, etc.). This resolved and installed hermes-agent's
*entire* dependency set into that interpreter, not just the mcp-specific packages —
apparently normal, `hermes --help` / `hermes mcp list` worked fine before and after.

**This is not backed up or restored by 03/04** — `tools\` is explicitly excluded as
"large code/runtime, not state" (see `03-backup-hermes-state.ps1`'s header comment).
A fresh reinstall via `02-install-hermes-secure.ps1` gets a brand-new `tools\`
interpreter, which means **this `uv pip install -e ".[mcp]"` step must be re-run after
every fresh reinstall**, even though `mcp_servers` config itself (in `config.yaml`,
which *is* backed up) will already list `expertassist`/`olk` and look configured. If
`hermes mcp test <name>` fails right after a reinstall with an import error, this is
why — re-run the `uv pip install` command above before troubleshooting anything else.

## Registering with Hermes

```
hermes mcp add olk --command olk --args mcp --account ekartka@hotmail.com
```

Prompts to enable all 40 discovered tools — answer `y`. Verify with `hermes mcp test
olk`.

## Write/destructive tools are opt-in, and off by default

`olk mcp` only exposes read tools unless you explicitly opt in with `--allow-write=...`
/ `--allow-send=...` / `--allow-destructive=...`. To let Hermes move or delete mail:

```
hermes mcp add olk --command olk --args mcp --account ekartka@hotmail.com \
  --allow-write=mail_move --allow-destructive=mail_delete
```

(re-adding an existing server name prompts `Overwrite? [y/N]` before the usual
`Enable all N tools?` prompt — pipe two `y`s if scripting this non-interactively, or
just answer both interactively.) `mail_delete` is Graph's standard delete (moves to
Deleted Items, not a hard purge). Hermes's own approval gate
(`tools/mcp_tool.py`: a tool call needs manual approval unless its `readOnlyHint`
annotation is exactly `True` at discovery) still requires per-call approval under
`approvals.mode: manual` for both — enabling them here doesn't bypass that.

## Bulk move/delete: `olk mail move`/`mail delete` are single-message only

Neither has a bulk mode — each takes exactly one message ID. For a "move/delete every
message matching X" capability (search once, act on all matches, in one tool call
instead of N), see `hermes/olk-bulk-mcp.py`: a small stdio MCP server that wraps
repeated `olk mail search` + `mail move`/`mail delete` calls behind two tools,
`bulk_move_messages` and `bulk_delete_messages`. Registered separately:

```
hermes mcp add olk-bulk --command "<hermes>\tools\python-<version>\python.exe" \
  --args "C:\LocalCode\ai\hermes\olk-bulk-mcp.py" --account ekartka@hotmail.com
```

Both tools default to `dry_run=true` (report matches without acting) and cap `top` at
200 per call, on top of the same manual-approval gate as above — two independent
safety layers before anything actually moves or deletes.

**Gotcha 4: the interpreter path is pinned, not resolved.** `hermes mcp add
--command` stores whatever path you give it verbatim in `config.yaml` — it's never
re-resolved later. Since the bundled interpreter's path is versioned
(`tools\python-<version>\python.exe`), a Hermes update/reinstall that bumps the
Python version silently strands `olk-bulk` pointing at a path that no longer exists.
`05-update-hermes.ps1`'s post-update MCP verification step now detects and
self-heals this (re-points `mcp_servers.olk-bulk.command` at whatever interpreter
actually exists after the update) — if you ever add another Python-based MCP server
by hand, give it the same treatment or expect this same failure mode.
