# Vendored copy

Upstream:  https://github.com/Mehmoodqureshi/mcp-browser-extension (MIT, see LICENSE; renamed from chrome-mcp 2026-10-15)
Local archive of the full upstream history: C:\LocalCode\archive\mcp-browser-extension.git (git clone --mirror, 2026-10-15; upstream may go commercial, every release to date is MIT)
Branch:    main

<!-- machine-readable: read by ../07-check-chrome-mcp-upstream.ps1; update on every re-sync -->
UPSTREAM_COMMIT=54bf9765bcd519d30cd3be50369bd626698c3d6c
UPSTREAM_VERSION=0.9.22
SYNCED_ON=2026-10-13
REVIEWED_THROUGH=3dc15806d029483345eb0af4f785a80f0d7f5ca6

## Local changes

Record every edit to vendored files here (file + why), so a re-sync can re-apply or
drop them deliberately.

- 2026-10-15 Cherry-picked upstream `3c6545f` (0.9.27, "Fixes #7"; our report
  https://github.com/Mehmoodqureshi/mcp-browser-extension/issues/7) onto the 0.9.22 base:
  `src/mcp/helpers.ts` (`extractLinks` falls back to parsing `getHtml` with htmlparser2 when eval is
  denied), `src/mcp/tools.ts` (`get_text`/`read_as_markdown` report `savedTo`), `src/executor/types.ts`
  (`getHtml` returns `frameId`), tests `test/helpers.test.ts` + `test/output-caps.test.ts`, and the
  `htmlparser2` dependency (`package.json`/lock). This REPLACES our own earlier 2026-10-13 patches of the
  same code, which were dropped. Not taken from that commit: the MCP SDK bump to ^1.32.1 (dependency
  security refresh; take it deliberately with a full test run). Verified: tsc clean, `npm test` 369 pass / 0 fail.
- DELIBERATELY NOT TAKEN (reviewed 2026-10-15, upstream 0.9.23 -> 0.9.27): the 0.9.24 **email-verification
  access gate** (`src/access.ts`, `requireAccess` on every browser tool; calls
  mcp-browser-extension.com/api/access, needs MCP elicitation, fails closed if the service is down). It
  would block unattended Hermes jobs and add a third-party dependency. Also skipped: `draw` tool, the rename,
  extension-identity/`extension doctor|repair` (0.9.23). Re-evaluate if upstream adds an opt-out; do NOT
  blindly re-sync. `REVIEWED_THROUGH` above makes `07` report only commits newer than 3dc1580.
- Not changed (judged already covered or not worth diverging): `get_text` already takes
  `maxBytes` (default 256 KB, up to 16 MB) and reports `truncated`/`totalBytes`; the
  `POLICY_DENIED` code is shared by site-allowlist and capability denials, but the message
  already names the flag (e.g. `--unsafe-enable-eval`).

## Notes

Upstream telemetry (PostHog) and auto-update are on by default; run with
`--no-telemetry --no-auto-update`.

## Re-syncing

`.\07-check-chrome-mcp-upstream.ps1` shows whether upstream moved and lists the
new commits/CHANGELOG. To re-sync: clone upstream to a scratch dir, diff against
`chrome-mcp/`, apply the wanted changes, then update the three fields above.
