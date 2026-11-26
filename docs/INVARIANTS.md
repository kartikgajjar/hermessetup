# Invariants (rule -> check; a rule with no check is a Gap)

| rule | check |
|---|---|
| No shell call starts with cd/pushd/Set-Location | guard.py; tests/factory/test_guard.py |
| No AI co-author trailer in commits | guard.py + commit-msg hook; test_guard.py, test_hooks_misc.py |
| No polling loops / file watchers | guard.py; test_guard.py |
| State and verdict records written only by status.py / dispatch.py | guard.py + settings deny; test_guard.py |
| No raw git commit/merge/push on the default branch (incl. git -C form) | guard.py; test_guard.py |
| No git stash, commit -a, push, or backlog/VERSION edit in a worktree | guard.py; test_guard.py |
| Agents do not edit high-risk files | guard.py (agent_id); test_guard.py |
| Agents report 'background tasks: none' / stop with no running tasks | stop_guard.py; test_hooks_misc.py |
| Row done only with a standing verdict of the role its bar names | status.py; test_status.py |
| Product change voids a verdict; support-only keeps it | factory_common.standing; test_verdicts.py |
| Verifier output and verdict records match their schemas | dispatch.py; test_dispatch.py |
| No lost update with concurrent writers | status.py lock; test_status.py |
| Changed product files covered by standing verdicts, proof passes, controls pass | gate.py; test_gate.py; CI factory-gate |
| Merge with no human step needs A2 | dispatch.py integrate; test_dispatch.py |
| Working level = lowest(ceiling, tier cap, earned) | factory_common.working_level; test_profile.py |
| Generated agent/skill copies match sources; shared blocks identical | gen.py --check; test_gen.py |
| settings.json matches the working level | profile.py settings --check; test_profile.py |
| Only one packet at a time touches the live Hermes install | Gap: rule only (X3 heavy_lock not installed) |
| Never patch Hermes's own Python source | Gap: rule only (outside this repo) |
