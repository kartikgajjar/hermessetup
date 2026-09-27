# hermessetup

PowerShell scripts that make installing, updating, and reinstalling
[Hermes Agent](https://hermes-agent.nousresearch.com/) on Windows simple
and repeatable.

Hermes's official installer works fine on its own, but its state is spread
across dozens of files and folders under `%LOCALAPPDATA%\hermes`. These
scripts turn "reinstall Hermes from scratch" into one safe command that
brings back your configuration, credentials, cron jobs, and platform
integrations exactly as they were.

## Scripts

| Script | Purpose |
|---|---|
| `01-cleanup-hermes.ps1` | Fully removes Hermes (config, auth, sessions, Docker containers, PATH/env vars). |
| `02-install-hermes-secure.ps1` | Fresh install via the official installer, with a hardened, Docker-sandboxed, manual-approval baseline applied automatically. |
| `03-backup-hermes-state.ps1` | Backs up config, credentials, memories, skills, sessions, cron jobs, platform pairings, plugins, and the autostart shortcut. |
| `04-restore-hermes-state.ps1` | Restores everything `03` backed up onto a fresh install, reinstalls any optional platform packages (e.g. Discord), and starts the gateway. |
| `05-update-hermes.ps1` | Runs `hermes update` with a bounded retry around a known Windows gateway-discovery race instead of letting it abort outright. |

## Daily reinstall workflow

```powershell
.\03-backup-hermes-state.ps1
.\01-cleanup-hermes.ps1 -Force
.\02-install-hermes-secure.ps1
.\04-restore-hermes-state.ps1
```

Backup location defaults to `%USERPROFILE%\OneDrive\Documents\Hermes`;
override with `-Destination` / `-Source` on the backup/restore scripts.

## Requirements

- Windows, PowerShell 5.1+
- Docker Desktop (used as the sandboxed terminal backend)
