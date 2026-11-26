# Decisions

| id | date | decision | user's words |
|---|---|---|---|
| U1 | 2026-11-23 | Install mode: fresh (no bootstrap factory or homegrown setup found) | "fresh" |
| U2 | 2026-11-23 | Project: hermessetup - PowerShell scripts that install, back up, restore and update Hermes Agent on this Windows machine, plus its personal MCP integrations | "keep this" |
| U3 | 2026-11-23 | Agent prefix: hs | "yes" |
| U4 | 2026-11-23 | Tools: Claude Code only | "Claude Code only" |
| U5 | 2026-11-23 | Remote GitHub; new .github/workflows/factory-gate.yml on windows-latest; blocking once the user protects main | "A" |
| U6 | 2026-11-23 | CLAUDE.md stays tracked; only additions, after a diff | "tracked" |
| U7 | 2026-11-23 | Machine: Windows 11 Home, 32 GB RAM, max 3 parallel agents; one packet at a time on the live install | "3" |
| U8 | 2026-11-23 | Lifetime: kept | "kept" |
| U9 | 2026-11-23 | Exposure: internal (note: the GitHub repo is public) | "internal" |
| U10 | 2026-11-23 | Data: internal metadata | "internal" |
| U11 | 2026-11-23 | Transactions: internal writes -> tier T2 | "internal" |
| U12 | 2026-11-23 | Availability: best effort | "best effort" |
| U13 | 2026-11-23 | Regulation: none | "none" |
| U14 | 2026-11-23 | Autonomy ceiling A3 (working level starts A0; T2 caps A2 off / A1 on risk paths) | "a3" |
| U15 | 2026-11-23 | No reference docs; this prompt alone | "prompt alone" |
| U16 | 2026-11-23 | AI co-author trailer: never (also the user's global rule) | default, matches global CLAUDE.md |
| U17 | 2026-11-23 | Models: strong claude-opus-5-5, standard claude-sonnet-5-5, cheap claude-haiku-5-5; dev uses standard for 02/04/05, olk-bulk-mcp.py, chrome-mcp/ | "proceed" |
| U18 | 2026-11-23 | A2 needs 10 packets at A1, first-time-right >= 80%, no escaped defect, every verdict standing at merge | "ok" |
| U19 | 2026-11-23 | Extension triggers as proposed; X5 met, first after Phase 3 | "proceed" |
| U20 | 2026-11-23 | Allowed read-only fetches of Claude Code and GitHub docs (rule 4) | "yes" |
| U21 | 2026-11-23 | Risk paths as proposed (config/risk-paths.yaml) | "ok, proceed" |
| U22 | 2026-11-23 | Quiet-point exceptions accepted: uncommitted notes-mcp-expertassist.md, untracked fastnav/ - never staged or touched | "b" |
| U23 | 2026-11-23 | Branches: factory-install, factory-rehearsal/* | "go" |
