# Auxiliary model routing — Haiku for simple tasks

> Personal/optional: this documents one machine's config decision. Nothing in
> `01`/`02`/`04`/`05` requires or assumes it.

Hermes has a built-in `auxiliary:` config block (`hermes_cli/config_defaults.py`,
`_aux()` helper) for side tasks separate from the main conversation loop — title
generation, approval classification, memory query rewriting, mail scoring, etc. Every
block defaults to `provider: auto` / `model: ""`, which means **"inherit the main
model"** — by default, Hermes was running these simple/high-volume tasks on the same
model as real conversation (Sonnet 5), not a cheaper one.

## What changed

Set `provider: anthropic` / `model: claude-haiku-4-5-20251001` on these 12 blocks:

```
auxiliary.approval             # classifier — hermes-agent's own comment recommends a cheap model
auxiliary.title_generation
auxiliary.memory_query_rewrite
auxiliary.triage_specifier     # comment: "cheap model OK"
auxiliary.profile_describer    # comment: "short, cheap"
auxiliary.goal_judge
auxiliary.curator              # comment explicitly suggests routing this cheaper
auxiliary.monitor              # comment: "important-mail 0-10 scorer; high-volume, small model fine"
auxiliary.skills_hub
auxiliary.mcp
auxiliary.tts_audio_tags
auxiliary.vision
```

via `hermes config set --force auxiliary.<block>.provider anthropic` /
`...model claude-haiku-4-5-20251001` (two leaf-key calls per block — `hermes config
get auxiliary.<block>` shows the full per-block shape).

## Deliberately left on `auto` (inherit the main/smart model)

- `auxiliary.review` — the `/review` reviewer is "a full subagent" doing real
  code/quality review, not a simple classifier.
- `auxiliary.kanban_decomposer` — comment notes it "emits a JSON graph of child tasks
  (more tokens)", i.e. more structurally complex than `triage_specifier`.
- `auxiliary.compression` — conversation compression affects long-term context
  fidelity; no explicit "cheap is fine" signal from hermes-agent's own comments, so
  left on the main model until/unless compression quality is verified fine on Haiku.

If Haiku turns out to be too weak for any of the 12 switched blocks (bad titles,
mis-scored mail, etc.), that block's `model` is the one to revert to `""` (back to
`auto`/main model) — no need to touch the others.
