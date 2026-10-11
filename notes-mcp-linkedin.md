# LinkedIn MCP — setup notes

> Personal/optional: documents one machine's MCP integration. Nothing in `01`/`02`/`04`/`05`
> requires it.

[stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server)
(PyPI `mcp-server-linkedin`), 20 tools: profiles, messaging, companies, jobs, people/post search, feed.
It drives a real browser (patchright) using a saved LinkedIn session.

## Setup (done 2026-10-11)

```powershell
# 1. one-time interactive login (browser opens; may ask app confirm / captcha)
$env:UV_HTTP_TIMEOUT=300; uvx mcp-server-linkedin@4.27.0 --login

# 2. register (pipe Y: `mcp add` prompts "Enable all N tools?")
"Y" | hermes mcp add linkedin --command uvx --env UV_HTTP_TIMEOUT=300 --connect-timeout 120 --args mcp-server-linkedin@4.27.0

# 3. apply
hermes gateway restart
```

## Things to know

- **Version is pinned** (4.27.0), deliberately not `@latest`: upstream's `@latest` auto-updates, so a
  gateway restart would run unreviewed code. Bump by re-running `hermes mcp add` (or editing the
  version in `config.yaml`) after checking PyPI/changelog, then restart the gateway.
- **Session lives outside Hermes state**: `%USERPROFILE%\.linkedin-mcp\profile\` (browser cache in
  `patchright-browsers\`). Not covered by `03`/`04` and untouched by a Hermes reinstall; the
  registration itself rides along in `config.yaml`. Sessions expire — redo `--login`
  (`--logout` clears it).
- **All 20 tools enabled, including `send_message` and `connect_with_person`.** No `trust:` key, so
  Hermes runs them unprompted (see olk note). Chosen deliberately; the only guards are code/SOUL.md.
- **Risk**: LinkedIn's User Agreement bars automated access; the account can be restricted. Keep
  volume low, personal use only.
- Windows: `DLL load failed ... _greenlet` → install the VC++ Redistributable or greenlet >= 3.5.5.
