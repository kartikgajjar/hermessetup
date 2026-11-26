# Threat model (T2) - hermessetup factory

| asset | threat | control |
|---|---|---|
| Live Hermes install (%LOCALAPPDATA%\hermes) | an agent runs a destructive script (01/02/04) or mutates config | ask rules on the scripts and hermes config/gateway/mcp; agent rule; wait list |
| Personal mail (olk, olk-bulk) | an agent calls a mail tool for real | olk on the ask list; verify skill drives tools/list only; dry_run defaults |
| Secrets (.env, tokens, Credential Manager) | committed to the public repo | gate secret scan; risk paths; secrets live outside the repo |
| Personal data in docs/logs | written into tracked files of a public repo | gate privacy scan (emails, phones) |
| Agent config (hooks, agents, skills, settings) | an agent weakens its own guardrails | guard blocks agent edits to high-risk files; settings deny; approval U<n> required by the gate |
| Verdicts | forged or stale | only dispatch.py writes them; schema; product fingerprint; gate recomputes |
| main branch | direct push bypassing the gate | branch protection (user sets it) + required factory-gate; guard on raw push from main |
| Guard itself | text-matching bypass (e.g. an alias or a script) | stated limit: the guard stops mistakes, not a determined bypass; CI gate is the boundary |
