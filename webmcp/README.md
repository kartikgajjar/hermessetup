# webmcp — multi-site, multi-account browser-session MCP

Generalises [stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server)
(Apache-2.0; architecture and browser rules borrowed, code written fresh): a real patchright
browser with a persistent, hand-logged-in profile, driven by MCP tools that read page `innerText`.
One server, many sites; each site is one file in `src/webmcp/sites/`. Each site can hold **several
logins** ("accounts").

Sites now: `tipranks`, `etrade` (read-only; no order tools exist), `facebook` (your saved reels only), `substack` (read posts, inbox, saved list, search). **Their URLs and logged-in
heuristics are uncalibrated** — see "First login" below.

## Setup

```powershell
cd <repo>\webmcp
uv sync
uv run patchright install chromium      # only needed if real Chrome isn't installed (default uses Chrome)

# log in by hand, once per account (headed window; 2FA/captcha fine). Labels are yours.
uv run webmcp --login tipranks --account personal
uv run webmcp --login etrade   --account brokerage
uv run webmcp --login etrade   --account ira

uv run webmcp --status --check          # which sessions are still alive
```

Register with Hermes (see `../notes-mcp-webmcp.md`), then `hermes gateway restart`.

## Accounts

* Identity is `(site, account)`; sessions live in `%USERPROFILE%\.webmcp\<site>\<account>\profile`
  (override root with `WEBMCP_HOME`). Treat that tree like passwords.
* Every tool takes optional `account=`. With exactly one account it is implied; with several, omitting it
  returns an error listing the labels. `webmcp_accounts` lists what exists.
* One browser per account, calls on the same account serialise, different accounts can run in parallel
  up to `max_browsers` (default 2). Idle browsers close after 600 s.
* An OS file lock per profile stops `--login` and the server (or two servers) opening one profile at once.

## First login: calibrate a site

The adapters were written without a logged-in session to test against. After logging in:

```powershell
uv run webmcp --probe tipranks --account personal --url https://www.tipranks.com/stocks/aapl
```

It prints final URL, `is_logged_in`, body length and the first 800 characters. Fix `is_logged_in`,
`auth_url_markers` and the page URLs in `sites/<site>.py` until it reports correctly, and re-run
`--probe` against a logged-out profile to confirm it flips.

## Config (`%USERPROFILE%\.webmcp\config.json`, optional)

```json
{ "max_browsers": 2, "idle_seconds": 600, "channel": "chrome",
  "sites": { "etrade": { "tools": ["etrade_accounts"] }, "tipranks": { "enabled": true } } }
```

`tools`, when present, is an allowlist for that site; `"enabled": false` hides a site entirely.
`--sites tipranks` on the command line restricts one server instance.

## Adding a site

Copy `sites/tipranks.py`: subclass `SiteAdapter` (hosts, login_url, home_url, `auth_url_markers`,
`is_logged_in`), declare tools in `register()` with `svc.read(site, account, url)`, export `ADAPTER`.
Rules (from the upstream project): decide auth from URL then structure, not button text; read
`innerText`, avoid class-name selectors; never set a user agent; keep tools read-only unless a
specific write tool is opted in.
