# F-scripts

- What: the numbered scripts' contract: they parse, and each carries comment-based help with .SYNOPSIS.
- Drive: `python tools/check.py --help-render`
- End state: `check: PASS`.
- Gotchas: baseline 2026-11-23: `Get-Help .\\0X-*.ps1` shows no synopsis for any script because the
  `#requires` line comes before the help block (`python tools/check.py --get-help` fails). Backlog candidate.
  Never execute 01/02/04 to "test" them: they are destructive and have no dry run.
