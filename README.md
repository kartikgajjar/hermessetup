# Hermes Setup

PowerShell scripts that make installing, updating, and reinstalling
[Hermes Agent](https://hermes-agent.nousresearch.com/) on Windows simple
and repeatable.

Hermes's official installer works fine on its own, but its state is spread
across dozens of files and folders under `%LOCALAPPDATA%\hermes`. These
scripts turn "reinstall Hermes from scratch" into one safe command that
brings back your configuration, credentials, cron jobs, and platform
integrations exactly as they were.

## Installation

Clone this repo, then run the install script from an elevated PowerShell
prompt:

```powershell
git clone https://github.com/kartikgajjar/hermessetup.git
cd hermessetup
.\02-install-hermes-secure.ps1
```

This installs Hermes Agent fresh via the official installer and applies a
hardened, Docker-sandboxed, manual-approval baseline config automatically.

## Script

| Script | Purpose |
|---|---|
| `01-cleanup-hermes.ps1` | Fully removes Hermes (config, auth, sessions, Docker containers, PATH/env vars). |
| `02-install-hermes-secure.ps1` | Fresh install via the official installer, with a hardened, Docker-sandboxed, manual-approval baseline applied automatically. |
| `03-backup-hermes-state.ps1` | Backs up config, credentials, memories, skills, sessions, cron jobs, platform pairings, plugins, and the autostart shortcut. |
| `04-restore-hermes-state.ps1` | Restores everything `03` backed up onto a fresh install, reinstalls any optional platform packages (e.g. Discord), and starts the gateway. |
| `05-update-hermes.ps1` | Runs `hermes update` with a bounded retry around a known Windows gateway-discovery race instead of letting it abort outright. |

## Clean Re-install

Run these four scripts in order, from an elevated PowerShell prompt, to
wipe and reinstall Hermes from scratch while keeping everything you've
configured:

```powershell
.\03-backup-hermes-state.ps1          # 1. Save current state to OneDrive
.\01-cleanup-hermes.ps1 -Force        # 2. Fully remove Hermes
.\02-install-hermes-secure.ps1        # 3. Fresh install + hardened baseline
.\04-restore-hermes-state.ps1         # 4. Restore state, packages, gateway
```

- Backup location defaults to `%USERPROFILE%\OneDrive\Documents\Hermes`;
  override with `-Destination` (script `03`) / `-Source` (script `04`).
- Step 4 also reinstalls any optional platform package your `.env` needs
  (e.g. Discord) and starts the gateway for you — no manual restart needed.
- Run this whenever you want the latest Hermes commit without losing
  config, credentials, cron jobs, or platform pairings.

## Requirements

- Windows, PowerShell 5.1+
- Docker Desktop (used as the sandboxed terminal backend)
