# expertassist MCP — setup notes

> Personal/optional: this documents one machine's specific MCP integration.
> Nothing in `01`/`02`/`04`/`05` requires or assumes it — a fresh Hermes
> install has zero MCP servers configured, and stays that way unless you
> deliberately add one. Only relevant if you're setting up this exact
> integration.

Content-discovery service (Reddit/HN/GitHub/YouTube tracking, transcripts, full-text
search) registered as a Hermes MCP server. 13 tools, all read/query-oriented
(add-source, list-sources, query-items, get-transcript, search-transcripts, etc.).

## The gotcha: auth header shape

The server requires a **raw `x-api-key: <key>` header** — not `Authorization: Bearer
<key>`. Hermes's `hermes mcp add --auth header` flow, and a naive first pass at
`config.yaml`, defaulted to the `Authorization: Bearer` shape and got a `401 Missing
x-api-key header` every time.

**This is a per-client quirk, not a server bug.** Any MCP client that defaults to
Bearer-style auth will trip over this exact same way — it's not specific to Hermes.
If you re-add this server (or add it to a different MCP client), the header must be
set explicitly:

```yaml
mcp_servers:
  expertassist:
    url: https://mcp.expertassist.work/mcp
    headers:
      x-api-key: ${MCP_EXPERTASSIST_API_KEY}
    enabled: true
```

The key itself lives in Hermes's `.env` as `MCP_EXPERTASSIST_API_KEY` — that value was
correct throughout; only the header name/shape was wrong.

## Registering from scratch

`hermes mcp add`'s interactive prompts (auth confirm, header name, header value) use a
raw-keypress prompt library that needs a real TTY — piping answers via stdin from a
non-interactive shell is unreliable (works partially, then hangs). Run it in an actual
terminal:

```
hermes mcp add expertassist --url https://mcp.expertassist.work/mcp --auth header
```

Answer: `y` (requires auth) → header name `x-api-key` → header value (your key).

Verify with `hermes mcp test expertassist` — should report `✓ Tools discovered: 13`.
