# F-chrome-mcp

- What: vendored chrome-mcp (`chrome-mcp/`), registered as `node chrome-mcp/dist/src/cli.js --no-auto-update --no-telemetry`.
- Drive: `npm --prefix chrome-mcp test` (clean, build, extension build, node --test; ~15 s), then
  `python tools/mcp_probe.py --expect tabs_list,snapshot,get_text -- node chrome-mcp/dist/src/cli.js --no-auto-update --no-telemetry`
- End state: suite `# fail 0` (baseline 2026-11-23: 371 tests, 369 pass, 2 skipped); probe exit 0 with 21 tools.
- Gotchas: driving a page needs Chrome running and the extension paired: not part of the automated drive.
  `npm test` rebuilds `dist/` (gitignored).
