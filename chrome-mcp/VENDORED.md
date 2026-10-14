# Vendored copy

Upstream:  https://github.com/Mehmoodqureshi/chrome-mcp (MIT, see LICENSE)
Branch:    main

<!-- machine-readable: read by ../07-check-chrome-mcp-upstream.ps1; update on every re-sync -->
UPSTREAM_COMMIT=54bf9765bcd519d30cd3be50369bd626698c3d6c
UPSTREAM_VERSION=0.9.22
SYNCED_ON=2026-10-13

## Local changes

Record every edit to vendored files here (file + why), so a re-sync can re-apply or
drop them deliberately.

- 2026-10-13 `src/mcp/helpers.ts` (+ tests in `test/helpers.test.ts`) -- reported upstream as
  https://github.com/Mehmoodqureshi/chrome-mcp/issues/7 (check it on every re-sync: once fixed
  there, drop this patch and take upstream's version): `extractLinks` now
  catches the thrown `POLICY_DENIED` for a disabled `eval` and falls back to parsing
  `getHtml` (it already had that fallback, but checked a `{ok:false}` result that is never
  returned, so `extract_links` failed outright in the default safe-mode). The fallback now
  also resolves relative hrefs against the tab URL, decodes `&amp;` etc., drops `#`/`javascript:`
  links, and honors `sameOriginOnly`. Only an eval denial falls back; other errors propagate.
- 2026-10-13 `src/mcp/tools.ts` (same upstream issue, "Minor, related" section): `get_text` returns `savedTo` (host path of the full,
  uncapped read) when its output was truncated, and `read_as_markdown`'s truncation notice
  names that path instead of "the task's results/ dir". The path was previously discarded.
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
