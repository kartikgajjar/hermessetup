# Engineering

## Definition of done
A row is done when all three hold:
1. The bar is met: the brief's `Bar:` line (proof command, live drive of mapped features, verdict role).
2. The verdict is recorded: `tools/dispatch.py verify` wrote a PASS or PASS+NOTES record from the role the bar
   names, and its product fingerprint still matches.
3. The merge result is proven: the proof command passed on the merge result (`dispatch.py integrate` locally, or
   the `factory-gate` CI job on the pull request).
An item is `built` when every row is done; `shipped` when `tools/ship.py` released it.

## Release flow
1. Items carry a `release` field in `docs/prd/backlog.json`.
2. `python tools/status.py measures --release R` - review first-time-right, second rounds, escaped defects.
3. T2 at release: security review and dependency/licence audit (advisory), rollback rehearsed when the release
   touches risk paths.
4. `python tools/ship.py R --version X.Y.Z --audit` (or `--audit-waived <reason>`) - always the user's go.
5. Triage the register (`docs/learning-process.md`).

## How briefs are written
- Use the `hs-lead` skill's template: one fixed length; constraints (owned files, interface, do-not-touch, proof
  command, bar) are fixed lines, the rest is a hint.
- `Why:` sits under `Goal:` - who uses the result and what goes wrong if it is wrong.
- `Bar:` comes from `status.py ready`; raise it if needed, never below the T2 floor.
- An item that changes a data shape, a store, an id scheme, an interface or a source gets a one-page design record
  (`docs/design/<item>.md`, linked from the backlog item) before it is split into packets.
