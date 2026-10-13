# Vendored copy

Upstream:  https://github.com/Mehmoodqureshi/chrome-mcp (MIT, see LICENSE)
Branch:    main

<!-- machine-readable: read by ../07-check-chrome-mcp-upstream.ps1; update on every re-sync -->
UPSTREAM_COMMIT=54bf9765bcd519d30cd3be50369bd626698c3d6c
UPSTREAM_VERSION=0.9.22
SYNCED_ON=2026-10-13

## Local changes

None yet. Record every edit to vendored files here (file + why), so a re-sync
can re-apply or drop them deliberately.

## Notes

Upstream telemetry (PostHog) and auto-update are on by default; run with
`--no-telemetry --no-auto-update`.

## Re-syncing

`.\07-check-chrome-mcp-upstream.ps1` shows whether upstream moved and lists the
new commits/CHANGELOG. To re-sync: clone upstream to a scratch dir, diff against
`chrome-mcp/`, apply the wanted changes, then update the three fields above.
