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
hardened, Docker-sandboxed, manual-approval baseline config automatically,
and creates the `/output` folder the agent saves reports to (see below).

## Agent output folder (`/output`)

The Docker sandbox is isolated, so files the agent writes inside it (e.g. `/root/report.html`)
aren't normal Windows paths and can't be opened from the GUI. `02` and `04` therefore create one
shared folder and mount it into the sandbox as `/output`; `SOUL.md` tells the agent to save
generated reports there.

- Default location: `%OneDrive%\Documents\Hermes-Output` (so it syncs), or
  `%USERPROFILE%\Documents\Hermes-Output` if OneDrive isn't present. Override by setting the
  `HERMES_OUTPUT_DIR` environment variable before running `02`/`04`. The script prints the path it
  used ("Agent output folder (mounted at /output): ...").
- It's the only host folder the sandbox can see; nothing else on your PC is exposed.
- It's deliberately separate from the backup folder (`...\Documents\Hermes`), which `03` rewrites.
- A mount change only applies to a *new* container. After changing it by hand, remove the running
  one (`docker rm -f hermes-<id>`; safe — working files are bind-mounted from the host).

## Discord Gateway Setup

Discord isn't configured by default on a new install. To enable it:

1. Create a bot at the [Discord Developer Portal](https://discord.com/developers/applications) and copy its token.
2. Add these to `%LOCALAPPDATA%\hermes\.env`:
   ```
   DISCORD_BOT_TOKEN=<your-bot-token>
   DISCORD_ALLOWED_USERS=<your-discord-user-id>
   ```
3. Install the Discord package (optional platforms aren't installed by default):
   ```powershell
   hermes pm install --extra discord
   ```
4. Enable Discord as a platform:
   ```powershell
   hermes gateway setup
   ```
5. Start/restart the gateway:
   ```powershell
   hermes gateway restart
   ```

Once `DISCORD_BOT_TOKEN` is set, `04-restore-hermes-state.ps1` automates
steps 3 and 5 on every future reinstall — this walkthrough only matters
the first time.

## Script

| Script | Purpose |
|---|---|
| `01-cleanup-hermes.ps1` | Fully removes Hermes (config, auth, sessions, Docker containers, PATH/env vars). |
| `02-install-hermes-secure.ps1` | Fresh install via the official installer, with a hardened, Docker-sandboxed, manual-approval baseline applied automatically, plus the `/output` report folder. |
| `03-backup-hermes-state.ps1` | Backs up config, credentials, memories, skills, sessions, cron jobs, platform pairings, plugins, and the autostart shortcut. |
| `04-restore-hermes-state.ps1` | Restores everything `03` backed up onto a fresh install, reinstalls any optional platform packages (e.g. Discord), and starts the gateway. |
| `05-update-hermes.ps1` | Runs `hermes update` with a bounded retry around a known Windows gateway-discovery race instead of letting it abort outright. |
| `06-reap-hermes-containers.ps1` | Removes orphaned Docker sandbox containers (`hermes-agent=1`) left running when a Hermes process dies before its idle reaper fires. `-Install` schedules it every 10 min; `-Now` reaps all idle ones immediately. |

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
