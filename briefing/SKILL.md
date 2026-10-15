---
name: daily-briefing
description: Daily personal briefing (markets, work/tooling, career) built from the ExpertAssist feed, saved as HTML and emailed inline. Reads personal context from assets/context.md.
---

# Daily briefing

You produce ONE briefing per run. Optimise for a reader who gives it 3 minutes: decisions first,
detail after, nothing repeated.

## 0. Preflight (fail loudly)

1. `skill_view` this skill's `assets/context.md`. If any field still contains `<<...>>` or is empty,
   STOP. Send no briefing; reply "Context incomplete: <fields>". Never fall back to generic output.
2. Read `assets/state.json` (seen-item ids from earlier runs; may be empty on the first run).
3. Read `assets/template.html`. Use it as-is: same CSS, same section order, inline styles only.

## 1. Gather (use subagents for transcripts; synthesise only from their distilled notes)

- Videos from the last 36 h and discovery items since the last run (ExpertAssist). Query **per
  sourceType** (youtube, hackernews, reddit, github, arxiv, devto, linkedin, ...) — a single
  score-sorted query is dominated by HN/Reddit.
- **Market snapshot from data, not speech.** Fetch VIX, 10y yield, crude, DXY and the next 3
  scheduled macro events with chrome-mcp (open a new background tab, read, close it) or a data
  tool. Show each value with its timestamp and source. If a number cannot be fetched, print `n/a`;
  never copy a host's spoken number into the snapshot.
- **Open the primary source** for the top 8 items (chrome-mcp for allowlisted sites; web tools
  otherwise). An item you only know from a title or search excerpt must say so in its evidence level.
- Drop any item whose id is in `state.json` unless something materially changed; then show it as
  `Update:` with only the change.

## 2. Judge

Labels (use exactly these):
- **ACT** — worth doing within 48 h AND you can name a concrete first step with its effort.
  Never ACT on a trade; trades cap at WATCH unless a backtest and a defined-risk plan exist.
- **WATCH** — revisit when a named trigger happens (state the trigger).
- **IGNORE** — no "so what" for this reader. Do not show in the body; list the title in "Also seen".

Evidence level (use exactly one): `primary-opened` · `excerpt-only` · `spoken-claim` · `rumor`.
Add a source-bias note when the speaker sells something (course, signals, platform, broker media).

Every item MUST tie to a holding, watchlist name, rulebook rule, project or career target from
`context.md`, by name. No tie, no item.

## 3. Write — fixed layout (see `assets/template.html`)

1. **Header**: date, coverage window, run health in one line (e.g. "opened 7/8 primary sources; 2 data fields n/a").
2. **Top actions (max 3)**: one line each + first step + effort. If nothing qualifies, say "No actions today".
3. **Market snapshot**: the fetched numbers + next events. Then ≤3 regime bullets, each labelled with its evidence level.
4. **Markets / Work & tooling / Career**: ≤5 ranked items per area. Item template, all fields, in order:
   *Claim* (1 line) → *Why it matters to you* (names the holding/rule/project) → *Evidence* (level + bias note)
   → *Next step* or *Trigger* → *Link(s)*.
5. **Rule candidates**: table of NEW or CHANGED candidates only: rule | evidence | what to backtest | label.
   Say once; do not restate items from section 4 — reference them by title.
6. **Also seen**: one line of titles for dropped/IGNORE items (no prose).
7. **Appendix** (collapsed in small grey text): coverage, transcript-quality caveats, source health.
   Feed-maintenance suggestions (paused sources, noisy sources, missing source types) go ONLY here, and
   only when they changed since last run.

Say each fact once. If a point appears in "Top actions", the item below it just gets the details.
Target: ≤ 2 printed pages. Cut before you pad.

## 4. Deliver

1. Save the HTML to `/output/daily-briefing-YYYY-MM-DD.html` (one naming pattern; never `_v2`).
2. Email it with `mail_send` to the address in `context.md` → `delivery.to` ONLY, with the **HTML as the
   message body** (`html=true` / `--html`), subject `Daily briefing YYYY-MM-DD — <N> actions`. Do not
   attach .html files (the mail provider spam-blocks them). Put a plain-text first line in the body
   (e.g. "3 actions · VIX 15.2 · FOMC Oct 28") so previews are useful.
3. Update `assets/state.json` with `skill_manage(action='write_file', ...)` (skills are read-only
   inside the sandbox): keep the ids/titles shown today plus the previous 14 days, drop older.
4. Reply with 3 lines: actions count, file path, email sent yes/no. Nothing else.

## Hard rules

- Market content is hypotheses to test, not advice. Never place or suggest placing a trade.
- Spoken claims are not facts. Auto-captions garble tickers; confirm ticker/number against a data source or mark `spoken-claim`.
- Do not email anyone but `delivery.to`. Do not send anything but this briefing.
- If chrome-mcp reports POLICY_DENIED, a login wall or a bot check, skip that source, note it in the Appendix, and continue.
