# Daily briefing context  (copy to assets/context.md in the skill and fill in)
# Keep the real file OUT of git. Any value left as <<...>> makes the job stop instead of guessing.

delivery:
  to: <<your email address>>

holdings:            # ticker | instrument (stock/option/ETF) | rough size or "small/medium/large"
  - <<TICKER | instrument | size>>

watchlist:
  - <<TICKER | why you watch it>>

trading_profile:
  style: <<e.g. defined-risk options, premium selling, DTE range>>
  risk_limits: <<max loss per trade / per week>>
  tools: <<e.g. ORATS for backtests>>

rulebook:            # your current rules, one per line, so the briefing can flag overlap/conflict
  - <<rule>>

projects:            # name | what it is | stage | what it needs right now
  - <<Dark Factory | ... | ... | ...>>
  - <<Loop 24 | ... | ... | ...>>
  - <<AI governance scorecard | ... | ... | ...>>
  - <<air-gapped LLM | ... | ... | ...>>

career:
  target_roles: <<e.g. Director/VP, industry>>
  timeline: <<when>>
  positioning_angles: <<what you want to be known for>>

ignore_topics:       # things that never earn a slot
  - <<topic>>
