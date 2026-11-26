# Feature map (verify-hermessetup)

User-facing features of this repo, how to reach and drive each, and the end state that proves it.
`docs/path-map.yaml` ties product paths to these ids; `status.py ready` shows the tie for a task.
Never weaken a journey to make it pass.

| id | feature | drive | live install touched? |
|---|---|---|---|
| F-olk-bulk | olk-bulk MCP server (bulk mail move/delete, sender policy) | mcp_probe over stdio | no (tools/list only) |
| F-chrome-mcp | vendored chrome-mcp server | suite + mcp_probe over stdio | no |
| F-scripts | the 01-10 scripts' contract: parse + help block | check.py --help-render | no |
| F-update-plan | 05-update-hermes.ps1 -Plan | runs hermes update --plan | reads Hermes upstream: wait list |
