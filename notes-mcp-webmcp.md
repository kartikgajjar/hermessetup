# webmcp — setup notes

> Personal/optional: this repo's own multi-site browser-session MCP (`webmcp/`), generalising the
> LinkedIn MCP pattern (see `notes-mcp-linkedin.md`). Nothing in `01`/`02`/`04`/`05` requires it.

Code, usage and the add-a-site recipe: `webmcp/README.md`. Sites: `tipranks`, `etrade` (read-only), `facebook` (saved reels only: `facebook_saved_reels`, `facebook_reel`; Meta is the most likely to checkpoint the account — few scrolls, own account).

## Registering (placeholders: `<repo>` = this repo's checkout, `<uv>` = absolute path to `uv.exe`)

Account labels are yours to choose; they are never hardcoded. The exact commands and labels used on
this machine are in `notes-mcp-webmcp.local.md` (untracked, see `.gitignore`).

```powershell
uv run --project <repo>\webmcp webmcp --login facebook --account <label>      # once per account
"Y" | hermes mcp add webmcp --command <uv> --connect-timeout 120 --args run --project <repo>\webmcp webmcp --sites facebook,tipranks,substack
hermes gateway restart
```

Facebook's adapter is calibrated against the real Saved page (reels appear as `/reel/<id>` links; card text
= caption/author/collection). To add TipRanks/E*Trade later: log in, `--probe`, then re-register without
`--sites facebook` (or with a comma list) and restart the gateway.

`uv` must be on the gateway's PATH (otherwise give `--command` the absolute `uv.exe`; Hermes ships one at
`%LOCALAPPDATA%\hermes\tools\uv-*\uv.exe`, but its version dir changes on update). It runs from this
repo's own `.venv`, so the `mcp` extra / bundled-Python repointing issues in `05` don't apply.

## Things to know

* **Sessions** live in `%USERPROFILE%\.webmcp\<site>\<account>\profile` — outside Hermes state, not in
  `03`/`04`'s backups, untouched by a Hermes reinstall. Credential-equivalent; never commit or share.
* **Multi-account**: pass `account=` on any tool; implied when a site has only one. Re-login one account
  with `webmcp --login <site> --account <label>` (server must not have that profile open — it closes idle
  browsers after 10 min, or restart the gateway).
* **No `trust:` key** on registration => Hermes runs these tools unprompted (same as olk/linkedin).
  Guard is in code: tools are `readOnlyHint`, E*Trade exposes only a fixed page allowlist, `web_read`
  refuses URLs outside the site's own domain, and `config.json` can allowlist tools per site.
* **E*Trade** logs out on short idle; expect "session expired" results rather than silent retries.
  Prefer E*Trade's official OAuth API for balances/positions once page text isn't enough.
* **Risk**: both sites' terms restrict automated access; accounts can be flagged. Low volume, personal use.
* Adapters are **uncalibrated** until a `--probe` against a real login has been done (README).
