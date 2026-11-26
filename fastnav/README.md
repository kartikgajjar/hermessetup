# fastnav — Jev-style "System 1" browser loop (spike, 2026-10-20)

How it works: the page becomes a numbered list of elements (chrome-mcp `snapshot`). A judge then answers fixed-option questions about it: which action, and which element. Plain code carries out the decision. Low confidence or `blocked` stops the goal as `escalate`, and the report goes to a reasoning agent. Background: the Jev / TypeSafe idea from the Browser Use "Jev Ultrafast" agent. See the module docstrings for the design.

```powershell
# needs: llama-server up (..\..\scripts\start-model.ps1, production Hermes-2-Pro), Chrome running, chrome-mcp paired
python -I fastnav\loop.py --url https://medium.com --goal "open Medium's About page"
python -I fastnav\loop.py --url https://medium.com --goal "..." --dry-run      # judge step 1 only, act on nothing
python -I fastnav\loop.py --url ... --goal ... --judge jev                    # needs TYPESAFE_API_KEY (API shape unverified)
```

Exit code 0 means every goal was verified done. Exit code 2 means it escalated: the JSON on stdout contains the last state and history. Each step is logged to `fastnav\runs\*.jsonl`.

## What the first live runs showed (medium.com, local judge)

- **Picking an action or element works well.** Synthetic pages scored ≥ 0.97. Real medium.com picks were right at 0.80–0.99 confidence.
- **Ask for the element only after choosing the action.** Asked blind, with a "0 = none" option, the right link scored 0.43. Told "the agent will CLICK…", the same link scored 0.93. The loop now asks in that order.
- **Speed:** about 3–6 s per step with the local judge on the RTX 4050 (prefill of the element list). The vendor's figure for Jev is about 0.3 s.
- **Checking "done" is the weak part, as the video warned.**
  - The judge never answers `done`. On the target page it kept clicking the link to the page it was already on, so the loop has a repeat guard that turns this into a done claim.
  - The local 8B model then **rejects a correct done**: it said NO to medium.com/about both as a free-form verifier and as a yes/no judge.
  - Result: the loop fails safe and escalates, but it can't complete a goal unattended. The verifier needs a stronger model (Claude, or the agent calling fastnav).

Not done yet: Claude as verifier (no `ANTHROPIC_API_KEY` here), `JevJudge` against the real API, file-input and iframe handling beyond a note.
