# F-update-plan

- What: `05-update-hermes.ps1 -Plan` runs `hermes update --plan` once, read-only for the install.
- Drive: `pwsh -NoProfile -File 05-update-hermes.ps1 -Plan`
- End state: exit 0 and a plan printed.
- Gotchas: it reads the Hermes upstream (external pull): wait list, the user approves each run.
